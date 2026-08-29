"""Identity resolution and normalized catalog writes.

The resolver is intentionally conservative. Hard external IDs are preferred.
Exact title/date matching is only used when it yields one plausible candidate;
there is no automatic fuzzy merge.
"""

from __future__ import annotations

from collections import Counter
import json
import logging
import sqlite3
from typing import Iterable
import uuid

from gmd.db import run_json, utcnow
from gmd.models import ExternalID, NormalizedTitle
from gmd.normalize import clean_text, normalize_title, payload_hash, stable_id

LOGGER = logging.getLogger(__name__)

FIELD_RANKS: dict[str, dict[str, int]] = {
    "title": {"tmdb": 95, "tvdb": 90, "tvmaze": 85, "simkl": 55},
    "overview": {"tmdb": 95, "tvmaze": 88, "tvdb": 80, "simkl": 0},
    "poster": {"tmdb": 95, "tvdb": 90, "tvmaze": 85, "simkl": 0},
    "format": {"tvdb": 95, "tvmaze": 90, "tmdb": 85, "simkl": 55},
}
SOURCE_DATE_WEIGHT = {
    "tvmaze": 3.0,
    "tvdb": 2.5,
    "simkl": 2.25,
    "tmdb": 2.0,
    "seed": 1.0,
}


class CatalogWriter:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def ingest(self, item: NormalizedTitle, *, observed_at: str | None = None) -> str:
        item.ensure_primary_id()
        observed_at = observed_at or utcnow()

        title_id = self._resolve_title(item)
        self._upsert_title(title_id, item, observed_at)
        self._upsert_identities(title_id, item)
        self._upsert_aliases(title_id, item)
        self._upsert_countries(title_id, item)
        self._upsert_genres(title_id, item)
        self._upsert_networks(title_id, item)
        self._upsert_quality_flags(title_id, item)
        self._upsert_events(title_id, item, observed_at)
        self._upsert_source_record(title_id, item, observed_at)
        self._recompute_title_confidence(title_id, observed_at)
        return title_id

    def _resolve_title(self, item: NormalizedTitle) -> str:
        identity_matches: list[str] = []
        for external in item.external_ids:
            row = self.connection.execute(
                "SELECT title_id FROM identity_keys WHERE key = ?",
                (external.key,),
            ).fetchone()
            if not row:
                continue
            candidate = str(row["title_id"])
            if external.source == item.source or self._identity_compatible(
                candidate, item
            ):
                identity_matches.append(candidate)
            else:
                LOGGER.warning(
                    "remote identity rejected by sanity checks",
                    extra={
                        "structured": {
                            "incoming": f"{item.source}:{item.source_id}",
                            "remote_key": external.key,
                            "candidate": candidate,
                        }
                    },
                )

        if identity_matches:
            counts = Counter(identity_matches)
            selected, _ = counts.most_common(1)[0]
            if len(counts) > 1:
                self._flag_identity_conflict(selected, item, counts)
            return selected

        exact = self._strict_exact_match(item)
        if exact:
            return exact

        # Stable within a provider; later hard IDs can attach to the same record.
        return stable_id("title", item.source, item.source_id)

    def _identity_compatible(
        self,
        title_id: str,
        item: NormalizedTitle,
    ) -> bool:
        incoming_aliases = {
            normalize_title(item.title),
            normalize_title(item.original_title),
            *(normalize_title(alias.value) for alias in item.aliases),
        }
        incoming_aliases.discard("")
        existing_aliases = {
            str(row["normalized_alias"])
            for row in self.connection.execute(
                "SELECT normalized_alias FROM aliases WHERE title_id = ?",
                (title_id,),
            )
        }
        title_match = bool(incoming_aliases.intersection(existing_aliases))

        incoming_date = next(
            (
                event.date
                for event in item.events
                if event.event_type == "series_premiere"
            ),
            None,
        )
        row = self.connection.execute(
            """
            SELECT event_date FROM events
            WHERE title_id = ? AND event_type = 'series_premiere'
            ORDER BY event_date LIMIT 1
            """,
            (title_id,),
        ).fetchone()
        date_match = False
        if incoming_date and row and row["event_date"]:
            difference = self.connection.execute(
                "SELECT ABS(julianday(?) - julianday(?))",
                (incoming_date, str(row["event_date"])),
            ).fetchone()[0]
            date_match = difference is not None and float(difference) <= 7.0

        existing_countries = {
            str(row["country_code"])
            for row in self.connection.execute(
                "SELECT DISTINCT country_code FROM countries WHERE title_id = ?",
                (title_id,),
            )
        }
        country_match = bool(
            item.countries
            and existing_countries
            and item.countries.intersection(existing_countries)
        )

        corroborating_remote_ids = sum(
            1
            for external in item.external_ids
            if external.source != item.source
            and self.connection.execute(
                "SELECT 1 FROM identity_keys WHERE key = ? AND title_id = ?",
                (external.key, title_id),
            ).fetchone()
        )
        corroborated_identity = corroborating_remote_ids >= 2 and title_match

        return corroborated_identity or sum((title_match, date_match, country_match)) >= 2

    def _strict_exact_match(self, item: NormalizedTitle) -> str | None:
        premiere_date = next(
            (event.date for event in item.events if event.event_type == "series_premiere"),
            None,
        )
        aliases = {
            normalize_title(item.title),
            normalize_title(item.original_title),
            *(normalize_title(alias.value) for alias in item.aliases),
        }
        aliases.discard("")
        if not premiere_date or not aliases:
            return None

        placeholders = ",".join("?" for _ in aliases)
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT a.title_id
            FROM aliases AS a
            JOIN events AS e ON e.title_id = a.title_id
            WHERE a.normalized_alias IN ({placeholders})
              AND e.event_type = 'series_premiere'
              AND ABS(julianday(e.event_date) - julianday(?)) <= 1
            """,
            (*sorted(aliases), premiere_date),
        ).fetchall()
        candidates = [str(row["title_id"]) for row in rows]
        if not candidates:
            return None
        if len(candidates) == 1:
            candidate = candidates[0]
            if self._countries_compatible(candidate, item.countries):
                return candidate
            return None

        compatible = [
            candidate
            for candidate in candidates
            if self._countries_compatible(candidate, item.countries)
        ]
        return compatible[0] if len(compatible) == 1 else None

    def _countries_compatible(self, title_id: str, incoming: set[str]) -> bool:
        if not incoming:
            return True
        existing = {
            str(row["country_code"])
            for row in self.connection.execute(
                "SELECT DISTINCT country_code FROM countries WHERE title_id = ?",
                (title_id,),
            )
        }
        return not existing or bool(existing.intersection(incoming))

    def _flag_identity_conflict(
        self,
        selected: str,
        item: NormalizedTitle,
        counts: Counter[str],
    ) -> None:
        detail = (
            f"{item.source}:{item.source_id} points at multiple canonical records: "
            + ", ".join(f"{key} ({count})" for key, count in counts.items())
        )
        self.connection.execute(
            """
            INSERT INTO quality_flags(title_id, flag, source, detail)
            VALUES (?, 'identity_conflict', ?, ?)
            ON CONFLICT(title_id, flag, source) DO UPDATE SET detail = excluded.detail
            """,
            (selected, item.source, detail),
        )
        LOGGER.warning(
            "identity conflict",
            extra={"structured": {"title_id": selected, "detail": detail}},
        )

    def _upsert_title(
        self,
        title_id: str,
        item: NormalizedTitle,
        observed_at: str,
    ) -> None:
        row = self.connection.execute(
            "SELECT * FROM titles WHERE id = ?", (title_id,)
        ).fetchone()

        title_rank = FIELD_RANKS["title"].get(item.source, 50)
        overview_rank = FIELD_RANKS["overview"].get(item.source, 50)
        poster_rank = FIELD_RANKS["poster"].get(item.source, 50)
        format_rank = FIELD_RANKS["format"].get(item.source, 50)

        if row is None:
            self.connection.execute(
                """
                INSERT INTO titles(
                    id, canonical_title, original_title, overview,
                    original_language, format, status, runtime_minutes,
                    poster_url, backdrop_url, first_air_date, date_conflict,
                    confidence, title_rank, overview_rank, poster_rank,
                    format_rank, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 0.50, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title_id,
                    item.title,
                    item.original_title,
                    item.overview,
                    item.original_language,
                    item.format,
                    item.status,
                    item.runtime_minutes,
                    item.poster_url,
                    item.backdrop_url,
                    title_rank,
                    overview_rank if item.overview else 0,
                    poster_rank if item.poster_url else 0,
                    format_rank,
                    observed_at,
                    observed_at,
                ),
            )
            return

        canonical_title = str(row["canonical_title"])
        current_title_rank = int(row["title_rank"])
        if item.title and (
            title_rank > current_title_rank
            or (title_rank == current_title_rank and len(item.title) < len(canonical_title))
        ):
            canonical_title = item.title
            current_title_rank = title_rank

        overview = str(row["overview"] or "")
        current_overview_rank = int(row["overview_rank"])
        if item.overview and (
            overview_rank > current_overview_rank
            or (
                overview_rank == current_overview_rank
                and len(item.overview) > len(overview)
            )
        ):
            overview = item.overview
            current_overview_rank = overview_rank

        poster_url = row["poster_url"]
        current_poster_rank = int(row["poster_rank"])
        if item.poster_url and poster_rank >= current_poster_rank:
            poster_url = item.poster_url
            current_poster_rank = poster_rank

        current_format = str(row["format"] or "Unknown")
        current_format_rank = int(row["format_rank"])
        if item.format and (
            format_rank > current_format_rank or current_format == "Unknown"
        ):
            current_format = item.format
            current_format_rank = format_rank

        self.connection.execute(
            """
            UPDATE titles
            SET canonical_title = ?,
                original_title = COALESCE(NULLIF(?, ''), original_title),
                overview = ?,
                original_language = COALESCE(NULLIF(?, ''), original_language),
                format = ?,
                status = COALESCE(NULLIF(?, ''), status),
                runtime_minutes = COALESCE(?, runtime_minutes),
                poster_url = ?,
                backdrop_url = COALESCE(NULLIF(?, ''), backdrop_url),
                title_rank = ?,
                overview_rank = ?,
                poster_rank = ?,
                format_rank = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                canonical_title,
                item.original_title or "",
                overview,
                item.original_language or "",
                current_format,
                item.status or "",
                item.runtime_minutes,
                poster_url,
                item.backdrop_url or "",
                current_title_rank,
                current_overview_rank,
                current_poster_rank,
                current_format_rank,
                observed_at,
                title_id,
            ),
        )

    def _upsert_identities(self, title_id: str, item: NormalizedTitle) -> None:
        for external in item.external_ids:
            row = self.connection.execute(
                "SELECT title_id FROM identity_keys WHERE key = ?",
                (external.key,),
            ).fetchone()
            if row and str(row["title_id"]) != title_id:
                self.connection.execute(
                    """
                    INSERT INTO quality_flags(title_id, flag, source, detail)
                    VALUES (?, 'identity_key_collision', ?, ?)
                    ON CONFLICT(title_id, flag, source) DO UPDATE SET
                        detail = excluded.detail
                    """,
                    (
                        title_id,
                        item.source,
                        f"{external.key} already belongs to {row['title_id']}",
                    ),
                )
                continue

            # Provider IDs become canonical only when observed from that provider.
            # Cross-provider IDs may resolve an already-known key, but a lone remote
            # claim is not allowed to reserve a future provider ID. This prevents a
            # bad remote ID from poisoning later imports. IMDb is accepted from
            # TMDB/TVmaze because there is no IMDb collector and those endpoints are
            # used as corroborating bridges.
            if not row and not self._identity_is_publishable(item, external):
                continue

            self.connection.execute(
                """
                INSERT INTO identity_keys(
                    key, title_id, source, external_id, source_url
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    source_url = COALESCE(
                        excluded.source_url, identity_keys.source_url
                    )
                """,
                (
                    external.key,
                    title_id,
                    external.source,
                    external.value,
                    external.url,
                ),
            )

    @staticmethod
    def _identity_is_publishable(
        item: NormalizedTitle,
        external: ExternalID,
    ) -> bool:
        if external.source == item.source:
            return True
        return external.source == "imdb" and item.source in {"tmdb", "tvmaze"}

    def _upsert_aliases(self, title_id: str, item: NormalizedTitle) -> None:
        values = [(item.title, None), (item.original_title or "", None)]
        values.extend((alias.value, alias.language) for alias in item.aliases)
        for alias, language in values:
            alias = clean_text(alias)
            normalized = normalize_title(alias)
            if not alias or not normalized:
                continue
            self.connection.execute(
                """
                INSERT INTO aliases(title_id, alias, normalized_alias, language, source)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(title_id, normalized_alias, source) DO UPDATE SET
                    alias = excluded.alias,
                    language = COALESCE(excluded.language, aliases.language)
                """,
                (title_id, alias, normalized, language, item.source),
            )

    def _upsert_countries(self, title_id: str, item: NormalizedTitle) -> None:
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO countries(title_id, country_code, source)
            VALUES (?, ?, ?)
            """,
            [(title_id, country, item.source) for country in sorted(item.countries)],
        )

    def _upsert_genres(self, title_id: str, item: NormalizedTitle) -> None:
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO genres(title_id, genre, source)
            VALUES (?, ?, ?)
            """,
            [(title_id, genre, item.source) for genre in sorted(item.genres)],
        )

    def _upsert_networks(self, title_id: str, item: NormalizedTitle) -> None:
        for network in item.networks:
            self.connection.execute(
                """
                INSERT INTO networks(
                    title_id, network_name, network_country, network_type, source
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(title_id, network_name, source) DO UPDATE SET
                    network_country = COALESCE(excluded.network_country, networks.network_country),
                    network_type = COALESCE(excluded.network_type, networks.network_type)
                """,
                (
                    title_id,
                    network.name,
                    network.country,
                    network.kind,
                    item.source,
                ),
            )

    def _upsert_quality_flags(self, title_id: str, item: NormalizedTitle) -> None:
        for flag, detail in item.quality_flags:
            self.connection.execute(
                """
                INSERT INTO quality_flags(title_id, flag, source, detail)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(title_id, flag, source) DO UPDATE SET detail = excluded.detail
                """,
                (title_id, flag, item.source, detail),
            )

    def _upsert_events(
        self,
        title_id: str,
        item: NormalizedTitle,
        observed_at: str,
    ) -> None:
        for observation in item.events:
            if observation.event_type == "series_premiere":
                season = -1
                episode = -1
                country = ""
                network = ""
            else:
                season = observation.season_number or -1
                episode = observation.episode_number or -1
                country = observation.country or ""
                network = observation.network or ""

            event_id = stable_id(
                "event",
                title_id,
                observation.event_type,
                season,
                episode,
                country,
                network,
            )
            self.connection.execute(
                """
                INSERT INTO events(
                    id, title_id, event_type, event_date, season_number,
                    episode_number, country_code, network_name, confidence,
                    date_conflict, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(
                    title_id, event_type, season_number, episode_number,
                    country_code, network_name
                ) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (
                    event_id,
                    title_id,
                    observation.event_type,
                    observation.date,
                    season,
                    episode,
                    country,
                    network,
                    observation.confidence,
                    observed_at,
                    observed_at,
                ),
            )

            # Resolve the ID in case the UNIQUE conflict found an older event ID.
            row = self.connection.execute(
                """
                SELECT id FROM events
                WHERE title_id = ? AND event_type = ?
                  AND season_number = ? AND episode_number = ?
                  AND country_code = ? AND network_name = ?
                """,
                (title_id, observation.event_type, season, episode, country, network),
            ).fetchone()
            actual_event_id = str(row["id"])

            self.connection.execute(
                """
                INSERT INTO event_evidence(
                    event_id, source, source_record_id, reported_date,
                    source_url, observed_at, raw_hash, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, source, source_record_id) DO UPDATE SET
                    reported_date = excluded.reported_date,
                    source_url = COALESCE(excluded.source_url, event_evidence.source_url),
                    observed_at = excluded.observed_at,
                    raw_hash = excluded.raw_hash,
                    confidence = excluded.confidence
                """,
                (
                    actual_event_id,
                    item.source,
                    observation.source_record_id,
                    observation.date,
                    observation.source_url,
                    observed_at,
                    payload_hash(item.raw),
                    observation.confidence,
                ),
            )
            self._recompute_event(actual_event_id, observed_at)

    def _recompute_event(self, event_id: str, observed_at: str) -> None:
        rows = self.connection.execute(
            """
            SELECT source, reported_date, confidence
            FROM event_evidence
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchall()
        if not rows:
            return

        weighted: Counter[str] = Counter()
        sources_by_date: dict[str, set[str]] = {}
        for row in rows:
            source = str(row["source"])
            date_value = str(row["reported_date"])
            weighted[date_value] += SOURCE_DATE_WEIGHT.get(source, 1.0)
            sources_by_date.setdefault(date_value, set()).add(source)

        max_weight = max(weighted.values())
        tied = sorted(
            date_value
            for date_value, weight in weighted.items()
            if weight == max_weight
        )
        chosen = self._break_date_tie(tied, rows)
        conflict = int(len(weighted) > 1)
        source_count = len({str(row["source"]) for row in rows})
        agreeing = len(sources_by_date.get(chosen, set()))
        confidence = min(0.99, 0.45 + (0.12 * source_count) + (0.10 * agreeing))
        if conflict:
            confidence = max(0.45, confidence - 0.08)

        self.connection.execute(
            """
            UPDATE events
            SET event_date = ?, confidence = ?, date_conflict = ?, updated_at = ?
            WHERE id = ?
            """,
            (chosen, round(confidence, 3), conflict, observed_at, event_id),
        )
        event = self.connection.execute(
            "SELECT title_id, event_type FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        if event and event["event_type"] == "series_premiere":
            self.connection.execute(
                """
                UPDATE titles
                SET first_air_date = ?, date_conflict = ?, updated_at = ?
                WHERE id = ?
                """,
                (chosen, conflict, observed_at, event["title_id"]),
            )

    @staticmethod
    def _break_date_tie(tied: list[str], rows: Iterable[sqlite3.Row]) -> str:
        if len(tied) == 1:
            return tied[0]
        priority = ("tvmaze", "tvdb", "tmdb")
        for source in priority:
            for row in rows:
                if str(row["source"]) == source and str(row["reported_date"]) in tied:
                    return str(row["reported_date"])
        return tied[0]

    def _upsert_source_record(
        self,
        title_id: str,
        item: NormalizedTitle,
        observed_at: str,
    ) -> None:
        compact_payload = {
            "source": item.source,
            "source_id": item.source_id,
            "title": item.title,
            "original_title": item.original_title,
            "source_url": item.source_url,
            "external_ids": [
                {"source": value.source, "value": value.value}
                for value in item.external_ids
            ],
            "events": [
                {"type": value.event_type, "date": value.date}
                for value in item.events
            ],
            "raw_keys": sorted(str(key) for key in item.raw),
        }
        self.connection.execute(
            """
            INSERT INTO source_records(
                source, external_id, title_id, fetched_at, source_updated_at,
                payload_hash, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, external_id) DO UPDATE SET
                title_id = excluded.title_id,
                fetched_at = excluded.fetched_at,
                source_updated_at = excluded.source_updated_at,
                payload_hash = excluded.payload_hash,
                payload_json = excluded.payload_json
            """,
            (
                item.source,
                item.source_id,
                title_id,
                observed_at,
                item.source_updated_at,
                payload_hash(item.raw),
                run_json(compact_payload),
            ),
        )

    def _recompute_title_confidence(self, title_id: str, observed_at: str) -> None:
        source_count = int(
            self.connection.execute(
                """
                SELECT COUNT(DISTINCT source)
                FROM source_records
                WHERE title_id = ?
                """,
                (title_id,),
            ).fetchone()[0]
        )
        hard_ids = int(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM identity_keys
                WHERE title_id = ?
                  AND source IN ('tmdb', 'tvdb', 'tvmaze', 'simkl', 'imdb')
                """,
                (title_id,),
            ).fetchone()[0]
        )
        severe_flags = int(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM quality_flags
                WHERE title_id = ? AND flag IN (
                    'identity_conflict',
                    'identity_key_collision',
                    'provider_problematic_entry'
                )
                """,
                (title_id,),
            ).fetchone()[0]
        )
        confidence = min(0.99, 0.42 + (0.14 * source_count) + (0.05 * min(hard_ids, 4)))
        confidence = max(0.20, confidence - (0.18 * severe_flags))
        self.connection.execute(
            "UPDATE titles SET confidence = ?, updated_at = ? WHERE id = ?",
            (round(confidence, 3), observed_at, title_id),
        )


def new_run_id() -> str:
    return uuid.uuid4().hex

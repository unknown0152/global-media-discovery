"""Simkl Calendar v2 evidence parsing and private coverage probes.

Only premiere and finale evidence is normalized. Regular episode airings are
deliberately ignored, and raw Simkl metadata is not used as a replacement for
TMDB or TheTVDB.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from gmd import __version__
from gmd.collector.http import HTTPClient
from gmd.db import connect_ro
from gmd.models import EventObservation, ExternalID, Network, NormalizedTitle
from gmd.normalize import clean_text, normalize_country, normalize_language

SIMKL_CALENDAR_BASE = "https://data.simkl.in/calendar/v2"
SIMKL_CATALOGS = ("tv", "anime")
MATCHABLE_ID_SOURCES = ("tmdb", "tvdb", "imdb")
FINALE_EVENT_TYPES = {
    1: "midseason_finale",
    2: "season_finale",
    3: "series_finale",
}


def _positive_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Simkl {field} must be a positive integer")
    return value


def _utc_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("Simkl calendar date must be an ISO 8601 UTC timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Simkl calendar date is invalid") from error
    return value


def _calendar_day(value: object) -> str | None:
    text = clean_text(value)[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _integer_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def validate_calendar_payload(payload: object) -> dict[str, Any]:
    """Validate the v2 join contract and return a bounded summary."""
    if not isinstance(payload, Mapping):
        raise ValueError("Simkl Calendar v2 response must be an object")
    calendar = payload.get("calendar")
    metadata = payload.get("metadata")
    if not isinstance(calendar, list) or not isinstance(metadata, Mapping):
        raise ValueError("Simkl Calendar v2 response needs calendar and metadata")

    metadata_ids: set[int] = set()
    for raw_key, raw_item in metadata.items():
        try:
            key_id = int(str(raw_key))
        except ValueError as error:
            raise ValueError("Simkl metadata key must be a numeric ID") from error
        _positive_integer(key_id, field="metadata key")
        if not isinstance(raw_item, Mapping):
            raise ValueError("Simkl metadata record must be an object")
        ids = raw_item.get("ids")
        if not isinstance(ids, Mapping):
            raise ValueError("Simkl metadata record is missing ids")
        item_id = ids.get("simkl_id", ids.get("simkl"))
        if _positive_integer(item_id, field="metadata ID") != key_id:
            raise ValueError("Simkl metadata key and record ID disagree")
        title = raw_item.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Simkl metadata record is missing a title")
        metadata_ids.add(key_id)

    dates: list[str] = []
    schedule_keys: set[tuple[object, ...]] = set()
    duplicate_entries = 0
    referenced_ids: set[int] = set()
    finale_counts: Counter[str] = Counter()
    schedule_types: Counter[str] = Counter()
    for raw_entry in calendar:
        if not isinstance(raw_entry, Mapping):
            raise ValueError("Simkl calendar entry must be an object")
        simkl_id = _positive_integer(raw_entry.get("simkl_id"), field="calendar ID")
        if simkl_id not in metadata_ids:
            raise ValueError("Simkl calendar entry has no matching metadata")
        timestamp = _utc_timestamp(raw_entry.get("date"))
        referenced_ids.add(simkl_id)
        dates.append(timestamp)
        episode = raw_entry.get("episode")
        season_number = None
        episode_number = None
        if episode is not None:
            if not isinstance(episode, Mapping):
                raise ValueError("Simkl episode value must be an object")
            season_number = _integer_or_none(episode.get("season"))
            episode_number = _integer_or_none(episode.get("episode"))
            if episode_number == 1 and season_number in {None, 1}:
                schedule_types["series_premiere_candidate"] += 1
            elif episode_number == 1:
                schedule_types["season_premiere"] += 1
            else:
                schedule_types["regular_episode"] += 1
        else:
            schedule_types["release_without_episode"] += 1
        schedule_key = (simkl_id, timestamp, season_number, episode_number)
        if schedule_key in schedule_keys:
            duplicate_entries += 1
        schedule_keys.add(schedule_key)
        finale_type = _integer_or_none(raw_entry.get("finale_type"))
        finale_counts[str(finale_type) if finale_type is not None else "none"] += 1

    orphan_metadata = len(metadata_ids - referenced_ids)
    return {
        "calendar_entries": len(calendar),
        "metadata_records": len(metadata_ids),
        "referenced_titles": len(referenced_ids),
        "duplicate_schedule_entries": duplicate_entries,
        "orphan_metadata_records": orphan_metadata,
        "coverage_start": min(dates) if dates else None,
        "coverage_end": max(dates) if dates else None,
        "finale_types": dict(sorted(finale_counts.items())),
        "schedule_types": dict(sorted(schedule_types.items())),
    }


class SimklCalendarCollector:
    """Fetch the public, edge-cached Simkl Calendar v2 TV feed."""

    def __init__(self, client_id: str, http: HTTPClient, *, app_version: str) -> None:
        client_id = client_id.strip()
        if not client_id or len(client_id) > 256 or any(
            character.isspace() for character in client_id
        ):
            raise ValueError("a valid Simkl Client ID is required")
        self.client_id = client_id
        self.http = http
        self.app_version = app_version

    def calendar(self, catalog: str = "tv") -> Mapping[str, Any]:
        if catalog not in SIMKL_CATALOGS:
            raise ValueError(f"unsupported Simkl calendar: {catalog}")
        payload = self.http.request_json(
            f"{SIMKL_CALENDAR_BASE}/{catalog}.json",
            params={
                "client_id": self.client_id,
                "app-name": "global-media-discovery",
                "app-version": self.app_version,
            },
        )
        validate_calendar_payload(payload)
        if not isinstance(payload, Mapping):
            raise ValueError("validated Simkl payload is unavailable")
        return payload


def normalize_simkl_tv(
    payload: Mapping[str, Any],
    *,
    start: date,
    end: date,
) -> list[NormalizedTitle]:
    """Normalize TV premieres/finales while excluding ordinary episodes."""
    validate_calendar_payload(payload)
    calendar = payload.get("calendar")
    metadata = payload.get("metadata")
    if not isinstance(calendar, list) or not isinstance(metadata, Mapping):
        raise ValueError("validated Simkl payload is unavailable")

    relevant: dict[int, list[Mapping[str, Any]]] = {}
    for raw_entry in calendar:
        if not isinstance(raw_entry, Mapping):
            continue
        simkl_id = _positive_integer(raw_entry.get("simkl_id"), field="calendar ID")
        event_day = _calendar_day(raw_entry.get("date"))
        if not event_day or not (start.isoformat() <= event_day <= end.isoformat()):
            continue
        episode = raw_entry.get("episode")
        season_number = None
        episode_number = None
        if isinstance(episode, Mapping):
            season_number = _integer_or_none(episode.get("season"))
            episode_number = _integer_or_none(episode.get("episode"))
        finale_type = _integer_or_none(raw_entry.get("finale_type"))
        is_premiere = episode_number == 1
        is_finale = finale_type in FINALE_EVENT_TYPES
        if is_premiere or is_finale:
            relevant.setdefault(simkl_id, []).append(raw_entry)

    normalized: list[NormalizedTitle] = []
    for simkl_id, entries in sorted(relevant.items()):
        raw_metadata = metadata.get(str(simkl_id))
        if not isinstance(raw_metadata, Mapping):
            raise ValueError(f"Simkl metadata is missing for {simkl_id}")
        normalized.append(
            _normalize_simkl_title(
                simkl_id,
                raw_metadata,
                entries,
                start=start,
                end=end,
            )
        )
    return normalized


def _normalize_simkl_title(
    simkl_id: int,
    metadata: Mapping[str, Any],
    entries: list[Mapping[str, Any]],
    *,
    start: date,
    end: date,
) -> NormalizedTitle:
    title = clean_text(metadata.get("title")) or f"Simkl {simkl_id}"
    ids = metadata.get("ids")
    if not isinstance(ids, Mapping):
        raise ValueError(f"Simkl metadata IDs are missing for {simkl_id}")
    slug = clean_text(ids.get("slug"))
    source_url = f"https://simkl.com/tv/{simkl_id}/{slug}".rstrip("/")
    country = normalize_country(metadata.get("country"))
    language = normalize_language(metadata.get("original_language"))
    network_name = clean_text(metadata.get("network"))

    item = NormalizedTitle(
        source="simkl",
        source_id=str(simkl_id),
        title=title,
        original_language=language,
        format="TV Series",
        status=clean_text(metadata.get("status")) or None,
        source_url=source_url,
        countries={country} if country else set(),
        networks=(
            [Network(network_name, country, "Schedule evidence")]
            if network_name
            else []
        ),
        raw={
            "simkl_id": simkl_id,
            "title": title,
            "ids": {
                key: ids.get(key)
                for key in ("simkl_id", "slug", *MATCHABLE_ID_SOURCES)
                if ids.get(key) is not None
            },
            "country": country,
            "original_language": language,
            "network": network_name or None,
            "calendar": [dict(entry) for entry in entries],
        },
    )
    item.external_ids.append(ExternalID("simkl", str(simkl_id), source_url))
    for source in MATCHABLE_ID_SOURCES:
        value = clean_text(ids.get(source))
        if not value:
            continue
        item.external_ids.append(ExternalID(source, value, _external_url(source, value)))

    seen: set[tuple[str, int | None, int | None, str]] = set()
    for entry in entries:
        event_day = _calendar_day(entry.get("date"))
        episode = entry.get("episode")
        if not event_day:
            continue
        season_number = None
        episode_number = None
        if isinstance(episode, Mapping):
            season_number = _integer_or_none(episode.get("season"))
            episode_number = _integer_or_none(episode.get("episode"))
        if episode_number == 1:
            event_type = (
                "series_premiere" if season_number in {None, 1} else "season_premiere"
            )
            _append_event(
                item,
                seen,
                event_type=event_type,
                event_day=event_day,
                season_number=season_number,
                episode_number=episode_number,
                country=country,
                network=network_name,
                source_url=source_url,
            )
        finale_type = _integer_or_none(entry.get("finale_type"))
        if finale_type in FINALE_EVENT_TYPES:
            _append_event(
                item,
                seen,
                event_type=FINALE_EVENT_TYPES[finale_type],
                event_day=event_day,
                season_number=season_number,
                episode_number=episode_number,
                country=country,
                network=network_name,
                source_url=source_url,
            )

    if not any(event.event_type == "series_premiere" for event in item.events):
        release_day = _calendar_day(metadata.get("release_date"))
        if release_day and start.isoformat() <= release_day <= end.isoformat():
            _append_event(
                item,
                seen,
                event_type="series_premiere",
                event_day=release_day,
                season_number=1,
                episode_number=1,
                country=country,
                network=network_name,
                source_url=source_url,
            )

    item.ensure_primary_id()
    return item


def _append_event(
    item: NormalizedTitle,
    seen: set[tuple[str, int | None, int | None, str]],
    *,
    event_type: str,
    event_day: str,
    season_number: int | None,
    episode_number: int | None,
    country: str | None,
    network: str,
    source_url: str,
) -> None:
    key = (event_type, season_number, episode_number, event_day)
    if key in seen:
        return
    seen.add(key)
    record_id = ":".join(
        (
            item.source_id,
            event_type,
            str(season_number if season_number is not None else "x"),
            str(episode_number if episode_number is not None else "x"),
        )
    )
    item.events.append(
        EventObservation(
            event_type=event_type,
            date=event_day,
            source_record_id=record_id,
            source_url=source_url,
            season_number=season_number,
            episode_number=episode_number,
            country=country,
            network=network or None,
            confidence=0.84,
        )
    )


def _external_url(source: str, value: str) -> str | None:
    if source == "tmdb":
        return f"https://www.themoviedb.org/tv/{value}"
    if source == "tvdb":
        return f"https://thetvdb.com/dereferrer/series/{value}"
    if source == "imdb":
        return f"https://www.imdb.com/title/{value}/"
    return None


def _portable_identity_index(connection: sqlite3.Connection) -> dict[str, str]:
    """Build the index without relying on non-SQLite ``LIKE ANY`` syntax."""
    clauses = " OR ".join("key LIKE ?" for _ in MATCHABLE_ID_SOURCES)
    parameters = tuple(f"{source}:%" for source in MATCHABLE_ID_SOURCES)
    rows = connection.execute(
        f"SELECT key, title_id FROM identity_keys WHERE {clauses}", parameters
    ).fetchall()
    return {str(row["key"]): str(row["title_id"]) for row in rows}


def _premiere_candidate_ids(payload: Mapping[str, Any]) -> set[int]:
    calendar = payload.get("calendar")
    if not isinstance(calendar, list):
        return set()
    candidates: set[int] = set()
    for entry in calendar:
        if not isinstance(entry, Mapping):
            continue
        episode = entry.get("episode")
        if not isinstance(episode, Mapping) or _integer_or_none(
            episode.get("episode")
        ) != 1:
            continue
        if _integer_or_none(episode.get("season")) not in {None, 1}:
            continue
        simkl_id = entry.get("simkl_id")
        if isinstance(simkl_id, int) and not isinstance(simkl_id, bool):
            candidates.add(simkl_id)
    return candidates


def compare_with_catalog(
    payload: Mapping[str, Any],
    connection: sqlite3.Connection,
    *,
    sample_limit: int = 10,
) -> dict[str, Any]:
    """Compare verified provider IDs without changing or merging catalog rows."""
    metadata = payload["metadata"]
    if not isinstance(metadata, Mapping):
        raise ValueError("validated Simkl metadata is unavailable")
    local_ids = _portable_identity_index(connection)
    premiere_candidates = _premiere_candidate_ids(payload)
    matched = 0
    conflicts = 0
    matched_premieres = 0
    conflicting_premieres = 0
    match_sources: Counter[str] = Counter()
    unmatched: list[dict[str, Any]] = []
    unmatched_premieres: list[dict[str, Any]] = []

    for raw_key, raw_item in metadata.items():
        if not isinstance(raw_item, Mapping):
            continue
        ids = raw_item.get("ids")
        if not isinstance(ids, Mapping):
            continue
        candidates: set[str] = set()
        for source in MATCHABLE_ID_SOURCES:
            raw_value = ids.get(source)
            if raw_value is None or isinstance(raw_value, (dict, list)):
                continue
            value = str(raw_value).strip()
            if not value:
                continue
            title_id = local_ids.get(f"{source}:{value}")
            if title_id:
                candidates.add(title_id)
                match_sources[source] += 1
        if len(candidates) == 1:
            matched += 1
            if int(str(raw_key)) in premiere_candidates:
                matched_premieres += 1
        elif len(candidates) > 1:
            conflicts += 1
            if int(str(raw_key)) in premiere_candidates:
                conflicting_premieres += 1
        elif len(unmatched) < sample_limit:
            unmatched.append(
                {
                    "simkl_id": int(str(raw_key)),
                    "title": str(raw_item.get("title", "")),
                    "release_date": raw_item.get("release_date"),
                    "country": raw_item.get("country"),
                    "network": raw_item.get("network"),
                }
            )
        if (
            not candidates
            and int(str(raw_key)) in premiere_candidates
            and len(unmatched_premieres) < sample_limit
        ):
            unmatched_premieres.append(
                {
                    "simkl_id": int(str(raw_key)),
                    "title": str(raw_item.get("title", "")),
                    "release_date": raw_item.get("release_date"),
                    "country": raw_item.get("country"),
                    "network": raw_item.get("network"),
                }
            )

    total = len(metadata)
    return {
        "matched_catalog_titles": matched,
        "unmatched_catalog_titles": max(0, total - matched - conflicts),
        "identity_conflicts": conflicts,
        "match_rate_percent": round((matched / total * 100.0), 2) if total else 0.0,
        "matching_id_sources": dict(sorted(match_sources.items())),
        "unmatched_sample": unmatched,
        "premiere_candidates": len(premiere_candidates),
        "matched_premiere_candidates": matched_premieres,
        "unmatched_premiere_candidates": max(
            0,
            len(premiere_candidates) - matched_premieres - conflicting_premieres,
        ),
        "conflicting_premiere_candidates": conflicting_premieres,
        "unmatched_premiere_sample": unmatched_premieres,
    }


class SimklCalendarProbe:
    """Fetch and assess Calendar v2 without publishing any records."""

    def __init__(self, client_id: str, http: HTTPClient) -> None:
        client_id = client_id.strip()
        if not client_id or len(client_id) > 256 or any(
            character.isspace() for character in client_id
        ):
            raise ValueError("a valid Simkl Client ID is required")
        self.client_id = client_id
        self.http = http

    def run(self, database_path: Path, catalogs: tuple[str, ...]) -> dict[str, Any]:
        unsupported = sorted(set(catalogs) - set(SIMKL_CATALOGS))
        if unsupported:
            raise ValueError(f"unsupported Simkl catalogs: {', '.join(unsupported)}")
        results: dict[str, Any] = {}
        with connect_ro(database_path) as connection:
            for catalog in catalogs:
                payload = self.http.request_json(
                    f"{SIMKL_CALENDAR_BASE}/{catalog}.json",
                    params={
                        "client_id": self.client_id,
                        "app-name": "global-media-discovery",
                        "app-version": __version__,
                    },
                )
                validation = validate_calendar_payload(payload)
                if not isinstance(payload, Mapping):
                    raise ValueError("validated Simkl payload is unavailable")
                comparison = compare_with_catalog(payload, connection)
                results[catalog] = {**validation, **comparison}

        return {
            "status": "ok",
            "mode": "private_non_publishing_probe",
            "database_access": "read_only",
            "requests_made": len(catalogs),
            "catalogs": results,
        }

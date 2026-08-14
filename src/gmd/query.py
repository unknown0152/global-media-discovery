"""Read-only catalog query layer."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from gmd.db import connect_ro, database_stamp, get_meta


@dataclass(frozen=True, slots=True)
class EventFilters:
    start: date
    end: date
    query: str = ""
    country: str = ""
    language: str = ""
    network: str = ""
    genre: str = ""
    format: str = ""
    source: str = ""
    event_type: str = ""
    confidence: str = ""
    conflict: str = ""
    sort: str = "date_asc"
    limit: int = 60
    offset: int = 0


class CatalogQueries:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @property
    def stamp(self) -> str:
        return database_stamp(self.database_path)

    def health(self) -> dict[str, Any]:
        if not self.database_path.exists():
            return {"status": "starting", "database": "missing"}
        try:
            with connect_ro(self.database_path) as connection:
                row = connection.execute("SELECT 1").fetchone()
                counts = self._counts(connection)
                return {
                    "status": "ok" if row else "error",
                    "database": "ready",
                    "updated_at": get_meta(connection, "updated_at"),
                    "catalog_version": get_meta(connection, "catalog_version", "0"),
                    **counts,
                }
        except sqlite3.Error as error:
            return {"status": "error", "database": str(error)}

    def meta(self) -> dict[str, Any]:
        with connect_ro(self.database_path) as connection:
            bounds = connection.execute(
                """
                SELECT MIN(event_date) AS min_date, MAX(event_date) AS max_date
                FROM events
                """
            ).fetchone()
            run = connection.execute(
                """
                SELECT started_at, finished_at, status, sources_json, metrics_json
                FROM runs
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            formats = [
                row["format"]
                for row in connection.execute(
                    """
                    SELECT DISTINCT format FROM titles
                    WHERE format IS NOT NULL AND format != ''
                    ORDER BY format COLLATE NOCASE
                    """
                )
            ]
            result: dict[str, Any] = {
                "site_name": get_meta(connection, "site_name", "Global Media Discovery"),
                "updated_at": get_meta(connection, "updated_at"),
                "catalog_version": int(get_meta(connection, "catalog_version", "0") or "0"),
                "date_bounds": {
                    "min": bounds["min_date"] if bounds else None,
                    "max": bounds["max_date"] if bounds else None,
                },
                "formats": formats,
                **self._counts(connection),
            }
            if run:
                result["last_run"] = {
                    "started_at": run["started_at"],
                    "finished_at": run["finished_at"],
                    "status": run["status"],
                    "sources": _safe_json(run["sources_json"], {}),
                    "metrics": _safe_json(run["metrics_json"], {}),
                }
            return result

    def events(self, filters: EventFilters) -> dict[str, Any]:
        clauses = ["e.event_date BETWEEN ? AND ?"]
        params: list[Any] = [filters.start.isoformat(), filters.end.isoformat()]

        if filters.query:
            escaped = _like(filters.query)
            clauses.append(
                """
                (
                    t.canonical_title LIKE ? ESCAPE '\\'
                    OR t.original_title LIKE ? ESCAPE '\\'
                    OR EXISTS (
                        SELECT 1 FROM aliases a
                        WHERE a.title_id = t.id AND a.alias LIKE ? ESCAPE '\\'
                    )
                )
                """
            )
            params.extend([escaped, escaped, escaped])

        if filters.country:
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM countries c
                    WHERE c.title_id = t.id AND c.country_code = ?
                )
                """
            )
            params.append(filters.country.upper())

        if filters.language:
            clauses.append("t.original_language = ?")
            params.append(filters.language)

        if filters.network:
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM networks n
                    WHERE n.title_id = t.id AND n.network_name = ?
                )
                """
            )
            params.append(filters.network)

        if filters.genre:
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM genres g
                    WHERE g.title_id = t.id AND g.genre = ?
                )
                """
            )
            params.append(filters.genre)

        if filters.format:
            clauses.append("t.format = ?")
            params.append(filters.format)

        if filters.source:
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM event_evidence ev
                    WHERE ev.event_id = e.id AND ev.source = ?
                )
                """
            )
            params.append(filters.source)

        if filters.event_type:
            clauses.append("e.event_type = ?")
            params.append(filters.event_type)

        if filters.confidence == "high":
            clauses.append("e.confidence >= 0.85")
        elif filters.confidence == "medium":
            clauses.append("e.confidence >= 0.65 AND e.confidence < 0.85")
        elif filters.confidence == "low":
            clauses.append("e.confidence < 0.65")

        if filters.conflict == "only":
            clauses.append("e.date_conflict = 1")
        elif filters.conflict == "exclude":
            clauses.append("e.date_conflict = 0")

        where = "\n AND ".join(f"({clause.strip()})" for clause in clauses)
        order_by = {
            "date_asc": "e.event_date ASC, t.canonical_title COLLATE NOCASE ASC",
            "date_desc": "e.event_date DESC, t.canonical_title COLLATE NOCASE ASC",
            "title_asc": "t.canonical_title COLLATE NOCASE ASC, e.event_date ASC",
            "confidence_desc": (
                "e.confidence DESC, e.event_date ASC, "
                "t.canonical_title COLLATE NOCASE ASC"
            ),
        }[filters.sort]
        with connect_ro(self.database_path) as connection:
            totals = connection.execute(
                    f"""
                    SELECT COUNT(*) AS total,
                           SUM(CASE WHEN e.date_conflict = 1 THEN 1 ELSE 0 END)
                               AS conflicts
                    FROM events e
                    JOIN titles t ON t.id = e.title_id
                    WHERE {where}
                    """,
                    params,
                ).fetchone()
            total = int(totals["total"] or 0)

            rows = connection.execute(
                f"""
                SELECT
                    e.id AS event_id,
                    e.event_type,
                    e.event_date,
                    e.season_number,
                    e.episode_number,
                    e.country_code AS event_country,
                    e.network_name AS event_network,
                    e.confidence AS event_confidence,
                    e.date_conflict,
                    t.id AS title_id,
                    t.canonical_title,
                    t.original_title,
                    t.overview,
                    t.original_language,
                    t.format,
                    t.status,
                    t.runtime_minutes,
                    t.poster_url,
                    t.backdrop_url,
                    t.confidence AS title_confidence
                FROM events e
                JOIN titles t ON t.id = e.title_id
                WHERE {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,
                (*params, filters.limit, filters.offset),
            ).fetchall()

            items = [self._base_event(row) for row in rows]
            self._enrich(connection, items)
            return {
                "items": items,
                "pagination": {
                    "total": total,
                    "limit": filters.limit,
                    "offset": filters.offset,
                    "has_more": filters.offset + len(items) < total,
                },
                "range": {
                    "from": filters.start.isoformat(),
                    "to": filters.end.isoformat(),
                },
                "summary": {
                    "matching_events": total,
                    "date_conflicts": int(totals["conflicts"] or 0),
                },
            }

    def search_titles(self, query: str, limit: int, offset: int) -> dict[str, Any]:
        escaped = _like(query)
        params = (escaped, escaped, escaped)
        where = """
            t.canonical_title LIKE ? ESCAPE '\\'
            OR t.original_title LIKE ? ESCAPE '\\'
            OR EXISTS (
                SELECT 1 FROM aliases a
                WHERE a.title_id = t.id AND a.alias LIKE ? ESCAPE '\\'
            )
        """
        with connect_ro(self.database_path) as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM titles t WHERE {where}", params
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT t.id, t.canonical_title, t.original_title,
                       t.original_language, t.format, t.poster_url,
                       t.confidence,
                       MIN(e.event_date) AS first_event_date,
                       MAX(e.date_conflict) AS date_conflict
                FROM titles t
                LEFT JOIN events e ON e.title_id = t.id
                WHERE {where}
                GROUP BY t.id
                ORDER BY t.canonical_title COLLATE NOCASE ASC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
            items = [
                {
                    "id": row["id"],
                    "name": row["canonical_title"],
                    "original_name": row["original_title"],
                    "language": row["original_language"],
                    "format": row["format"],
                    "poster_url": row["poster_url"],
                    "confidence": round(float(row["confidence"] or 0), 3),
                    "first_event_date": row["first_event_date"],
                    "date_conflict": bool(row["date_conflict"]),
                }
                for row in rows
            ]
            return {
                "query": query,
                "items": items,
                "pagination": {
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "has_more": offset + len(items) < total,
                },
            }

    def title(self, title_id: str) -> dict[str, Any] | None:
        with connect_ro(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT
                    e.id AS event_id,
                    e.event_type,
                    e.event_date,
                    e.season_number,
                    e.episode_number,
                    e.country_code AS event_country,
                    e.network_name AS event_network,
                    e.confidence AS event_confidence,
                    e.date_conflict,
                    t.id AS title_id,
                    t.canonical_title,
                    t.original_title,
                    t.overview,
                    t.original_language,
                    t.format,
                    t.status,
                    t.runtime_minutes,
                    t.poster_url,
                    t.backdrop_url,
                    t.confidence AS title_confidence
                FROM titles t
                LEFT JOIN events e
                  ON e.title_id = t.id AND e.event_type = 'series_premiere'
                WHERE t.id = ?
                ORDER BY e.event_date
                LIMIT 1
                """,
                (title_id,),
            ).fetchone()
            if not row:
                return None
            item = self._base_event(row)
            self._enrich(connection, [item], include_aliases=True)
            event_rows = connection.execute(
                """
                SELECT id, event_type, event_date, season_number, episode_number,
                       country_code, network_name, confidence, date_conflict
                FROM events WHERE title_id = ?
                ORDER BY event_date, event_type
                """,
                (title_id,),
            ).fetchall()
            item["events"] = [
                {
                    "id": event["id"],
                    "type": event["event_type"],
                    "date": event["event_date"],
                    "season_number": _nullable_number(event["season_number"]),
                    "episode_number": _nullable_number(event["episode_number"]),
                    "country": event["country_code"] or None,
                    "network": event["network_name"] or None,
                    "confidence": round(float(event["confidence"] or 0), 3),
                    "date_conflict": bool(event["date_conflict"]),
                }
                for event in event_rows
            ]
            return item

    def facets(self, start: date, end: date) -> dict[str, Any]:
        with connect_ro(self.database_path) as connection:
            params = (start.isoformat(), end.isoformat())
            return {
                "countries": self._facet(
                    connection,
                    """
                    SELECT c.country_code AS value, COUNT(DISTINCT e.id) AS count
                    FROM events e
                    JOIN countries c ON c.title_id = e.title_id
                    WHERE e.event_date BETWEEN ? AND ?
                    GROUP BY c.country_code
                    ORDER BY count DESC, value ASC
                    """,
                    params,
                ),
                "languages": self._facet(
                    connection,
                    """
                    SELECT t.original_language AS value, COUNT(DISTINCT e.id) AS count
                    FROM events e
                    JOIN titles t ON t.id = e.title_id
                    WHERE e.event_date BETWEEN ? AND ?
                      AND t.original_language IS NOT NULL
                      AND t.original_language != ''
                    GROUP BY t.original_language
                    ORDER BY count DESC, value ASC
                    """,
                    params,
                ),
                "networks": self._facet(
                    connection,
                    """
                    SELECT n.network_name AS value, COUNT(DISTINCT e.id) AS count
                    FROM events e
                    JOIN networks n ON n.title_id = e.title_id
                    WHERE e.event_date BETWEEN ? AND ?
                    GROUP BY n.network_name
                    ORDER BY count DESC, value COLLATE NOCASE ASC
                    LIMIT 250
                    """,
                    params,
                ),
                "genres": self._facet(
                    connection,
                    """
                    SELECT g.genre AS value, COUNT(DISTINCT e.id) AS count
                    FROM events e
                    JOIN genres g ON g.title_id = e.title_id
                    WHERE e.event_date BETWEEN ? AND ?
                    GROUP BY g.genre
                    ORDER BY count DESC, value COLLATE NOCASE ASC
                    """,
                    params,
                ),
                "formats": self._facet(
                    connection,
                    """
                    SELECT t.format AS value, COUNT(DISTINCT e.id) AS count
                    FROM events e
                    JOIN titles t ON t.id = e.title_id
                    WHERE e.event_date BETWEEN ? AND ?
                    GROUP BY t.format
                    ORDER BY count DESC, value COLLATE NOCASE ASC
                    """,
                    params,
                ),
                "sources": self._facet(
                    connection,
                    """
                    SELECT ev.source AS value, COUNT(DISTINCT ev.event_id) AS count
                    FROM event_evidence ev
                    JOIN events e ON e.id = ev.event_id
                    WHERE e.event_date BETWEEN ? AND ?
                    GROUP BY ev.source
                    ORDER BY count DESC, value ASC
                    """,
                    params,
                ),
                "event_types": self._facet(
                    connection,
                    """
                    SELECT e.event_type AS value, COUNT(*) AS count
                    FROM events e
                    WHERE e.event_date BETWEEN ? AND ?
                    GROUP BY e.event_type
                    ORDER BY count DESC, value ASC
                    """,
                    params,
                ),
            }

    def status(self) -> dict[str, Any]:
        with connect_ro(self.database_path) as connection:
            states = connection.execute(
                """
                SELECT source, last_success_at, last_attempt_at, status
                FROM collection_state
                WHERE source IN ('tmdb', 'tvdb', 'tvmaze')
                ORDER BY source
                """
            ).fetchall()
            run = connection.execute(
                """
                SELECT started_at, finished_at, status, sources_json, metrics_json
                FROM runs ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            payload: dict[str, Any] = {
                "status": "ok",
                "updated_at": get_meta(connection, "updated_at"),
                "sources": [dict(row) for row in states],
            }
            if run:
                payload["last_run"] = {
                    "started_at": run["started_at"],
                    "finished_at": run["finished_at"],
                    "status": run["status"],
                    "sources": _safe_json(run["sources_json"], {}),
                    "metrics": _safe_json(run["metrics_json"], {}),
                }
            return payload

    def stats(self) -> dict[str, Any]:
        with connect_ro(self.database_path) as connection:
            bounds = connection.execute(
                "SELECT MIN(event_date) AS min, MAX(event_date) AS max FROM events"
            ).fetchone()
            by_source = self._facet(
                connection,
                """
                SELECT source AS value, COUNT(*) AS count
                FROM source_records GROUP BY source ORDER BY source
                """,
                (),
            )
            by_type = self._facet(
                connection,
                """
                SELECT event_type AS value, COUNT(*) AS count
                FROM events GROUP BY event_type ORDER BY count DESC
                """,
                (),
            )
            return {
                **self._counts(connection),
                "evidence_count": int(
                    connection.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0]
                ),
                "date_bounds": {"min": bounds["min"], "max": bounds["max"]},
                "by_source": by_source,
                "by_event_type": by_type,
            }

    def coverage(self) -> dict[str, Any]:
        """Describe what is present without implying catalog completeness."""
        with connect_ro(self.database_path) as connection:
            bounds = connection.execute(
                """
                SELECT MIN(event_date) AS min_date,
                       MAX(event_date) AS max_date,
                       COUNT(DISTINCT event_date) AS active_days
                FROM events
                """
            ).fetchone()
            years = [
                {
                    "year": int(row["year"]),
                    "title_count": int(row["title_count"]),
                    "event_count": int(row["event_count"]),
                    "evidence_count": int(row["evidence_count"] or 0),
                    "active_day_count": int(row["active_day_count"]),
                    "conflict_count": int(row["conflict_count"] or 0),
                }
                for row in connection.execute(
                    """
                    SELECT substr(e.event_date, 1, 4) AS year,
                           COUNT(DISTINCT e.title_id) AS title_count,
                           COUNT(*) AS event_count,
                           SUM((SELECT COUNT(*) FROM event_evidence ev
                                WHERE ev.event_id = e.id)) AS evidence_count,
                           COUNT(DISTINCT e.event_date) AS active_day_count,
                           SUM(e.date_conflict) AS conflict_count
                    FROM events e
                    GROUP BY substr(e.event_date, 1, 4)
                    ORDER BY year
                    """
                )
            ]
            sources = [
                {
                    "source": row["source"],
                    "event_count": int(row["event_count"]),
                    "evidence_count": int(row["evidence_count"]),
                    "reported_date_min": row["reported_date_min"],
                    "reported_date_max": row["reported_date_max"],
                }
                for row in connection.execute(
                    """
                    SELECT source,
                           COUNT(DISTINCT event_id) AS event_count,
                           COUNT(*) AS evidence_count,
                           MIN(reported_date) AS reported_date_min,
                           MAX(reported_date) AS reported_date_max
                    FROM event_evidence
                    GROUP BY source
                    ORDER BY source
                    """
                )
            ]
            countries = [
                {
                    "country": row["country"],
                    "title_count": int(row["title_count"]),
                    "event_count": int(row["event_count"]),
                }
                for row in connection.execute(
                    """
                    SELECT c.country_code AS country,
                           COUNT(DISTINCT c.title_id) AS title_count,
                           COUNT(DISTINCT e.id) AS event_count
                    FROM countries c
                    JOIN events e ON e.title_id = c.title_id
                    GROUP BY c.country_code
                    ORDER BY event_count DESC, country
                    """
                )
            ]
            return {
                "scope": (
                    "Observed provider records, not a claim of complete worldwide "
                    "television coverage. Sparse dates can mean no provider report."
                ),
                "updated_at": get_meta(connection, "updated_at"),
                "date_bounds": {
                    "min": bounds["min_date"],
                    "max": bounds["max_date"],
                },
                "active_day_count": int(bounds["active_days"] or 0),
                "years": years,
                "sources": sources,
                "countries": countries,
                **self._counts(connection),
                "evidence_count": int(
                    connection.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0]
                ),
            }

    def calendar(self, month: str) -> dict[str, Any]:
        start = date.fromisoformat(f"{month}-01")
        if start.month == 12:
            next_month = date(start.year + 1, 1, 1)
        else:
            next_month = date(start.year, start.month + 1, 1)

        with connect_ro(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT event_date, COUNT(*) AS count,
                       SUM(CASE WHEN date_conflict = 1 THEN 1 ELSE 0 END) AS conflicts
                FROM events
                WHERE event_date >= ? AND event_date < ?
                GROUP BY event_date
                ORDER BY event_date
                """,
                (start.isoformat(), next_month.isoformat()),
            ).fetchall()
            return {
                "month": month,
                "days": [
                    {
                        "date": row["event_date"],
                        "count": int(row["count"]),
                        "conflicts": int(row["conflicts"] or 0),
                    }
                    for row in rows
                ],
            }

    def credits(self) -> dict[str, Any]:
        return {
            "sources": [
                {
                    "id": "tmdb",
                    "name": "TMDB",
                    "url": "https://www.themoviedb.org/",
                    "notice": (
                        "This product uses the TMDB API but is not endorsed "
                        "or certified by TMDB."
                    ),
                },
                {
                    "id": "tvdb",
                    "name": "TheTVDB",
                    "url": "https://thetvdb.com/",
                    "notice": "Metadata provided by TheTVDB.",
                },
                {
                    "id": "tvmaze",
                    "name": "TVmaze",
                    "url": "https://www.tvmaze.com/",
                    "notice": "TVmaze data is used under CC BY-SA.",
                },
            ]
        }

    @staticmethod
    def _counts(connection: sqlite3.Connection) -> dict[str, int]:
        return {
            "title_count": int(connection.execute("SELECT COUNT(*) FROM titles").fetchone()[0]),
            "event_count": int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
            "source_record_count": int(
                connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
            ),
            "conflict_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM events WHERE date_conflict = 1"
                ).fetchone()[0]
            ),
        }

    @staticmethod
    def _base_event(row: sqlite3.Row) -> dict[str, Any]:
        event_id = row["event_id"]
        return {
            "event_id": event_id,
            "event_type": row["event_type"] if event_id else None,
            "date": row["event_date"],
            "season_number": _nullable_number(row["season_number"]),
            "episode_number": _nullable_number(row["episode_number"]),
            "event_country": row["event_country"] or None,
            "event_network": row["event_network"] or None,
            "confidence": round(float(row["event_confidence"] or 0), 3),
            "date_conflict": bool(row["date_conflict"]),
            "title": {
                "id": row["title_id"],
                "name": row["canonical_title"],
                "original_name": row["original_title"],
                "overview": row["overview"] or "",
                "language": row["original_language"],
                "format": row["format"],
                "status": row["status"],
                "runtime_minutes": row["runtime_minutes"],
                "poster_url": row["poster_url"],
                "backdrop_url": row["backdrop_url"],
                "confidence": round(float(row["title_confidence"] or 0), 3),
            },
            "countries": [],
            "genres": [],
            "networks": [],
            "external_ids": [],
            "evidence": [],
            "quality_flags": [],
        }

    def _enrich(
        self,
        connection: sqlite3.Connection,
        items: list[dict[str, Any]],
        *,
        include_aliases: bool = False,
    ) -> None:
        if not items:
            return
        title_ids = [item["title"]["id"] for item in items]
        event_ids = [item["event_id"] for item in items if item.get("event_id")]
        by_title = {item["title"]["id"]: item for item in items}
        by_event = {item["event_id"]: item for item in items if item.get("event_id")}
        placeholders = ",".join("?" for _ in title_ids)

        for row in connection.execute(
            f"""
            SELECT DISTINCT title_id, country_code
            FROM countries WHERE title_id IN ({placeholders})
            ORDER BY country_code
            """,
            title_ids,
        ):
            by_title[row["title_id"]]["countries"].append(row["country_code"])

        for row in connection.execute(
            f"""
            SELECT DISTINCT title_id, genre
            FROM genres WHERE title_id IN ({placeholders})
            ORDER BY genre COLLATE NOCASE
            """,
            title_ids,
        ):
            by_title[row["title_id"]]["genres"].append(row["genre"])

        for row in connection.execute(
            f"""
            SELECT DISTINCT title_id, network_name, network_country, network_type
            FROM networks WHERE title_id IN ({placeholders})
            ORDER BY network_name COLLATE NOCASE
            """,
            title_ids,
        ):
            by_title[row["title_id"]]["networks"].append(
                {
                    "name": row["network_name"],
                    "country": row["network_country"],
                    "type": row["network_type"],
                }
            )

        for row in connection.execute(
            f"""
            SELECT title_id, source, external_id, source_url
            FROM identity_keys WHERE title_id IN ({placeholders})
            ORDER BY CASE source
                WHEN 'imdb' THEN 1
                WHEN 'tmdb' THEN 2
                WHEN 'tvdb' THEN 3
                WHEN 'tvmaze' THEN 4
                ELSE 9 END, source
            """,
            title_ids,
        ):
            by_title[row["title_id"]]["external_ids"].append(
                {
                    "source": row["source"],
                    "id": row["external_id"],
                    "url": row["source_url"],
                }
            )

        for row in connection.execute(
            f"""
            SELECT title_id, flag, source, detail
            FROM quality_flags WHERE title_id IN ({placeholders})
            ORDER BY flag, source
            """,
            title_ids,
        ):
            by_title[row["title_id"]]["quality_flags"].append(
                {
                    "flag": row["flag"],
                    "source": row["source"],
                    "detail": row["detail"],
                }
            )

        if include_aliases:
            for row in connection.execute(
                f"""
                SELECT title_id, alias, language, source
                FROM aliases WHERE title_id IN ({placeholders})
                ORDER BY alias COLLATE NOCASE
                """,
                title_ids,
            ):
                by_title[row["title_id"]].setdefault("aliases", []).append(
                    {
                        "name": row["alias"],
                        "language": row["language"],
                        "source": row["source"],
                    }
                )

        if event_ids:
            event_placeholders = ",".join("?" for _ in event_ids)
            for row in connection.execute(
                f"""
                SELECT event_id, source, source_record_id, reported_date,
                       source_url, observed_at, confidence
                FROM event_evidence
                WHERE event_id IN ({event_placeholders})
                ORDER BY reported_date, source
                """,
                event_ids,
            ):
                by_event[row["event_id"]]["evidence"].append(
                    {
                        "source": row["source"],
                        "source_record_id": row["source_record_id"],
                        "reported_date": row["reported_date"],
                        "url": row["source_url"],
                        "observed_at": row["observed_at"],
                        "confidence": round(float(row["confidence"] or 0), 3),
                    }
                )

        for item in items:
            item["date_assessment"] = _date_assessment(item)

    @staticmethod
    def _facet(
        connection: sqlite3.Connection,
        sql: str,
        params: Iterable[Any],
    ) -> list[dict[str, Any]]:
        return [
            {"value": row["value"], "count": int(row["count"])}
            for row in connection.execute(sql, tuple(params))
            if row["value"]
        ]


def _nullable_number(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if number < 0 else number


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _safe_json(value: object, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


_DATE_MEANINGS = {
    "series_premiere": (
        "original_series_premiere",
        "Original series premiere",
        "The first known release of the series, not its later arrival on a local service.",
    ),
    "season_premiere": (
        "season_premiere",
        "Season premiere",
        "The first known release of this season.",
    ),
    "special": (
        "special_release",
        "Special release",
        "The reported release date for this television special.",
    ),
}


def _date_assessment(item: dict[str, Any]) -> dict[str, Any]:
    selected = item.get("date")
    evidence = item.get("evidence") or []
    meaning, label, description = _DATE_MEANINGS.get(
        str(item.get("event_type") or ""),
        ("television_event", "Television event", "The date reported for this event."),
    )
    reports: dict[str, set[str]] = defaultdict(set)
    for row in evidence:
        reported = str(row.get("reported_date") or "")
        source = str(row.get("source") or "")
        if reported:
            reports[reported].add(source)
        row["date_meaning"] = meaning
        row["date_precision"] = "day"
        row["supports_selected_date"] = bool(selected and reported == selected)
        row["difference_days"] = _date_difference(reported, selected)

    sources = sorted({str(row.get("source")) for row in evidence if row.get("source")})
    supporting = sorted(reports.get(str(selected), set()))
    other_dates = [
        {"date": reported, "sources": sorted(source_names)}
        for reported, source_names in sorted(reports.items())
        if reported != selected
    ]
    if not evidence:
        status = "unverified"
        method = "no_provider_report"
        explanation = "No provider date evidence is currently available."
    elif len(reports) > 1:
        status = "disputed"
        method = "weighted_provider_consensus"
        explanation = (
            "Providers report different dates. The selected date uses weighted provider "
            "agreement; every conflicting report remains visible."
        )
    elif len(sources) > 1:
        status = "corroborated"
        method = "provider_agreement"
        explanation = f"{len(sources)} independent providers report the same date."
    else:
        status = "single_source"
        method = "single_provider_report"
        explanation = "This date currently relies on one provider report."

    return {
        "selected_date": selected,
        "meaning": meaning,
        "meaning_label": label,
        "meaning_description": description,
        "precision": "day",
        "status": status,
        "source_count": len(sources),
        "distinct_date_count": len(reports),
        "supporting_sources": supporting,
        "other_dates": other_dates,
        "selection_method": method,
        "explanation": explanation,
    }


def _date_difference(reported: str, selected: object) -> int | None:
    if not reported or not selected:
        return None
    try:
        return (date.fromisoformat(reported) - date.fromisoformat(str(selected))).days
    except ValueError:
        return None

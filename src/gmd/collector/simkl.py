"""Private, non-publishing probes for Simkl Calendar v2 data.

This module deliberately does not integrate with ``CollectorPipeline``.  It is
used to assess coverage and identity overlap before Simkl is considered as a
production evidence source.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from gmd.collector.http import HTTPClient
from gmd.db import connect_ro

SIMKL_CALENDAR_BASE = "https://data.simkl.in/calendar/v2"
SIMKL_CATALOGS = ("tv", "anime")
MATCHABLE_ID_SOURCES = ("tmdb", "tvdb", "imdb")


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
            season_number = episode.get("season")
            episode_number = episode.get("episode")
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
        finale_type = raw_entry.get("finale_type")
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
        if not isinstance(episode, Mapping) or episode.get("episode") != 1:
            continue
        if episode.get("season") not in {None, 1}:
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
        if not client_id or len(client_id) > 256 or any(char.isspace() for char in client_id):
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
                        "app-version": "2.0.0",
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

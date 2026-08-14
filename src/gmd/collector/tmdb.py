"""TMDB collector and normalizer."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
import logging
from typing import Any, Iterator

from gmd.collector.http import HTTPClient
from gmd.models import Alias, EventObservation, ExternalID, Network, NormalizedTitle
from gmd.normalize import (
    clean_text,
    infer_format,
    normalize_country,
    normalize_language,
)

LOGGER = logging.getLogger(__name__)
BASE_URL = "https://api.themoviedb.org/3"

TV_GENRES: dict[int, str] = {
    10759: "Action & Adventure",
    16: "Animation",
    35: "Comedy",
    80: "Crime",
    99: "Documentary",
    18: "Drama",
    10751: "Family",
    10762: "Kids",
    9648: "Mystery",
    10763: "News",
    10764: "Reality",
    10765: "Sci-Fi & Fantasy",
    10766: "Soap",
    10767: "Talk",
    10768: "War & Politics",
    37: "Western",
}


def _months(start: date, end: date) -> Iterator[tuple[date, date]]:
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        last = date(cursor.year, cursor.month, monthrange(cursor.year, cursor.month)[1])
        yield max(start, cursor), min(end, last)
        cursor = date(
            cursor.year + (cursor.month == 12),
            1 if cursor.month == 12 else cursor.month + 1,
            1,
        )


class TMDBCollector:
    def __init__(self, token: str, client: HTTPClient) -> None:
        self.token = token
        self.client = client
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def discover(self, start: date, end: date) -> list[dict[str, Any]]:
        records: dict[int, dict[str, Any]] = {}
        for chunk_start, chunk_end in _months(start, end):
            page = 1
            total_pages = 1
            while page <= total_pages:
                response = self.client.request_json(
                    f"{BASE_URL}/discover/tv",
                    params={
                        "first_air_date.gte": chunk_start.isoformat(),
                        "first_air_date.lte": chunk_end.isoformat(),
                        "sort_by": "first_air_date.asc",
                        "include_adult": "false",
                        "include_null_first_air_dates": "false",
                        "language": "en-US",
                        "page": page,
                    },
                    headers=self.headers,
                )
                total_pages = min(int(response.get("total_pages") or 1), 500)
                for item in response.get("results") or []:
                    if item.get("id") is not None:
                        records[int(item["id"])] = item
                LOGGER.info(
                    "TMDB discover page",
                    extra={
                        "structured": {
                            "from": chunk_start.isoformat(),
                            "to": chunk_end.isoformat(),
                            "page": page,
                            "total_pages": total_pages,
                            "records": len(records),
                        }
                    },
                )
                page += 1
        return list(records.values())

    def details(self, tmdb_id: int) -> dict[str, Any]:
        return self.client.request_json(
            f"{BASE_URL}/tv/{tmdb_id}",
            params={
                "language": "en-US",
                "append_to_response": "external_ids",
            },
            headers=self.headers,
        )


def normalize_tmdb(
    record: dict[str, Any],
    *,
    details: dict[str, Any] | None = None,
) -> NormalizedTitle:
    merged = dict(record)
    if details:
        merged.update(details)

    tmdb_id = str(merged["id"])
    genre_names = {
        clean_text(item.get("name"))
        for item in (merged.get("genres") or [])
        if isinstance(item, dict) and clean_text(item.get("name"))
    }
    if not genre_names:
        genre_names = {
            TV_GENRES[genre_id]
            for genre_id in (merged.get("genre_ids") or [])
            if genre_id in TV_GENRES
        }

    countries = {
        code
        for code in (
            normalize_country(value)
            for value in (merged.get("origin_country") or [])
        )
        if code
    }

    title = clean_text(merged.get("name") or merged.get("original_name")) or f"TMDB {tmdb_id}"
    original_title = clean_text(merged.get("original_name")) or None
    poster_path = merged.get("poster_path")
    backdrop_path = merged.get("backdrop_path")

    explicit_format = None
    if "Animation" in genre_names:
        explicit_format = "Animation"
    elif "Documentary" in genre_names:
        explicit_format = "Documentary"
    elif "Reality" in genre_names:
        explicit_format = "Reality"
    elif "Talk" in genre_names:
        explicit_format = "Talk Show"
    elif "News" in genre_names:
        explicit_format = "News"

    normalized = NormalizedTitle(
        source="tmdb",
        source_id=tmdb_id,
        title=title,
        original_title=original_title,
        overview=clean_text(merged.get("overview")),
        original_language=normalize_language(merged.get("original_language")),
        format=infer_format(explicit=explicit_format, genres=genre_names),
        status=clean_text(merged.get("status")) or None,
        runtime_minutes=_runtime(merged),
        poster_url=(
            f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None
        ),
        backdrop_url=(
            f"https://image.tmdb.org/t/p/w780{backdrop_path}" if backdrop_path else None
        ),
        source_updated_at=None,
        source_url=f"https://www.themoviedb.org/tv/{tmdb_id}",
        aliases=[Alias(title, "en")],
        countries=countries,
        genres=genre_names,
        raw=merged,
    )

    normalized.external_ids.append(
        ExternalID("tmdb", tmdb_id, normalized.source_url)
    )
    external = merged.get("external_ids") or {}
    imdb_id = clean_text(external.get("imdb_id"))
    tvdb_id = external.get("tvdb_id")
    if imdb_id:
        normalized.external_ids.append(
            ExternalID("imdb", imdb_id, f"https://www.imdb.com/title/{imdb_id}/")
        )
    if tvdb_id:
        normalized.external_ids.append(
            ExternalID("tvdb", str(tvdb_id), f"https://thetvdb.com/dereferrer/series/{tvdb_id}")
        )

    for network in merged.get("networks") or []:
        if not isinstance(network, dict):
            continue
        name = clean_text(network.get("name"))
        if name:
            normalized.networks.append(
                Network(name=name, country=normalize_country(network.get("origin_country")))
            )

    first_air_date = clean_text(merged.get("first_air_date"))
    if first_air_date:
        normalized.events.append(
            EventObservation(
                event_type="series_premiere",
                date=first_air_date[:10],
                source_record_id=tmdb_id,
                source_url=normalized.source_url,
                confidence=0.74,
            )
        )

    if bool(merged.get("adult")):
        normalized.quality_flags.append(("adult_record", "TMDB marked this record as adult."))

    normalized.ensure_primary_id()
    return normalized


def _runtime(record: dict[str, Any]) -> int | None:
    runtimes = [
        int(value)
        for value in (record.get("episode_run_time") or [])
        if isinstance(value, (int, float)) and int(value) > 0
    ]
    if runtimes:
        return round(sum(runtimes) / len(runtimes))
    value = record.get("runtime")
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return None

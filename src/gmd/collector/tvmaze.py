"""TVmaze schedule collector and normalizer."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import logging
from typing import Any

from gmd.collector.http import HTTPClient
from gmd.models import Alias, EventObservation, ExternalID, Network, NormalizedTitle
from gmd.normalize import (
    clean_text,
    infer_format,
    normalize_country,
    normalize_language,
    strip_html,
)

LOGGER = logging.getLogger(__name__)
BASE_URL = "https://api.tvmaze.com"


class TVMazeCollector:
    def __init__(self, client: HTTPClient) -> None:
        self.client = client

    def full_schedule(self) -> list[dict[str, Any]]:
        data = self.client.request_json(f"{BASE_URL}/schedule/full")
        return [item for item in data if isinstance(item, dict)]

    def web_schedule(self, day: date) -> list[dict[str, Any]]:
        data = self.client.request_json(
            f"{BASE_URL}/schedule/web", params={"date": day.isoformat()}
        )
        return [item for item in data if isinstance(item, dict)]

    def premieres(
        self,
        start: date,
        end: date,
        *,
        recent_days: int,
        backfill_days: int = 0,
    ) -> list[dict[str, Any]]:
        episodes: dict[str, dict[str, Any]] = {}

        for episode in self.full_schedule():
            if not _is_premiere(episode):
                continue
            airdate = clean_text(episode.get("airdate"))[:10]
            if start.isoformat() <= airdate <= end.isoformat():
                episodes[str(episode.get("id"))] = episode

        scan_days = max(recent_days, backfill_days)
        recent_start = max(start, date.today() - timedelta(days=scan_days))
        recent_end = min(end, date.today() + timedelta(days=30))
        day = recent_start
        while day <= recent_end:
            for episode in self.web_schedule(day):
                if _is_premiere(episode):
                    episodes[str(episode.get("id"))] = episode
            LOGGER.info(
                "TVmaze web schedule",
                extra={
                    "structured": {
                        "date": day.isoformat(),
                        "premieres_total": len(episodes),
                    }
                },
            )
            day += timedelta(days=1)

        return list(episodes.values())

    def show(self, show_id: int | str) -> dict[str, Any]:
        data = self.client.request_json(f"{BASE_URL}/shows/{show_id}")
        if not isinstance(data, dict):
            raise RuntimeError(f"TVmaze show {show_id} returned no data")
        return data


def normalize_tvmaze(
    episode: dict[str, Any],
    *,
    show_override: dict[str, Any] | None = None,
) -> NormalizedTitle:
    show = show_override or ((episode.get("_embedded") or {}).get("show") or {})
    if not show:
        raise ValueError("TVmaze episode does not contain embedded show data")

    show_id = str(show["id"])
    title = clean_text(show.get("name")) or f"TVmaze {show_id}"
    source_url = clean_text(show.get("url")) or f"https://www.tvmaze.com/shows/{show_id}"

    network_data = show.get("network") or show.get("webChannel") or {}
    network_name = clean_text(network_data.get("name"))
    network_country = normalize_country((network_data.get("country") or {}).get("code"))
    network_kind = "Streaming" if show.get("webChannel") else "Broadcast"

    genres = {
        clean_text(value)
        for value in (show.get("genres") or [])
        if clean_text(value)
    }
    show_type = clean_text(show.get("type"))

    normalized = NormalizedTitle(
        source="tvmaze",
        source_id=show_id,
        title=title,
        original_title=title,
        overview=strip_html(show.get("summary")),
        original_language=normalize_language(show.get("language")),
        format=infer_format(explicit=show_type, genres=genres),
        status=clean_text(show.get("status")) or None,
        runtime_minutes=_runtime(show),
        poster_url=clean_text((show.get("image") or {}).get("original")) or None,
        source_updated_at=_updated_at(show.get("updated")),
        source_url=source_url,
        aliases=[Alias(title, normalize_language(show.get("language")))],
        countries={network_country} if network_country else set(),
        genres=genres,
        networks=(
            [Network(network_name, network_country, network_kind)]
            if network_name
            else []
        ),
        raw={"episode": episode, "show": show},
    )

    normalized.external_ids.append(ExternalID("tvmaze", show_id, source_url))
    externals = show.get("externals") or {}
    tvdb_id = externals.get("thetvdb")
    imdb_id = clean_text(externals.get("imdb"))
    if tvdb_id:
        normalized.external_ids.append(
            ExternalID("tvdb", str(tvdb_id), f"https://thetvdb.com/dereferrer/series/{tvdb_id}")
        )
    if imdb_id:
        normalized.external_ids.append(
            ExternalID("imdb", imdb_id, f"https://www.imdb.com/title/{imdb_id}/")
        )

    airdate = clean_text(episode.get("airdate") or show.get("premiered"))
    if airdate:
        normalized.events.append(
            EventObservation(
                event_type="series_premiere",
                date=airdate[:10],
                source_record_id=str(episode.get("id") or show_id),
                source_url=clean_text(episode.get("url")) or source_url,
                season_number=_int_or_none(episode.get("season")),
                episode_number=_int_or_none(episode.get("number")),
                country=network_country,
                network=network_name or None,
                confidence=0.88,
            )
        )

    if not normalized.overview:
        normalized.quality_flags.append(
            ("missing_overview", "TVmaze has no summary for this title.")
        )
    if not normalized.poster_url:
        normalized.quality_flags.append(
            ("missing_poster", "TVmaze has no primary poster for this title.")
        )

    normalized.ensure_primary_id()
    return normalized


def _is_premiere(episode: dict[str, Any]) -> bool:
    return episode.get("season") == 1 and episode.get("number") == 1


def _runtime(show: dict[str, Any]) -> int | None:
    for key in ("averageRuntime", "runtime"):
        value = _int_or_none(show.get(key))
        if value and value > 0:
            return value
    return None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _updated_at(value: object) -> str | None:
    try:
        timestamp = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")

"""TheTVDB v4 collector and normalizer."""

from __future__ import annotations

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
BASE_URL = "https://api4.thetvdb.com/v4"

REMOTE_SOURCE_MAP: dict[str, str] = {
    "TheMovieDB.com": "tmdb",
    "TV Maze": "tvmaze",
    "IMDB": "imdb",
    "Wikidata": "wikidata",
    "Wikipedia": "wikipedia",
}


class TVDBCollector:
    def __init__(self, api_key: str, client: HTTPClient) -> None:
        self.api_key = api_key
        self.client = client
        self.token = ""

    def login(self) -> None:
        response = self.client.request_json(
            f"{BASE_URL}/login",
            method="POST",
            body={"apikey": self.api_key},
        )
        self.token = str((response.get("data") or {}).get("token") or "")
        if not self.token:
            raise RuntimeError("TheTVDB login succeeded without returning a token")

    @property
    def headers(self) -> dict[str, str]:
        if not self.token:
            self.login()
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def iter_series(self) -> Iterator[dict[str, Any]]:
        page = 0
        while True:
            response = self.client.request_json(
                f"{BASE_URL}/series",
                params={"page": page},
                headers=self.headers,
            )
            data = response.get("data") or []
            if not data:
                return

            for item in data:
                if isinstance(item, dict):
                    yield item

            LOGGER.info(
                "TVDB catalog page",
                extra={"structured": {"page": page, "records": len(data)}},
            )
            links = response.get("links") or {}
            if not links.get("next"):
                return
            page += 1

    def series_in_window(self, start: date, end: date) -> list[dict[str, Any]]:
        matches: dict[str, dict[str, Any]] = {}
        for item in self.iter_series():
            first = clean_text(
                item.get("firstAired")
                or item.get("first_aired")
                or item.get("firstAirTime")
            )[:10]
            provider_id = clean_text(item.get("id"))
            if provider_id and first and start.isoformat() <= first <= end.isoformat():
                matches[provider_id] = item
        return list(matches.values())

    def extended(self, tvdb_id: int | str) -> dict[str, Any]:
        response = self.client.request_json(
            f"{BASE_URL}/series/{tvdb_id}/extended",
            headers=self.headers,
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"TheTVDB extended record {tvdb_id} returned no data")
        return data


def normalize_tvdb(record: dict[str, Any]) -> NormalizedTitle:
    tvdb_id = str(record["id"])
    slug = clean_text(record.get("slug")) or tvdb_id
    source_url = f"https://thetvdb.com/series/{slug}"

    aliases = [
        Alias(clean_text(item.get("name")), normalize_language(item.get("language")))
        for item in (record.get("aliases") or [])
        if isinstance(item, dict) and clean_text(item.get("name"))
    ]
    english_alias = next(
        (alias.value for alias in aliases if alias.language == "en"),
        "",
    )
    original_name = clean_text(record.get("name"))
    original_language = normalize_language(record.get("originalLanguage"))
    title = (
        original_name
        if original_language == "en" and original_name
        else english_alias or original_name or f"TVDB {tvdb_id}"
    )

    genres = {
        clean_text(item.get("name"))
        for item in (record.get("genres") or [])
        if isinstance(item, dict) and clean_text(item.get("name"))
    }
    tags = {
        clean_text(item.get("name"))
        for item in (record.get("tags") or [])
        if isinstance(item, dict) and clean_text(item.get("name"))
    }
    countries = {
        code
        for code in [normalize_country(record.get("originalCountry"))]
        if code
    }

    original_network = record.get("originalNetwork") or {}
    network_name = clean_text(original_network.get("name"))
    network_country = normalize_country(original_network.get("country"))
    network_type = _network_type(original_network)

    normalized = NormalizedTitle(
        source="tvdb",
        source_id=tvdb_id,
        title=title,
        original_title=original_name or None,
        overview=clean_text(record.get("overview")),
        original_language=original_language,
        format=infer_format(
            genres=genres,
            tags=tags,
            explicit=_format_hint(record, genres),
        ),
        status=clean_text((record.get("status") or {}).get("name")) or None,
        runtime_minutes=_positive_int(record.get("averageRuntime")),
        poster_url=_image_url(record.get("image")),
        source_updated_at=clean_text(record.get("lastUpdated")) or None,
        source_url=source_url,
        aliases=[Alias(title, "en"), *aliases],
        countries=countries,
        genres=genres,
        networks=(
            [Network(network_name, network_country, network_type)]
            if network_name
            else []
        ),
        raw=record,
    )

    normalized.external_ids.append(ExternalID("tvdb", tvdb_id, source_url))
    for remote in record.get("remoteIds") or []:
        if not isinstance(remote, dict):
            continue
        source_name = clean_text(remote.get("sourceName"))
        value = clean_text(remote.get("id"))
        mapped = REMOTE_SOURCE_MAP.get(source_name)
        if mapped and value:
            normalized.external_ids.append(
                ExternalID(mapped, value, _remote_url(mapped, value))
            )

    first_aired = clean_text(record.get("firstAired"))
    if first_aired:
        normalized.events.append(
            EventObservation(
                event_type="series_premiere",
                date=first_aired[:10],
                source_record_id=tvdb_id,
                source_url=source_url,
                network=network_name or None,
                confidence=0.80,
            )
        )

    for item in record.get("lists") or []:
        if not isinstance(item, dict):
            continue
        name = clean_text(item.get("name"))
        overview = clean_text(item.get("overview"))
        if "problematic entry" in name.casefold():
            normalized.quality_flags.append(
                ("provider_problematic_entry", overview or name)
            )

    if not normalized.overview:
        normalized.quality_flags.append(
            ("missing_overview", "TheTVDB has no overview for this title.")
        )
    if not normalized.poster_url:
        normalized.quality_flags.append(
            ("missing_poster", "TheTVDB has no primary poster for this title.")
        )

    normalized.ensure_primary_id()
    return normalized


def _image_url(value: object) -> str | None:
    image = clean_text(value) if isinstance(value, str) else ""
    if not image:
        return None
    if image.startswith("/"):
        return f"https://artworks.thetvdb.com{image}"
    return image


def _network_type(network: dict[str, Any]) -> str | None:
    for option in network.get("tagOptions") or []:
        if not isinstance(option, dict):
            continue
        if clean_text(option.get("tagName")).casefold() == "company type":
            return clean_text(option.get("name")) or None
    return None


def _format_hint(record: dict[str, Any], genres: set[str]) -> str | None:
    status = clean_text((record.get("status") or {}).get("recordType"))
    for genre in genres:
        folded = genre.casefold()
        if folded == "mini-series":
            return "Miniseries"
        if folded in {"reality", "documentary", "animation"}:
            return genre
    return status.title() if status and status != "series" else None


def _positive_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _remote_url(source: str, value: str) -> str | None:
    if source == "tmdb":
        return f"https://www.themoviedb.org/tv/{value}"
    if source == "tvmaze":
        return f"https://www.tvmaze.com/shows/{value}"
    if source == "imdb":
        return f"https://www.imdb.com/title/{value}/"
    if source == "wikidata":
        return f"https://www.wikidata.org/wiki/{value}"
    if source == "wikipedia":
        return f"https://en.wikipedia.org/wiki/{value}"
    return None

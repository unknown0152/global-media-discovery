"""Internal normalized source models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ExternalID:
    source: str
    value: str
    url: str | None = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.value}"


@dataclass(frozen=True, slots=True)
class Alias:
    value: str
    language: str | None = None


@dataclass(frozen=True, slots=True)
class Network:
    name: str
    country: str | None = None
    kind: str | None = None


@dataclass(frozen=True, slots=True)
class EventObservation:
    event_type: str
    date: str
    source_record_id: str
    source_url: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    country: str | None = None
    network: str | None = None
    confidence: float = 0.75


@dataclass(slots=True)
class NormalizedTitle:
    source: str
    source_id: str
    title: str
    original_title: str | None = None
    overview: str = ""
    original_language: str | None = None
    format: str = "Unknown"
    status: str | None = None
    runtime_minutes: int | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    source_updated_at: str | None = None
    source_url: str | None = None
    external_ids: list[ExternalID] = field(default_factory=list)
    aliases: list[Alias] = field(default_factory=list)
    countries: set[str] = field(default_factory=set)
    genres: set[str] = field(default_factory=set)
    networks: list[Network] = field(default_factory=list)
    events: list[EventObservation] = field(default_factory=list)
    quality_flags: list[tuple[str, str]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def ensure_primary_id(self) -> None:
        key = ExternalID(self.source, self.source_id, self.source_url)
        if all(existing.key != key.key for existing in self.external_ids):
            self.external_ids.insert(0, key)

"""Dependency-light read-only WSGI API."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, timedelta
import json
import logging
import re
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs
import uuid

from gmd.config import Settings, load_settings
from gmd.query import CatalogQueries, EventFilters

LOGGER = logging.getLogger(__name__)
_TITLE_ID_RE = re.compile(r"^[a-z0-9_]{8,80}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.per_minute:
                retry = max(1, int(60 - (now - bucket[0])))
                return False, retry
            bucket.append(now)
            if len(self._buckets) > 10000:
                self._prune(cutoff)
            return True, 0

    def _prune(self, cutoff: float) -> None:
        for key in list(self._buckets):
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if not bucket:
                self._buckets.pop(key, None)


class ReadOnlyAPI:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.queries = CatalogQueries(self.settings.database_path)
        self.rate_limiter = RateLimiter(self.settings.rate_limit_per_minute)

    def __call__(
        self,
        environ: dict[str, Any],
        start_response: Callable[..., Any],
    ) -> list[bytes]:
        request_id = uuid.uuid4().hex[:16]
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/"))
        client_ip = self._client_ip(environ)

        if method not in {"GET", "HEAD"}:
            return self._respond(
                start_response,
                405,
                {
                    "error": {
                        "code": "method_not_allowed",
                        "message": "This public API is read-only.",
                        "request_id": request_id,
                    }
                },
                method=method,
                extra_headers=[("Allow", "GET, HEAD")],
            )

        allowed, retry_after = self.rate_limiter.allow(client_ip)
        if not allowed:
            return self._respond(
                start_response,
                429,
                {
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests. Please try again shortly.",
                        "request_id": request_id,
                    }
                },
                method=method,
                extra_headers=[("Retry-After", str(retry_after))],
            )

        try:
            query = parse_qs(str(environ.get("QUERY_STRING", "")), keep_blank_values=False)
            payload, _cache_seconds = self._route(path, query)
            status = 200
        except APIError as error:
            status = error.status
            payload = {
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "request_id": request_id,
                }
            }
        except Exception:
            LOGGER.exception(
                "unhandled API error",
                extra={"structured": {"path": path, "request_id": request_id}},
            )
            status = 500
            payload = {
                "error": {
                    "code": "internal_error",
                    "message": "The catalog API encountered an unexpected error.",
                    "request_id": request_id,
                }
            }

        return self._respond(
            start_response,
            status,
            payload,
            method=method,
            cache_seconds=0,
            request_id=request_id,
        )

    def _route(
        self,
        path: str,
        query: dict[str, list[str]],
    ) -> tuple[dict[str, Any], int]:
        prefix = self.settings.api_prefix

        if path == f"{prefix}/health":
            payload = self.queries.health()
            status = payload.get("status")
            if status == "error":
                raise APIError(503, "catalog_unavailable", "The catalog is not ready.")
            return payload, 0

        if not self.settings.database_path.exists():
            raise APIError(503, "catalog_starting", "The catalog is being initialized.")

        if path == f"{prefix}/meta":
            payload = self.queries.meta()
            payload["integrations"] = {
                "seerr": {
                    "configured": bool(self.settings.seerr_public_url),
                    "mode": "authenticated_handoff",
                    "public_url": self.settings.seerr_public_url or None,
                }
            }
            return payload, 30

        if path == f"{prefix}/status":
            return self.queries.status(), 15

        if path == f"{prefix}/stats":
            return self.queries.stats(), 60

        if path == f"{prefix}/coverage":
            return self.queries.coverage(), 300

        if path in {f"{prefix}/events", f"{prefix}/date-range"}:
            filters = self._filters(query)
            return self.queries.events(filters), 60

        if path in {f"{prefix}/facets", f"{prefix}/filters"}:
            start, end = self._date_range(query)
            return self.queries.facets(start, end), 300

        if path == f"{prefix}/search":
            search_query = _bounded(query, "q", maximum=200, required=True)
            limit = _integer(
                query,
                "limit",
                default=40,
                minimum=1,
                maximum=self.settings.max_page_size,
            )
            offset = _integer(
                query, "offset", default=0, minimum=0, maximum=1_000_000
            )
            return self.queries.search_titles(search_query, limit, offset), 60

        if path == f"{prefix}/calendar":
            month = _one(query, "month") or date.today().strftime("%Y-%m")
            if not _MONTH_RE.fullmatch(month):
                raise APIError(400, "invalid_month", "month must use YYYY-MM.")
            try:
                return self.queries.calendar(month), 300
            except ValueError as error:
                raise APIError(
                    400,
                    "invalid_month",
                    "month is not a valid calendar month.",
                ) from error

        if path == f"{prefix}/credits":
            return self.queries.credits(), 3600

        title_prefix = f"{prefix}/titles/"
        if path.startswith(title_prefix):
            title_id = path[len(title_prefix):]
            if not _TITLE_ID_RE.fullmatch(title_id):
                raise APIError(400, "invalid_title_id", "Invalid title identifier.")
            item = self.queries.title(title_id)
            if item is None:
                raise APIError(404, "not_found", "Title not found.")
            return item, 300

        raise APIError(404, "not_found", "API route not found.")

    def _filters(self, query: dict[str, list[str]]) -> EventFilters:
        start, end = self._date_range(query)
        limit = _integer(
            query,
            "limit",
            default=60,
            minimum=1,
            maximum=self.settings.max_page_size,
        )
        offset = _integer(query, "offset", default=0, minimum=0, maximum=1_000_000)
        conflict = (_one(query, "conflict") or "").lower()
        if conflict not in {"", "only", "exclude"}:
            raise APIError(
                400,
                "invalid_conflict_filter",
                "conflict must be only or exclude.",
            )
        confidence = (_one(query, "confidence") or "").lower()
        if confidence not in {"", "high", "medium", "low"}:
            raise APIError(
                400,
                "invalid_confidence_filter",
                "confidence must be high, medium, or low.",
            )
        sort = (_one(query, "sort") or "date_asc").lower()
        if sort not in {"date_asc", "date_desc", "title_asc", "confidence_desc"}:
            raise APIError(400, "invalid_sort", "Unsupported sort order.")
        country = (_bounded(query, "country", maximum=2) or "").upper()
        if country and not re.fullmatch(r"[A-Z]{2}", country):
            raise APIError(400, "invalid_country", "country must be an ISO alpha-2 code.")
        language = (_bounded(query, "language", maximum=8) or "").lower()
        if language and not re.fullmatch(r"[a-z]{2,8}", language):
            raise APIError(400, "invalid_language", "language is not valid.")
        source = (_bounded(query, "source", maximum=24) or "").lower()
        if source and not re.fullmatch(r"[a-z0-9_-]+", source):
            raise APIError(400, "invalid_source", "source is not valid.")
        event_type = (_bounded(query, "event_type", maximum=40) or "").lower()
        if event_type and not re.fullmatch(r"[a-z0-9_]+", event_type):
            raise APIError(400, "invalid_event_type", "event_type is not valid.")

        return EventFilters(
            start=start,
            end=end,
            query=_bounded(query, "q", maximum=200),
            country=country,
            language=language,
            network=_bounded(query, "network", maximum=160),
            genre=_bounded(query, "genre", maximum=80),
            format=_bounded(query, "format", maximum=80),
            source=source,
            event_type=event_type,
            confidence=confidence,
            conflict=conflict,
            sort=sort,
            limit=limit,
            offset=offset,
        )

    def _date_range(self, query: dict[str, list[str]]) -> tuple[date, date]:
        single = _one(query, "date")
        start_raw = single or _one(query, "from") or date.today().isoformat()
        end_raw = single or _one(query, "to") or start_raw
        try:
            start = date.fromisoformat(start_raw)
            end = date.fromisoformat(end_raw)
        except ValueError as error:
            raise APIError(
                400,
                "invalid_date",
                "Dates must use ISO format YYYY-MM-DD.",
            ) from error
        if end < start:
            raise APIError(400, "invalid_range", "to must not be earlier than from.")
        if (end - start).days + 1 > self.settings.max_query_days:
            raise APIError(
                400,
                "range_too_large",
                f"A query may cover at most {self.settings.max_query_days} days.",
            )
        return start, end

    @staticmethod
    def _client_ip(environ: dict[str, Any]) -> str:
        forwarded = str(environ.get("HTTP_X_FORWARDED_FOR", "")).split(",")[0].strip()
        return forwarded or str(environ.get("REMOTE_ADDR", "unknown"))

    def _respond(
        self,
        start_response: Callable[..., Any],
        status: int,
        payload: object,
        *,
        method: str,
        cache_seconds: int = 0,
        request_id: str = "",
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> list[bytes]:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        start_response(
            f"{status} {_reason(status)}",
            self._headers(
                cache_seconds,
                request_id,
                content_length=len(body),
                extra=extra_headers,
            ),
        )
        return [] if method == "HEAD" else [body]

    @staticmethod
    def _headers(
        cache_seconds: int,
        request_id: str,
        *,
        content_length: int | None = None,
        extra: list[tuple[str, str]] | None = None,
    ) -> list[tuple[str, str]]:
        cache = (
            f"public, max-age={cache_seconds}, stale-while-revalidate={cache_seconds * 5}"
            if cache_seconds
            else "no-store"
        )
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", cache),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
            ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"),
            ("Cross-Origin-Opener-Policy", "same-origin"),
            ("Cross-Origin-Resource-Policy", "same-origin"),
        ]
        if request_id:
            headers.append(("X-Request-ID", request_id))
        if content_length is not None:
            headers.append(("Content-Length", str(content_length)))
        if extra:
            headers.extend(extra)
        return headers


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _one(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0].strip() if values else None


def _integer(
    query: dict[str, list[str]],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = _one(query, key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise APIError(400, f"invalid_{key}", f"{key} must be an integer.") from error
    if value < minimum or value > maximum:
        raise APIError(
            400,
            f"invalid_{key}",
            f"{key} must be between {minimum} and {maximum}.",
        )
    return value


def _bounded(
    query: dict[str, list[str]],
    key: str,
    *,
    maximum: int,
    required: bool = False,
) -> str:
    value = _one(query, key) or ""
    if required and not value:
        raise APIError(400, f"missing_{key}", f"{key} is required.")
    if len(value) > maximum:
        raise APIError(400, f"{key}_too_long", f"{key} may be at most {maximum} characters.")
    return value


def _reason(status: int) -> str:
    return {
        200: "OK",
        304: "Not Modified",
        400: "Bad Request",
        404: "Not Found",
        405: "Method Not Allowed",
        429: "Too Many Requests",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }.get(status, "Unknown")

"""Small dependency-free HTTP client with retries and polite throttling."""

from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import json
import logging
import random
import time
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class HTTPClient:
    user_agent: str
    timeout: int = 45
    min_delay_seconds: float = 0.20
    max_attempts: int = 5
    _last_request_at: float = field(init=False, default=0.0, repr=False)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.min_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def request_json(
        self,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        method: str = "GET",
        body: object | None = None,
    ) -> Any:
        if params:
            query = urllib.parse.urlencode(
                {key: value for key, value in params.items() if value is not None}
            )
            url = f"{url}{'&' if '?' in url else '?'}{query}"

        payload = None
        merged_headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": self.user_agent,
        }
        if headers:
            merged_headers.update(headers)
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            merged_headers.setdefault("Content-Type", "application/json")

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._throttle()
            request = urllib.request.Request(
                url,
                data=payload,
                headers=merged_headers,
                method=method,
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    if response.headers.get("Content-Encoding", "").lower() == "gzip":
                        raw = gzip.decompress(raw)
                    self._last_request_at = time.monotonic()
                    return json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as error:
                self._last_request_at = time.monotonic()
                detail = error.read().decode("utf-8", "replace")[:2000]
                last_error = RuntimeError(
                    f"HTTP {error.code} for {url}: {detail or error.reason}"
                )
                if error.code not in {408, 425, 429, 500, 502, 503, 504}:
                    raise last_error from error
                retry_after = error.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 0
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                self._last_request_at = time.monotonic()
                last_error = error
                delay = 0

            if attempt < self.max_attempts:
                delay = max(delay, min(30.0, 0.75 * (2 ** (attempt - 1))))
                delay += random.uniform(0.0, 0.25)
                LOGGER.warning(
                    "source request failed; retrying",
                    extra={
                        "structured": {
                            "url": url,
                            "attempt": attempt,
                            "delay_seconds": round(delay, 2),
                            "error": str(last_error),
                        }
                    },
                )
                time.sleep(delay)

        raise RuntimeError(
            f"request failed after {self.max_attempts} attempts: {url}"
        ) from last_error

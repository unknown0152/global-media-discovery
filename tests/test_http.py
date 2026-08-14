from __future__ import annotations

import json
from io import BytesIO
import unittest
from unittest.mock import patch
import urllib.error

from gmd.collector.http import HTTPClient


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.headers: dict[str, str] = {}

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class HTTPClientTests(unittest.TestCase):
    def test_slots_client_initializes_and_decodes_json(self) -> None:
        client = HTTPClient("GlobalMediaDiscoveryTest/1.0", min_delay_seconds=0)
        with patch(
            "urllib.request.urlopen",
            return_value=_Response({"status": "ok"}),
        ):
            self.assertEqual(
                client.request_json("https://example.invalid/test"),
                {"status": "ok"},
            )
        self.assertGreater(client._last_request_at, 0)

    def test_transient_rate_limit_is_retried(self) -> None:
        client = HTTPClient(
            "GlobalMediaDiscoveryTest/1.0",
            min_delay_seconds=0,
            max_attempts=2,
        )
        limited = urllib.error.HTTPError(
            "https://example.invalid/test",
            429,
            "Too Many Requests",
            {"Retry-After": "1"},
            BytesIO(b'{"error":"limited"}'),
        )
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=[limited, _Response({"status": "ok"})],
            ),
            patch("gmd.collector.http.time.sleep") as sleep,
            patch("gmd.collector.http.random.uniform", return_value=0),
        ):
            self.assertEqual(
                client.request_json("https://example.invalid/test"),
                {"status": "ok"},
            )
        sleep.assert_called_once_with(1.0)


if __name__ == "__main__":
    unittest.main()

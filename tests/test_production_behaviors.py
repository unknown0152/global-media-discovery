from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gmd.api import ReadOnlyAPI
from gmd.collector.pipeline import CollectorPipeline
from gmd.collector.seed import import_seed
from gmd.collector.tvmaze import TVMazeCollector
from gmd.config import load_settings
from gmd.db import (
    backup_database,
    connect_rw,
    publish_database,
    staging_database,
    validate_database,
)
from gmd.ui import ReadOnlyUI, render_title


class ProductionBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temp.name) / "catalog.sqlite3"
        with staging_database(self.database, site_name="Production Test") as staging:
            with connect_rw(staging) as connection:
                import_seed(connection, self.root / "seed")
            publish_database(staging, self.database)
        self.settings = replace(
            load_settings(),
            data_dir=Path(self.temp.name),
            database_path=self.database,
            seed_dir=self.root / "seed",
            site_name="Production Test",
            rate_limit_per_minute=10000,
        )
        self.api = ReadOnlyAPI(self.settings)
        self.ui = ReadOnlyUI(self.settings)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        query: str = "",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], dict[str, object]]:
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status
            captured["headers"] = dict(headers)

        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "REMOTE_ADDR": f"127.0.0.{hash((method, path, query)) % 200 + 1}",
        }
        environ.update(headers or {})
        chunks = self.api(environ, start_response)
        body = b"".join(chunks)
        payload = json.loads(body) if body else {}
        return (
            int(str(captured["status"]).split()[0]),
            captured["headers"],  # type: ignore[return-value]
            payload,
        )

    def test_stable_public_routes_and_head(self) -> None:
        routes = (
            ("/api/v1/health", ""),
            ("/api/v1/status", ""),
            ("/api/v1/stats", ""),
            ("/api/v1/coverage", ""),
            ("/api/v1/filters", "from=2026-08-13&to=2026-08-13"),
            ("/api/v1/date-range", "from=2026-08-13&to=2026-08-13"),
            ("/api/v1/search", "q=Hit%20Point"),
        )
        for path, query in routes:
            with self.subTest(path=path):
                status, _, payload = self.request("GET", path, query)
                self.assertEqual(status, 200, payload)
                status, headers, payload = self.request("HEAD", path, query)
                self.assertEqual(status, 200)
                self.assertEqual(payload, {})
                self.assertIn("Content-Length", headers)

    def test_api_never_returns_an_empty_conditional_response(self) -> None:
        path = "/api/v1/events"
        query = "from=2026-08-13&to=2026-08-13&limit=1"
        status, headers, payload = self.request(
            "GET",
            path,
            query,
            {"HTTP_IF_NONE_MATCH": '"previous-browser-value"'},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("ETag", headers)
        self.assertEqual(len(payload["items"]), 1)

    def test_htmx_fragments_are_read_only_and_escaped(self) -> None:
        _, _, events = self.request(
            "GET",
            "/api/v1/events",
            "from=2026-08-13&to=2026-08-13&limit=1",
        )
        title_id = events["items"][0]["title"]["id"]
        status, headers, body = self.ui_request(
            "GET",
            f"/ui/v1/titles/{title_id}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn('data-htmx-fragment="title-detail"', body)
        self.assertNotIn("<script", body.lower())

        status, headers, body = self.ui_request("HEAD", "/ui/v1/credits")
        self.assertEqual(status, 200)
        self.assertEqual(body, "")
        self.assertGreater(int(headers["Content-Length"]), 0)

        status, _, body = self.ui_request("GET", "/ui/v1/coverage")
        self.assertEqual(status, 200)
        self.assertIn('data-htmx-fragment="coverage"', body)
        self.assertIn("Coverage by year", body)

        status, headers, body = self.ui_request("POST", "/ui/v1/credits")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, HEAD")
        self.assertIn("read-only", body)

        malicious = dict(events["items"][0])
        malicious["title"] = dict(malicious["title"])
        malicious["title"]["name"] = '<script>alert("x")</script>'
        malicious["title"]["poster_url"] = "javascript:alert(1)"
        escaped = render_title(malicious)
        self.assertNotIn("<script", escaped.lower())
        self.assertNotIn("javascript:", escaped.lower())
        self.assertIn("&lt;script&gt;", escaped)

    def test_coverage_is_explicit_and_counted(self) -> None:
        status, _, payload = self.request("GET", "/api/v1/coverage")
        self.assertEqual(status, 200)
        self.assertIn("not a claim of complete", payload["scope"])
        self.assertEqual(payload["event_count"], payload["title_count"])
        self.assertGreater(payload["active_day_count"], 0)
        self.assertGreater(payload["evidence_count"], 0)
        self.assertTrue(payload["years"])
        self.assertTrue(payload["sources"])

    def test_all_unsupported_methods_are_rejected(self) -> None:
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"):
            with self.subTest(method=method):
                status, headers, payload = self.request(method, "/api/v1/events")
                self.assertEqual(status, 405)
                self.assertEqual(headers["Allow"], "GET, HEAD")
                self.assertEqual(payload["error"]["code"], "method_not_allowed")

    def ui_request(
        self,
        method: str,
        path: str,
    ) -> tuple[int, dict[str, str], str]:
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status
            captured["headers"] = dict(headers)

        chunks = self.ui(
            {
                "REQUEST_METHOD": method,
                "PATH_INFO": path,
                "REMOTE_ADDR": "127.0.0.210",
            },
            start_response,
        )
        return (
            int(str(captured["status"]).split()[0]),
            captured["headers"],  # type: ignore[return-value]
            b"".join(chunks).decode("utf-8"),
        )

    def test_search_filters_sort_and_pagination(self) -> None:
        query = "from=2026-08-13&to=2026-08-13&limit=1&sort=title_asc"
        status, _, first = self.request("GET", "/api/v1/events", query)
        self.assertEqual(status, 200)
        self.assertEqual(len(first["items"]), 1)
        self.assertTrue(first["pagination"]["has_more"])
        self.assertEqual(first["summary"]["matching_events"], 8)

        item = first["items"][0]
        filters = {
            "q": item["title"]["name"],
            "country": item["countries"][0],
            "language": item["title"]["language"],
            "format": item["title"]["format"],
            "event_type": item["event_type"],
            "source": item["evidence"][0]["source"],
        }
        for key, value in filters.items():
            if not value:
                continue
            encoded = str(value).replace(" ", "%20")
            filtered = f"from=2026-08-13&to=2026-08-13&{key}={encoded}"
            status, _, payload = self.request("GET", "/api/v1/events", filtered)
            self.assertEqual(status, 200, (key, payload))
            self.assertGreater(payload["pagination"]["total"], 0, key)

        status, _, result = self.request("GET", "/api/v1/search", "q=Hit%20Point")
        self.assertEqual(status, 200)
        self.assertIn("Hit Point", {item["name"] for item in result["items"]})

    def test_input_limits_and_enumerations(self) -> None:
        invalid = (
            ("country=USA", "country_too_long"),
            ("confidence=certain", "invalid_confidence_filter"),
            ("sort=random", "invalid_sort"),
            ("limit=10000", "invalid_limit"),
            (f"q={'x' * 201}", "q_too_long"),
        )
        for extra, code in invalid:
            query = f"from=2026-08-13&to=2026-08-13&{extra}"
            status, _, payload = self.request("GET", "/api/v1/events", query)
            self.assertEqual(status, 400, extra)
            self.assertEqual(payload["error"]["code"], code)

    def test_invalid_staging_never_replaces_live_catalog(self) -> None:
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with staging_database(self.database, site_name="Production Test") as staging:
            with connect_rw(staging) as connection:
                connection.execute("UPDATE events SET event_date = '2026-02-30'")
                connection.commit()
            with self.assertRaisesRegex(RuntimeError, "malformed event dates"):
                publish_database(staging, self.database)
        after = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual(validate_database(self.database)["integrity"], "ok")

    def test_all_source_failures_preserve_valid_live_catalog(self) -> None:
        settings = replace(
            self.settings,
            enable_tmdb=False,
            enable_tvdb=False,
            enable_tvmaze=True,
            backup_retention=2,
        )
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with patch.object(
            CollectorPipeline,
            "_update_tvmaze",
            side_effect=RuntimeError("simulated source outage"),
        ):
            result = CollectorPipeline(settings).update()
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(result["retained_live_catalog"])
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())
        self.assertEqual(len(list((Path(self.temp.name) / "backups").glob("*.sqlite3"))), 1)

    def test_one_source_failure_does_not_block_another_source(self) -> None:
        settings = replace(
            self.settings,
            enable_tmdb=True,
            tmdb_token="configured-for-test",
            enable_tvdb=False,
            enable_tvmaze=True,
        )
        with (
            patch.object(
                CollectorPipeline,
                "_update_tmdb",
                side_effect=RuntimeError("simulated TMDB outage"),
            ),
            patch.object(CollectorPipeline, "_update_tvmaze", return_value={"ingested": 0}),
        ):
            result = CollectorPipeline(settings).update()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source_results"]["tvmaze"], "ok")
        self.assertIn("error", result["source_results"]["tmdb"])
        self.assertEqual(validate_database(self.database)["integrity"], "ok")

    def test_tvmaze_backfill_scans_past_dates_without_credentials(self) -> None:
        class FakeClient:
            def request_json(self, url: str, **kwargs: object) -> list[object]:
                return []

        collector = TVMazeCollector(FakeClient())  # type: ignore[arg-type]
        seen: list[date] = []
        collector.full_schedule = lambda: []  # type: ignore[method-assign]
        collector.web_schedule = lambda day: seen.append(day) or []  # type: ignore[method-assign]
        today = date.today()
        collector.premieres(
            today - timedelta(days=10),
            today + timedelta(days=2),
            recent_days=0,
            backfill_days=10,
        )
        self.assertEqual(min(seen), today - timedelta(days=10))
        self.assertEqual(max(seen), today + timedelta(days=2))

    def test_backup_is_private_and_recoverable(self) -> None:
        destination = Path(self.temp.name) / "backups" / "catalog-test.sqlite3"
        backup_database(self.database, destination)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(validate_database(destination)["integrity"], "ok")


if __name__ == "__main__":
    unittest.main()

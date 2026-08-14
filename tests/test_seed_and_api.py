from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest

from gmd.api import ReadOnlyAPI
from gmd.collector.pipeline import CollectorPipeline
from gmd.collector.seed import import_seed
from gmd.config import load_settings
from gmd.db import (
    connect_rw,
    initialize_database,
    publish_database,
    staging_database,
    validate_database,
)
from gmd.query import CatalogQueries, EventFilters
from datetime import date


class SeedAndAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(__file__).resolve().parents[1]
        cls.db_path = Path(cls.temp.name) / "catalog.sqlite3"
        with staging_database(cls.db_path, site_name="Test Media") as staging:
            with connect_rw(staging) as connection:
                cls.seed_metrics = import_seed(connection, cls.root / "seed")
            publish_database(staging, cls.db_path)
        cls.settings = replace(
            load_settings(),
            data_dir=Path(cls.temp.name),
            database_path=cls.db_path,
            seed_dir=cls.root / "seed",
            site_name="Test Media",
        )
        cls.api = ReadOnlyAPI(cls.settings)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_compact_seed_is_valid_and_populated(self) -> None:
        result = validate_database(self.db_path)
        self.assertEqual(result["integrity"], "ok")
        self.assertGreaterEqual(result["titles"], 250)
        self.assertGreaterEqual(result["events"], 250)
        self.assertEqual(self.seed_metrics["errors"], 0)

    def test_schema_avoids_legacy_without_rowid_integrity_false_positives(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database_path = Path(temp) / "schema.sqlite3"
            initialize_database(database_path, site_name="Compatibility Test")
            with connect_rw(database_path) as connection:
                table_sql = connection.execute(
                    "SELECT sql FROM sqlite_schema WHERE type = 'table'"
                ).fetchall()
            self.assertFalse(
                any("WITHOUT ROWID" in str(row["sql"]) for row in table_sql)
            )

    def test_bootstrap_quarantines_an_invalid_existing_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            database_path = data_dir / "catalog.sqlite3"
            database_path.write_bytes(b"not a sqlite database")
            settings = replace(
                self.settings,
                data_dir=data_dir,
                database_path=database_path,
                seed_dir=self.root / "seed",
            )
            result = CollectorPipeline(settings).bootstrap(seed_only=True)
            self.assertEqual(result["status"], "bootstrapped")
            self.assertEqual(validate_database(database_path)["integrity"], "ok")
            quarantined = list(
                (data_dir / "backups").glob("catalog-invalid-*.sqlite3")
            )
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(quarantined[0].read_bytes(), b"not a sqlite database")

    def test_seed_rejects_known_bad_state_of_play_remote_id(self) -> None:
        queries = CatalogQueries(self.db_path)
        result = queries.events(
            EventFilters(
                start=date(2026, 8, 10),
                end=date(2026, 8, 10),
                query="State of Play",
                limit=20,
            )
        )
        item = next(
            entry
            for entry in result["items"]
            if entry["title"]["name"] == "State of Play"
        )
        keys = {
            (identity["source"], identity["id"])
            for identity in item["external_ids"]
        }
        self.assertIn(("tmdb", "328256"), keys)
        self.assertIn(("tvdb", "458152"), keys)
        self.assertNotIn(("tmdb", "280419"), keys)

    def test_august_13_query_returns_seeded_premieres(self) -> None:
        result = CatalogQueries(self.db_path).events(
            EventFilters(
                start=date(2026, 8, 13),
                end=date(2026, 8, 13),
                limit=200,
            )
        )
        self.assertEqual(result["pagination"]["total"], 8)
        names = {item["title"]["name"] for item in result["items"]}
        self.assertIn("Hit Point", names)
        self.assertIn("My Brilliant Career", names)

    def test_wsgi_api_is_read_only(self) -> None:
        status, headers, payload = self.request("GET", "/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")

        status, headers, payload = self.request("POST", "/api/v1/events")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, HEAD")
        self.assertEqual(payload["error"]["code"], "method_not_allowed")

    def test_wsgi_api_validates_ranges_and_title_ids(self) -> None:
        status, _, payload = self.request(
            "GET", "/api/v1/events", "from=2026-08-13&to=2026-08-01"
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_range")

        status, _, payload = self.request("GET", "/api/v1/titles/not%2Fsafe")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_title_id")

    def request(
        self,
        method: str,
        path: str,
        query: str = "",
    ) -> tuple[int, dict[str, str], dict[str, object]]:
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status
            captured["headers"] = dict(headers)

        chunks = self.api(
            {
                "REQUEST_METHOD": method,
                "PATH_INFO": path,
                "QUERY_STRING": query,
                "REMOTE_ADDR": "127.0.0.1",
            },
            start_response,
        )
        body = b"".join(chunks)
        payload = json.loads(body) if body else {}
        return (
            int(str(captured["status"]).split()[0]),
            captured["headers"],  # type: ignore[return-value]
            payload,
        )


if __name__ == "__main__":
    unittest.main()

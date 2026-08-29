from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from gmd.collector.http import HTTPClient, redact_url
from gmd.collector.simkl import (
    SIMKL_CATALOGS,
    SimklCalendarCollector,
    SimklCalendarProbe,
    compare_with_catalog,
    normalize_simkl_tv,
    validate_calendar_payload,
)
from gmd.collector.tmdb import normalize_tmdb
from gmd.collector.tvdb import normalize_tvdb
from gmd.db import connect_ro, connect_rw, initialize_database
from gmd.reconcile import CatalogWriter


def calendar_payload() -> dict[str, object]:
    return {
        "calendar": [
            {
                "simkl_id": 101,
                "date": "2026-08-29T20:00:00Z",
                "finale_type": None,
                "episode": {"season": 1, "episode": 1},
            },
            {
                "simkl_id": 102,
                "date": "2026-08-30T20:00:00Z",
                "finale_type": 2,
                "episode": {"season": 2, "episode": 8},
            },
        ],
        "metadata": {
            "101": {
                "title": "Known Show",
                "release_date": "2026-08-29T20:00:00Z",
                "country": "us",
                "network": "Example",
                "ids": {"simkl_id": 101, "tmdb": "501", "tvdb": "601"},
            },
            "102": {
                "title": "Unmatched Show",
                "release_date": "2026-08-30T20:00:00Z",
                "country": "dk",
                "network": "Example DK",
                "ids": {"simkl_id": 102, "tmdb": "502"},
            },
        },
    }


class SimklPayloadTests(unittest.TestCase):
    def test_adapter_uses_public_calendar_not_oauth_sync(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src/gmd/collector/simkl.py"
        ).read_text(encoding="utf-8")
        self.assertIn("https://data.simkl.in/calendar/v2", source)
        self.assertNotIn('"/sync/', source)

    def test_v2_payload_is_validated_and_summarized(self) -> None:
        summary = validate_calendar_payload(calendar_payload())
        self.assertEqual(summary["calendar_entries"], 2)
        self.assertEqual(summary["metadata_records"], 2)
        self.assertEqual(summary["referenced_titles"], 2)
        self.assertEqual(summary["duplicate_schedule_entries"], 0)
        self.assertEqual(summary["finale_types"], {"2": 1, "none": 1})
        self.assertEqual(
            summary["schedule_types"],
            {"regular_episode": 1, "series_premiere_candidate": 1},
        )

    def test_orphan_calendar_entry_is_rejected(self) -> None:
        payload = calendar_payload()
        calendar = payload["calendar"]
        self.assertIsInstance(calendar, list)
        calendar.append(
            {"simkl_id": 999, "date": "2026-08-31T20:00:00Z"}
        )
        with self.assertRaisesRegex(ValueError, "no matching metadata"):
            validate_calendar_payload(payload)

    def test_non_utc_or_invalid_calendar_date_is_rejected(self) -> None:
        for invalid in ("2026-08-29T20:00:00+02:00", "2026-02-30T20:00:00Z"):
            with self.subTest(invalid=invalid):
                payload = calendar_payload()
                calendar = payload["calendar"]
                self.assertIsInstance(calendar, list)
                calendar[0]["date"] = invalid
                with self.assertRaisesRegex(ValueError, "calendar date"):
                    validate_calendar_payload(payload)

    def test_duplicate_schedule_entries_are_reported(self) -> None:
        payload = calendar_payload()
        calendar = payload["calendar"]
        self.assertIsInstance(calendar, list)
        calendar.append(dict(calendar[0]))
        summary = validate_calendar_payload(payload)
        self.assertEqual(summary["duplicate_schedule_entries"], 1)

    def test_url_redaction_hides_simkl_and_provider_credentials(self) -> None:
        safe = redact_url(
            "https://example.test/calendar?client_id=public-id&token=secret&q=tv"
        )
        self.assertNotIn("public-id", safe)
        self.assertNotIn("secret", safe)
        self.assertIn("q=tv", safe)

    def test_collector_sends_only_public_app_identification(self) -> None:
        class FakeHTTP(HTTPClient):
            def __init__(self) -> None:
                super().__init__("test", min_delay_seconds=0)
                self.params: object = None

            def request_json(self, url: str, **kwargs: object) -> object:
                self.assert_calendar_url = url
                self.params = kwargs.get("params")
                return calendar_payload()

        http = FakeHTTP()
        collector = SimklCalendarCollector(
            "client-id",
            http,
            app_version="test-version",
        )
        collector.calendar("tv")
        self.assertTrue(http.assert_calendar_url.endswith("/tv.json"))
        self.assertEqual(
            http.params,
            {
                "client_id": "client-id",
                "app-name": "global-media-discovery",
                "app-version": "test-version",
            },
        )
        self.assertNotIn("secret", str(http.params).lower())

    def test_normalizer_keeps_premieres_and_finales_not_regular_episodes(self) -> None:
        payload = calendar_payload()
        calendar = payload["calendar"]
        metadata = payload["metadata"]
        self.assertIsInstance(calendar, list)
        self.assertIsInstance(metadata, dict)
        calendar.extend(
            [
                {
                    "simkl_id": 102,
                    "date": "2026-08-30T18:00:00Z",
                    "finale_type": None,
                    "episode": {"season": 2, "episode": 1},
                },
                {
                    "simkl_id": 103,
                    "date": "2026-08-31T18:00:00Z",
                    "finale_type": None,
                    "episode": {"season": 3, "episode": 4},
                },
                {
                    "simkl_id": 104,
                    "date": "2026-08-31T20:00:00Z",
                    "finale_type": 3,
                },
            ]
        )
        metadata["101"]["ids"]["slug"] = "known-show"
        metadata["102"]["ids"]["slug"] = "unmatched-show"
        metadata["103"] = {
            "title": "Regular Episode Only",
            "release_date": "2022-01-01",
            "country": "gb",
            "network": "Example GB",
            "ids": {"simkl_id": 103, "slug": "regular-episode-only"},
        }
        metadata["104"] = {
            "title": "Finale Without Episode Numbers",
            "release_date": "2020-01-01",
            "country": "ca",
            "network": "Example CA",
            "ids": {"simkl_id": 104, "slug": "finale-without-numbers"},
        }
        records = normalize_simkl_tv(
            payload,
            start=date(2026, 8, 1),
            end=date(2026, 9, 30),
        )
        self.assertEqual(
            {record.source_id for record in records},
            {"101", "102", "104"},
        )
        events = {
            event.event_type
            for record in records
            for event in record.events
        }
        self.assertEqual(
            events,
            {
                "series_premiere",
                "season_premiere",
                "season_finale",
                "series_finale",
            },
        )
        known = next(record for record in records if record.source_id == "101")
        self.assertEqual(known.source_url, "https://simkl.com/tv/101/known-show")
        self.assertNotIn("poster", known.raw)
        self.assertNotIn("ratings", known.raw)


class SimklCatalogProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "catalog.sqlite3"
        initialize_database(self.db_path, site_name="Simkl Probe Test")
        with connect_rw(self.db_path) as connection:
            writer = CatalogWriter(connection)
            writer.ingest(
                normalize_tmdb(
                    {
                        "id": 501,
                        "name": "Known Show",
                        "first_air_date": "2026-08-29",
                        "origin_country": ["US"],
                    }
                )
            )
            writer.ingest(
                normalize_tvdb(
                    {
                        "id": 777,
                        "name": "Different Show",
                        "firstAired": "2025-01-01",
                        "originalCountry": "gbr",
                    }
                )
            )
            connection.commit()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_comparison_uses_ids_and_does_not_fuzzy_match(self) -> None:
        payload = calendar_payload()
        with connect_ro(self.db_path) as connection:
            result = compare_with_catalog(payload, connection)
        self.assertEqual(result["matched_catalog_titles"], 1)
        self.assertEqual(result["unmatched_catalog_titles"], 1)
        self.assertEqual(result["identity_conflicts"], 0)
        self.assertEqual(result["premiere_candidates"], 1)
        self.assertEqual(result["matched_premiere_candidates"], 1)
        self.assertEqual(result["unmatched_premiere_candidates"], 0)
        sample = result["unmatched_sample"]
        self.assertEqual(sample[0]["title"], "Unmatched Show")

    def test_contradictory_external_ids_are_reported_not_merged(self) -> None:
        payload = calendar_payload()
        metadata = payload["metadata"]
        self.assertIsInstance(metadata, dict)
        known = metadata["101"]
        self.assertIsInstance(known, dict)
        known["ids"] = {"simkl_id": 101, "tmdb": "501", "tvdb": "777"}
        with connect_ro(self.db_path) as connection:
            result = compare_with_catalog(payload, connection)
        self.assertEqual(result["matched_catalog_titles"], 0)
        self.assertEqual(result["identity_conflicts"], 1)

    def test_probe_makes_two_requests_and_keeps_database_read_only(self) -> None:
        class FakeHTTP(HTTPClient):
            def __init__(self) -> None:
                super().__init__("test", min_delay_seconds=0)
                self.calls: list[tuple[str, object]] = []

            def request_json(self, url: str, **kwargs: object) -> object:
                self.calls.append((url, kwargs.get("params")))
                return calendar_payload()

        before = self.db_path.read_bytes()
        http = FakeHTTP()
        result = SimklCalendarProbe("private-test-id", http).run(
            self.db_path, SIMKL_CATALOGS
        )
        after = self.db_path.read_bytes()
        self.assertEqual(result["mode"], "private_non_publishing_probe")
        self.assertEqual(result["requests_made"], 2)
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(before, after)
        self.assertNotIn("private-test-id", str(result))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
import sqlite3
import tempfile
import unittest

from gmd.collector.http import HTTPClient
from gmd.collector.pipeline import CollectorPipeline
from gmd.collector.tmdb import normalize_tmdb
from gmd.collector.tvdb import TVDBCollector, normalize_tvdb
from gmd.config import load_settings
from gmd.db import (
    backup_database,
    connect_rw,
    initialize_database,
    validate_database,
)
from gmd.models import EventObservation, ExternalID, NormalizedTitle
from gmd.reconcile import CatalogWriter
from gmd.query import CatalogQueries


class ReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "catalog.sqlite3"
        initialize_database(self.db_path, site_name="Test Catalog")
        self.connection = connect_rw(self.db_path)
        self.writer = CatalogWriter(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_hard_remote_id_merges_and_preserves_date_evidence(self) -> None:
        self.writer.ingest(
            normalize_tmdb(
                {
                    "id": 1001,
                    "name": "Signal House",
                    "original_name": "Signal House",
                    "first_air_date": "2026-08-10",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "overview": "A test series.",
                    "genre_ids": [18],
                }
            )
        )
        tvdb_id = self.writer.ingest(
            normalize_tvdb(
                {
                    "id": 2002,
                    "name": "Signal House",
                    "slug": "signal-house",
                    "firstAired": "2026-08-11",
                    "originalCountry": "usa",
                    "originalLanguage": "eng",
                    "aliases": [],
                    "genres": [{"name": "Drama"}],
                    "remoteIds": [
                        {"sourceName": "TheMovieDB.com", "id": "1001"},
                        {"sourceName": "IMDB", "id": "tt0001001"},
                    ],
                }
            )
        )
        self.connection.commit()

        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM titles").fetchone()[0], 1
        )
        event = self.connection.execute(
            "SELECT event_date, date_conflict FROM events"
        ).fetchone()
        self.assertEqual(event["event_date"], "2026-08-11")
        self.assertEqual(event["date_conflict"], 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM event_evidence"
            ).fetchone()[0],
            2,
        )
        keys = {
            row["key"]
            for row in self.connection.execute("SELECT key FROM identity_keys")
        }
        self.assertTrue({"tmdb:1001", "tvdb:2002"} <= keys)
        self.assertNotIn("imdb:tt0001001", keys)

        public = CatalogQueries(self.db_path).title(tvdb_id)
        self.assertIsNotNone(public)
        assessment = public["date_assessment"]
        self.assertEqual(assessment["status"], "disputed")
        self.assertEqual(assessment["selected_date"], "2026-08-11")
        self.assertEqual(assessment["distinct_date_count"], 2)
        self.assertEqual(assessment["selection_method"], "weighted_provider_consensus")
        reports = {row["source"]: row for row in public["evidence"]}
        self.assertTrue(reports["tvdb"]["supports_selected_date"])
        self.assertEqual(reports["tmdb"]["difference_days"], -1)
        self.assertEqual(reports["tmdb"]["date_meaning"], "original_series_premiere")

    def test_bad_remote_provider_id_does_not_poison_identity(self) -> None:
        self.writer.ingest(
            normalize_tmdb(
                {
                    "id": 1001,
                    "name": "Unrelated Wilderness Show",
                    "original_name": "Unrelated Wilderness Show",
                    "first_air_date": "2025-01-01",
                    "origin_country": ["GB"],
                    "original_language": "en",
                }
            )
        )
        correct_id = self.writer.ingest(
            normalize_tmdb(
                {
                    "id": 1002,
                    "name": "State of Play",
                    "original_name": "State of Play",
                    "first_air_date": "2026-08-10",
                    "origin_country": ["US"],
                    "original_language": "en",
                }
            )
        )
        tvdb_id = self.writer.ingest(
            normalize_tvdb(
                {
                    "id": 2002,
                    "name": "State of Play",
                    "slug": "state-of-play",
                    "firstAired": "2026-08-10",
                    "originalCountry": "usa",
                    "originalLanguage": "eng",
                    "aliases": [],
                    "remoteIds": [
                        {"sourceName": "TheMovieDB.com", "id": "1001"},
                    ],
                }
            )
        )
        self.connection.commit()

        self.assertEqual(tvdb_id, correct_id)
        wrong_owner = self.connection.execute(
            "SELECT title_id FROM identity_keys WHERE key = 'tmdb:1001'"
        ).fetchone()["title_id"]
        self.assertNotEqual(wrong_owner, correct_id)
        correct_keys = {
            row["key"]
            for row in self.connection.execute(
                "SELECT key FROM identity_keys WHERE title_id = ?",
                (correct_id,),
            )
        }
        self.assertNotIn("tmdb:1001", correct_keys)
        self.assertIn("tvdb:2002", correct_keys)
        canonical = self.connection.execute(
            "SELECT canonical_title FROM titles WHERE id = ?",
            (correct_id,),
        ).fetchone()["canonical_title"]
        self.assertEqual(canonical, "State of Play")

    def test_unverified_cross_provider_id_is_not_reserved(self) -> None:
        title_id = self.writer.ingest(
            normalize_tvdb(
                {
                    "id": 3003,
                    "name": "Provider Only",
                    "slug": "provider-only",
                    "firstAired": "2026-08-06",
                    "originalCountry": "fra",
                    "originalLanguage": "fra",
                    "aliases": [],
                    "remoteIds": [
                        {"sourceName": "TheMovieDB.com", "id": "999999"},
                    ],
                }
            )
        )
        self.connection.commit()
        keys = {
            row["key"]
            for row in self.connection.execute(
                "SELECT key FROM identity_keys WHERE title_id = ?",
                (title_id,),
            )
        }
        self.assertEqual(keys, {"tvdb:3003"})

    def test_similar_titles_are_not_fuzzy_merged(self) -> None:
        for record in (
            {
                "id": 1,
                "name": "The House",
                "original_name": "The House",
                "first_air_date": "2026-08-01",
                "origin_country": ["GB"],
                "original_language": "en",
            },
            {
                "id": 2,
                "name": "House",
                "original_name": "House",
                "first_air_date": "2026-08-01",
                "origin_country": ["GB"],
                "original_language": "en",
            },
        ):
            self.writer.ingest(normalize_tmdb(record))
        self.connection.commit()
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM titles").fetchone()[0], 2
        )

    def test_two_existing_provider_ids_can_corroborate_simkl_identity(self) -> None:
        canonical_id = self.writer.ingest(
            normalize_tmdb(
                {
                    "id": 4001,
                    "name": "Corroborated Show",
                    "first_air_date": "2020-01-01",
                    "origin_country": ["US"],
                }
            )
        )
        self.writer.ingest(
            normalize_tvdb(
                {
                    "id": 5001,
                    "name": "Corroborated Show",
                    "firstAired": "2020-01-01",
                    "originalCountry": "usa",
                    "remoteIds": [
                        {"sourceName": "TheMovieDB.com", "id": "4001"},
                    ],
                }
            )
        )
        simkl = NormalizedTitle(
            source="simkl",
            source_id="6001",
            title="Corroborated Show",
            format="TV Series",
            external_ids=[
                ExternalID("simkl", "6001", "https://simkl.com/tv/6001/show"),
                ExternalID("tmdb", "4001"),
                ExternalID("tvdb", "5001"),
            ],
            events=[
                EventObservation(
                    event_type="season_premiere",
                    date="2026-09-01",
                    source_record_id="6001:season_premiere:2:1",
                    season_number=2,
                    episode_number=1,
                )
            ],
        )
        simkl_id = self.writer.ingest(simkl)
        self.connection.commit()
        self.assertEqual(simkl_id, canonical_id)
        owner = self.connection.execute(
            "SELECT title_id FROM identity_keys WHERE key = 'simkl:6001'"
        ).fetchone()["title_id"]
        self.assertEqual(owner, canonical_id)

    def test_tvdb_relative_artwork_is_made_absolute(self) -> None:
        normalized = normalize_tvdb(
            {
                "id": 4444,
                "name": "Artwork Test",
                "slug": "artwork-test",
                "firstAired": "2026-08-04",
                "originalCountry": "gbr",
                "originalLanguage": "eng",
                "image": "/banners/v4/series/4444/posters/test.jpg",
            }
        )
        self.assertEqual(
            normalized.poster_url,
            "https://artworks.thetvdb.com/banners/v4/series/4444/posters/test.jpg",
        )

    def test_tvdb_basic_records_remain_eligible_for_extended_enrichment(self) -> None:
        basic = {
            "id": 5555,
            "name": "Bridge Test",
            "slug": "bridge-test",
            "firstAired": "2026-08-04",
            "lastUpdated": "2026-08-04 12:00:00",
            "originalCountry": "usa",
            "originalLanguage": "eng",
        }
        self.writer.ingest(normalize_tvdb(basic))
        self.connection.commit()
        settings = replace(
            load_settings(),
            data_dir=Path(self.temp.name),
            database_path=self.db_path,
        )
        pipeline = CollectorPipeline(settings)
        self.assertTrue(pipeline._tvdb_needs_extended(self.connection, basic))

        extended = {
            **basic,
            "remoteIds": [
                {"sourceName": "TheMovieDB.com", "id": "7777"},
            ],
        }
        self.writer.ingest(normalize_tvdb(extended))
        self.connection.commit()
        self.assertFalse(pipeline._tvdb_needs_extended(self.connection, basic))

    def test_tvdb_window_accepts_numeric_provider_ids(self) -> None:
        class FakeClient(HTTPClient):
            def __init__(self) -> None:
                super().__init__("test", min_delay_seconds=0)

            def request_json(self, *args: object, **kwargs: object) -> dict[str, object]:
                return {
                    "data": [
                        {
                            "id": 987654,
                            "name": "Numeric Identifier",
                            "firstAired": "2026-08-14",
                        }
                    ],
                    "links": {},
                }

        collector = TVDBCollector("configured", FakeClient())
        collector.token = "test-token"
        records = collector.series_in_window(
            date(2026, 8, 1),
            date(2026, 8, 31),
        )
        self.assertEqual([record["id"] for record in records], [987654])

        normalized = normalize_tvdb(
            {
                **records[0],
                "remoteIds": [{"sourceName": "TheMovieDB.com", "id": 123456}],
            }
        )
        self.assertIn(
            ("tmdb", "123456"),
            {(identity.source, identity.value) for identity in normalized.external_ids},
        )

    def test_database_validation_rejects_bad_dates(self) -> None:
        self.writer.ingest(
            normalize_tmdb(
                {
                    "id": 9,
                    "name": "Invalid Calendar Date",
                    "first_air_date": "2026-08-02",
                    "origin_country": ["DK"],
                }
            )
        )
        self.connection.commit()
        self.connection.execute(
            "UPDATE events SET event_date = '2026-02-30'"
        )
        self.connection.commit()
        self.connection.close()
        self.connection = sqlite3.connect(":memory:")
        with self.assertRaisesRegex(RuntimeError, "malformed event dates"):
            validate_database(self.db_path)

    def test_database_validation_rejects_events_without_evidence(self) -> None:
        self.writer.ingest(
            normalize_tmdb(
                {
                    "id": 11,
                    "name": "Evidence Required",
                    "first_air_date": "2026-08-05",
                    "origin_country": ["DK"],
                }
            )
        )
        self.connection.commit()
        self.connection.execute("DELETE FROM event_evidence")
        self.connection.commit()
        self.connection.close()
        self.connection = sqlite3.connect(":memory:")
        with self.assertRaisesRegex(RuntimeError, "events without evidence"):
            validate_database(self.db_path)

    def test_backup_is_closed_and_validated(self) -> None:
        self.writer.ingest(
            normalize_tmdb(
                {
                    "id": 10,
                    "name": "Backup Test",
                    "first_air_date": "2026-08-03",
                    "origin_country": ["US"],
                }
            )
        )
        self.connection.commit()
        destination = Path(self.temp.name) / "backups" / "catalog.sqlite3"
        backup_database(self.db_path, destination)
        self.assertEqual(validate_database(destination)["titles"], 1)
        self.assertFalse(Path(str(destination) + "-wal").exists())
        self.assertFalse(Path(str(destination) + "-shm").exists())


if __name__ == "__main__":
    unittest.main()

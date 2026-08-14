"""Bundled starter-catalog import.

The production installer ships a compact normalized SQLite snapshot rather
than redistributing the raw metadata-provider exports. JSON imports remain
supported for local development and reproducibility.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sqlite3
from typing import Any

from gmd.collector.tmdb import normalize_tmdb
from gmd.collector.tvdb import normalize_tvdb
from gmd.collector.tvmaze import normalize_tvmaze
from gmd.reconcile import CatalogWriter

LOGGER = logging.getLogger(__name__)

# Parent tables must be copied before children because foreign keys are active.
_SEED_TABLES: tuple[str, ...] = (
    "titles",
    "identity_keys",
    "aliases",
    "countries",
    "genres",
    "networks",
    "quality_flags",
    "events",
    "event_evidence",
    "source_records",
)


def import_seed(
    connection: sqlite3.Connection,
    seed_dir: Path,
) -> dict[str, int]:
    """Import the starter catalog into an initialized writable database."""

    sqlite_path = seed_dir / "catalog.sqlite3"
    if sqlite_path.exists():
        return _import_sqlite(connection, sqlite_path)
    return _import_json(connection, seed_dir)


def _import_sqlite(
    connection: sqlite3.Connection,
    sqlite_path: Path,
) -> dict[str, int]:
    uri = f"file:{sqlite_path.resolve()}?mode=ro"
    connection.execute("ATTACH DATABASE ? AS seeddb", (uri,))
    try:
        for table in _SEED_TABLES:
            connection.execute(
                f"INSERT OR IGNORE INTO main.{table} SELECT * FROM seeddb.{table}"
            )
        connection.commit()

        metrics = {
            "titles": int(
                connection.execute("SELECT COUNT(*) FROM seeddb.titles").fetchone()[0]
            ),
            "events": int(
                connection.execute("SELECT COUNT(*) FROM seeddb.events").fetchone()[0]
            ),
            "evidence": int(
                connection.execute(
                    "SELECT COUNT(*) FROM seeddb.event_evidence"
                ).fetchone()[0]
            ),
            "source_records": int(
                connection.execute(
                    "SELECT COUNT(*) FROM seeddb.source_records"
                ).fetchone()[0]
            ),
            "errors": 0,
        }
    finally:
        # DETACH must happen outside an active write transaction.
        connection.commit()
        connection.execute("DETACH DATABASE seeddb")
    return metrics


def _import_json(
    connection: sqlite3.Connection,
    seed_dir: Path,
) -> dict[str, int]:
    writer = CatalogWriter(connection)
    metrics = {"tmdb": 0, "tvdb": 0, "tvmaze": 0, "errors": 0}

    sources = (
        ("tmdb", seed_dir / "tmdb_aug_1_13_2026.json", normalize_tmdb),
        ("tvdb", seed_dir / "tvdb_aug_1_13_2026_extended.json", normalize_tvdb),
        ("tvmaze", seed_dir / "tvmaze_aug_1_13_2026.json", normalize_tvmaze),
    )
    for source, path, normalizer in sources:
        if not path.exists():
            continue
        for record in _load_array(path):
            try:
                writer.ingest(normalizer(record))
                metrics[source] += 1
            except Exception:
                metrics["errors"] += 1
                LOGGER.exception(
                    "failed to import seed record",
                    extra={
                        "structured": {
                            "source": source,
                            "record_id": record.get("id"),
                        }
                    },
                )

    connection.commit()
    return metrics


def _load_array(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"seed file must contain a JSON array: {path}")
    return [item for item in data if isinstance(item, dict)]

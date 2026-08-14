#!/usr/bin/env python3
"""Build a compact normalized starter catalog from local provider exports.

Raw exports are inputs only and are never copied into the project or release.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Callable

from gmd.collector.tmdb import normalize_tmdb
from gmd.collector.tvdb import normalize_tvdb
from gmd.collector.tvmaze import normalize_tvmaze
from gmd.db import (
    connect_rw,
    initialize_database,
    publish_database,
    set_meta,
    utcnow,
)
from gmd.models import NormalizedTitle
from gmd.reconcile import CatalogWriter

Normalizer = Callable[[dict[str, object]], NormalizedTitle]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--tmdb", type=Path)
    result.add_argument("--tvmaze", type=Path)
    result.add_argument("--tvdb", type=Path)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--site-name", default="Global Media Discovery")
    return result


def load_array(path: Path) -> list[dict[str, object]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"expected JSON array: {path}")
    return [item for item in value if isinstance(item, dict)]


def main() -> int:
    args = parser().parse_args()
    sources: list[tuple[str, Path, Normalizer]] = []
    if args.tmdb:
        sources.append(("tmdb", args.tmdb, normalize_tmdb))
    if args.tvmaze:
        sources.append(("tvmaze", args.tvmaze, normalize_tvmaze))
    if args.tvdb:
        sources.append(("tvdb", args.tvdb, normalize_tvdb))
    if not sources:
        raise SystemExit("provide at least one of --tmdb, --tvmaze, or --tvdb")

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    metrics = {source: 0 for source, _, _ in sources}
    metrics["errors"] = 0

    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        staging = Path(temp) / "catalog.sqlite3"
        initialize_database(staging, site_name=args.site_name)
        with connect_rw(staging) as connection:
            writer = CatalogWriter(connection)
            for source, path, normalizer in sources:
                for record in load_array(path):
                    try:
                        writer.ingest(normalizer(record))
                        metrics[source] += 1
                    except Exception as error:
                        metrics["errors"] += 1
                        print(
                            f"ERROR {source}:{record.get('id', '?')}: {error}",
                            flush=True,
                        )
            set_meta(connection, "site_name", args.site_name)
            set_meta(connection, "bootstrap_at", utcnow())
            set_meta(connection, "seed_metrics", json.dumps(metrics, sort_keys=True))
            connection.commit()
        validation = publish_database(staging, output)

    connection = sqlite3.connect(output)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("VACUUM")
        connection.execute("PRAGMA optimize")
        connection.commit()
    finally:
        connection.close()
    for suffix in ("-wal", "-shm"):
        Path(str(output) + suffix).unlink(missing_ok=True)
    os.chmod(output, 0o644)

    print(json.dumps({"metrics": metrics, "validation": validation}, indent=2))
    return 0 if metrics["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

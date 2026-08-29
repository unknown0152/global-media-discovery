"""Command-line entrypoints for deployment and operations."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from wsgiref.simple_server import make_server

from gmd import __version__
from gmd.api import ReadOnlyAPI
from gmd.collector.pipeline import CollectorPipeline
from gmd.collector.http import HTTPClient
from gmd.collector.simkl import SIMKL_CATALOGS, SimklCalendarProbe
from gmd.config import load_settings
from gmd.db import (
    backup_database,
    prune_backups,
    restore_database,
    validate_database,
)
from gmd.log import configure_logging
from gmd.query import CatalogQueries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gmd",
        description="Global Media Discovery read-only catalog",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    bootstrap = sub.add_parser("bootstrap", help="create the catalog from bundled seed data")
    bootstrap.add_argument(
        "--with-update",
        action="store_true",
        help="run live source collection after publishing the seed",
    )

    sub.add_parser("update", help="run one live collection cycle")
    sub.add_parser("collector-daemon", help="run scheduled collection forever")
    sub.add_parser("validate", help="validate the live SQLite catalog")
    sub.add_parser("stats", help="print catalog metadata as JSON")

    backup = sub.add_parser("backup", help="create a consistent SQLite backup")
    backup.add_argument("destination", nargs="?")

    restore = sub.add_parser("restore", help="atomically restore a validated backup")
    restore.add_argument("source")

    sub.add_parser("collector-health", help=argparse.SUPPRESS)

    simkl = sub.add_parser(
        "simkl-probe",
        help="privately assess Simkl Calendar v2 without publishing data",
    )
    simkl.add_argument(
        "--catalog",
        choices=("both", *SIMKL_CATALOGS),
        default="both",
        help="calendar catalog to assess (default: both)",
    )

    dev = sub.add_parser("api-dev", help="run a development WSGI server")
    dev.add_argument("--host", default="127.0.0.1")
    dev.add_argument("--port", type=int, default=8080)

    return parser


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    args = build_parser().parse_args(argv)

    if args.command == "bootstrap":
        pipeline = CollectorPipeline(settings)
        result = pipeline.bootstrap(seed_only=True)
        if args.with_update:
            result["update"] = pipeline.update()
        _print(result)
        return 0

    if args.command == "update":
        _print(CollectorPipeline(settings).update())
        return 0

    if args.command == "collector-daemon":
        CollectorPipeline(settings).daemon()
        return 0

    if args.command == "validate":
        _print(validate_database(settings.database_path))
        return 0

    if args.command == "stats":
        _print(CatalogQueries(settings.database_path).meta())
        return 0

    if args.command == "backup":
        destination = (
            Path(args.destination)
            if args.destination
            else settings.data_dir
            / "backups"
            / f"catalog-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
        )
        created = backup_database(settings.database_path, destination)
        prune_backups(settings.data_dir / "backups", keep=settings.backup_retention)
        _print({"backup": str(created)})
        return 0

    if args.command == "restore":
        source = Path(args.source).resolve()
        backup_root = (settings.data_dir / "backups").resolve()
        if backup_root not in source.parents:
            raise SystemExit("restore source must be inside the catalog backup directory")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safety = backup_root / f"catalog-pre-restore-{stamp}.sqlite3"
        backup_database(settings.database_path, safety)
        result = restore_database(source, settings.database_path)
        prune_backups(backup_root, keep=settings.backup_retention)
        _print({"status": "restored", "safety_backup": str(safety), **result})
        return 0

    if args.command == "collector-health":
        validation = validate_database(settings.database_path)
        if not validation.get("titles"):
            return 1
        return 0

    if args.command == "simkl-probe":
        if not settings.simkl_client_id:
            raise SystemExit(
                "Simkl Client ID is not configured; the Client Secret is not required"
            )
        catalogs = SIMKL_CATALOGS if args.catalog == "both" else (args.catalog,)
        client = HTTPClient(
            f"global-media-discovery/{__version__}",
            timeout=settings.request_timeout_seconds,
            min_delay_seconds=settings.source_delay_ms / 1000,
        )
        result = SimklCalendarProbe(settings.simkl_client_id, client).run(
            settings.database_path,
            catalogs,
        )
        _print(result)
        return 0

    if args.command == "api-dev":
        api = ReadOnlyAPI(settings)
        with make_server(args.host, args.port, api) as server:
            print(f"Development API listening on http://{args.host}:{args.port}")
            server.serve_forever()
        return 0

    return 2


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    sys.exit(main())

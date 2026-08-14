"""SQLite lifecycle and atomic publication."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import TracebackType
from typing import Iterator

SCHEMA_VERSION = 1


class ManagedConnection(sqlite3.Connection):
    """SQLite connection that also closes when used as a context manager.

    The standard sqlite3 context manager commits or rolls back but deliberately
    leaves the file descriptor open. Atomic catalog replacement requires every
    staging connection to be closed first, so the project uses this small
    subclass consistently.
    """

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def schema_text() -> str:
    return Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


def connect_rw(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=60.0, factory=ManagedConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 60000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA temp_store = MEMORY")
    return connection


def connect_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0, factory=ManagedConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_database(path: Path, *, site_name: str) -> None:
    with connect_rw(path) as connection:
        connection.executescript(schema_text())
        now = utcnow()
        values = {
            "schema_version": str(SCHEMA_VERSION),
            "site_name": site_name,
            "created_at": now,
            "updated_at": now,
            "catalog_version": "0",
        }
        connection.executemany(
            """
            INSERT INTO meta(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            values.items(),
        )
        connection.commit()


def set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO meta(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def get_meta(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def validate_database(path: Path) -> dict[str, int | str]:
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"database is missing or empty: {path}")

    with connect_ro(path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")

        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise RuntimeError(f"SQLite foreign_key_check found {len(foreign_keys)} errors")

        schema_version = get_meta(connection, "schema_version")
        if schema_version != str(SCHEMA_VERSION):
            raise RuntimeError(
                f"unsupported schema version {schema_version!r}; expected {SCHEMA_VERSION}"
            )

        counts = {
            "titles": connection.execute("SELECT COUNT(*) FROM titles").fetchone()[0],
            "events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "evidence": connection.execute(
                "SELECT COUNT(*) FROM event_evidence"
            ).fetchone()[0],
            "sources": connection.execute(
                "SELECT COUNT(DISTINCT source) FROM source_records"
            ).fetchone()[0],
        }

        bad_dates = connection.execute(
            """
            SELECT COUNT(*) FROM events
            WHERE event_date NOT GLOB
                  '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
               OR date(event_date) IS NULL
               OR date(event_date) != event_date
            """
        ).fetchone()[0]
        if bad_dates:
            raise RuntimeError(f"catalog contains {bad_dates} malformed event dates")

        bad_evidence_dates = connection.execute(
            """
            SELECT COUNT(*) FROM event_evidence
            WHERE reported_date NOT GLOB
                  '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
               OR date(reported_date) IS NULL
               OR date(reported_date) != reported_date
            """
        ).fetchone()[0]
        if bad_evidence_dates:
            raise RuntimeError(
                f"catalog contains {bad_evidence_dates} malformed evidence dates"
            )

        bad_required = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM titles
               WHERE trim(canonical_title) = '') +
              (SELECT COUNT(*) FROM events
               WHERE trim(event_type) = '' OR trim(event_date) = '') +
              (SELECT COUNT(*) FROM event_evidence
               WHERE trim(source) = '' OR trim(source_record_id) = '') +
              (SELECT COUNT(*) FROM source_records
               WHERE trim(source) = '' OR trim(external_id) = '')
            """
        ).fetchone()[0]
        if bad_required:
            raise RuntimeError(f"catalog contains {bad_required} blank required fields")

        missing_evidence = connection.execute(
            """
            SELECT COUNT(*)
            FROM events e
            WHERE NOT EXISTS (
                SELECT 1 FROM event_evidence ee WHERE ee.event_id = e.id
            )
            """
        ).fetchone()[0]
        if missing_evidence:
            raise RuntimeError(f"catalog contains {missing_evidence} events without evidence")

        if not counts["titles"] or not counts["events"] or not counts["evidence"]:
            raise RuntimeError("catalog must contain titles, events, and evidence")

        return {
            "integrity": "ok",
            "schema_version": schema_version,
            **counts,
        }


def checkpoint_and_compact(connection: sqlite3.Connection) -> None:
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("PRAGMA optimize")
    connection.commit()


@contextmanager
def staging_database(live_path: Path, *, site_name: str) -> Iterator[Path]:
    """Yield a writable staging database in the live directory.

    The caller validates and publishes it with :func:`publish_database`.
    """

    live_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging_name = tempfile.mkstemp(
        prefix=".catalog-next-", suffix=".sqlite3", dir=live_path.parent
    )
    os.close(descriptor)
    staging_path = Path(staging_name)

    try:
        if live_path.exists() and live_path.stat().st_size:
            shutil.copy2(live_path, staging_path)
        else:
            initialize_database(staging_path, site_name=site_name)
        yield staging_path
    finally:
        for candidate in (
            staging_path,
            Path(str(staging_path) + "-wal"),
            Path(str(staging_path) + "-shm"),
        ):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


def publish_database(staging_path: Path, live_path: Path) -> dict[str, int | str]:
    with connect_rw(staging_path) as connection:
        # Apply additive schema updates to the staging copy. The live catalog is
        # never migrated in place and remains available throughout collection.
        connection.executescript(schema_text())
        old_version = int(get_meta(connection, "catalog_version", "0") or "0")
        set_meta(connection, "catalog_version", str(old_version + 1))
        set_meta(connection, "updated_at", utcnow())
        checkpoint_and_compact(connection)
        # The API bind-mounts /data read-only.  Leaving the published database
        # in WAL mode makes SQLite try to create shared-memory sidecars there,
        # so checkpoint and switch the completed staging file back to a
        # self-contained rollback journal before its atomic publication.
        connection.execute("PRAGMA journal_mode = DELETE")

    result = validate_database(staging_path)

    # Ensure file contents and directory entry are durable before replacement.
    with staging_path.open("rb") as handle:
        os.fsync(handle.fileno())

    os.replace(staging_path, live_path)

    directory_fd = os.open(live_path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

    return result


def database_stamp(path: Path) -> str:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return "missing"
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


def backup_database(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    with connect_ro(source) as src, sqlite3.connect(
        destination, factory=ManagedConnection
    ) as dst:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode = DELETE")
        dst.execute("PRAGMA optimize")
        dst.commit()
    validate_database(destination)
    destination.chmod(0o600)
    return destination


def prune_backups(directory: Path, *, keep: int) -> list[Path]:
    """Remove only older GMD catalog backups, retaining at least one."""

    candidates = sorted(
        directory.glob("catalog-*.sqlite3"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    removed: list[Path] = []
    for path in candidates[max(1, keep) :]:
        path.unlink()
        removed.append(path)
    return removed


def restore_database(source: Path, destination: Path) -> dict[str, int | str]:
    """Validate and atomically restore a catalog without exposing a partial file."""

    validate_database(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".catalog-restore-", suffix=".sqlite3", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        temporary.chmod(0o600)
        result = validate_database(temporary)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return result
    finally:
        temporary.unlink(missing_ok=True)


def run_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

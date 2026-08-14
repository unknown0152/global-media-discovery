"""Collector orchestration and atomic catalog publication."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator

from gmd.collector.http import HTTPClient
from gmd.collector.seed import import_seed
from gmd.collector.tmdb import TMDBCollector, normalize_tmdb
from gmd.collector.tvdb import TVDBCollector, normalize_tvdb
from gmd.collector.tvmaze import TVMazeCollector, normalize_tvmaze
from gmd.config import Settings
from gmd.db import (
    backup_database,
    connect_ro,
    connect_rw,
    get_meta,
    initialize_database,
    publish_database,
    prune_backups,
    run_json,
    set_meta,
    staging_database,
    utcnow,
    validate_database,
)
from gmd.normalize import clean_text
from gmd.reconcile import CatalogWriter

LOGGER = logging.getLogger(__name__)


class CollectorPipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.status_path = settings.data_dir / "collector-status.json"
        self.lock_path = settings.data_dir / ".collector.lock"

    def bootstrap(self, *, seed_only: bool = False) -> dict[str, Any]:
        with self._lock():
            existing: dict[str, Any] | None = None
            if self.settings.database_path.exists():
                try:
                    existing = {
                        "status": "exists",
                        **validate_database(self.settings.database_path),
                    }
                except Exception:
                    LOGGER.exception("existing database is invalid; quarantining it")
                    self._quarantine_invalid_database()

            if existing is not None:
                result = existing
            else:
                with staging_database(
                    self.settings.database_path, site_name=self.settings.site_name
                ) as staging:
                    with connect_rw(staging) as connection:
                        metrics = (
                            import_seed(connection, self.settings.seed_dir)
                            if self.settings.enable_seed
                            else {}
                        )
                        set_meta(connection, "site_name", self.settings.site_name)
                        set_meta(connection, "bootstrap_at", utcnow())
                        set_meta(connection, "seed_metrics", run_json(metrics))
                        connection.commit()
                    validation = publish_database(staging, self.settings.database_path)

                result = {"status": "bootstrapped", "seed": metrics, **validation}
                self._write_status(result)

        # Run live collection after releasing the bootstrap lock. The CLI normally
        # requests seed-only bootstrap and starts the long-running collector as a
        # separate service, but this path remains safe for direct use.
        if not seed_only:
            result["update"] = self.update()
        return result

    def update(self) -> dict[str, Any]:
        try:
            return self._update()
        except LockBusy:
            raise
        except Exception as error:
            failed = {
                "status": "error",
                "finished_at": utcnow(),
                "error": type(error).__name__,
            }
            self._write_status(failed)
            raise

    def _update(self) -> dict[str, Any]:
        with self._lock():
            if not self.settings.database_path.exists():
                initialize_database(
                    self.settings.database_path, site_name=self.settings.site_name
                )

            started_at = utcnow()
            result: dict[str, Any] = {
                "status": "running",
                "started_at": started_at,
                "source_results": {},
                "metrics": {},
            }
            backup_dir = self.settings.data_dir / "backups"
            backup_name = (
                "catalog-pre-update-"
                f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
            )
            backup_database(self.settings.database_path, backup_dir / backup_name)
            prune_backups(backup_dir, keep=self.settings.backup_retention)
            result["backup_created"] = True
            self._write_status(result)

            with staging_database(
                self.settings.database_path, site_name=self.settings.site_name
            ) as staging:
                with connect_rw(staging) as connection:
                    run_id = self._start_run(connection, started_at)
                    writer = CatalogWriter(connection)
                    start = date.today() - timedelta(days=self.settings.past_days)
                    end = date.today() + timedelta(days=self.settings.future_days)
                    successful_sources = 0

                    if self.settings.enable_seed and self._catalog_empty(connection):
                        seed_metrics = import_seed(connection, self.settings.seed_dir)
                        result["metrics"]["seed"] = seed_metrics

                    if self.settings.enable_tmdb and self.settings.tmdb_token:
                        try:
                            metrics = self._update_tmdb(connection, writer, start, end)
                            result["source_results"]["tmdb"] = "ok"
                            result["metrics"]["tmdb"] = metrics
                            successful_sources += 1
                            self._set_source_state(connection, "tmdb", "ok")
                        except Exception as error:
                            LOGGER.exception("TMDB update failed")
                            result["source_results"]["tmdb"] = f"error: {error}"
                            self._set_source_state(connection, "tmdb", "error", str(error))
                    else:
                        result["source_results"]["tmdb"] = "disabled or missing token"

                    if self.settings.enable_tvmaze:
                        try:
                            metrics = self._update_tvmaze(writer, start, end)
                            result["source_results"]["tvmaze"] = "ok"
                            result["metrics"]["tvmaze"] = metrics
                            successful_sources += 1
                            self._set_source_state(connection, "tvmaze", "ok")
                        except Exception as error:
                            LOGGER.exception("TVmaze update failed")
                            result["source_results"]["tvmaze"] = f"error: {error}"
                            self._set_source_state(connection, "tvmaze", "error", str(error))

                    if self.settings.enable_tvdb and self.settings.tvdb_key:
                        try:
                            if self._source_due(
                                connection,
                                "tvdb",
                                timedelta(days=self.settings.tvdb_full_scan_days),
                            ):
                                metrics = self._update_tvdb(
                                    connection, writer, start, end
                                )
                                result["source_results"]["tvdb"] = "ok"
                                result["metrics"]["tvdb"] = metrics
                                successful_sources += 1
                                self._set_source_state(connection, "tvdb", "ok")
                            else:
                                result["source_results"]["tvdb"] = "not due"
                        except Exception as error:
                            LOGGER.exception("TVDB update failed")
                            result["source_results"]["tvdb"] = f"error: {error}"
                            self._set_source_state(connection, "tvdb", "error", str(error))
                    else:
                        result["source_results"]["tvdb"] = "disabled or missing key"

                    counts = self._counts(connection)
                    result["metrics"]["catalog"] = counts
                    status = "ok" if successful_sources else "degraded"
                    finished_at = utcnow()
                    self._finish_run(
                        connection,
                        run_id,
                        finished_at=finished_at,
                        status=status,
                        sources=result["source_results"],
                        metrics=result["metrics"],
                    )
                    set_meta(connection, "last_collector_run", finished_at)
                    set_meta(connection, "last_collector_status", status)
                    connection.commit()

                if not successful_sources:
                    result.update(self._counts_from_live())
                    result["status"] = "degraded"
                    result["retained_live_catalog"] = True
                    result["finished_at"] = utcnow()
                    self._write_status(result)
                    return result

                validation = publish_database(staging, self.settings.database_path)

            result.update(validation)
            result["status"] = "ok" if successful_sources else "degraded"
            result["finished_at"] = utcnow()
            self._write_status(result)
            return result

    def daemon(self) -> None:
        if not self.settings.database_path.exists():
            self.bootstrap(seed_only=True)

        if self.settings.collector_run_on_start:
            remaining = self._seconds_until_due()
            if remaining <= 0:
                try:
                    self.update()
                except LockBusy:
                    LOGGER.info("collector run already active")
                except Exception:
                    LOGGER.exception("initial collector update failed")
            else:
                LOGGER.info(
                    "recent successful collection found; startup run deferred",
                    extra={"structured": {"seconds_until_due": remaining}},
                )

        while True:
            sleep_seconds = max(1, self._seconds_until_due())
            LOGGER.info(
                "collector sleeping",
                extra={"structured": {"seconds": sleep_seconds}},
            )
            time.sleep(sleep_seconds)
            try:
                self.update()
            except LockBusy:
                LOGGER.info("scheduled run skipped because another run is active")
            except Exception:
                LOGGER.exception("scheduled collector update failed")

    def _update_tmdb(
        self,
        connection: sqlite3.Connection,
        writer: CatalogWriter,
        start: date,
        end: date,
    ) -> dict[str, int]:
        client = self._client("tmdb")
        collector = TMDBCollector(self.settings.tmdb_token, client)
        records = collector.discover(start, end)
        records.sort(key=lambda item: self._date_distance(item.get("first_air_date")))

        enriched = 0
        errors = 0
        for record in records:
            details = None
            if (
                enriched < self.settings.tmdb_detail_limit
                and self._tmdb_needs_details(connection, record)
            ):
                try:
                    details = collector.details(int(record["id"]))
                    enriched += 1
                except Exception:
                    errors += 1
                    LOGGER.exception(
                        "TMDB details failed",
                        extra={"structured": {"tmdb_id": record.get("id")}},
                    )
            try:
                writer.ingest(normalize_tmdb(record, details=details))
            except Exception:
                errors += 1
                LOGGER.exception(
                    "TMDB ingest failed",
                    extra={"structured": {"tmdb_id": record.get("id")}},
                )

        connection.commit()
        return {
            "discovered": len(records),
            "enriched": enriched,
            "errors": errors,
        }

    def _update_tvmaze(
        self,
        writer: CatalogWriter,
        start: date,
        end: date,
    ) -> dict[str, int]:
        client = self._client("tvmaze", delay_ms=max(self.settings.source_delay_ms, 520))
        collector = TVMazeCollector(client)
        backfill_due = self._source_due(
            writer.connection,
            "tvmaze_backfill",
            timedelta(days=self.settings.tvmaze_backfill_interval_days),
        )
        episodes = collector.premieres(
            start,
            end,
            recent_days=self.settings.tvmaze_recent_days,
            backfill_days=(self.settings.tvmaze_backfill_days if backfill_due else 0),
        )

        ingested = 0
        fetched_shows = 0
        errors = 0
        for episode in episodes:
            try:
                show = ((episode.get("_embedded") or {}).get("show") or None)
                if not show:
                    show_id = self._tvmaze_show_id(episode)
                    if not show_id:
                        raise ValueError("TVmaze episode has no show reference")
                    show = collector.show(show_id)
                    fetched_shows += 1
                writer.ingest(normalize_tvmaze(episode, show_override=show))
                ingested += 1
            except Exception:
                errors += 1
                LOGGER.exception(
                    "TVmaze ingest failed",
                    extra={"structured": {"episode_id": episode.get("id")}},
                )
        writer.connection.commit()
        if backfill_due:
            self._set_source_state(writer.connection, "tvmaze_backfill", "ok")
            writer.connection.commit()
        return {
            "premieres": len(episodes),
            "ingested": ingested,
            "fetched_shows": fetched_shows,
            "errors": errors,
            "backfill_days": self.settings.tvmaze_backfill_days if backfill_due else 0,
        }

    def _update_tvdb(
        self,
        connection: sqlite3.Connection,
        writer: CatalogWriter,
        start: date,
        end: date,
    ) -> dict[str, int]:
        client = self._client("tvdb")
        collector = TVDBCollector(self.settings.tvdb_key, client)
        basic = collector.series_in_window(start, end)
        basic.sort(key=lambda item: self._date_distance(item.get("firstAired")))

        ingested = 0
        enriched = 0
        errors = 0
        extended_candidates = 0

        for record in basic:
            needs_extended = self._tvdb_needs_extended(connection, record)
            if needs_extended:
                extended_candidates += 1

            # Prefer ingesting the extended record first. It contains TMDB,
            # TVmaze, and IMDb IDs that prevent a temporary provider-only title
            # from being created before identity resolution has enough evidence.
            if needs_extended and enriched < self.settings.tvdb_extended_limit:
                try:
                    extended = collector.extended(record["id"])
                    writer.ingest(normalize_tvdb(extended))
                    ingested += 1
                    enriched += 1
                    continue
                except Exception:
                    errors += 1
                    LOGGER.exception(
                        "TVDB extended ingest failed; falling back to basic record",
                        extra={"structured": {"tvdb_id": record.get("id")}},
                    )

            try:
                writer.ingest(normalize_tvdb(record))
                ingested += 1
            except Exception:
                errors += 1
                LOGGER.exception(
                    "TVDB basic ingest failed",
                    extra={"structured": {"tvdb_id": record.get("id")}},
                )

        connection.commit()
        return {
            "window_records": len(basic),
            "ingested": ingested,
            "extended_candidates": extended_candidates,
            "enriched": enriched,
            "errors": errors,
        }

    def _client(self, name: str, *, delay_ms: int | None = None) -> HTTPClient:
        return HTTPClient(
            user_agent=(
                "GlobalMediaDiscovery/1.0 "
                f"({self.settings.public_url or 'self-hosted'}; {name})"
            ),
            timeout=self.settings.request_timeout_seconds,
            min_delay_seconds=(delay_ms or self.settings.source_delay_ms) / 1000,
        )

    def _tmdb_needs_details(
        self,
        connection: sqlite3.Connection,
        record: dict[str, Any],
    ) -> bool:
        row = connection.execute(
            """
            SELECT fetched_at FROM source_records
            WHERE source = 'tmdb' AND external_id = ?
            """,
            (str(record.get("id")),),
        ).fetchone()
        if not row:
            return True
        airdate = clean_text(record.get("first_air_date"))
        if airdate and airdate >= (date.today() - timedelta(days=30)).isoformat():
            return self._timestamp_old(str(row["fetched_at"]), timedelta(days=7))
        return False

    def _tvdb_needs_extended(
        self,
        connection: sqlite3.Connection,
        record: dict[str, Any],
    ) -> bool:
        row = connection.execute(
            """
            SELECT source_updated_at, payload_json FROM source_records
            WHERE source = 'tvdb' AND external_id = ?
            """,
            (str(record.get("id")),),
        ).fetchone()
        if not row:
            return True
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return True
        raw_keys = payload.get("raw_keys") if isinstance(payload, dict) else None
        if not isinstance(raw_keys, list) or "remoteIds" not in raw_keys:
            return True
        return clean_text(row["source_updated_at"]) != clean_text(
            record.get("lastUpdated")
        )

    @staticmethod
    def _timestamp_old(value: str, age: timedelta) -> bool:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        return datetime.now(timezone.utc) - parsed > age

    @staticmethod
    def _tvmaze_show_id(episode: dict[str, Any]) -> str | None:
        href = clean_text(((episode.get("_links") or {}).get("show") or {}).get("href"))
        return href.rstrip("/").split("/")[-1] if href else None

    @staticmethod
    def _date_distance(value: object) -> int:
        try:
            parsed = date.fromisoformat(clean_text(str(value))[:10])
        except ValueError:
            return 10**9
        return abs((parsed - date.today()).days)

    @staticmethod
    def _catalog_empty(connection: sqlite3.Connection) -> bool:
        return connection.execute("SELECT COUNT(*) FROM titles").fetchone()[0] == 0

    @staticmethod
    def _counts(connection: sqlite3.Connection) -> dict[str, int]:
        return {
            "titles": int(connection.execute("SELECT COUNT(*) FROM titles").fetchone()[0]),
            "events": int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
            "evidence": int(
                connection.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0]
            ),
        }

    def _counts_from_live(self) -> dict[str, int]:
        with connect_ro(self.settings.database_path) as connection:
            return self._counts(connection)

    def _seconds_until_due(self) -> int:
        interval = self.settings.update_interval_hours * 3600
        try:
            with connect_ro(self.settings.database_path) as connection:
                last_run = get_meta(connection, "last_collector_run")
            parsed = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - parsed).total_seconds()
            return max(0, int(interval - elapsed))
        except (OSError, sqlite3.Error, ValueError):
            return 0

    @staticmethod
    def _start_run(connection: sqlite3.Connection, started_at: str) -> int:
        cursor = connection.execute(
            """
            INSERT INTO runs(started_at, status, sources_json, metrics_json)
            VALUES (?, 'running', '{}', '{}')
            """,
            (started_at,),
        )
        connection.commit()
        return int(cursor.lastrowid)

    @staticmethod
    def _finish_run(
        connection: sqlite3.Connection,
        run_id: int,
        *,
        finished_at: str,
        status: str,
        sources: object,
        metrics: object,
        error: str | None = None,
    ) -> None:
        connection.execute(
            """
            UPDATE runs
            SET finished_at = ?, status = ?, sources_json = ?,
                metrics_json = ?, error = ?
            WHERE id = ?
            """,
            (
                finished_at,
                status,
                run_json(sources),
                run_json(metrics),
                error,
                run_id,
            ),
        )

    def _source_due(
        self,
        connection: sqlite3.Connection,
        source: str,
        interval: timedelta,
    ) -> bool:
        row = connection.execute(
            "SELECT last_success_at FROM collection_state WHERE source = ?",
            (source,),
        ).fetchone()
        if not row or not row["last_success_at"]:
            return True
        return self._timestamp_old(str(row["last_success_at"]), interval)

    @staticmethod
    def _set_source_state(
        connection: sqlite3.Connection,
        source: str,
        status: str,
        detail: str | None = None,
    ) -> None:
        now = utcnow()
        connection.execute(
            """
            INSERT INTO collection_state(
                source, last_success_at, last_attempt_at, status, detail
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                last_success_at = CASE
                    WHEN excluded.status = 'ok' THEN excluded.last_success_at
                    ELSE collection_state.last_success_at
                END,
                last_attempt_at = excluded.last_attempt_at,
                status = excluded.status,
                detail = excluded.detail
            """,
            (
                source,
                now if status == "ok" else None,
                now,
                status,
                detail,
            ),
        )

    def _quarantine_invalid_database(self) -> None:
        live = self.settings.database_path
        if not live.exists():
            return
        backups = self.settings.data_dir / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = backups / f"catalog-invalid-{stamp}.sqlite3"
        os.replace(live, destination)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(live) + suffix)
            try:
                sidecar.unlink()
            except FileNotFoundError:
                pass
        LOGGER.warning(
            "invalid catalog quarantined",
            extra={"structured": {"destination": str(destination)}},
        )

    def _write_status(self, value: object) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.status_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.status_path)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise LockBusy("another collector process is already running") from error
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


class LockBusy(RuntimeError):
    pass

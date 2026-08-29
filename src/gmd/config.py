"""Configuration loading.

Configuration is deliberately environment-based so the same image can run as
API or collector. Secrets may be supplied as Docker secret files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

DEFAULT_DATA_DIR: Final = Path("/data")
DEFAULT_SEED_DIR: Final = Path("/app/seed")


def _read_secret(name: str, file_name: str) -> str:
    direct = os.getenv(name, "").strip()
    if direct:
        return direct

    path_value = os.getenv(file_name, "").strip()
    if not path_value:
        return ""

    try:
        return Path(path_value).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _public_http_url(name: str) -> str:
    """Return a safe browser-facing HTTP(S) base URL or disable the setting."""
    raw = os.getenv(name, "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return raw


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_path: Path
    seed_dir: Path
    site_name: str
    public_url: str
    seerr_public_url: str
    api_prefix: str
    tmdb_token: str
    tvdb_key: str
    simkl_client_id: str
    simkl_rules_accepted: bool
    update_interval_hours: int
    past_days: int
    future_days: int
    tvdb_full_scan_days: int
    tvdb_extended_limit: int
    tmdb_detail_limit: int
    tvmaze_recent_days: int
    tvmaze_backfill_days: int
    tvmaze_backfill_interval_days: int
    request_timeout_seconds: int
    source_delay_ms: int
    log_level: str
    enable_seed: bool
    enable_tmdb: bool
    enable_tvdb: bool
    enable_tvmaze: bool
    enable_simkl: bool
    max_query_days: int
    max_page_size: int
    rate_limit_per_minute: int
    collector_run_on_start: bool
    backup_retention: int

    @property
    def today(self) -> date:
        return date.today()


def load_settings() -> Settings:
    data_dir = Path(os.getenv("GMD_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()
    database_path = Path(
        os.getenv("GMD_DATABASE_PATH", str(data_dir / "catalog.sqlite3"))
    ).expanduser()
    seed_dir = Path(os.getenv("GMD_SEED_DIR", str(DEFAULT_SEED_DIR))).expanduser()

    return Settings(
        data_dir=data_dir,
        database_path=database_path,
        seed_dir=seed_dir,
        site_name=os.getenv("GMD_SITE_NAME", "Global Media Discovery").strip()
        or "Global Media Discovery",
        public_url=os.getenv("GMD_PUBLIC_URL", "").strip(),
        seerr_public_url=_public_http_url("GMD_SEERR_PUBLIC_URL"),
        api_prefix="/api/v1",
        tmdb_token=_read_secret("TMDB_TOKEN", "TMDB_TOKEN_FILE"),
        tvdb_key=_read_secret("TVDB_KEY", "TVDB_KEY_FILE"),
        simkl_client_id=_read_secret("SIMKL_CLIENT_ID", "SIMKL_CLIENT_ID_FILE"),
        simkl_rules_accepted=_bool(
            "GMD_SIMKL_RULES_ACCEPTED",
            _bool("GMD_SIMKL_PERMISSION_CONFIRMED", False),
        ),
        update_interval_hours=_int(
            "GMD_UPDATE_INTERVAL_HOURS", 24, minimum=1, maximum=168
        ),
        past_days=_int("GMD_PAST_DAYS", 365, minimum=0, maximum=3650),
        future_days=_int("GMD_FUTURE_DAYS", 540, minimum=1, maximum=3650),
        tvdb_full_scan_days=_int(
            "GMD_TVDB_FULL_SCAN_DAYS", 7, minimum=1, maximum=90
        ),
        tvdb_extended_limit=_int(
            "GMD_TVDB_EXTENDED_LIMIT", 600, minimum=0, maximum=10000
        ),
        tmdb_detail_limit=_int(
            "GMD_TMDB_DETAIL_LIMIT", 1200, minimum=0, maximum=20000
        ),
        tvmaze_recent_days=_int(
            "GMD_TVMAZE_RECENT_DAYS", 30, minimum=0, maximum=365
        ),
        tvmaze_backfill_days=_int(
            "GMD_TVMAZE_BACKFILL_DAYS", 365, minimum=0, maximum=3650
        ),
        tvmaze_backfill_interval_days=_int(
            "GMD_TVMAZE_BACKFILL_INTERVAL_DAYS", 7, minimum=1, maximum=90
        ),
        request_timeout_seconds=_int(
            "GMD_REQUEST_TIMEOUT_SECONDS", 45, minimum=5, maximum=180
        ),
        source_delay_ms=_int("GMD_SOURCE_DELAY_MS", 220, minimum=0, maximum=5000),
        log_level=os.getenv("GMD_LOG_LEVEL", "INFO").upper(),
        enable_seed=_bool("GMD_ENABLE_SEED", True),
        enable_tmdb=_bool("GMD_ENABLE_TMDB", True),
        enable_tvdb=_bool("GMD_ENABLE_TVDB", True),
        enable_tvmaze=_bool("GMD_ENABLE_TVMAZE", True),
        enable_simkl=_bool("GMD_ENABLE_SIMKL", False),
        max_query_days=_int("GMD_MAX_QUERY_DAYS", 366, minimum=1, maximum=3650),
        max_page_size=_int("GMD_MAX_PAGE_SIZE", 200, minimum=20, maximum=1000),
        rate_limit_per_minute=_int(
            "GMD_RATE_LIMIT_PER_MINUTE", 180, minimum=10, maximum=10000
        ),
        collector_run_on_start=_bool("GMD_COLLECTOR_RUN_ON_START", True),
        backup_retention=_int("GMD_BACKUP_RETENTION", 14, minimum=1, maximum=365),
    )

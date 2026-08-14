PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '{}',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE TABLE IF NOT EXISTS titles (
    id TEXT PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    original_title TEXT,
    overview TEXT NOT NULL DEFAULT '',
    original_language TEXT,
    format TEXT NOT NULL DEFAULT 'Unknown',
    status TEXT,
    runtime_minutes INTEGER,
    poster_url TEXT,
    backdrop_url TEXT,
    first_air_date TEXT,
    date_conflict INTEGER NOT NULL DEFAULT 0 CHECK (date_conflict IN (0, 1)),
    confidence REAL NOT NULL DEFAULT 0.50 CHECK (confidence >= 0 AND confidence <= 1),
    title_rank INTEGER NOT NULL DEFAULT 0,
    overview_rank INTEGER NOT NULL DEFAULT 0,
    poster_rank INTEGER NOT NULL DEFAULT 0,
    format_rank INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identity_keys (
    key TEXT PRIMARY KEY,
    title_id TEXT NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    source_url TEXT
);

CREATE INDEX IF NOT EXISTS idx_identity_title ON identity_keys(title_id);
CREATE INDEX IF NOT EXISTS idx_identity_source_id ON identity_keys(source, external_id);

CREATE TABLE IF NOT EXISTS aliases (
    title_id TEXT NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    language TEXT,
    source TEXT NOT NULL,
    PRIMARY KEY (title_id, normalized_alias, source)
);

CREATE INDEX IF NOT EXISTS idx_alias_normalized ON aliases(normalized_alias);

CREATE TABLE IF NOT EXISTS countries (
    title_id TEXT NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    country_code TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (title_id, country_code, source)
);

CREATE INDEX IF NOT EXISTS idx_countries_code ON countries(country_code);
CREATE INDEX IF NOT EXISTS idx_countries_code_title ON countries(country_code, title_id);

CREATE TABLE IF NOT EXISTS genres (
    title_id TEXT NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    genre TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (title_id, genre, source)
);

CREATE INDEX IF NOT EXISTS idx_genres_name ON genres(genre);
CREATE INDEX IF NOT EXISTS idx_genres_name_title ON genres(genre, title_id);

CREATE TABLE IF NOT EXISTS networks (
    title_id TEXT NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    network_name TEXT NOT NULL,
    network_country TEXT,
    network_type TEXT,
    source TEXT NOT NULL,
    PRIMARY KEY (title_id, network_name, source)
);

CREATE INDEX IF NOT EXISTS idx_network_name ON networks(network_name);
CREATE INDEX IF NOT EXISTS idx_network_name_title ON networks(network_name, title_id);

CREATE TABLE IF NOT EXISTS quality_flags (
    title_id TEXT NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    flag TEXT NOT NULL,
    source TEXT NOT NULL,
    detail TEXT,
    PRIMARY KEY (title_id, flag, source)
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    title_id TEXT NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    event_date TEXT NOT NULL,
    season_number INTEGER NOT NULL DEFAULT -1,
    episode_number INTEGER NOT NULL DEFAULT -1,
    country_code TEXT NOT NULL DEFAULT '',
    network_name TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.50 CHECK (confidence >= 0 AND confidence <= 1),
    date_conflict INTEGER NOT NULL DEFAULT 0 CHECK (date_conflict IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (
        title_id,
        event_type,
        season_number,
        episode_number,
        country_code,
        network_name
    )
);

CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date);
CREATE INDEX IF NOT EXISTS idx_events_type_date ON events(event_type, event_date);
CREATE INDEX IF NOT EXISTS idx_events_title ON events(title_id);
CREATE INDEX IF NOT EXISTS idx_events_date_title ON events(event_date, title_id);
CREATE INDEX IF NOT EXISTS idx_events_date_conflict ON events(event_date, date_conflict);

CREATE TABLE IF NOT EXISTS event_evidence (
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    reported_date TEXT NOT NULL,
    source_url TEXT,
    observed_at TEXT NOT NULL,
    raw_hash TEXT,
    confidence REAL NOT NULL DEFAULT 0.70,
    PRIMARY KEY (event_id, source, source_record_id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_source ON event_evidence(source);
CREATE INDEX IF NOT EXISTS idx_evidence_reported_date ON event_evidence(reported_date);

CREATE TABLE IF NOT EXISTS source_records (
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title_id TEXT NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    fetched_at TEXT NOT NULL,
    source_updated_at TEXT,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_source_records_title ON source_records(title_id);

CREATE TABLE IF NOT EXISTS collection_state (
    source TEXT PRIMARY KEY,
    cursor TEXT,
    last_success_at TEXT,
    last_attempt_at TEXT,
    status TEXT NOT NULL DEFAULT 'never',
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_titles_canonical_title ON titles(canonical_title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_titles_language ON titles(original_language);
CREATE INDEX IF NOT EXISTS idx_titles_format ON titles(format);
CREATE INDEX IF NOT EXISTS idx_titles_first_air ON titles(first_air_date);

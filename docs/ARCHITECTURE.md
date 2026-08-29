# Architecture

## Goal

Global Media Discovery is not a recommendation engine. It is a worldwide
television event index whose primary question is **what premiered on a given
date?** The design optimizes for broad recall, source transparency, and a small
self-hosting footprint.

## Runtime components

### Caddy

Caddy is the only internet-facing container. It serves the static frontend,
adds security headers, compresses responses, proxies `/api/*` JSON and `/ui/*`
HTML fragments, and handles automatic HTTPS when a domain is configured.

### Browser interface

Tailwind CSS 4 is compiled during development and committed as a minified
static asset. HTMX 4 is pinned and served locally. HTMX requests only escaped
HTML fragments through GET endpoints; it cannot mutate the catalog. The JSON
API remains available for programmatic clients and calendar interactions.

An optional Seerr handoff builds a normal browser link from a configured
public Seerr base URL and a verified TMDB identity. Seerr—not GMD—handles
authentication, permissions, quotas, season selection, and request creation.
Titles without a verified TMDB key are never guessed or fuzzy-matched for this
purpose.

### Read-only API

The API is a dependency-light WSGI application behind Gunicorn. Every request
opens SQLite in `mode=ro` and enables `PRAGMA query_only`. Only `GET` and `HEAD`
are accepted. Inputs are length-bounded and all user values are bound SQL
parameters.

### Collector

The collector is the only application component with a writable catalog mount.
It performs source collection, normalization, identity reconciliation,
evidence retention, validation, and atomic publication. It sleeps between
runs; no task queue or external database is required.

## Data model

### Titles

A title stores the currently preferred display fields: canonical title,
original title, overview, original language, format, runtime, artwork, status,
and confidence.

### Identity keys

Every known external identifier is stored as an independent key, for example:

```text
tmdb:329274
tvdb:447580
tvmaze:93356
imdb:tt43673968
```

Hard identifiers are evidence, not unquestionable truth. Collisions are
quality-flagged rather than silently reassigned.

### Events

The initial product publishes `series_premiere` events. The schema already
supports season, episode, country, and network dimensions so later releases
can add:

```text
season_premiere
episode_release
streaming_added
streaming_removed
```

without replacing the core title model.

### Event evidence

Each source observation remains attached to the event:

```text
source       reported_date     source_record_id
TMDB         2026-08-13        309798
TheTVDB      2026-08-13        465292
TVmaze       2026-08-13        3684024
```

The UI can therefore show agreement and disagreement instead of hiding it.

## Identity resolution

Resolution is intentionally conservative:

1. Match any already-known hard external IDs.
2. If no ID matches, allow an exact normalized title or alias match within one
   day and require compatible origin countries when country evidence exists.
3. If more than one plausible candidate remains, do not merge.
4. Create a stable provider-scoped title when unresolved.
5. Flag external-ID collisions and provider-marked problematic entries.

No edit-distance or AI fuzzy match is allowed to merge records automatically.
A future review tool may suggest merges, but suggestions must remain separate
from automatic publication.

## Canonical field selection

Different providers are ranked per field rather than globally. For example,
TMDB is preferred for overviews and artwork, TheTVDB for format classification,
and TVmaze for scheduled premiere evidence. A source with a stronger field
rank may replace a weaker value while all aliases and evidence remain stored.

## Canonical date selection

Source observations are weighted and the date with the strongest agreement is
published. Ties prefer TVmaze, then TheTVDB, then TMDB. A conflict flag remains
set whenever more than one date exists, even after a canonical date is chosen.

## Atomic publication

```text
live catalog.sqlite3
        │ copy
        ▼
.catalog-next-XXXX.sqlite3
        │
        ├─ apply collection writes
        ├─ foreign_key_check
        ├─ integrity_check
        ├─ malformed-date check
        ├─ WAL checkpoint
        ├─ fsync file
        └─ atomic os.replace
                │
                ▼
        new live catalog.sqlite3
```

The API opens a new read-only connection per request, so it naturally observes
the replacement without a restart. A failed collection never modifies the
live catalog.

## Source schedules

- **TMDB:** date-window discovery every collector cycle; detail enrichment is
  capped and prioritized by distance from today.
- **TVmaze:** full future schedule plus recent daily web schedule checks every
  cycle.
- **TheTVDB:** complete paginated series scan on a configurable multi-day
  interval; extended records are fetched first for candidates needing identity
  enrichment.

The defaults favor a tiny VPS and can be adjusted in `.env`.

## Trust boundaries

```text
Internet
   │
   ▼
Caddy ────── static files
   │
   ▼
API container ── read-only DB mount

Browser ── optional verified-ID link ── Seerr authentication + request flow

Collector container ── writable DB mount + API-key file secrets ── providers
                    (separate egress-only Docker network)
```

The browser never receives source API credentials. The API and collector run
as non-root UID/GID `65532`, with dropped capabilities, read-only root
filesystems, no-new-privileges, resource limits, and log rotation.
GMD stores no Seerr API key and has no direct request-writing connection to
Seerr; its public API remains GET/HEAD-only.

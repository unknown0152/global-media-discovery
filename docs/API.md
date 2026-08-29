# Read-only API

Base path: `/api/v1`

Only `GET` and `HEAD` are accepted. Every response is JSON. The default rate
limit is 180 requests per minute per forwarded client address.

## Health

```http
GET /api/v1/health
```

Returns database readiness, update timestamp, catalog version, and counts.

## Metadata

```http
GET /api/v1/meta
```

Returns site name, date bounds, formats, counts, last collector run, and safe
public integration metadata. When the optional Seerr handoff is enabled,
`integrations.seerr` reports `authenticated_handoff` mode and its public base
URL. It never includes a Seerr or provider credential.

## Events

```http
GET /api/v1/events?from=2026-08-01&to=2026-08-31
```

Parameters:

| Name | Meaning |
|---|---|
| `date` | One day; overrides `from` and `to` |
| `from`, `to` | Inclusive ISO dates |
| `q` | Title or alias substring |
| `country` | Origin-country code, e.g. `FR` |
| `language` | Original-language code, e.g. `fr` |
| `network` | Exact network/service name |
| `genre` | Exact normalized genre |
| `format` | Exact format |
| `source` | `tmdb`, `tvdb`, or `tvmaze` |
| `event_type` | Exact normalized event type |
| `confidence` | `high`, `medium`, or `low` |
| `conflict` | `only` or `exclude` |
| `sort` | `date_asc`, `date_desc`, `title_asc`, or `confidence_desc` |
| `limit` | 1–200 by default configuration |
| `offset` | Pagination offset |

Example:

```http
GET /api/v1/events?from=2026-08-01&to=2026-08-31&country=FR&genre=Drama
```

Each event includes canonical title fields, countries, genres, networks,
external IDs, quality flags, all source-date evidence, and a `date_assessment`.
The assessment explains the date meaning, selection method, agreement status,
supporting sources, and alternate reported dates. Evidence rows identify
whether they support the selected date and their day difference from it.

## Facets

```http
GET /api/v1/facets?from=2026-08-01&to=2026-08-31
```

Returns available countries, languages, networks, genres, formats, and sources
with counts for the selected range.

`/api/v1/filters` is a stable alias for this route.

## Search, status, and statistics

```http
GET /api/v1/search?q=known+alias&limit=40&offset=0
GET /api/v1/status
GET /api/v1/stats
GET /api/v1/coverage
```

Search covers canonical, original, and alias titles across the complete
catalog. Status reports public collection timestamps and source results without
credentials. Statistics reports counts, sources, and event types. Coverage
reports observed date bounds, active dates, provider evidence, country counts,
and yearly density without claiming that provider data is exhaustive.

`/api/v1/date-range` is a stable alias for `/api/v1/events`.

## Calendar

```http
GET /api/v1/calendar?month=2026-08
```

Returns per-day premiere and conflict counts.

## Title detail

```http
GET /api/v1/titles/title_6fb3dc778dc594c16319e362
```

Returns the full public title/evidence record, including aliases.

Select the exact event opened from a card while keeping the stable title route:

```http
GET /api/v1/titles/title_6fb3dc778dc594c16319e362?event_id=event_abc123
```

## Credits

```http
GET /api/v1/credits
```

Returns provider names, links, and required attribution notices.

## Caching

Public JSON responses use `Cache-Control: no-store` and always return a
complete body. This prevents browser and service-worker caches from presenting
an older catalog after an atomic publication.

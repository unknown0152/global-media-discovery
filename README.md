# Global Media Discovery

[![CI](https://github.com/unknown0152/global-media-discovery/actions/workflows/ci.yml/badge.svg)](https://github.com/unknown0152/global-media-discovery/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/unknown0152/global-media-discovery?display_name=tag)](https://github.com/unknown0152/global-media-discovery/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-0f766e.svg)](LICENSE)
[![Read-only API](https://img.shields.io/badge/public_API-GET%20%2B%20HEAD-e64a31.svg)](docs/API.md)

**A self-hosted, date-first worldwide television premiere catalog.**

Most streaming interfaces begin with popularity. Global Media Discovery begins
with a date: choose any day, week, month, next-30-day window, or custom range
and see which television series providers report as premiering then—including
obscure and zero-vote titles.

[Download the latest release](https://github.com/unknown0152/global-media-discovery/releases/latest) ·
[Read the API documentation](docs/API.md)

## Why it is different

- **Date is the primary navigation.** Popularity is never an inclusion gate.
- **Evidence stays visible.** Every provider-reported premiere date is retained.
- **Disagreement is explicit.** Selected dates explain whether they are
  single-source, corroborated, disputed, or unverified.
- **Identity matching is conservative.** Verified external IDs are strongest;
  fuzzy title similarity never merges records automatically.
- **Coverage is honest.** The UI and API publish observed date, source, country,
  and yearly counts without claiming that provider data is exhaustive.
- **The public surface is read-only.** The website and API cannot modify the
  catalog or access provider credentials.

## Features

- Today, tomorrow, week, month, next 30 days, and custom date ranges.
- Previous/next period navigation and direct date selection.
- Country, language, network/service, genre, format, event type, source,
  confidence, and date-agreement filters.
- Canonical, original, and alias-title search.
- Provider evidence, confidence, observation time, selected-date support, and
  retained conflicting dates.
- Title details with artwork fallbacks, overview, origins, language, format,
  genres, networks, external identifiers, and known aliases.
- Responsive React 19.2 interface built with strict TypeScript 7, Vite
  8/Rolldown, Tailwind CSS 4, and TanStack Query, Router, and Virtual, plus
  light, dark, and system themes.
- Optional authenticated handoff to Seerr for titles with a verified TMDB ID;
  Seerr retains login, permissions, quotas, season selection, and approval.
- Scheduled TMDB, TheTVDB, and credential-free TVmaze collection.
- Validated staging databases and atomic SQLite publication.
- Built-in backup, restore, integrity checking, health, statistics, and logs.

## Quick start

The supported installer targets Debian or Ubuntu VPS hosts. Download both the
installer and checksums from the latest GitHub release, then verify before
running it:

```bash
version=2.0.0
base="https://github.com/unknown0152/global-media-discovery/releases/download/v${version}"
curl -fLO "${base}/global-media-discovery-installer-${version}.run"
curl -fLO "${base}/global-media-discovery-SHA256SUMS-${version}.txt"
grep "global-media-discovery-installer-${version}.run" \
  "global-media-discovery-SHA256SUMS-${version}.txt" | sha256sum -c -
sudo bash "global-media-discovery-installer-${version}.run"
```

The installer asks for the site name, optional domain, update frequency, and
optional TMDB/TheTVDB credentials. Leave the domain blank to serve HTTP on the
VPS IP at port `8080`; provide a domain already pointing at the VPS for
Caddy-managed HTTPS.

Rerunning a newer installer performs an upgrade while preserving
`/opt/global-media-discovery/data` and `/opt/global-media-discovery/secrets`.

### Non-interactive installation

```bash
sudo \
  GMD_NONINTERACTIVE=1 \
  GMD_DOMAIN=tv.example.com \
  GMD_SITE_NAME='Worldwide TV Calendar' \
  GMD_UPDATE_INTERVAL_HOURS=12 \
  TMDB_TOKEN='replace-me' \
  TVDB_KEY='replace-me' \
  bash global-media-discovery-installer-2.0.0.run
```

Interactive credential entry is safer on shared machines because exported or
inline environment values can be retained in shell history or process audit
records.

## Architecture and trust boundary

```text
TMDB ─────┐
TheTVDB ──┼── scheduled collector ── identity/evidence resolver
TVmaze ───┘                              │
                                         ▼
                                  staging SQLite DB
                                  validate + fsync
                                         │
                                    atomic replace
                                         ▼
Caddy ── Go 1.27 server ── React UI + GET-only API ── live SQLite DB (read-only)
Browser ── verified TMDB title handoff ── Seerr login and request flow (optional)
```

Only the collector receives provider secrets and a writable catalog mount. The
API runs with a read-only root filesystem, dropped Linux capabilities, no
provider-egress network, and a read-only SQLite mount. Unsupported HTTP methods
are rejected with `405 Method Not Allowed`.

### Optional Seerr integration

GMD can add an **Open in Seerr** action to title details without a plugin or a
Seerr API key. Configure the browser-facing Seerr base URL after installation:

```bash
sudo gmd seerr https://seerr.example.com
```

The action appears only for a verified TMDB identity and opens Seerr's own TV
detail page. Users authenticate and request there, so GMD never impersonates an
administrator or bypasses Seerr permissions. Use `sudo gmd seerr off` to
disable it.

Read the detailed [architecture](docs/ARCHITECTURE.md),
[operations guide](docs/OPERATIONS.md), and [API reference](docs/API.md).

## Operations

```bash
sudo gmd status              # containers and public API health
sudo gmd logs                # follow all GMD logs
sudo gmd logs collector      # follow collector logs only
sudo gmd update              # run one collection cycle
sudo gmd validate            # SQLite integrity and foreign-key checks
sudo gmd stats               # catalog counts, bounds, and last run
sudo gmd backup              # create a private consistent backup
sudo gmd restore NAME        # validate and atomically restore a backup
sudo gmd credentials         # securely update provider credentials
sudo gmd doctor              # end-to-end deployment checks
sudo gmd restart             # restart GMD services and wait for health
sudo gmd url                 # print the configured public URL
```

## Data behavior

1. Provider-owned IDs are the strongest identity evidence.
2. Cross-provider IDs are accepted only with compatibility checks.
3. Exact title/date/country matching is a conservative fallback.
4. Fuzzy title matching never merges records automatically.
5. Every source-reported date remains attached as evidence.
6. A selected date uses weighted provider agreement; ties are deterministic.
7. Conflicts and suspicious identity claims are flagged, not discarded.
8. A staging catalog must pass integrity, foreign-key, calendar-date, evidence,
   schema, and count validation before atomic publication.

The bundled starter database contains 257 normalized demonstration titles and
345 evidence records for 1–13 August 2026. Scheduled collection extends the
live catalog across the configured past/future window. Provider observations
are inherently incomplete and remain subject to provider terms.

## Development

Requirements: Go 1.27, Python 3.13, Node.js 24/npm, Docker Engine, and Docker
Compose. Go serves the embedded React build and public API; Python runs only
the private collector. Browser dependencies are compiled locally; no runtime
CDN is required.

```bash
npm ci
make web
make test
make release
```

The release gate compiles Go, Python, and strict TypeScript, runs the complete
unit and production-behavior suite, builds with Vite/Rolldown, validates shell
scripts and Compose hardening, verifies the seed database, and smoke-tests the
embedded self-extracting installer payload.

See [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md) before participating. Maintainers use the
documented [release process](docs/RELEASING.md) for reproducible tagged
artifacts.

## Releases, security, and licensing

- Release binaries, source archives, manifests, and checksums are published on
  [GitHub Releases](https://github.com/unknown0152/global-media-discovery/releases).
- Security issues should follow [SECURITY.md](SECURITY.md), not a public issue.
- Project code is available under the [MIT License](LICENSE).
- Metadata and artwork remain governed by their providers; required notices
  are documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This product uses the TMDB API but is not endorsed or certified by TMDB.

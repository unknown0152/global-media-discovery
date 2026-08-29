# Changelog

## 2.1.0 — 2026-08-29

- Add a permission-gated Simkl Calendar v2 TV evidence adapter using only the
  public Client ID; the Client Secret and user OAuth data are never used.
- Normalize series premieres, season premieres, mid-season finales, season
  finales, and series finales while explicitly discarding ordinary episodes.
- Require conservative date/country/title compatibility or multiple converging
  existing provider IDs; contradictory identifiers remain unresolved.
- Add a private non-publishing coverage probe, credential redaction, source
  failure isolation, atomic staging tests, and `gmd simkl` operations.
- Make title details resolve the exact event opened by the user so season and
  finale evidence remains visible and correctly attributed.
- Keep production Simkl publication disabled until an operator explicitly
  confirms provider permission for retention and normalized public evidence.
- Keep structured logs on stderr so management-command stdout remains valid
  JSON for automation and operator tooling.
- Serve the PWA manifest with its registered media type; no service worker is
  registered, so API responses cannot be retained in an offline cache.

## 2.0.0 — 2026-08-29

- Replace the public Python/WSGI runtime and HTMX shell with a single Go 1.27
  server that embeds and serves both the read-only API and production website.
- Rebuild the frontend with React 19.2.8, TypeScript 7.0.2 strict mode, Vite
  8.2.2/Rolldown, Tailwind CSS 4.3.3, and TanStack Query, Router, and Virtual.
- Keep filter, search, date, view, and sort state in bookmarkable URLs, with
  controlled country selection and deterministic Reset behavior.
- Preserve the Python collector as the only catalog writer, its provider
  credentials, conservative reconciliation, evidence model, staging database
  validation, backups, and atomic publication.
- Split public and collector images so the Go API has only a read-only database
  mount and no provider network or credential access.
- Add Go contract tests for stable routes, HEAD, input bounds, filters,
  pagination, evidence, and read-only method enforcement.

## 1.4.0 — 2026-08-29

- Add an optional GMD-to-Seerr title handoff using verified TMDB identities.
- Keep authentication, season selection, permissions, quotas, and request
  creation inside Seerr; GMD sends no credentials and remains read-only.
- Clearly explain when a title cannot be handed off because no verified TMDB
  identity exists instead of guessing or fuzzy-matching an identifier.
- Add a `gmd seerr` management command, safe URL validation, upgrade-safe
  configuration preservation, runtime integration metadata, and regression
  tests for identity and credential-boundary behavior.
- Accept TheTVDB's integer-valued series and remote identifiers so one provider
  payload shape cannot abort its otherwise isolated collection pass.

## 1.3.0 — 2026-08-14

- Explain the exact meaning of each selected premiere date and distinguish
  single-source, corroborated, disputed, and unverified dates.
- Expose provider confidence, observation time, selected-date support, and
  day differences while preserving every conflicting provider report.
- Add read-only JSON and HTMX coverage views with date bounds, active dates,
  yearly counts, country counts, provider counts, and an explicit scope caveat.
- Show evidence-aware agreement labels on result cards and richer date
  assessment details on desktop and mobile.
- Add regression tests for weighted conflicting-date selection, evidence
  annotations, coverage counts, and the new public routes.
- Allow release builds to omit the optional preview image when no current
  screenshot has been supplied.
- Preserve Caddy's live lock directory during upgrades so ownership preflight
  remains compatible with its protected lock files.
- Add GitHub CI, automated tagged releases, dependency updates, structured
  issue forms, a pull-request checklist, contribution guidance, and verified
  public release documentation.
- Synchronize the Python package, Compose, frontend, and installer versions
  under a regression test.

## 1.2.2 — 2026-08-14

- Replace the browser-restored native origin-country select with an accessible
  Tailwind button menu backed exclusively by application state.
- Route country choices and Reset through the same deterministic state update.

## 1.2.1 — 2026-08-14

- Initialize HTMX after dynamically rendered title cards are attached to the
  document, ensuring fragment actions are active in every supported browser.

## 1.2.0 — 2026-08-14

- Add a pinned Tailwind CSS 4.3 build pipeline and compiled production asset.
- Vendor HTMX 4.0.0-beta6 locally and use read-only HTML fragment routes for
  title details and metadata-source notices.
- Keep JSON API and HTMX fragments uncached to prevent stale catalog data.
- Add fragment escaping, URL validation, method enforcement, and browser tests.

## 1.1.3 — 2026-08-14

- Prevent empty conditional API responses by serving public JSON with
  `Cache-Control: no-store` and a complete body on every GET.
- Retry an unexpectedly empty browser response once without cache and show a
  controlled error instead of exposing a JSON parser exception.

## 1.1.2 — 2026-08-14

- Use the native select `change` event exclusively so Firefox and Safari do
  not reapply the previous facet value while a selection menu is closing.

## 1.1.1 — 2026-08-14

- Fix country and other facet selectors retaining stale DOM values after a
  filter is cleared or changed.
- Handle non-standard browser locale strings without aborting application
  startup.
- Refresh the frontend and service-worker cache keys.

## 1.1.0 — 2026-08-13

- Add stable status, statistics, search, filters, and date-range API routes.
- Add event-type and confidence filtering plus global server-side sorting.
- Strengthen catalog validation, automatic backup retention, atomic recovery,
  and invalid/all-failed update protection.
- Add due-aware restart scheduling, collector health checks, and periodic
  credential-free TVmaze backfill.
- Complete Today/Tomorrow/date-range navigation, mobile filter accessibility,
  evidence warnings, artwork fallbacks, offline states, and PWA cache safety.
- Bind unused IP-only HTTPS locally, run Caddy as a non-root user, and add
  cross-origin hardening headers.
- Expand the production regression suite from 23 to 34 tests.

## 1.0.1 — 2026-08-13

- Use standard SQLite rowid tables so catalog integrity checks work correctly
  on the SQLite 3.40 runtime included with Debian 12 container images.
- Add a schema compatibility regression test.
- Allow release artifacts to be built with the BSD tar shipped by macOS.

## 1.0.0 — 2026-08-13

- Complete date-first TV premiere website.
- Read-only WSGI API and SQLite query layer.
- TMDB, TheTVDB, and TVmaze collectors.
- Conservative identity resolution and source-date evidence.
- Atomic catalog publication and built-in validation.
- Responsive light/dark frontend and PWA shell.
- Hardened Docker Compose deployment behind Caddy.
- Self-extracting one-command Debian/Ubuntu installer.
- Compact normalized August 2026 starter catalog.
- Management CLI, backups, tests, and operator documentation.

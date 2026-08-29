# AI/developer guide

## Product invariant

Date is the primary navigation variable. Popularity must never be required for
publication. The public application remains read-only.

## Code map

- `src/gmd/collector/`: source clients, normalization, scheduling.
- `src/gmd/reconcile.py`: conservative canonical identity and evidence writes.
- `cmd/gmd-server/`: Go 1.27 GET/HEAD-only API and embedded frontend server.
- `frontend/`: React 19.2, strict TypeScript 7, Vite 8, and TanStack client.
- `src/gmd/query.py`: legacy-compatible Python query contract tests.
- `src/gmd/db.py`: schema lifecycle, validation, backup, atomic publication.
- `web/`: archived 1.x frontend retained only for upgrade history.
- `seed/catalog.sqlite3`: compact normalized starter data.
- `scripts/install.sh`: idempotent VPS installation.
- `tests/`: dependency-free acceptance tests.

## Non-negotiable rules

1. Never put API credentials in frontend code, Compose environment values, or
   committed files.
2. Never add a public write route without an explicit architectural decision.
3. Never fuzzy-merge identities automatically. Preserve unresolved candidates.
4. Preserve each source's date evidence even when a canonical date is chosen.
5. Build updates in a staging database and publish only after integrity checks.
6. Keep SQL parameterized. Dynamic SQL may only interpolate fixed internal
   fragments such as known table names or placeholder counts.
7. Run `make test` before release.

## Release

Update `VERSION`, `package.json`, `src/gmd/__init__.py`, and `CHANGELOG.md`, then run:

```bash
bash scripts/build-release.sh
```

# Contributing

Thank you for helping improve Global Media Discovery.

Participation is governed by the project [Code of Conduct](CODE_OF_CONDUCT.md).

## Before opening a change

- Use an issue to describe a user-visible bug or substantial feature first.
- Never include TMDB/TheTVDB credentials, live `.env` files, private logs, or
  catalog/database dumps.
- Preserve the date-first, read-only public architecture.
- Do not add fuzzy-only automatic title merging or discard conflicting date
  evidence.

## Development workflow

```bash
git clone https://github.com/unknown0152/global-media-discovery.git
cd global-media-discovery
npm ci
make web
make test
```

Keep changes focused. Add regression coverage for behavior changes and update
the API or operations documentation when public behavior changes.

## Pull-request checklist

- `make test` passes.
- Generated browser assets are current after frontend changes.
- Public API routes remain GET/HEAD-only and use parameterized SQL.
- Credentials cannot enter source, browser assets, logs, URLs, or fixtures.
- Identity reconciliation remains conservative and evidence-preserving.
- Database changes use validated staging publication and recovery tests.
- `CHANGELOG.md` describes user-visible changes.

By submitting a contribution, you agree that it may be distributed under the
project's MIT license.

Maintainers should follow [docs/RELEASING.md](docs/RELEASING.md) when publishing
a version.

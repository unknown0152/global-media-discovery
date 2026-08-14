# Security policy

## Deployment model

The public surface is intentionally read-only:

- Caddy serves static files and proxies only `/api/*`.
- The API accepts only `GET` and `HEAD`.
- The API container has a read-only root filesystem, no Linux capabilities,
  no-new-privileges, bounded resources, and a read-only catalog mount.
- Only the collector has write access to the catalog directory.
- API credentials are Docker file secrets and are never exposed to the browser.
- The database is rebuilt in a staging file, checked, and atomically published.

## Reporting

Please use GitHub's private vulnerability reporting form:

<https://github.com/unknown0152/global-media-discovery/security/advisories/new>

Do not open a public issue for a suspected vulnerability and do not include
provider credentials, secret-file contents, database dumps, or private logs.

For a private deployment, stop the affected service, preserve logs, rotate any
possibly exposed API credentials, and review `gmd logs` before restarting.
Do not include TMDB or TheTVDB credentials in public reports.

## Supported versions

| Version | Supported |
|---|---|
| 1.3.x | Yes |
| Earlier versions | No |

Security fixes target the current release. Rerunning a newer self-extracting
installer upgrades code while preserving `/opt/global-media-discovery/data`
and `/opt/global-media-discovery/secrets`.

# Operations

## Requirements

- A Debian or Ubuntu VPS.
- Root or sudo access.
- For HTTPS: a DNS A/AAAA record pointing the chosen domain at the VPS and
  inbound ports 80 and 443 allowed.
- Optional but strongly recommended: TMDB Read Access Token and TheTVDB API
  key. TVmaze requires no key.

## Install or upgrade

```bash
sudo bash global-media-discovery-installer-2.1.1.run
```

The installer is idempotent. It stops the previous stack, copies new code,
preserves `data/` and `secrets/`, validates Compose, rebuilds the image,
bootstraps the starter catalog if needed, starts services, and polls the public
health endpoint.

## Files

```text
/opt/global-media-discovery/
├── .env
├── compose.yaml
├── data/
│   ├── catalog.sqlite3
│   ├── collector-status.json
│   └── backups/
└── secrets/
    ├── tmdb_token
    ├── tvdb_key
    └── simkl_client_id
```

`.env` and secret files are mode `0600`/`0400`. The data directory is owned by
the application UID `65532`.

## DNS and HTTPS

When a domain is entered, Caddy listens on host ports 80 and 443 and manages
certificates. Verify DNS first:

```bash
getent ahosts tv.example.com
```

Then check:

```bash
gmd status
gmd logs caddy
```

For an IP-only deployment, leave the domain blank; the installer defaults to
`http://SERVER_IP:8080`.

## Manual collection

```bash
gmd update
```

The collector lock prevents overlapping updates. The public site continues
serving the previous validated catalog while collection runs.

## Validate

```bash
gmd validate
```

Validation checks:

- SQLite `integrity_check`;
- foreign-key consistency;
- supported schema version;
- event date shape and actual calendar validity;
- evidence date validity and required fields;
- an evidence record for every event;
- non-empty core catalog counts.

## Backup and restore

Create a consistent SQLite backup:

```bash
gmd backup
```

Backups appear in `data/backups/`.

Restore a validated backup atomically (the current catalog is backed up first):

```bash
gmd restore catalog-YYYYMMDDTHHMMSSZ.sqlite3
gmd validate
```

The collector also creates a private pre-update backup and retains the newest
14 GMD catalog backups by default (`GMD_BACKUP_RETENTION`).

## Rotate provider credentials

```bash
sudo gmd credentials
```

The command prompts without echoing values, preserves a credential when its
prompt is left blank, fixes ownership and permissions, and recreates the
services. Run `gmd update` afterward for an immediate collection cycle. Avoid
placing actual keys in public issue reports, command history, or screenshots.

## Private Simkl test and API-rules gate

Simkl Calendar v2 support uses only the public Client ID. The Client Secret and
user OAuth tokens are neither requested nor mounted. Test coverage against the
live catalog without publishing anything:

```bash
sudo gmd simkl test
sudo gmd simkl status
```

GMD uses the public, edge-cached Calendar v2 file and never calls the OAuth
`/sync/*` endpoints, so watchlist activity polling and `date_from` do not apply.
Before enabling, read Simkl's API rules and confirm this deployment is
non-commercial, below Simkl's published revenue threshold, or appropriately
licensed. Then record acceptance explicitly and run a normal atomic update:

```bash
sudo gmd simkl enable --api-rules-accepted
sudo gmd update
```

Disable new collection while retaining already validated evidence with:

```bash
sudo gmd simkl disable
```

## Connect Seerr

Configure the URL users already use to reach Seerr:

```bash
sudo gmd seerr https://seerr.example.com
sudo gmd seerr                         # show current state
```

The command verifies Seerr's public status endpoint before changing `.env` and
recreates only the GMD services. GMD does not need or store a Seerr API key.
Title details then link verified TMDB records into Seerr, where the user signs
in and completes the request under normal permissions and quotas.

Disable the handoff with:

```bash
sudo gmd seerr off
```

## Reconfigure

The simplest supported path is rerunning the installer; existing secrets can
be kept by pressing Enter at the secret prompts. Advanced operators may edit
`.env` and then run:

```bash
gmd config
gmd restart
```

## Troubleshooting

### Site starts but shows only August 2026 starter data

The live collector may still be running its first full scan:

```bash
gmd logs collector
```

The website remains usable during that scan.

### API health fails

```bash
gmd status
gmd logs api
gmd validate
```

An invalid live database discovered during bootstrap is quarantined to
`data/backups/catalog-invalid-*.sqlite3` and rebuilt from the starter snapshot.

### Caddy cannot issue a certificate

Confirm the domain resolves to the VPS, ports 80/443 are reachable, and no
other service owns those ports:

```bash
sudo ss -ltnp | grep -E ':(80|443)\b'
gmd logs caddy
```

### Source errors

Source failures are isolated. A TMDB or TheTVDB failure does not discard the
last good catalog and TVmaze can continue independently. Inspect:

```bash
gmd logs collector
cat /opt/global-media-discovery/data/collector-status.json
```

## Remove

Create a backup first. Then:

```bash
sudo gmd stop
sudo rm -f /usr/local/bin/gmd
sudo rm -rf /opt/global-media-discovery
```

Docker itself is not removed because other services on the VPS may use it.

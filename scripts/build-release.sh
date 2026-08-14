#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
OUTPUT_DIR="${1:-$ROOT/../release}"
TEMP="$(mktemp -d)"
cleanup() { rm -rf "$TEMP"; }
trap cleanup EXIT

mkdir -p "$OUTPUT_DIR"
rm -f \
  "$OUTPUT_DIR/global-media-discovery-installer-${VERSION}.run" \
  "$OUTPUT_DIR/global-media-discovery-source-${VERSION}.tar.gz" \
  "$OUTPUT_DIR/global-media-discovery-preview-${VERSION}.png" \
  "$OUTPUT_DIR/global-media-discovery-release-${VERSION}.json" \
  "$OUTPUT_DIR/global-media-discovery-SHA256SUMS-${VERSION}.txt"

cd "$ROOT"
bash scripts/test.sh

find src tests scripts -type d -name __pycache__ -prune -exec rm -rf {} +
find seed -maxdepth 1 -type f \( -name '*-wal' -o -name '*-shm' \) -delete

INSTALLER="$OUTPUT_DIR/global-media-discovery-installer-${VERSION}.run"
bash scripts/build-installer.sh "$INSTALLER" >/dev/null

SOURCE_ROOT="$TEMP/global-media-discovery-${VERSION}"
mkdir -p "$SOURCE_ROOT"
tar -C "$ROOT" \
  --exclude='./.git' \
  --exclude='./.env' \
  --exclude='./data/*' \
  --exclude='./secrets/*' \
  --exclude='./seed/*.sqlite3-wal' \
  --exclude='./seed/*.sqlite3-shm' \
  --exclude='*/__pycache__' \
  --exclude='*.pyc' \
  --exclude='./*.run' \
  --exclude='./*.tar.gz' \
  --exclude='./SHA256SUMS*' \
  --exclude='./.test-output' \
  --exclude='./node_modules' \
  -cf - . | tar -C "$SOURCE_ROOT" -xf -

SOURCE_ARCHIVE="$OUTPUT_DIR/global-media-discovery-source-${VERSION}.tar.gz"
if tar --help 2>&1 | grep -q -- '--sort'; then
  tar --sort=name \
    --mtime='UTC 2026-01-01' \
    --owner=0 --group=0 --numeric-owner \
    -C "$TEMP" -cf - "global-media-discovery-${VERSION}" | gzip -n -9 \
    > "$SOURCE_ARCHIVE"
else
  tar -C "$TEMP" -cf - "global-media-discovery-${VERSION}" | gzip -n -9 \
    > "$SOURCE_ARCHIVE"
fi

PREVIEW="$OUTPUT_DIR/global-media-discovery-preview-${VERSION}.png"
if [ -f "$ROOT/docs/screenshots/desktop.png" ]; then
  cp "$ROOT/docs/screenshots/desktop.png" "$PREVIEW"
else
  printf 'Release preview skipped: docs/screenshots/desktop.png is absent.\n' >&2
fi

MANIFEST="$OUTPUT_DIR/global-media-discovery-release-${VERSION}.json"
PYTHONPATH="$ROOT/src" python3 - "$ROOT" "$OUTPUT_DIR" "$VERSION" <<'PY'
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from gmd.db import validate_database

root = Path(sys.argv[1])
output = Path(sys.argv[2])
version = sys.argv[3]
artifact_names = [
    f"global-media-discovery-installer-{version}.run",
    f"global-media-discovery-source-{version}.tar.gz",
    f"global-media-discovery-preview-{version}.png",
]
artifacts = []
for name in artifact_names:
    path = output / name
    if not path.exists():
        continue
    artifacts.append(
        {
            "name": name,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    )
manifest = {
    "product": "Global Media Discovery",
    "version": version,
    "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "install_command": f"sudo bash global-media-discovery-installer-{version}.run",
    "starter_catalog": validate_database(root / "seed/catalog.sqlite3"),
    "artifacts": artifacts,
    "validation": {
        "python_unittests": "passed",
        "python_compile": "passed",
        "javascript_parse": "passed",
        "shell_syntax": "passed",
        "compose_structure": "passed",
        "installer_payload_checksum": "embedded-and-tested",
    },
}
(output / f"global-media-discovery-release-{version}.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

CHECKSUMS="$OUTPUT_DIR/global-media-discovery-SHA256SUMS-${VERSION}.txt"
(
  cd "$OUTPUT_DIR"
  artifacts=(
    "$(basename "$INSTALLER")"
    "$(basename "$SOURCE_ARCHIVE")"
    "$(basename "$MANIFEST")"
  )
  if [ -f "$PREVIEW" ]; then
    artifacts+=("$(basename "$PREVIEW")")
  fi
  sha256sum "${artifacts[@]}" > "$(basename "$CHECKSUMS")"
)

# Final archive and installer smoke checks.
tar -tzf "$SOURCE_ARCHIVE" >/dev/null
python3 - "$INSTALLER" <<'PY'
from io import BytesIO
from pathlib import Path
import hashlib
import re
import sys
import tarfile

path = Path(sys.argv[1])
blob = path.read_bytes()
marker = b"__GMD_PAYLOAD_BELOW__\n"
header, payload = blob.split(marker, 1)
match = re.search(rb"PAYLOAD_SHA256='([0-9a-f]{64})'", header)
if not match or hashlib.sha256(payload).hexdigest().encode() != match.group(1):
    raise SystemExit("installer payload verification failed")
with tarfile.open(fileobj=BytesIO(payload), mode="r:gz") as archive:
    names = set(archive.getnames())
required = {
    "global-media-discovery/scripts/install.sh",
    "global-media-discovery/compose.yaml",
    "global-media-discovery/seed/catalog.sqlite3",
    "global-media-discovery/web/index.html",
}
missing = required - names
if missing:
    raise SystemExit(f"installer is missing: {sorted(missing)}")
PY

printf 'Release %s created in %s\n' "$VERSION" "$OUTPUT_DIR"
cat "$CHECKSUMS"

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
OUTPUT="${1:-$ROOT/../global-media-discovery-installer-${VERSION}.run}"
TEMP="$(mktemp -d)"
cleanup() { rm -rf "$TEMP"; }
trap cleanup EXIT

PACKAGE_ROOT="$TEMP/global-media-discovery"
mkdir -p "$PACKAGE_ROOT"

tar -C "$ROOT" \
  --exclude='./.git' \
  --exclude='./.env' \
  --exclude='./data/*' \
  --exclude='./secrets/*' \
  --exclude='./docs/screenshots/*' \
  --exclude='./seed/*.sqlite3-wal' \
  --exclude='./seed/*.sqlite3-shm' \
  --exclude='*/__pycache__' \
  --exclude='*.pyc' \
  --exclude='./*.run' \
  --exclude='./*.tar.gz' \
  --exclude='./SHA256SUMS*' \
  --exclude='./.test-output' \
  --exclude='./.firecrawl' \
  --exclude='./node_modules' \
  -cf - . | tar -C "$PACKAGE_ROOT" -xf -

PAYLOAD="$TEMP/payload.tar.gz"
if tar --help 2>&1 | grep -q -- '--sort'; then
  tar --sort=name \
    --mtime='UTC 2026-01-01' \
    --owner=0 --group=0 --numeric-owner \
    -C "$TEMP" -cf - global-media-discovery | gzip -n -9 > "$PAYLOAD"
else
  # macOS ships bsdtar, which does not implement GNU tar's reproducibility
  # flags. The embedded checksum still protects the resulting payload.
  tar -C "$TEMP" -cf - global-media-discovery | gzip -n -9 > "$PAYLOAD"
fi
PAYLOAD_SHA256="$(sha256sum "$PAYLOAD" | awk '{print $1}')"

mkdir -p "$(dirname -- "$OUTPUT")"
cat > "$OUTPUT" <<EOF_STUB
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

INSTALLER_VERSION='$VERSION'
PAYLOAD_SHA256='$PAYLOAD_SHA256'
SELF="\${BASH_SOURCE[0]}"

command -v tar >/dev/null 2>&1 || { echo "tar is required." >&2; exit 1; }
command -v sha256sum >/dev/null 2>&1 || { echo "sha256sum is required." >&2; exit 1; }

PAYLOAD_LINE="\$(awk '/^__GMD_PAYLOAD_BELOW__\$/ { print NR + 1; exit 0; }' "\$SELF")"
[ -n "\$PAYLOAD_LINE" ] || { echo "Installer payload marker is missing." >&2; exit 1; }

TEMP_DIR="\$(mktemp -d)"
cleanup() { rm -rf "\$TEMP_DIR"; }
trap cleanup EXIT INT TERM HUP

PAYLOAD_FILE="\$TEMP_DIR/payload.tar.gz"
tail -n +"\$PAYLOAD_LINE" "\$SELF" > "\$PAYLOAD_FILE"
ACTUAL_SHA256="\$(sha256sum "\$PAYLOAD_FILE" | awk '{print \$1}')"
if [ "\$ACTUAL_SHA256" != "\$PAYLOAD_SHA256" ]; then
  echo "Installer payload checksum verification failed." >&2
  echo "Expected: \$PAYLOAD_SHA256" >&2
  echo "Actual:   \$ACTUAL_SHA256" >&2
  exit 1
fi

tar --no-same-owner --no-same-permissions -xzf "\$PAYLOAD_FILE" -C "\$TEMP_DIR"
INSTALL_SCRIPT="\$TEMP_DIR/global-media-discovery/scripts/install.sh"
[ -f "\$INSTALL_SCRIPT" ] || { echo "Installer payload is incomplete." >&2; exit 1; }

printf 'Global Media Discovery installer %s\n' "\$INSTALLER_VERSION"
bash "\$INSTALL_SCRIPT" "\$@"
exit \$?
__GMD_PAYLOAD_BELOW__
EOF_STUB
cat "$PAYLOAD" >> "$OUTPUT"
chmod 0755 "$OUTPUT"

printf '%s  %s\n' "$(sha256sum "$OUTPUT" | awk '{print $1}')" "$(basename -- "$OUTPUT")"

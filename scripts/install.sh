#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="${GMD_INSTALL_DIR:-/opt/global-media-discovery}"
VERSION="$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")"

log() { printf '\033[1;34m[GMD]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[GMD]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[GMD]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run this installer as root: sudo bash $(basename "$0")"
[ "$(uname -s)" = "Linux" ] || die "This installer supports Linux VPS hosts."

load_os_release() {
  [ -r /etc/os-release ] || return 1
  # shellcheck disable=SC1091
  . /etc/os-release
}

ensure_host_tools() {
  local missing=()
  for command_name in awk curl grep sed sha256sum tar; do
    command -v "$command_name" >/dev/null 2>&1 || missing+=("$command_name")
  done
  [ "${#missing[@]}" -eq 0 ] && return

  if command -v apt-get >/dev/null 2>&1; then
    log "Installing required host tools: ${missing[*]}"
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
      ca-certificates curl coreutils gawk grep sed tar
  else
    die "Missing required commands: ${missing[*]}"
  fi
}

start_docker_daemon() {
  docker info >/dev/null 2>&1 && return
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now docker >/dev/null 2>&1 || true
  elif command -v service >/dev/null 2>&1; then
    service docker start >/dev/null 2>&1 || true
  fi
  docker info >/dev/null 2>&1 || die "Docker is installed, but the Docker daemon is not available."
}

install_docker() {
  if command -v docker >/dev/null 2>&1 \
    && docker compose version >/dev/null 2>&1; then
    start_docker_daemon
    return
  fi

  log "Docker Engine and Compose were not found. Installing from Docker's official apt repository."
  load_os_release || die "Cannot identify the operating system."
  case "${ID:-}" in
    debian|ubuntu) ;;
    *)
      die "Automatic Docker installation supports Debian or Ubuntu. Install Docker Engine + Compose, then rerun."
      ;;
  esac

  local codename="${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}"
  [ -n "$codename" ] || die "Cannot determine the Debian/Ubuntu release codename."

  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" \
    -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc

  cat > /etc/apt/sources.list.d/docker.sources <<EOF_DOCKER
Types: deb
URIs: https://download.docker.com/linux/${ID}
Suites: ${codename}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF_DOCKER

  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
    docker-compose-plugin
  start_docker_daemon
  docker compose version >/dev/null 2>&1 || die "Docker Compose installation failed."
}

ask() {
  local variable="$1" prompt="$2" default="$3" value=""
  if [ -n "${!variable:-}" ]; then
    printf -v "$variable" '%s' "${!variable}"
    return
  fi
  if [ "${GMD_NONINTERACTIVE:-0}" = "1" ]; then
    printf -v "$variable" '%s' "$default"
    return
  fi
  read -r -p "$prompt [$default]: " value
  printf -v "$variable" '%s' "${value:-$default}"
}

ask_secret() {
  local variable="$1" prompt="$2" existing_file="$3" value=""
  if [ -n "${!variable:-}" ]; then
    printf -v "$variable" '%s' "${!variable}"
    return
  fi
  if [ "${GMD_NONINTERACTIVE:-0}" = "1" ]; then
    if [ -f "$existing_file" ]; then
      printf -v "$variable" '%s' "$(cat "$existing_file")"
    else
      printf -v "$variable" '%s' ""
    fi
    return
  fi
  read -r -s -p "$prompt (blank keeps existing / skips): " value
  echo
  if [ -z "$value" ] && [ -f "$existing_file" ]; then
    value="$(cat "$existing_file")"
  fi
  printf -v "$variable" '%s' "$value"
}

dotenv_value() {
  local value="$1"
  value="${value//$'\n'/ }"
  value="${value//\'/’}"
  printf "'%s'" "$value"
}

existing_env_value() {
  local key="$1"
  [ -f "$INSTALL_DIR/.env" ] || return 0
  sed -n "s/^${key}='\(.*\)'$/\1/p" "$INSTALL_DIR/.env" | head -n1
}

validate_port() {
  local value="$1" label="$2"
  [[ "$value" =~ ^[0-9]+$ ]] || die "$label must be numeric."
  [ "$value" -ge 1 ] && [ "$value" -le 65535 ] \
    || die "$label must be between 1 and 65535."
}

validate_domain() {
  local value="$1"
  [ "${#value}" -le 253 ] || return 1
  [[ "$value" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]]
}

public_ip() {
  hostname -I 2>/dev/null | awk '{print $1}'
}

ensure_host_tools
install_docker

SITE_NAME="${GMD_SITE_NAME:-}"
DOMAIN="${GMD_DOMAIN:-}"
HTTP_PORT="${GMD_HTTP_PORT:-}"
UPDATE_HOURS="${GMD_UPDATE_INTERVAL_HOURS:-}"
ask SITE_NAME "Site name" "Global Media Discovery"
ask DOMAIN "Public domain (blank for HTTP by IP)" ""
ask UPDATE_HOURS "Automatic update interval in hours" "24"

SEERR_PUBLIC_URL="${GMD_SEERR_PUBLIC_URL:-}"
if [ -z "$SEERR_PUBLIC_URL" ]; then
  SEERR_PUBLIC_URL="$(existing_env_value GMD_SEERR_PUBLIC_URL)"
fi

SITE_NAME="${SITE_NAME//$'\n'/ }"
[ -n "$SITE_NAME" ] || die "Site name cannot be empty."
[ "${#SITE_NAME}" -le 120 ] || die "Site name must be 120 characters or fewer."
[[ "$UPDATE_HOURS" =~ ^[0-9]+$ ]] || die "Update interval must be numeric."
[ "$UPDATE_HOURS" -ge 1 ] && [ "$UPDATE_HOURS" -le 168 ] \
  || die "Update interval must be between 1 and 168 hours."

if [ -n "$DOMAIN" ]; then
  DOMAIN="${DOMAIN#http://}"
  DOMAIN="${DOMAIN#https://}"
  DOMAIN="${DOMAIN%%/*}"
  DOMAIN="${DOMAIN%.}"
  DOMAIN="${DOMAIN,,}"
  validate_domain "$DOMAIN" || die "Invalid public domain: $DOMAIN"
  SITE_ADDRESS="$DOMAIN"
  PUBLIC_URL="https://$DOMAIN"
  HTTP_PORT="80"
  HTTPS_PORT="443"
  HEALTH_URL="$PUBLIC_URL/api/v1/health"
  HTTPS_BIND="0.0.0.0"
else
  ask HTTP_PORT "Public HTTP port" "8080"
  validate_port "$HTTP_PORT" "HTTP port"
  SITE_ADDRESS="http://:80"
  HTTPS_PORT="${GMD_HTTPS_PORT:-8443}"
  validate_port "$HTTPS_PORT" "HTTPS placeholder port"
  ip="$(public_ip)"
  PUBLIC_URL="http://${ip:-127.0.0.1}:$HTTP_PORT"
  HEALTH_URL="http://127.0.0.1:$HTTP_PORT/api/v1/health"
  HTTPS_BIND="127.0.0.1"
fi

existing_tmdb="$INSTALL_DIR/secrets/tmdb_token"
existing_tvdb="$INSTALL_DIR/secrets/tvdb_key"
TMDB_TOKEN="${TMDB_TOKEN:-}"
TVDB_KEY="${TVDB_KEY:-}"
ask_secret TMDB_TOKEN "TMDB Read Access Token" "$existing_tmdb"
ask_secret TVDB_KEY "TheTVDB API key" "$existing_tvdb"

if [ -d "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/compose.yaml" ]; then
  log "Stopping the existing installation while preserving catalog data and secrets."
  (cd "$INSTALL_DIR" && docker compose down --remove-orphans) || true
fi

log "Installing version $VERSION to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 \
  ! -name data ! -name secrets -exec rm -rf -- {} +

tar -C "$SOURCE_DIR" \
  --exclude='./data' \
  --exclude='./secrets' \
  --exclude='./.env' \
  --exclude='./*.run' \
  --exclude='./seed/*.sqlite3-wal' \
  --exclude='./seed/*.sqlite3-shm' \
  -cf - . | tar --no-same-owner --no-same-permissions -C "$INSTALL_DIR" -xf -

chown -R root:root "$INSTALL_DIR"
chmod 0755 "$INSTALL_DIR"
find "$INSTALL_DIR" -path "$INSTALL_DIR/data" -prune -o \
  -path "$INSTALL_DIR/secrets" -prune -o \
  -path "$INSTALL_DIR/.env" -prune -o \
  -type d -exec chmod 0755 {} +
find "$INSTALL_DIR" -path "$INSTALL_DIR/data" -prune -o \
  -path "$INSTALL_DIR/secrets" -prune -o \
  -path "$INSTALL_DIR/.env" -prune -o \
  -type f -exec chmod 0644 {} +
chmod 0755 "$INSTALL_DIR/bin/gmd" "$INSTALL_DIR/scripts/"*.sh
mkdir -p "$INSTALL_DIR/data/backups" "$INSTALL_DIR/secrets"
chown -R 65532:65532 "$INSTALL_DIR/data"
chmod 0750 "$INSTALL_DIR/data" "$INSTALL_DIR/data/backups"

printf '%s' "$TMDB_TOKEN" > "$INSTALL_DIR/secrets/tmdb_token"
printf '%s' "$TVDB_KEY" > "$INSTALL_DIR/secrets/tvdb_key"
chown 65532:65532 "$INSTALL_DIR/secrets/tmdb_token" \
  "$INSTALL_DIR/secrets/tvdb_key"
chmod 0400 "$INSTALL_DIR/secrets/tmdb_token" \
  "$INSTALL_DIR/secrets/tvdb_key"
chmod 0700 "$INSTALL_DIR/secrets"

cat > "$INSTALL_DIR/.env" <<EOF_ENV
GMD_VERSION='$VERSION'
GMD_SITE_NAME=$(dotenv_value "$SITE_NAME")
GMD_SITE_ADDRESS=$(dotenv_value "$SITE_ADDRESS")
GMD_PUBLIC_URL=$(dotenv_value "$PUBLIC_URL")
GMD_SEERR_PUBLIC_URL=$(dotenv_value "$SEERR_PUBLIC_URL")
GMD_HTTP_PORT='$HTTP_PORT'
GMD_HTTPS_PORT='$HTTPS_PORT'
GMD_HTTPS_BIND='$HTTPS_BIND'
GMD_UPDATE_INTERVAL_HOURS='$UPDATE_HOURS'
GMD_PAST_DAYS='365'
GMD_FUTURE_DAYS='540'
GMD_TVDB_FULL_SCAN_DAYS='7'
GMD_TVDB_EXTENDED_LIMIT='600'
GMD_TMDB_DETAIL_LIMIT='1200'
GMD_TVMAZE_RECENT_DAYS='30'
GMD_TVMAZE_BACKFILL_DAYS='365'
GMD_TVMAZE_BACKFILL_INTERVAL_DAYS='7'
GMD_BACKUP_RETENTION='14'
GMD_API_WORKERS='2'
GMD_API_THREADS='4'
GMD_MAX_QUERY_DAYS='366'
GMD_MAX_PAGE_SIZE='200'
GMD_RATE_LIMIT_PER_MINUTE='180'
GMD_LOG_LEVEL='INFO'
GMD_LOG_FORMAT='json'
GMD_COLLECTOR_RUN_ON_START='true'
EOF_ENV
chmod 0600 "$INSTALL_DIR/.env"

install -m 0755 "$INSTALL_DIR/bin/gmd" /usr/local/bin/gmd

cd "$INSTALL_DIR"
log "Validating Docker Compose configuration."
docker compose config --quiet

log "Building the application image."
docker compose build --pull

log "Creating or validating the initial catalog."
docker compose run --rm --no-deps collector bootstrap

log "Preparing Caddy's private storage for its non-root process."
docker compose run --rm --no-deps --user 0:0 --cap-add CHOWN \
  --entrypoint /bin/sh caddy \
  -c 'find /data /config -path /data/caddy/locks -prune -o \
      -exec chown 1000:1000 {} +'

log "Starting API, scheduled collector, and web proxy."
docker compose up -d --remove-orphans

log "Waiting for the public health endpoint."
healthy=0
for _ in $(seq 1 100); do
  if curl -fsS --connect-timeout 5 --max-time 10 "$HEALTH_URL" \
    >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 3
done

cat > "$INSTALL_DIR/INSTALLATION.txt" <<EOF_SUMMARY
Global Media Discovery $VERSION
Website: $PUBLIC_URL
Installed: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Management: gmd status | gmd logs | gmd update | gmd backup
EOF_SUMMARY
chmod 0644 "$INSTALL_DIR/INSTALLATION.txt"

echo
if [ "$healthy" -eq 1 ]; then
  printf '\033[1;32mGlobal Media Discovery is ready.\033[0m\n'
else
  warn "Containers started, but the health endpoint did not answer within 5 minutes."
  if [ -n "$DOMAIN" ]; then
    warn "Confirm that $DOMAIN points to this VPS and that inbound TCP ports 80 and 443 are open."
  fi
  warn "Run: gmd logs"
  docker compose ps || true
fi
echo "Website: $PUBLIC_URL"
echo "Status:  gmd status"
echo "Logs:    gmd logs"
echo "Update:  gmd update"
echo "Backup:  gmd backup"
echo
if [ -z "$TMDB_TOKEN" ]; then
  warn "TMDB was skipped. Add a token to $INSTALL_DIR/secrets/tmdb_token and run: gmd restart"
fi
if [ -z "$TVDB_KEY" ]; then
  warn "TheTVDB was skipped. Add a key to $INSTALL_DIR/secrets/tvdb_key and run: gmd restart"
fi

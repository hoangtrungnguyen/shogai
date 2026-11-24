#!/usr/bin/env bash
# Docker mode for Shōgai: versioned builds, and deploys that back up the data before anything changes.
#
#   ./scripts/docker.sh [deploy]       build shogai:<VERSION>; stop the running version (its running jobs
#                                      finish first); back up the data folder; start the new version
#   ./scripts/docker.sh backup         back up the data folder now
#   ./scripts/docker.sh restore FILE   stop Shōgai and put a backup in place (the current data is backed up first)
#   ./scripts/docker.sh logs -f        anything else goes to `docker compose` (ps, down, build, …)
#
# Environment: SHOGAI_DATA_PATH (data folder, default ./data), SHOGAI_BACKUP_PATH (default ./backups),
# SHOGAI_BACKUP_KEEP (newest backups kept, default 20; 0 keeps all), SHOGAI_PORT (default 8000).
# Run an older image: SHOGAI_VERSION=0.1.0 docker compose up -d --no-build
set -euo pipefail
CALLER_DIR="$PWD"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

die() { echo "$*" >&2; exit 1; }

[ -f backend/.env ] || die "Missing backend/.env — copy backend/.env.example and set GEMINI_API_KEY"

VERSION="$(tr -d '[:space:]' < VERSION)"
[[ "$VERSION" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] \
  || die "VERSION must be a valid image tag (letters, digits, '.', '_', '-'), got: '$VERSION'"
export SHOGAI_VERSION="${SHOGAI_VERSION:-$VERSION}"

BACKUP_DIR="${SHOGAI_BACKUP_PATH:-backups}"
case "$BACKUP_DIR" in /*) ;; *) BACKUP_DIR="$ROOT/$BACKUP_DIR" ;; esac
KEEP="${SHOGAI_BACKUP_KEEP:-20}"
[[ "$KEEP" =~ ^[0-9]+$ ]] || die "SHOGAI_BACKUP_KEEP must be a number, got: '$KEEP'"

# Build stamp, passed to the image by docker-compose.yml.
SHOGAI_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then SHOGAI_COMMIT="$SHOGAI_COMMIT-dirty"; fi
SHOGAI_BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export SHOGAI_COMMIT SHOGAI_BUILD_DATE

# The host folder mounted at /app/data, exactly as docker compose resolves it.
require_data_dir() {
  DATA="$(docker compose config | awk '
    /^[[:space:]]*source:/ { src = $0; sub(/^[[:space:]]*source:[[:space:]]*/, "", src) }
    /^[[:space:]]*target:[[:space:]]*\/app\/data[[:space:]]*$/ { found = src }
    END { print found }')"
  [ -n "$DATA" ] && [ -d "$DATA" ] || die "No data folder at '${DATA:-?}'.
  First install?  mkdir -p '${DATA:-data}'
  Moved or re-cloned the repo?  Point SHOGAI_DATA_PATH at your existing data folder."
}

has_data() { [ -n "$(ls -A "$DATA")" ]; }
is_running() { [ -n "$(docker compose ps -q --status running shogai 2>/dev/null)" ]; }

# Version of the existing container (running or stopped), if there is one.
current_version() {
  local id v=""
  id="$(docker compose ps -a -q shogai 2>/dev/null | head -n 1)" || true
  [ -n "$id" ] || return 0
  v="$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.version"}}' "$id" 2>/dev/null)" || true
  [ "$v" != "<no value>" ] || v=""
  printf '%s' "$v" | tr -c 'A-Za-z0-9_.-' '_'
}

# Archive the data folder into BACKUP_DIR and print the archive's path. It only counts once it
# has been read back in full. .tmp holds in-flight uploads only.
backup() {
  local name tmp
  mkdir -p "$BACKUP_DIR"
  name="$BACKUP_DIR/$(date -u +%Y%m%dT%H%M%SZ)-$1.tar.gz"
  tmp="$name.partial"
  # COPYFILE_DISABLE and --no-xattrs keep macOS metadata out, so archives restore cleanly on Linux too.
  if COPYFILE_DISABLE=1 tar -czf "$tmp" --no-xattrs --exclude ./.tmp -C "$DATA" . && tar -tzf "$tmp" > /dev/null; then
    mv "$tmp" "$name"
    echo "$name"
  else
    rm -f "$tmp"
    return 1
  fi
}

# A backup needs at most the data folder's size (the archive is compressed). Checked before anything is
# stopped, so a full disk leaves the running version alone instead of failing half-way.
require_backup_space() {
  local need free
  mkdir -p "$BACKUP_DIR"
  need="$(du -sk "$DATA" | awk '{print $1}')"
  free="$(df -Pk "$BACKUP_DIR" | awk 'NR == 2 {print $4}')"
  [[ "$need" =~ ^[0-9]+$ && "$free" =~ ^[0-9]+$ ]] || return 0  # can't tell: let the backup try
  [ "$free" -gt "$need" ] || die "Not enough free disk space for a backup: it needs up to $((need / 1024 + 1)) MB, and only $((free / 1024)) MB is free in $BACKUP_DIR. Nothing was stopped or changed.
  Free some space (for example: docker builder prune), or set SHOGAI_BACKUP_PATH to a folder on another disk."
}

# Keep the newest $KEEP archives (by their timestamped names); 0 keeps all.
prune() {
  [ "$KEEP" -gt 0 ] || return 0
  local old
  for old in $(ls -1 "$BACKUP_DIR" | grep -E '^[0-9]{8}T[0-9]{6}Z-.+\.tar\.gz$' | sort -r | tail -n +"$((KEEP + 1))"); do
    rm -f "$BACKUP_DIR/$old" && echo "Removed old backup $old (keeping the newest $KEEP)"
  done
}

stop_running() {
  echo "Stopping Shōgai${1:+ $1}; running jobs get up to 5 minutes to finish…"
  docker compose stop
}

deploy() {
  if [ "$SHOGAI_VERSION" != "$VERSION" ]; then
    die "Deploy builds the version in VERSION ($VERSION). To run the existing $SHOGAI_VERSION image:
  SHOGAI_VERSION=$SHOGAI_VERSION docker compose up -d --no-build"
  fi
  require_data_dir
  local previous archive="" was_running=""
  previous="$(current_version)"

  echo "Building Shōgai $VERSION ($SHOGAI_COMMIT); the running version keeps serving meanwhile…"
  docker compose build

  if has_data; then require_backup_space; fi
  if is_running; then was_running=1; stop_running "$previous"; fi
  if has_data; then
    if ! archive="$(backup "deploy-${previous:+$previous-}to-$VERSION")"; then
      [ -z "$was_running" ] || docker compose start
      die "Backup failed, so nothing was deployed${was_running:+ (the previous version is starting again)}; the data folder is unchanged."
    fi
    echo "Backed up data → $archive"
    prune
  fi

  if ! docker compose up -d --wait --wait-timeout 120; then
    echo >&2
    echo "Shōgai $VERSION did not become healthy. Logs: ./scripts/docker.sh logs --tail 50" >&2
    if [ -n "$archive" ]; then
      echo "Data from before this deploy: $archive" >&2
      echo "To go back:  ./scripts/docker.sh restore '$archive'" >&2
      [ -z "$previous" ] || echo "             SHOGAI_VERSION=$previous docker compose up -d --no-build" >&2
    fi
    exit 1
  fi
  echo "Shōgai $VERSION ($SHOGAI_COMMIT) → http://127.0.0.1:${SHOGAI_PORT:-8000}"
}

backup_now() {
  require_data_dir
  has_data || die "The data folder $DATA is empty; nothing to back up."
  require_backup_space
  local archive
  archive="$(backup manual)" || die "Backup failed."
  echo "Backed up data → $archive"
  prune
}

restore() {
  local file="${1:-}" safety=""
  [ -n "$file" ] || die "Usage: ./scripts/docker.sh restore <backup.tar.gz>   (backups are in $BACKUP_DIR)"
  case "$file" in /*) ;; *) file="$CALLER_DIR/$file" ;; esac
  [ -f "$file" ] || die "No such backup: $file"
  tar -tzf "$file" > /dev/null || die "Cannot read $file; nothing was changed."
  require_data_dir
  if has_data && [ ! -d "$DATA/people" ]; then
    die "$DATA does not look like a Shōgai data folder (no people/ inside); nothing was changed."
  fi

  if has_data; then require_backup_space; fi
  if is_running; then stop_running; fi
  if has_data; then
    safety="$(backup before-restore)" || die "Could not back up the current data; nothing was changed."
    echo "Backed up current data → $safety"
  fi
  find "$DATA" -mindepth 1 -delete \
    || die "Could not empty $DATA.${safety:+ Your previous data is in $safety.}"
  tar -xzf "$file" -C "$DATA" \
    || die "Restore failed part-way.${safety:+ Your previous data is in $safety:  ./scripts/docker.sh restore '$safety'}"
  echo "Restored $(basename "$file") into $DATA. Shōgai is stopped; start it with one of:"
  echo "  ./scripts/docker.sh                                         this checkout ($VERSION)"
  echo "  SHOGAI_VERSION=<version> docker compose up -d --no-build    an image you built before"
}

case "${1:-deploy}" in
  deploy) deploy ;;
  backup) backup_now ;;
  restore) restore "${2:-}" ;;
  *)
    if [ "$SHOGAI_VERSION" != "$VERSION" ] && [[ " $* " == *" build "* || " $* " == *" --build "* ]]; then
      die "Refusing to build this checkout ($VERSION) as $SHOGAI_VERSION."
    fi
    exec docker compose "$@"
    ;;
esac

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SOURCE_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
TARGET_ROOT="${1:-$(dirname -- "$SOURCE_ROOT")}"
NETWORK_NAME="media-automation"

if [ ! -f "$SOURCE_ROOT/Dockerfile" ] || [ ! -f "$SOURCE_ROOT/telegram-bot-api" ]; then
  echo "ERROR: Dockerfile or Linux telegram-bot-api binary is missing from $SOURCE_ROOT" >&2
  exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$SOURCE_ROOT" && sha256sum -c telegram-bot-api.sha256)
fi

mkdir -p "$TARGET_ROOT"

escape_sed() {
  printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

source_escaped="$(escape_sed "$SOURCE_ROOT")"
target_escaped="$(escape_sed "$TARGET_ROOT")"

sync_stack() {
  stack="$1"
  source_dir="$SCRIPT_DIR/$stack"
  target_dir="$TARGET_ROOT/$stack"
  mkdir -p "$target_dir"
  cp "$source_dir/docker-compose.yml" "$target_dir/docker-compose.yml"
  sed \
    -e "s|__SOURCE_ROOT__|$source_escaped|g" \
    -e "s|__STACK_ROOT__|$target_escaped|g" \
    "$source_dir/.env.example" > "$target_dir/.env.example"
  if [ ! -f "$target_dir/.env" ]; then
    cp "$target_dir/.env.example" "$target_dir/.env"
    echo "Created $target_dir/.env; edit its placeholder values before starting."
  fi
}

sync_stack video-manager-compose
sync_stack telegram-bot-api-compose
sync_stack n8n-compose

mkdir -p \
  "$TARGET_ROOT/video-manager-compose/data/fuzzy-search" \
  "$TARGET_ROOT/video-manager-compose/logs" \
  "$TARGET_ROOT/video-manager-compose/staging" \
  "$TARGET_ROOT/telegram-bot-api-compose/data" \
  "$TARGET_ROOT/n8n-compose/data"

if [ "$(id -u)" = "0" ]; then
  chown -R 1000:1000 "$TARGET_ROOT/n8n-compose/data"
fi

if command -v docker >/dev/null 2>&1; then
  if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    docker network create "$NETWORK_NAME" >/dev/null
    echo "Created Docker network $NETWORK_NAME."
  fi
else
  echo "WARNING: docker was not found; create the $NETWORK_NAME network manually."
fi

echo
echo "NAS layout synchronized under: $TARGET_ROOT"
echo "The source clone remains at: $SOURCE_ROOT"
echo "Next: edit each .env file, then follow $SOURCE_ROOT/nas/README.md"

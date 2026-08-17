#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=../release.env
# shellcheck disable=SC1091
source "$ROOT_DIR/release.env"

WEB_VERSION="$RF_MAPPING_WEB_VERSION"
RELEASE_FLAVOR="$RF_MAPPING_WEB_RELEASE_FLAVOR"
DIST_DIR="$ROOT_DIR/dist"
ARCHIVE_NAME="RF_Map_Viewer-$WEB_VERSION-$RELEASE_FLAVOR.tar.gz"
CHECKSUM_NAME="SHA256SUMS-$RELEASE_FLAVOR-$WEB_VERSION.txt"
ARCHIVE_PATH="$DIST_DIR/$ARCHIVE_NAME"
CHECKSUM_PATH="$DIST_DIR/$CHECKSUM_NAME"
STAGE_ROOT="$(mktemp -d /tmp/rfmapping-web-release.XXXXXX)"
PAYLOAD_ROOT="$STAGE_ROOT/RF_Map_Viewer-Web-$WEB_VERSION"

cleanup() {
  case "$STAGE_ROOT" in
    /tmp/rfmapping-web-release.*) rm -rf -- "$STAGE_ROOT" ;;
    *) echo "refusing unsafe Web release cleanup: $STAGE_ROOT" >&2 ;;
  esac
}
trap cleanup EXIT HUP INT TERM

test -d "$ROOT_DIR/frontend/dist"
test -f "$ROOT_DIR/frontend/dist/index.html"
mkdir -p "$DIST_DIR" "$PAYLOAD_ROOT"

cp "$ROOT_DIR/README.md" "$PAYLOAD_ROOT/README.md"
cp "$ROOT_DIR/pyproject.toml" "$PAYLOAD_ROOT/pyproject.toml"
cp "$ROOT_DIR/requirements.txt" "$PAYLOAD_ROOT/requirements.txt"
cp "$ROOT_DIR/release.env" "$PAYLOAD_ROOT/release.env"
rsync -a \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  "$ROOT_DIR/backend/" "$PAYLOAD_ROOT/backend/"
rsync -a "$ROOT_DIR/deploy/" "$PAYLOAD_ROOT/deploy/"
rsync -a \
  --exclude='node_modules' \
  --exclude='coverage' \
  "$ROOT_DIR/frontend/" "$PAYLOAD_ROOT/frontend/"

tar \
  --sort=name \
  --mtime='UTC 1970-01-01' \
  --owner=0 \
  --group=0 \
  --numeric-owner \
  -C "$STAGE_ROOT" \
  -czf "$ARCHIVE_PATH" \
  "$(basename "$PAYLOAD_ROOT")"
(
  cd "$DIST_DIR"
  sha256sum "$ARCHIVE_NAME" >"$CHECKSUM_NAME"
  sha256sum -c "$CHECKSUM_NAME"
)

test -s "$ARCHIVE_PATH"
test -s "$CHECKSUM_PATH"
tar -tzf "$ARCHIVE_PATH" >/dev/null
echo "Created Web $WEB_VERSION release archive: $ARCHIVE_PATH"
echo "Created checksum: $CHECKSUM_PATH"

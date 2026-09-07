#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=python_macos_release.env
source "$SCRIPT_DIR/python_macos_release.env"

APP_NAME="$RF_MAPPING_APP_NAME"
EXECUTABLE_NAME="$RF_MAPPING_EXECUTABLE_NAME"
BUNDLE_ID="$RF_MAPPING_BUNDLE_ID"

ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist/python"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
APP_BINARY="$APP_BUNDLE/Contents/MacOS/$EXECUTABLE_NAME"
BUILD_SCRIPT="$ROOT_DIR/script/build_python_macos_app.sh"

case "$MODE" in
  run|--bundle|bundle|--debug|debug|--logs|logs|--verify|verify)
    ;;
  *)
    echo "usage: $0 [run|--bundle|--debug|--logs|--verify]" >&2
    exit 2
    ;;
esac

case "$MODE" in
  run|--debug|debug|--logs|logs|--verify|verify)
    pkill -x "$EXECUTABLE_NAME" >/dev/null 2>&1 || true
    ;;
esac

"$BUILD_SCRIPT"

[[ -x "$APP_BINARY" ]] || {
  echo "error: built application executable is missing: $APP_BINARY" >&2
  exit 1
}

open_app() {
  /usr/bin/open "$APP_BUNDLE"
}

case "$MODE" in
  run)
    open_app
    ;;
  --bundle|bundle)
    ;;
  --debug|debug)
    lldb -- "$APP_BINARY"
    ;;
  --logs|logs)
    open_app
    /usr/bin/log stream --info --style compact --predicate "process == '$EXECUTABLE_NAME'"
    ;;
  --verify|verify)
    open_app
    sleep 1
    pgrep -f "$APP_BINARY" >/dev/null
    codesign --verify --deep --strict "$APP_BUNDLE"
    /usr/bin/mdls -name kMDItemVersion "$APP_BUNDLE"
    ;;
esac

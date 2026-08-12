#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=python_macos_release.env
source "$SCRIPT_DIR/python_macos_release.env"

APP_NAME="$RF_MAPPING_APP_NAME"
BUNDLE_ID="$RF_MAPPING_BUNDLE_ID"
APP_VERSION="$RF_MAPPING_APP_VERSION"
APP_BUILD="$RF_MAPPING_APP_BUILD"
APP_ARCHITECTURE="$RF_MAPPING_APP_ARCHITECTURE"
PYINSTALLER_VERSION="6.21.0"
NUMPY_VERSION="2.4.6"
PILLOW_VERSION="12.3.0"

ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/Library/Frameworks/Python.framework/Versions/3.14/bin/python3}"
BUILD_VENV="${RF_MAPPING_BUILD_VENV:-$HOME/Library/Caches/rfmapping-pyinstaller-3.14}"
WORK_DIR="${RF_MAPPING_BUILD_WORK:-$HOME/Library/Caches/rfmapping-pyinstaller-work}"
DIST_DIR="$ROOT_DIR/dist/python"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
APP_BINARY="$APP_BUNDLE/Contents/MacOS/$APP_NAME"
APP_RESOURCES="$APP_BUNDLE/Contents/Resources"
ARCHIVE_PATH="$DIST_DIR/RF_Map_Viewer-python-macos-arm64.zip"
INFO_PLIST="$APP_BUNDLE/Contents/Info.plist"
DATA_SOURCE="$ROOT_DIR/data"
ICON_MASTER="${RF_MAPPING_ICON_SOURCE:-$ROOT_DIR/assets/rf-mapping-viewer-icon-1024.png}"
ICONSET_DIR="$WORK_DIR/RFMappingViewer.iconset"
ICON_ICNS="$WORK_DIR/RFMappingViewer.icns"
PLIST_BUDDY=/usr/libexec/PlistBuddy

fail() {
  echo "error: $*" >&2
  exit 1
}

require_file() {
  [[ -f "$1" ]] || fail "Required file not found: $1"
}

require_nonempty_file() {
  [[ -s "$1" ]] || fail "Required non-empty file not found: $1"
}

require_safe_removal_target() {
  local target="${1%/}"
  [[ -n "$target" && "$target" == /* ]] \
    || fail "Refusing to remove a non-absolute build path: $1"
  case "$target" in
    /|"$HOME"|"$ROOT_DIR"|"$DIST_DIR"|"${BUILD_VENV%/}")
      fail "Refusing unsafe build cleanup target: $target"
      ;;
  esac
}

canonical_scoped_build_cache_path() {
  local label="$1"
  local target="${2%/}"
  local parent
  local name

  [[ -n "$target" && "$target" == /* ]] \
    || fail "$label must be an absolute path: $2"
  [[ "$target" != *"/../"* && "$target" != */.. && "$target" != *"/./"* && "$target" != */. ]] \
    || fail "$label may not contain dot traversal components: $target"
  name="$(basename "$target")"
  [[ "$name" == rfmapping-* ]] \
    || fail "$label basename must start with 'rfmapping-': $target"
  parent="$(dirname "$target")"
  [[ -d "$parent" ]] || fail "$label parent directory does not exist: $parent"
  parent="$(cd -P "$parent" && pwd)"
  target="$parent/$name"
  [[ "$parent" != "/" && "$parent" != "$HOME" && "$parent" != "$ROOT_DIR" && "$parent" != "$DIST_DIR" ]] \
    || fail "Refusing broad $label parent: $parent"
  case "$target/" in
    "$ROOT_DIR/"*|"$DIST_DIR/"*)
      fail "$label may not be inside the source or distribution tree: $target"
      ;;
  esac
  [[ ! -L "$target" ]] || fail "$label may not be a symlink: $target"
  printf '%s\n' "$target"
}

validate_distribution_tree() {
  local root_physical
  local dist_parent="$ROOT_DIR/dist"
  local dist_parent_physical
  local dist_physical

  root_physical="$(cd -P "$ROOT_DIR" && pwd)"
  [[ ! -L "$dist_parent" ]] \
    || fail "Distribution parent may not be a symlink: $dist_parent"
  if [[ -e "$dist_parent" ]]; then
    [[ -d "$dist_parent" ]] \
      || fail "Distribution parent is not a directory: $dist_parent"
  else
    mkdir "$dist_parent"
  fi
  dist_parent_physical="$(cd -P "$dist_parent" && pwd)"
  [[ "$dist_parent_physical" == "$root_physical/dist" ]] \
    || fail "Distribution parent escapes the physical source tree: $dist_parent_physical"

  [[ ! -L "$DIST_DIR" ]] \
    || fail "Distribution directory may not be a symlink: $DIST_DIR"
  if [[ -e "$DIST_DIR" ]]; then
    [[ -d "$DIST_DIR" ]] \
      || fail "Distribution path is not a directory: $DIST_DIR"
  else
    mkdir "$DIST_DIR"
  fi
  dist_physical="$(cd -P "$DIST_DIR" && pwd)"
  [[ "$dist_physical" == "$root_physical/dist/python" ]] \
    || fail "Distribution directory escapes the physical source tree: $dist_physical"
}

verify_plist_value() {
  local key="$1"
  local expected="$2"
  local actual
  actual="$("$PLIST_BUDDY" -c "Print :$key" "$INFO_PLIST")"
  [[ "$actual" == "$expected" ]] \
    || fail "Info.plist $key is '$actual'; expected '$expected'"
}

verify_arm64_macho_files() {
  local candidate
  local description
  local architectures

  while IFS= read -r -d '' candidate; do
    description="$(file -b "$candidate")"
    [[ "$description" == *"Mach-O"* ]] || continue
    architectures="$(lipo -archs "$candidate" 2>/dev/null)" \
      || fail "Unable to inspect architectures: $candidate"
    [[ "$architectures" == "$APP_ARCHITECTURE" ]] \
      || fail "Mach-O file is not $APP_ARCHITECTURE-only: $candidate ($architectures)"
  done < <(find "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Frameworks" -type f -print0)
}

# Keep path-safety helpers sourceable by the Linux fixture audit without
# invoking any macOS build tools.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  return 0
fi

require_file "$ROOT_DIR/rfmapping_gui.py"
require_file "$ICON_MASTER"
[[ -x "$PLIST_BUDDY" ]] || fail "PlistBuddy not found: $PLIST_BUDDY"
validate_distribution_tree
BUILD_VENV="$(canonical_scoped_build_cache_path "Build virtual environment" "$BUILD_VENV")"
WORK_DIR="$(canonical_scoped_build_cache_path "Build work directory" "$WORK_DIR")"
case "$BUILD_VENV/" in
  "$WORK_DIR/"*) fail "Build virtual environment may not be inside the work directory" ;;
esac
case "$WORK_DIR/" in
  "$BUILD_VENV/"*) fail "Build work directory may not be inside the virtual environment" ;;
esac

if [[ ! -x "$PYTHON_BIN" ]]; then
  fail "Python not found: $PYTHON_BIN"
fi

if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$BUILD_VENV"
fi

if ! "$BUILD_VENV/bin/python" -c "import PyInstaller, numpy, PIL; raise SystemExit(PyInstaller.__version__ != '$PYINSTALLER_VERSION' or numpy.__version__ != '$NUMPY_VERSION' or PIL.__version__ != '$PILLOW_VERSION')"; then
  "$BUILD_VENV/bin/python" -m pip install --disable-pip-version-check \
    "pyinstaller==$PYINSTALLER_VERSION" \
    "numpy==$NUMPY_VERSION" \
    "pillow==$PILLOW_VERSION"
fi

require_safe_removal_target "$APP_BUNDLE"
require_safe_removal_target "$WORK_DIR"
# Recheck immediately before destructive cleanup. In particular, never follow a
# dist or dist/python symlink even if it appeared after initial validation.
validate_distribution_tree
rm -rf "$APP_BUNDLE" "$WORK_DIR"
rm -f "$ARCHIVE_PATH"
mkdir -p "$DIST_DIR" "$WORK_DIR"

ICON_WIDTH="$(sips -g pixelWidth "$ICON_MASTER" | awk '/pixelWidth/ {print $2}')"
ICON_HEIGHT="$(sips -g pixelHeight "$ICON_MASTER" | awk '/pixelHeight/ {print $2}')"
if [[ "$ICON_WIDTH" != "1024" || "$ICON_HEIGHT" != "1024" ]]; then
  fail "App icon source must be 1024x1024 pixels: $ICON_MASTER"
fi

mkdir -p "$ICONSET_DIR"
sips -z 16 16 "$ICON_MASTER" --out "$ICONSET_DIR/icon_16x16.png" >/dev/null
sips -z 32 32 "$ICON_MASTER" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$ICON_MASTER" --out "$ICONSET_DIR/icon_32x32.png" >/dev/null
sips -z 64 64 "$ICON_MASTER" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$ICON_MASTER" --out "$ICONSET_DIR/icon_128x128.png" >/dev/null
sips -z 256 256 "$ICON_MASTER" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$ICON_MASTER" --out "$ICONSET_DIR/icon_256x256.png" >/dev/null
sips -z 512 512 "$ICON_MASTER" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$ICON_MASTER" --out "$ICONSET_DIR/icon_512x512.png" >/dev/null
cp "$ICON_MASTER" "$ICONSET_DIR/icon_512x512@2x.png"
iconutil -c icns "$ICONSET_DIR" -o "$ICON_ICNS"

run_pyinstaller() {
  "$BUILD_VENV/bin/pyinstaller" \
    --noconfirm \
    --clean \
    --windowed \
    --onedir \
    --target-architecture "$APP_ARCHITECTURE" \
    --name "$APP_NAME" \
    --osx-bundle-identifier "$BUNDLE_ID" \
    --icon "$ICON_ICNS" \
    --distpath "$DIST_DIR" \
    --workpath "$WORK_DIR/build" \
    --specpath "$WORK_DIR" \
    "$@" \
    "$ROOT_DIR/rfmapping_gui.py"
}

# macOS still ships Bash 3.2, where expanding an empty array under `set -u`
# raises an "unbound variable" error. Pass the optional data argument through
# positional parameters so the no-data release path remains valid.
if [[ -d "$DATA_SOURCE" ]]; then
  run_pyinstaller --add-data "$DATA_SOURCE:data"
else
  run_pyinstaller
fi

"$PLIST_BUDDY" -c "Set :CFBundleDisplayName $APP_NAME" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Set :CFBundleShortVersionString $APP_VERSION" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Delete :CFBundleVersion" "$INFO_PLIST" 2>/dev/null || true
"$PLIST_BUDDY" -c "Add :CFBundleVersion string $APP_BUILD" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Delete :LSMultipleInstancesProhibited" "$INFO_PLIST" 2>/dev/null || true
"$PLIST_BUDDY" -c "Add :LSMultipleInstancesProhibited bool true" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Delete :CFBundleDocumentTypes" "$INFO_PLIST" 2>/dev/null || true
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0 dict" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string 'RF Mapping JSON'" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Viewer" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:LSHandlerRank string Alternate" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 string json" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:0 string public.json" "$INFO_PLIST"

plutil -lint "$INFO_PLIST" >/dev/null
verify_plist_value CFBundleDisplayName "$APP_NAME"
verify_plist_value CFBundleExecutable "$APP_NAME"
verify_plist_value CFBundleIdentifier "$BUNDLE_ID"
verify_plist_value CFBundleShortVersionString "$APP_VERSION"
verify_plist_value CFBundleVersion "$APP_BUILD"
verify_plist_value LSMultipleInstancesProhibited true
verify_plist_value CFBundleDocumentTypes:0:CFBundleTypeRole Viewer
verify_plist_value CFBundleDocumentTypes:0:LSHandlerRank Alternate
verify_plist_value CFBundleDocumentTypes:0:LSItemContentTypes:0 public.json
verify_plist_value CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 json

require_nonempty_file "$APP_BINARY"
require_nonempty_file "$APP_RESOURCES/RFMappingViewer.icns"
if [[ -d "$DATA_SOURCE" ]]; then
  [[ -d "$APP_RESOURCES/data" ]] || fail "Bundled data directory is missing"
  SOURCE_JSON_COUNT="$(find "$DATA_SOURCE" -type f -name '*.json' | wc -l | tr -d '[:space:]')"
  BUNDLED_JSON_COUNT="$(find "$APP_RESOURCES/data" -type f -name '*.json' | wc -l | tr -d '[:space:]')"
  [[ "$SOURCE_JSON_COUNT" -gt 0 ]] || fail "No JSON resources found in $DATA_SOURCE"
  [[ "$BUNDLED_JSON_COUNT" == "$SOURCE_JSON_COUNT" ]] \
    || fail "Bundled JSON resource count does not match source"
  diff -qr -x '.DS_Store' -x '._*' "$DATA_SOURCE" "$APP_RESOURCES/data" >/dev/null \
    || fail "Bundled data does not match $DATA_SOURCE"
fi
verify_arm64_macho_files

codesign --force --deep --sign - "$APP_BUNDLE"
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

# PyInstaller creates the bundle before the document metadata above is added.
# Unregister the build-tree copy so Finder never caches that transient bundle
# (version 0.0.0, with no JSON claim) instead of the installed application.
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
if [[ -x "$LSREGISTER" ]]; then
  "$LSREGISTER" -u "$APP_BUNDLE" >/dev/null 2>&1 || true
fi

ditto -c -k --sequesterRsrc --keepParent "$APP_BUNDLE" "$ARCHIVE_PATH"
require_nonempty_file "$ARCHIVE_PATH"
unzip -tq "$ARCHIVE_PATH" >/dev/null \
  || fail "Archive integrity check failed: $ARCHIVE_PATH"

echo "Built Python $APP_VERSION Apple-silicon app: $APP_BUNDLE"
echo "Created archive: $ARCHIVE_PATH"

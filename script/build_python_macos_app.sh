#!/usr/bin/env bash
set -euo pipefail

APP_NAME="RF Map Viewer"
ARTIFACT_STEM="RF_Map_Viewer"
BUNDLE_ID="org.local.rfmapping.viewer"
APP_VERSION="${RF_MAP_VIEWER_VERSION:-1.8.2}"
APP_BUILD="${RF_MAP_VIEWER_BUILD:-10802}"
PYINSTALLER_VERSION="${RF_MAPPING_PYINSTALLER_VERSION:-6.21.0}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${RF_MAPPING_PYTHON_BIN:-${PYTHON_BIN:-python3}}"
BUILD_VENV="${RF_MAPPING_BUILD_VENV:-}"
WORK_DIR="${RF_MAPPING_BUILD_WORK:-$HOME/Library/Caches/rfmapping-pyinstaller-work}"
DIST_DIR="$ROOT_DIR/dist/python"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
APP_BINARY="$APP_BUNDLE/Contents/MacOS/$APP_NAME"
APP_RESOURCES="$APP_BUNDLE/Contents/Resources"
ARCHIVE_PATH="$DIST_DIR/$ARTIFACT_STEM-python-macos-arm64.zip"
INFO_PLIST="$APP_BUNDLE/Contents/Info.plist"
DATA_SOURCE="$ROOT_DIR/data"
RUNTIME_REQUIREMENTS="$ROOT_DIR/requirements-python-runtime.txt"
SUPPORT_DOCUMENTATION="$ROOT_DIR/README.md"
PYINSTALLER_HOOKS="$ROOT_DIR/packaging/pyinstaller-hooks"
ICON_MASTER="${RF_MAPPING_ICON_SOURCE:-$ROOT_DIR/assets/rf-mapping-viewer-icon-1024.png}"
ICONSET_DIR="$WORK_DIR/RFMappingViewer.iconset"
ICON_ICNS="$WORK_DIR/RFMappingViewer.icns"
PLIST_BUDDY=/usr/libexec/PlistBuddy
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
SIGNING_IDENTITY="${RF_MAPPING_CODESIGN_IDENTITY:-${CODE_SIGN_IDENTITY:--}}"

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

cleanup_staged_bundle() {
  if [[ -d "$APP_BUNDLE" && -x "$LSREGISTER" ]]; then
    "$LSREGISTER" -u "$APP_BUNDLE" >/dev/null 2>&1 || true
  fi
  rm -rf "$APP_BUNDLE"
}

# The staged bundle claims public.json while it is being packaged. Always
# remove it, including after a failed build, so Finder cannot rediscover it as
# a second Open With entry alongside the copy installed in /Applications.
trap cleanup_staged_bundle EXIT

verify_plist_value() {
  local key="$1"
  local expected="$2"
  local actual
  actual="$("$PLIST_BUDDY" -c "Print :$key" "$INFO_PLIST")"
  [[ "$actual" == "$expected" ]] \
    || fail "Info.plist $key is '$actual'; expected '$expected'"
}

clean_bundle_metadata() {
  local bundle="$1"

  find "$bundle" -type f \( -name '.DS_Store' -o -name '._*' \) -delete
  /usr/bin/xattr -cr "$bundle"
}

verify_archive_metadata() {
  local archive="$1"
  local entry

  while IFS= read -r entry; do
    case "$entry" in
      __MACOSX/*|*/__MACOSX/*|._*|*/._*)
        fail "Archive contains AppleDouble metadata: $entry"
        ;;
    esac
  done < <(unzip -Z1 "$archive")
}

verify_arm64_macho_files() {
  local candidate
  local description
  local architectures
  local macho_count=0

  while IFS= read -r -d '' candidate; do
    description="$(file -b "$candidate")"
    [[ "$description" == *"Mach-O"* ]] || continue
    ((macho_count += 1))
    architectures="$(lipo -archs "$candidate" 2>/dev/null)" \
      || fail "Unable to inspect architectures: $candidate"
    [[ "$architectures" == "arm64" ]] \
      || fail "Mach-O file is not arm64-only: $candidate ($architectures)"
  done < <(find "$APP_BUNDLE" -type f -print0)

  [[ "$macho_count" -gt 0 ]] || fail "No Mach-O files found in app bundle"
}

require_file "$ROOT_DIR/rfmapping_gui.py"
require_file "$ICON_MASTER"
require_file "$RUNTIME_REQUIREMENTS"
require_file "$SUPPORT_DOCUMENTATION"
require_file "$PYINSTALLER_HOOKS/hook-tkinterdnd2.py"
[[ -d "$DATA_SOURCE" ]] || fail "Data directory not found: $DATA_SOURCE"
[[ -x "$PLIST_BUDDY" ]] || fail "PlistBuddy not found: $PLIST_BUDDY"
[[ "$(uname -m)" == "arm64" ]] || fail "Python macOS builds require an Apple silicon host"

if [[ "$PYTHON_BIN" != */* ]]; then
  PYTHON_BIN="$(command -v "$PYTHON_BIN" || true)"
fi

if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  fail "Python not found: $PYTHON_BIN"
fi

PYTHON_MACHINE="$("$PYTHON_BIN" -c 'import platform; print(platform.machine())')"
[[ "$PYTHON_MACHINE" == "arm64" ]] \
  || fail "Python must run natively as arm64; got $PYTHON_MACHINE"
PYTHON_VERSION_TAG="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
[[ "$PYTHON_VERSION_TAG" == "3.14" ]] \
  || fail "Python 3.14 is required; got $PYTHON_VERSION_TAG"

if [[ -z "$BUILD_VENV" ]]; then
  BUILD_VENV="$HOME/Library/Caches/rf-map-viewer-pyinstaller-$PYTHON_VERSION_TAG-arm64"
fi

if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$BUILD_VENV"
fi

BUILD_PYTHON_TARGET="$("$BUILD_VENV/bin/python" -c 'import platform, sys; print(f"{sys.version_info.major}.{sys.version_info.minor}-{platform.machine()}")')"
[[ "$BUILD_PYTHON_TARGET" == "3.14-arm64" ]] \
  || fail "Build virtual environment must use Python 3.14 arm64; got $BUILD_PYTHON_TARGET"

"$BUILD_VENV/bin/python" -m pip install \
  --disable-pip-version-check \
  "pyinstaller==$PYINSTALLER_VERSION" \
  --requirement "$RUNTIME_REQUIREMENTS"
"$BUILD_VENV/bin/python" -c \
  'import tkinterdnd2; print("runtime dependency:", tkinterdnd2.__file__)'

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

"$BUILD_VENV/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --windowed \
  --onedir \
  --target-architecture arm64 \
  --name "$APP_NAME" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --icon "$ICON_ICNS" \
  --distpath "$DIST_DIR" \
  --workpath "$WORK_DIR/build" \
  --specpath "$WORK_DIR" \
  --additional-hooks-dir "$PYINSTALLER_HOOKS" \
  --add-data "$ROOT_DIR/data:data" \
  --add-data "$SUPPORT_DOCUMENTATION:." \
  "$ROOT_DIR/rfmapping_gui.py"

"$PLIST_BUDDY" -c "Set :CFBundleDisplayName $APP_NAME" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Set :CFBundleShortVersionString $APP_VERSION" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Delete :CFBundleVersion" "$INFO_PLIST" 2>/dev/null || true
"$PLIST_BUDDY" -c "Add :CFBundleVersion string $APP_BUILD" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Delete :LSMultipleInstancesProhibited" "$INFO_PLIST" 2>/dev/null || true
"$PLIST_BUDDY" -c "Add :LSMultipleInstancesProhibited bool true" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Delete :LSMinimumSystemVersion" "$INFO_PLIST" 2>/dev/null || true
"$PLIST_BUDDY" -c "Add :LSMinimumSystemVersion string 14.0" "$INFO_PLIST"
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
verify_plist_value LSMinimumSystemVersion 14.0
verify_plist_value CFBundleDocumentTypes:0:CFBundleTypeRole Viewer
verify_plist_value CFBundleDocumentTypes:0:LSHandlerRank Alternate
verify_plist_value CFBundleDocumentTypes:0:LSItemContentTypes:0 public.json
verify_plist_value CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 json

require_nonempty_file "$APP_BINARY"
require_nonempty_file "$APP_RESOURCES/RFMappingViewer.icns"
require_nonempty_file "$APP_RESOURCES/README.md"
[[ -d "$APP_RESOURCES/data" ]] || fail "Bundled data directory is missing"
SOURCE_JSON_COUNT="$(find "$DATA_SOURCE" -type f -name '*.json' | wc -l | tr -d '[:space:]')"
BUNDLED_JSON_COUNT="$(find "$APP_RESOURCES/data" -type f -name '*.json' | wc -l | tr -d '[:space:]')"
[[ "$SOURCE_JSON_COUNT" -gt 0 ]] || fail "No JSON resources found in $DATA_SOURCE"
[[ "$BUNDLED_JSON_COUNT" == "$SOURCE_JSON_COUNT" ]] \
  || fail "Bundled JSON resource count does not match source"
diff -qr -x '.DS_Store' -x '._*' "$DATA_SOURCE" "$APP_RESOURCES/data" >/dev/null \
  || fail "Bundled data does not match $DATA_SOURCE"
verify_arm64_macho_files

"$APP_BINARY" --self-test "$DATA_SOURCE/demo_rf_map.json"
"$APP_BINARY" --self-test-dnd

clean_bundle_metadata "$APP_BUNDLE"
SIGN_ARGUMENTS=(--force --deep --sign "$SIGNING_IDENTITY")
if [[ "$SIGNING_IDENTITY" != "-" ]]; then
  SIGN_ARGUMENTS+=(--options runtime --timestamp)
fi
codesign "${SIGN_ARGUMENTS[@]}" "$APP_BUNDLE"
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

ditto -c -k --norsrc --keepParent "$APP_BUNDLE" "$ARCHIVE_PATH"
require_nonempty_file "$ARCHIVE_PATH"
unzip -tq "$ARCHIVE_PATH" >/dev/null \
  || fail "Archive integrity check failed: $ARCHIVE_PATH"
verify_archive_metadata "$ARCHIVE_PATH"

cleanup_staged_bundle
trap - EXIT

echo "Packaged Python Apple silicon app and removed the staged bundle: $APP_BUNDLE"
echo "Created archive: $ARCHIVE_PATH"

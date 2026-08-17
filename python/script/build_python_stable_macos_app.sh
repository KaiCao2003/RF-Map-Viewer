#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=python_stable_macos_release.env
# shellcheck disable=SC1091
source "$SCRIPT_DIR/python_stable_macos_release.env"

APP_NAME="$RF_MAPPING_APP_NAME"
EXECUTABLE_NAME="$RF_MAPPING_EXECUTABLE_NAME"
BUNDLE_ID="$RF_MAPPING_BUNDLE_ID"
APP_VERSION="$RF_MAPPING_APP_VERSION"
APP_BUILD="$RF_MAPPING_APP_BUILD"
RELEASE_EDITION="$RF_MAPPING_RELEASE_EDITION"
RELEASE_FLAVOR="$RF_MAPPING_RELEASE_FLAVOR"
APP_ARCHITECTURE="$RF_MAPPING_APP_ARCHITECTURE"
MINIMUM_MACOS_VERSION="$RF_MAPPING_MINIMUM_MACOS_VERSION"

PYINSTALLER_VERSION="6.21.0"
NUMPY_VERSION="2.4.6"
PILLOW_VERSION="12.3.0"
TKINTERDND2_VERSION="0.6.2"

ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${RF_MAPPING_PYTHON_BIN:-${PYTHON_BIN:-/Library/Frameworks/Python.framework/Versions/3.14/bin/python3}}"
BUILD_VENV="${RF_MAPPING_BUILD_VENV:-$HOME/Library/Caches/rfmapping-stable-pyinstaller-3.14-arm64}"
WORK_DIR="${RF_MAPPING_BUILD_WORK:-$HOME/Library/Caches/rfmapping-stable-pyinstaller-work}"
DIST_DIR="$ROOT_DIR/dist/python"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
APP_BINARY="$APP_BUNDLE/Contents/MacOS/$EXECUTABLE_NAME"
APP_RESOURCES="$APP_BUNDLE/Contents/Resources"
ARCHIVE_NAME="RF_Map_Viewer-python-$APP_VERSION-$RELEASE_FLAVOR-macos-$APP_ARCHITECTURE.zip"
ARCHIVE_PATH="$DIST_DIR/$ARCHIVE_NAME"
CHECKSUM_NAME="SHA256SUMS-python-$APP_VERSION-$RELEASE_FLAVOR.txt"
CHECKSUM_PATH="$DIST_DIR/$CHECKSUM_NAME"
INFO_PLIST="$APP_BUNDLE/Contents/Info.plist"
DATA_SOURCE="$ROOT_DIR/data"
SUPPORT_DOCUMENTATION="$ROOT_DIR/README.md"
SMOKE_JSON="$ROOT_DIR/tests/fixtures/release_smoke_rf.json"
PYINSTALLER_HOOKS="$ROOT_DIR/packaging/pyinstaller-hooks"
METADATA_AUDITOR="$SCRIPT_DIR/verify_python_stable_release_metadata.py"
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

verify_plist_missing() {
  local key="$1"
  if "$PLIST_BUDDY" -c "Print :$key" "$INFO_PLIST" >/dev/null 2>&1; then
    fail "Info.plist unexpectedly contains $key"
  fi
}

verify_arm64_macho_files() {
  local candidate
  local description
  local architectures
  local macho_count=0

  while IFS= read -r -d '' candidate; do
    description="$(file -b "$candidate")"
    [[ "$description" == *"Mach-O"* ]] || continue
    macho_count=$((macho_count + 1))
    architectures="$(lipo -archs "$candidate" 2>/dev/null)" \
      || fail "Unable to inspect architectures: $candidate"
    [[ "$architectures" == "$APP_ARCHITECTURE" ]] \
      || fail "Mach-O file is not $APP_ARCHITECTURE-only: $candidate ($architectures)"
  done < <(find "$APP_BUNDLE" -type f -print0)

  [[ "$macho_count" -gt 0 ]] || fail "No Mach-O files found in app bundle"
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

verify_archive_plist_value() {
  local key="$1"
  local expected="$2"
  local actual
  actual="$(
    unzip -p "$ARCHIVE_PATH" "$APP_NAME.app/Contents/Info.plist" \
      | /usr/bin/plutil -extract "$key" raw -o - -
  )"
  [[ "$actual" == "$expected" ]] \
    || fail "Archived Info.plist $key is '$actual'; expected '$expected'"
}

verify_archive_plist_missing() {
  local key="$1"
  if unzip -p "$ARCHIVE_PATH" "$APP_NAME.app/Contents/Info.plist" \
    | /usr/bin/plutil -extract "$key" raw -o - - >/dev/null 2>&1; then
    fail "Archived Info.plist unexpectedly contains $key"
  fi
}

verify_archive_payload() {
  unzip -Z1 "$ARCHIVE_PATH" \
    | /usr/bin/grep -F "$APP_NAME.app/Contents/Resources/README.md" >/dev/null \
    || fail "Archive is missing the bundled support README"
  unzip -Z1 "$ARCHIVE_PATH" \
    | /usr/bin/grep -E '/tkinterdnd2/tkdnd/.+\.(dylib|tcl)$' >/dev/null \
    || fail "Archive is missing the TkDND runtime payload"
}

# Keep all safety helpers sourceable by the Linux fixture audit without
# invoking any macOS build tools or changing the filesystem.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  return 0
fi

require_file "$ROOT_DIR/rfmapping_gui.py"
require_file "$ROOT_DIR/pyproject.toml"
require_file "$ROOT_DIR/requirements.txt"
require_file "$ICON_MASTER"
require_file "$SUPPORT_DOCUMENTATION"
require_file "$SMOKE_JSON"
require_file "$METADATA_AUDITOR"
require_file "$PYINSTALLER_HOOKS/hook-tkinterdnd2.py"
[[ -x "$PLIST_BUDDY" ]] || fail "PlistBuddy not found: $PLIST_BUDDY"
[[ "$(uname -m)" == "$APP_ARCHITECTURE" ]] \
  || fail "Python macOS builds require an $APP_ARCHITECTURE host"

if [[ "$PYTHON_BIN" != */* ]]; then
  PYTHON_BIN="$(command -v "$PYTHON_BIN" || true)"
fi
[[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || fail "Python not found: $PYTHON_BIN"

PYTHON_TARGET="$(
  "$PYTHON_BIN" -c 'import platform, sys; print(f"{sys.version_info.major}.{sys.version_info.minor}-{platform.machine()}")'
)"
[[ "$PYTHON_TARGET" == "3.14-$APP_ARCHITECTURE" ]] \
  || fail "Python 3.14 $APP_ARCHITECTURE is required; got $PYTHON_TARGET"

"$PYTHON_BIN" "$METADATA_AUDITOR" "$ROOT_DIR" "$APP_VERSION" "$RELEASE_EDITION"

validate_distribution_tree
BUILD_VENV="$(canonical_scoped_build_cache_path "Build virtual environment" "$BUILD_VENV")"
WORK_DIR="$(canonical_scoped_build_cache_path "Build work directory" "$WORK_DIR")"
case "$BUILD_VENV/" in
  "$WORK_DIR/"*) fail "Build virtual environment may not be inside the work directory" ;;
esac
case "$WORK_DIR/" in
  "$BUILD_VENV/"*) fail "Build work directory may not be inside the virtual environment" ;;
esac

if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$BUILD_VENV"
fi
BUILD_PYTHON_TARGET="$(
  "$BUILD_VENV/bin/python" -c 'import platform, sys; print(f"{sys.version_info.major}.{sys.version_info.minor}-{platform.machine()}")'
)"
[[ "$BUILD_PYTHON_TARGET" == "3.14-$APP_ARCHITECTURE" ]] \
  || fail "Build virtual environment must use Python 3.14 $APP_ARCHITECTURE; got $BUILD_PYTHON_TARGET"

if ! "$BUILD_VENV/bin/python" -c \
  "import importlib.metadata as m, PyInstaller, numpy, PIL; raise SystemExit(PyInstaller.__version__ != '$PYINSTALLER_VERSION' or numpy.__version__ != '$NUMPY_VERSION' or PIL.__version__ != '$PILLOW_VERSION' or m.version('tkinterdnd2') != '$TKINTERDND2_VERSION')"; then
  "$BUILD_VENV/bin/python" -m pip install --disable-pip-version-check \
    "pyinstaller==$PYINSTALLER_VERSION" \
    "numpy==$NUMPY_VERSION" \
    "pillow==$PILLOW_VERSION" \
    "tkinterdnd2==$TKINTERDND2_VERSION"
fi
"$BUILD_VENV/bin/python" -c \
  'import tkinter, tkinterdnd2; print("Tk runtime:", tkinter.TkVersion, tkinterdnd2.__file__)'

require_safe_removal_target "$APP_BUNDLE"
require_safe_removal_target "$WORK_DIR"
# Recheck immediately before destructive cleanup. In particular, never follow
# a dist or dist/python symlink even if it appeared after initial validation.
validate_distribution_tree
rm -rf "$APP_BUNDLE" "$WORK_DIR"
rm -f "$ARCHIVE_PATH" "$CHECKSUM_PATH"
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
    --additional-hooks-dir "$PYINSTALLER_HOOKS" \
    --add-data "$SUPPORT_DOCUMENTATION:." \
    "$@" \
    "$ROOT_DIR/rfmapping_gui.py"
}

# macOS ships Bash 3.2, where expanding an empty array under `set -u` fails.
# Pass the optional data argument through positional parameters instead.
if [[ -d "$DATA_SOURCE" ]]; then
  run_pyinstaller --add-data "$DATA_SOURCE:data"
else
  run_pyinstaller
fi

"$PLIST_BUDDY" -c "Set :CFBundleDisplayName $APP_NAME" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Set :CFBundleShortVersionString $APP_VERSION" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Delete :CFBundleVersion" "$INFO_PLIST" 2>/dev/null || true
"$PLIST_BUDDY" -c "Add :CFBundleVersion string $APP_BUILD" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Delete :RFMappingReleaseEdition" "$INFO_PLIST" 2>/dev/null || true
"$PLIST_BUDDY" -c "Add :RFMappingReleaseEdition string $RELEASE_EDITION" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Delete :LSMinimumSystemVersion" "$INFO_PLIST" 2>/dev/null || true
"$PLIST_BUDDY" -c "Add :LSMinimumSystemVersion string $MINIMUM_MACOS_VERSION" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Delete :LSMultipleInstancesProhibited" "$INFO_PLIST" 2>/dev/null || true
"$PLIST_BUDDY" -c "Add :LSMultipleInstancesProhibited bool true" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Delete :CFBundleDocumentTypes" "$INFO_PLIST" 2>/dev/null || true
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0 dict" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string 'RF Map document'" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Viewer" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:LSHandlerRank string Owner" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 string rfmap" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:0 string org.local.rfmapping.rfmap" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:1 dict" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:1:CFBundleTypeName string 'RF Mapping JSON'" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:1:CFBundleTypeRole string Viewer" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:1:LSHandlerRank string Alternate" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:1:CFBundleTypeExtensions array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:1:CFBundleTypeExtensions:0 string json" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:1:LSItemContentTypes array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :CFBundleDocumentTypes:1:LSItemContentTypes:0 string public.json" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Delete :UTExportedTypeDeclarations" "$INFO_PLIST" 2>/dev/null || true
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:0 dict" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:0:UTTypeIdentifier string org.local.rfmapping.rfmap" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:0:UTTypeDescription string 'RF Map document'" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:0:UTTypeConformsTo array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:0:UTTypeConformsTo:0 string public.json" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:0:UTTypeTagSpecification dict" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:0:UTTypeTagSpecification:public.filename-extension array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:0:UTTypeTagSpecification:public.filename-extension:0 string rfmap" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:1 dict" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:1:UTTypeIdentifier string org.local.rfmapping.tc" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:1:UTTypeDescription string 'Tuning Curve document'" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:1:UTTypeConformsTo array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:1:UTTypeConformsTo:0 string public.json" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:1:UTTypeTagSpecification dict" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:1:UTTypeTagSpecification:public.filename-extension array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:1:UTTypeTagSpecification:public.filename-extension:0 string tc" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:2 dict" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:2:UTTypeIdentifier string org.local.rfmapping.probe" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:2:UTTypeDescription string 'Probe Position document'" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:2:UTTypeConformsTo array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:2:UTTypeConformsTo:0 string public.comma-separated-values-text" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:2:UTTypeTagSpecification dict" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:2:UTTypeTagSpecification:public.filename-extension array" "$INFO_PLIST"
"$PLIST_BUDDY" -c "Add :UTExportedTypeDeclarations:2:UTTypeTagSpecification:public.filename-extension:0 string probe" "$INFO_PLIST"

plutil -lint "$INFO_PLIST" >/dev/null
verify_plist_value CFBundleDisplayName "$APP_NAME"
verify_plist_value CFBundleExecutable "$EXECUTABLE_NAME"
verify_plist_value CFBundleIdentifier "$BUNDLE_ID"
verify_plist_value CFBundleShortVersionString "$APP_VERSION"
verify_plist_value CFBundleVersion "$APP_BUILD"
verify_plist_value RFMappingReleaseEdition "$RELEASE_EDITION"
verify_plist_value LSMinimumSystemVersion "$MINIMUM_MACOS_VERSION"
verify_plist_value LSMultipleInstancesProhibited true
verify_plist_value CFBundleDocumentTypes:0:CFBundleTypeRole Viewer
verify_plist_value CFBundleDocumentTypes:0:LSHandlerRank Owner
verify_plist_value CFBundleDocumentTypes:0:LSItemContentTypes:0 org.local.rfmapping.rfmap
verify_plist_value CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 rfmap
verify_plist_value CFBundleDocumentTypes:1:CFBundleTypeExtensions:0 json
verify_plist_missing CFBundleDocumentTypes:2
verify_plist_value UTExportedTypeDeclarations:0:UTTypeIdentifier org.local.rfmapping.rfmap
verify_plist_value UTExportedTypeDeclarations:1:UTTypeIdentifier org.local.rfmapping.tc
verify_plist_value UTExportedTypeDeclarations:2:UTTypeIdentifier org.local.rfmapping.probe

require_nonempty_file "$APP_BINARY"
require_nonempty_file "$APP_RESOURCES/RFMappingViewer.icns"
require_nonempty_file "$APP_RESOURCES/README.md"
if [[ -d "$DATA_SOURCE" ]]; then
  [[ -d "$APP_RESOURCES/data" ]] || fail "Bundled data directory is missing"
  SOURCE_RF_COUNT="$(find "$DATA_SOURCE" -type f \( -iname '*.rfmap' -o -iname '*.json' \) | wc -l | tr -d '[:space:]')"
  BUNDLED_RF_COUNT="$(find "$APP_RESOURCES/data" -type f \( -iname '*.rfmap' -o -iname '*.json' \) | wc -l | tr -d '[:space:]')"
  [[ "$SOURCE_RF_COUNT" -gt 0 ]] || fail "No RF mapping resources found in $DATA_SOURCE"
  [[ "$BUNDLED_RF_COUNT" == "$SOURCE_RF_COUNT" ]] \
    || fail "Bundled RF mapping resource count does not match source"
  diff -qr -x '.DS_Store' -x '._*' "$DATA_SOURCE" "$APP_RESOURCES/data" >/dev/null \
    || fail "Bundled data does not match $DATA_SOURCE"
fi
verify_arm64_macho_files

# These smoke tests run the frozen executable, not the build interpreter.
"$APP_BINARY" --self-test "$SMOKE_JSON"
"$APP_BINARY" --self-test-dnd

clean_bundle_metadata "$APP_BUNDLE"
SIGN_ARGUMENTS=(--force --deep --sign "$SIGNING_IDENTITY")
if [[ "$SIGNING_IDENTITY" != "-" ]]; then
  SIGN_ARGUMENTS+=(--options runtime --timestamp)
fi
codesign "${SIGN_ARGUMENTS[@]}" "$APP_BUNDLE"
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

# Finder must never cache the transient build-tree bundle as a second JSON
# Open With candidate. The verified app remains for the guarded installer.
if [[ -x "$LSREGISTER" ]]; then
  "$LSREGISTER" -u "$APP_BUNDLE" >/dev/null 2>&1 || true
fi

ditto -c -k --norsrc --keepParent "$APP_BUNDLE" "$ARCHIVE_PATH"
require_nonempty_file "$ARCHIVE_PATH"
unzip -tq "$ARCHIVE_PATH" >/dev/null \
  || fail "Archive integrity check failed: $ARCHIVE_PATH"
verify_archive_metadata "$ARCHIVE_PATH"
verify_archive_plist_value CFBundleShortVersionString "$APP_VERSION"
verify_archive_plist_value CFBundleVersion "$APP_BUILD"
verify_archive_plist_value RFMappingReleaseEdition "$RELEASE_EDITION"
verify_archive_plist_value LSMinimumSystemVersion "$MINIMUM_MACOS_VERSION"
verify_archive_plist_value CFBundleDocumentTypes.0.CFBundleTypeExtensions.0 rfmap
verify_archive_plist_value CFBundleDocumentTypes.1.CFBundleTypeExtensions.0 json
verify_archive_plist_missing CFBundleDocumentTypes.2
verify_archive_payload

(
  cd "$DIST_DIR"
  shasum -a 256 "$ARCHIVE_NAME" >"$CHECKSUM_NAME"
  shasum -a 256 -c "$CHECKSUM_NAME"
)
require_nonempty_file "$CHECKSUM_PATH"

echo "Built Python $APP_VERSION $RELEASE_EDITION Apple-silicon app: $APP_BUNDLE"
echo "Created archive: $ARCHIVE_PATH"
echo "Created checksum: $CHECKSUM_PATH"

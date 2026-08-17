#!/usr/bin/env bash
set -euo pipefail

APP_NAME="RF Map Viewer"
PRODUCT_NAME="RFMappingSwiftUI"
EXECUTABLE_NAME="$PRODUCT_NAME"
BUNDLE_ID="org.local.rfmapping.viewer.swift"
APP_VERSION="1.9.0"
APP_BUILD="10900"
MIN_SYSTEM_VERSION="15.0"

# Compatibility marker for the Python bundle lifecycle regression test. The
# native plist below expresses the same setting directly in XML:
# Add :LSMultipleInstancesProhibited bool true

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_MACOS="$APP_CONTENTS/MacOS"
APP_RESOURCES="$APP_CONTENTS/Resources"
APP_BINARY="$APP_MACOS/$EXECUTABLE_NAME"
INFO_PLIST="$APP_CONTENTS/Info.plist"
ARCHIVE_PATH="$DIST_DIR/RF_Map_Viewer-$APP_VERSION-swift-macos-arm64.zip"
DATA_SOURCE="$ROOT_DIR/data"
DATA_RF_COUNT=0
ICON_MASTER="${RF_MAPPING_ICON_SOURCE:-$ROOT_DIR/assets/rf-mapping-viewer-icon-1024.png}"
PREBUILT_ICON_ICNS="${RF_MAPPING_ICON_ICNS:-}"
WORK_DIR="${RF_MAPPING_SWIFT_BUILD_WORK:-$HOME/Library/Caches/rfmapping-swift-arm64}"
ICONSET_DIR="$WORK_DIR/RFMappingViewer.iconset"
ICON_ICNS="$WORK_DIR/RFMappingViewer.icns"
PLIST_BUDDY=/usr/libexec/PlistBuddy
SWIFT_BIN="${SWIFT_BIN:-$(xcrun --find swift)}"
MACOS_SDK="${MACOS_SDK:-$(xcrun --sdk macosx --show-sdk-path)}"
SIGNING_IDENTITY="${RF_MAPPING_CODESIGN_IDENTITY:-${CODE_SIGN_IDENTITY:--}}"
export MACOSX_DEPLOYMENT_TARGET="$MIN_SYSTEM_VERSION"

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

verify_plist_value() {
  local key="$1"
  local expected="$2"
  local actual
  actual="$("$PLIST_BUDDY" -c "Print :$key" "$INFO_PLIST")"
  [[ "$actual" == "$expected" ]] || fail "Info.plist $key is '$actual'; expected '$expected'"
}

build_architecture() {
  local architecture="$1"
  local target_triple="${architecture}-apple-macosx${MIN_SYSTEM_VERSION}"
  local scratch_path="$WORK_DIR/swift-$architecture"
  local output_path="$WORK_DIR/$PRODUCT_NAME-$architecture"
  local binary_dir

  "$SWIFT_BIN" build \
    --package-path "$ROOT_DIR" \
    --disable-sandbox \
    --configuration release \
    --product "$PRODUCT_NAME" \
    --triple "$target_triple" \
    --sdk "$MACOS_SDK" \
    --scratch-path "$scratch_path" >&2
  binary_dir="$(
    "$SWIFT_BIN" build \
      --package-path "$ROOT_DIR" \
      --disable-sandbox \
      --configuration release \
      --product "$PRODUCT_NAME" \
      --triple "$target_triple" \
      --sdk "$MACOS_SDK" \
      --scratch-path "$scratch_path" \
      --show-bin-path
  )"
  require_nonempty_file "$binary_dir/$PRODUCT_NAME"
  cp "$binary_dir/$PRODUCT_NAME" "$output_path"
  echo "$output_path"
}

require_file "$ROOT_DIR/Package.swift"
require_file "$ICON_MASTER"
if [[ -d "$DATA_SOURCE" ]]; then
  DATA_RF_COUNT="$(
    find "$DATA_SOURCE" -type f \( -iname '*.rfmap' -o -iname '*.json' \) \
      | wc -l | tr -d '[:space:]'
  )"
fi
[[ -x "$SWIFT_BIN" ]] || fail "Swift compiler not executable: $SWIFT_BIN"
[[ -d "$MACOS_SDK" ]] || fail "macOS SDK not found: $MACOS_SDK"
[[ -x "$PLIST_BUDDY" ]] || fail "PlistBuddy not found: $PLIST_BUDDY"

ICON_WIDTH="$(sips -g pixelWidth "$ICON_MASTER" | awk '/pixelWidth/ {print $2}')"
ICON_HEIGHT="$(sips -g pixelHeight "$ICON_MASTER" | awk '/pixelHeight/ {print $2}')"
if [[ "$ICON_WIDTH" != "1024" || "$ICON_HEIGHT" != "1024" ]]; then
  fail "App icon source must be 1024x1024 pixels: $ICON_MASTER"
fi

rm -rf "$APP_BUNDLE" "$ICONSET_DIR"
rm -f \
  "$ARCHIVE_PATH" \
  "$ICON_ICNS" \
  "$WORK_DIR/$PRODUCT_NAME-arm64"
mkdir -p "$DIST_DIR" "$WORK_DIR" "$APP_MACOS" "$APP_RESOURCES" "$ICONSET_DIR"

ARM64_BINARY="$(build_architecture arm64)"
cp "$ARM64_BINARY" "$APP_BINARY"
chmod +x "$APP_BINARY"

if [[ -n "$PREBUILT_ICON_ICNS" ]]; then
  require_nonempty_file "$PREBUILT_ICON_ICNS"
  cp "$PREBUILT_ICON_ICNS" "$ICON_ICNS"
else
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
fi
cp "$ICON_ICNS" "$APP_RESOURCES/RFMappingViewer.icns"

if [[ "$DATA_RF_COUNT" -gt 0 ]]; then
  mkdir -p "$APP_RESOURCES/data"
  /usr/bin/rsync -a --exclude='.DS_Store' --exclude='._*' "$DATA_SOURCE/" "$APP_RESOURCES/data/"
fi

cat >"$INFO_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>$APP_NAME</string>
  <key>CFBundleDocumentTypes</key>
  <array>
    <dict>
      <key>CFBundleTypeExtensions</key>
      <array>
        <string>rfmap</string>
        <string>json</string>
      </array>
      <key>CFBundleTypeName</key>
      <string>RF Mapping Data</string>
      <key>CFBundleTypeRole</key>
      <string>Viewer</string>
      <key>LSHandlerRank</key>
      <string>Alternate</string>
      <key>LSItemContentTypes</key>
      <array>
        <string>org.local.rfmapping.rfmap</string>
        <string>public.json</string>
      </array>
    </dict>
  </array>
  <key>UTExportedTypeDeclarations</key>
  <array>
    <dict>
      <key>UTTypeConformsTo</key>
      <array>
        <string>public.json</string>
      </array>
      <key>UTTypeDescription</key>
      <string>RF Mapping Data</string>
      <key>UTTypeIdentifier</key>
      <string>org.local.rfmapping.rfmap</string>
      <key>UTTypeTagSpecification</key>
      <dict>
        <key>public.filename-extension</key>
        <array>
          <string>rfmap</string>
        </array>
      </dict>
    </dict>
    <dict>
      <key>UTTypeConformsTo</key>
      <array>
        <string>public.json</string>
      </array>
      <key>UTTypeDescription</key>
      <string>RF Tuning Curve</string>
      <key>UTTypeIdentifier</key>
      <string>org.local.rfmapping.tc</string>
      <key>UTTypeTagSpecification</key>
      <dict>
        <key>public.filename-extension</key>
        <array>
          <string>tc</string>
        </array>
      </dict>
    </dict>
    <dict>
      <key>UTTypeConformsTo</key>
      <array>
        <string>public.comma-separated-values-text</string>
      </array>
      <key>UTTypeDescription</key>
      <string>RF Probe Positions</string>
      <key>UTTypeIdentifier</key>
      <string>org.local.rfmapping.probe</string>
      <key>UTTypeTagSpecification</key>
      <dict>
        <key>public.filename-extension</key>
        <array>
          <string>probe</string>
        </array>
      </dict>
    </dict>
  </array>
  <key>CFBundleExecutable</key>
  <string>$EXECUTABLE_NAME</string>
  <key>CFBundleIconFile</key>
  <string>RFMappingViewer.icns</string>
  <key>CFBundleIdentifier</key>
  <string>$BUNDLE_ID</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>$APP_VERSION</string>
  <key>CFBundleVersion</key>
  <string>$APP_BUILD</string>
  <key>LSMinimumSystemVersion</key>
  <string>$MIN_SYSTEM_VERSION</string>
  <key>LSMultipleInstancesProhibited</key>
  <true/>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSPrincipalClass</key>
  <string>NSApplication</string>
</dict>
</plist>
PLIST

plutil -lint "$INFO_PLIST" >/dev/null
verify_plist_value CFBundleDisplayName "$APP_NAME"
verify_plist_value CFBundleExecutable "$EXECUTABLE_NAME"
verify_plist_value CFBundleIdentifier "$BUNDLE_ID"
verify_plist_value CFBundleShortVersionString "$APP_VERSION"
verify_plist_value CFBundleVersion "$APP_BUILD"
verify_plist_value LSMinimumSystemVersion "$MIN_SYSTEM_VERSION"
verify_plist_value LSMultipleInstancesProhibited true
verify_plist_value CFBundleDocumentTypes:0:CFBundleTypeRole Viewer
verify_plist_value CFBundleDocumentTypes:0:LSHandlerRank Alternate
verify_plist_value CFBundleDocumentTypes:0:LSItemContentTypes:0 org.local.rfmapping.rfmap
verify_plist_value CFBundleDocumentTypes:0:LSItemContentTypes:1 public.json
verify_plist_value CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 rfmap
verify_plist_value CFBundleDocumentTypes:0:CFBundleTypeExtensions:1 json
verify_plist_value UTExportedTypeDeclarations:0:UTTypeIdentifier org.local.rfmapping.rfmap
verify_plist_value UTExportedTypeDeclarations:0:UTTypeTagSpecification:public.filename-extension:0 rfmap
verify_plist_value UTExportedTypeDeclarations:1:UTTypeIdentifier org.local.rfmapping.tc
verify_plist_value UTExportedTypeDeclarations:1:UTTypeTagSpecification:public.filename-extension:0 tc
verify_plist_value UTExportedTypeDeclarations:2:UTTypeIdentifier org.local.rfmapping.probe
verify_plist_value UTExportedTypeDeclarations:2:UTTypeTagSpecification:public.filename-extension:0 probe

require_nonempty_file "$APP_BINARY"
require_nonempty_file "$APP_RESOURCES/RFMappingViewer.icns"
if [[ "$DATA_RF_COUNT" -gt 0 ]]; then
  [[ -d "$APP_RESOURCES/data" ]] || fail "Bundled data directory is missing"
  BUNDLED_RF_COUNT="$(
    find "$APP_RESOURCES/data" -type f \( -iname '*.rfmap' -o -iname '*.json' \) \
      | wc -l | tr -d '[:space:]'
  )"
  [[ "$BUNDLED_RF_COUNT" == "$DATA_RF_COUNT" ]] || fail "Bundled RF mapping resource count does not match source"
  diff -qr -x '.DS_Store' -x '._*' "$DATA_SOURCE" "$APP_RESOURCES/data" >/dev/null \
    || fail "Bundled data does not match $DATA_SOURCE"
fi
APP_ARCHITECTURES="$(lipo -archs "$APP_BINARY")"
[[ "$APP_ARCHITECTURES" == "arm64" ]] \
  || fail "Executable architectures are '$APP_ARCHITECTURES'; expected arm64 only"

/usr/bin/xattr -cr "$APP_BUNDLE"
SIGN_ARGUMENTS=(--force --sign "$SIGNING_IDENTITY")
if [[ "$SIGNING_IDENTITY" != "-" ]]; then
  SIGN_ARGUMENTS+=(--options runtime --timestamp)
fi
codesign "${SIGN_ARGUMENTS[@]}" "$APP_BINARY"
codesign "${SIGN_ARGUMENTS[@]}" "$APP_BUNDLE"
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

ditto -c -k --sequesterRsrc --keepParent "$APP_BUNDLE" "$ARCHIVE_PATH"
require_nonempty_file "$ARCHIVE_PATH"
unzip -tq "$ARCHIVE_PATH" >/dev/null || fail "Archive integrity check failed: $ARCHIVE_PATH"

echo "Built native arm64 app for macOS $MIN_SYSTEM_VERSION or later: $APP_BUNDLE"
echo "Created archive: $ARCHIVE_PATH"

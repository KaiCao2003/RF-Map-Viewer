#!/usr/bin/env bash
set -euo pipefail

APP_NAME="TC Comparison"
PRODUCT_NAME="TCComparisonApp"
EXECUTABLE_NAME="$PRODUCT_NAME"
BUNDLE_ID="org.local.rfmapping.tc-comparison.swift"
APP_VERSION="1.0.0"
APP_BUILD="10000"
MIN_SYSTEM_VERSION="15.0"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_MACOS="$APP_CONTENTS/MacOS"
APP_RESOURCES="$APP_CONTENTS/Resources"
APP_BINARY="$APP_MACOS/$EXECUTABLE_NAME"
INFO_PLIST="$APP_CONTENTS/Info.plist"
ARCHIVE_PATH="$DIST_DIR/TC_Comparison-$APP_VERSION-swift-macos-arm64.zip"
ICON_MASTER="${TC_COMPARISON_ICON_SOURCE:-$ROOT_DIR/assets/tc-comparison-icon-1024.png}"
WORK_DIR="${TC_COMPARISON_BUILD_WORK:-${TMPDIR:-/tmp}/rfmapping-tc-comparison-build}"
ICONSET_DIR="$WORK_DIR/TCComparison.iconset"
ICON_ICNS="$WORK_DIR/TCComparison.icns"
PLIST_BUDDY=/usr/libexec/PlistBuddy
SWIFT_BIN="${SWIFT_BIN:-$(xcrun --find swift)}"
MACOS_SDK="${MACOS_SDK:-$(xcrun --sdk macosx --show-sdk-path)}"
SIGNING_IDENTITY="${TC_COMPARISON_CODESIGN_IDENTITY:-${CODE_SIGNING_IDENTITY:--}}"
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

[[ "$(uname -s)" == "Darwin" ]] || fail "TC Comparison requires a macOS build host"
[[ "$(uname -m)" == "arm64" ]] || fail "TC Comparison is released for Apple silicon"
require_file "$ROOT_DIR/Package.swift"
require_file "$ICON_MASTER"
[[ -x "$SWIFT_BIN" ]] || fail "Swift compiler not executable: $SWIFT_BIN"
[[ -d "$MACOS_SDK" ]] || fail "macOS SDK not found: $MACOS_SDK"
[[ -x "$PLIST_BUDDY" ]] || fail "PlistBuddy not found: $PLIST_BUDDY"

ICON_WIDTH="$(sips -g pixelWidth "$ICON_MASTER" | awk '/pixelWidth/ {print $2}')"
ICON_HEIGHT="$(sips -g pixelHeight "$ICON_MASTER" | awk '/pixelHeight/ {print $2}')"
[[ "$ICON_WIDTH" == "1024" && "$ICON_HEIGHT" == "1024" ]] \
  || fail "App icon source must be 1024x1024 pixels: $ICON_MASTER"

rm -rf "$APP_BUNDLE" "$ICONSET_DIR"
rm -f "$ARCHIVE_PATH" "$ICON_ICNS"
mkdir -p "$DIST_DIR" "$WORK_DIR" "$APP_MACOS" "$APP_RESOURCES" "$ICONSET_DIR"

TARGET_TRIPLE="arm64-apple-macosx$MIN_SYSTEM_VERSION"
SCRATCH_PATH="$WORK_DIR/swift-arm64"
"$SWIFT_BIN" build \
  --package-path "$ROOT_DIR" \
  --disable-sandbox \
  --configuration release \
  --product "$PRODUCT_NAME" \
  --triple "$TARGET_TRIPLE" \
  --sdk "$MACOS_SDK" \
  --scratch-path "$SCRATCH_PATH"
BIN_DIR="$(
  "$SWIFT_BIN" build \
    --package-path "$ROOT_DIR" \
    --disable-sandbox \
    --configuration release \
    --product "$PRODUCT_NAME" \
    --triple "$TARGET_TRIPLE" \
    --sdk "$MACOS_SDK" \
    --scratch-path "$SCRATCH_PATH" \
    --show-bin-path
)"
require_nonempty_file "$BIN_DIR/$PRODUCT_NAME"
cp "$BIN_DIR/$PRODUCT_NAME" "$APP_BINARY"
chmod +x "$APP_BINARY"

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
cp "$ICON_ICNS" "$APP_RESOURCES/TCComparison.icns"

cat >"$INFO_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>$APP_NAME</string>
  <key>CFBundleExecutable</key>
  <string>$EXECUTABLE_NAME</string>
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
  <key>CFBundleIconFile</key>
  <string>TCComparison</string>
  <key>LSMinimumSystemVersion</key>
  <string>$MIN_SYSTEM_VERSION</string>
  <key>LSMultipleInstancesProhibited</key>
  <true/>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>CFBundleDocumentTypes</key>
  <array>
    <dict>
      <key>CFBundleTypeExtensions</key>
      <array>
        <string>tc</string>
        <string>json</string>
      </array>
      <key>CFBundleTypeName</key>
      <string>RF Tuning Curve</string>
      <key>CFBundleTypeRole</key>
      <string>Viewer</string>
      <key>LSHandlerRank</key>
      <string>Alternate</string>
      <key>LSItemContentTypes</key>
      <array>
        <string>org.local.rfmapping.tc</string>
        <string>public.json</string>
      </array>
    </dict>
  </array>
  <key>UTExportedTypeDeclarations</key>
  <array>
    <dict>
      <key>UTTypeConformsTo</key>
      <array><string>public.json</string></array>
      <key>UTTypeDescription</key>
      <string>RF Tuning Curve</string>
      <key>UTTypeIdentifier</key>
      <string>org.local.rfmapping.tc</string>
      <key>UTTypeTagSpecification</key>
      <dict>
        <key>public.filename-extension</key>
        <array><string>tc</string></array>
      </dict>
    </dict>
  </array>
</dict>
</plist>
PLIST

"$PLIST_BUDDY" -c "Print :CFBundleIdentifier" "$INFO_PLIST" >/dev/null
codesign --force --deep --options runtime --sign "$SIGNING_IDENTITY" "$APP_BUNDLE"
codesign --verify --deep --strict "$APP_BUNDLE"

/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP_BUNDLE" "$ARCHIVE_PATH"
require_nonempty_file "$ARCHIVE_PATH"

echo "Built: $APP_BUNDLE"
echo "Archive: $ARCHIVE_PATH"

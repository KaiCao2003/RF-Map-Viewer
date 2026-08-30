#!/usr/bin/env bash
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd "$TEST_DIR/.." && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_SCRIPT="$SCRIPT_DIR/build_python_stable_macos_app.sh"
WINDOWS_BUILD_SCRIPT="$SCRIPT_DIR/build_python_stable_windows_app.ps1"
INSTALLER="$SCRIPT_DIR/install_python_macos_app.sh"
RELEASE_CONFIG="$SCRIPT_DIR/python_stable_macos_release.env"
METADATA_AUDITOR="$SCRIPT_DIR/verify_python_stable_release_metadata.py"
TK9_HOOK_PATCHER="$SCRIPT_DIR/patch_pyinstaller_tk9_runtime_hook.py"
TK9_HOOK_BACKPORT="$ROOT_DIR/packaging/pyinstaller-hooks/rthooks/pyi_rth__tkinter.py"

TEST_COUNT=0
FIXTURE_ROOT=""

fail_test() {
  echo "not ok $((TEST_COUNT + 1)) - $*" >&2
  exit 1
}

pass_test() {
  TEST_COUNT=$((TEST_COUNT + 1))
  echo "ok $TEST_COUNT - $*"
}

assert_file_contains() {
  local file="$1"
  local text="$2"
  /usr/bin/grep -F -- "$text" "$file" >/dev/null \
    || fail_test "$file does not contain required stable release marker: $text"
}

cleanup() {
  if [[ -n "$FIXTURE_ROOT" && -d "$FIXTURE_ROOT" ]]; then
    case "$FIXTURE_ROOT" in
      /tmp/rfmapping-stable-release-test.*)
        /bin/rm -rf -- "$FIXTURE_ROOT"
        ;;
      *)
        echo "refusing unsafe fixture cleanup: $FIXTURE_ROOT" >&2
        ;;
    esac
  fi
}
trap cleanup EXIT HUP INT TERM

for required in \
  "$BUILD_SCRIPT" \
  "$WINDOWS_BUILD_SCRIPT" \
  "$INSTALLER" \
  "$RELEASE_CONFIG" \
  "$METADATA_AUDITOR" \
  "$TK9_HOOK_PATCHER" \
  "$TK9_HOOK_BACKPORT"; do
  [[ -s "$required" ]] || fail_test "required stable release file is missing: $required"
done
/bin/bash -n "$BUILD_SCRIPT" || fail_test "stable build script failed Bash syntax validation"
/bin/bash -n "$INSTALLER" || fail_test "macOS installer failed Bash syntax validation"
python3 -m py_compile "$METADATA_AUDITOR" "$TK9_HOOK_PATCHER" "$TK9_HOOK_BACKPORT" \
  || fail_test "stable metadata auditor failed Python syntax validation"
pass_test "stable release helpers pass syntax validation"

(
  # shellcheck source=../python_stable_macos_release.env
  source "$RELEASE_CONFIG"
  [[ "$RF_MAPPING_APP_NAME" == "RF Map Viewer" ]]
  [[ "$RF_MAPPING_EXECUTABLE_NAME" == "RF Map Viewer" ]]
  [[ "$RF_MAPPING_BUNDLE_ID" == "org.local.rfmapping.viewer" ]]
  [[ "$RF_MAPPING_APP_VERSION" == "1.9.6" ]]
  [[ "$RF_MAPPING_PACKAGE_VERSION" == "1.9.6" ]]
  [[ "$RF_MAPPING_APP_BUILD" == "10908" ]]
  [[ "$RF_MAPPING_RELEASE_EDITION" == "Full" ]]
  [[ "$RF_MAPPING_RELEASE_FLAVOR" == "full" ]]
) || fail_test "canonical stable Python metadata is incomplete or unexpected"
pass_test "canonical metadata identifies Python stable 1.9.6 build 10908"

for marker in \
  'source "$SCRIPT_DIR/python_stable_macos_release.env"' \
  'METADATA_AUDITOR="$SCRIPT_DIR/verify_python_stable_release_metadata.py"' \
  '"$ROOT_DIR/rfmapping_gui.py"' \
  '"$APP_BINARY" --self-test "$SMOKE_JSON"' \
  '"$APP_BINARY" --self-test-export "$EXPORT_SMOKE_DIR"' \
  'Archive must not bundle RF, tuning-curve, probe, or smoke sample data' \
  'Add :CFBundleDocumentTypes:1:CFBundleTypeExtensions:0 string json' \
  'Add :UTExportedTypeDeclarations:1:UTTypeIdentifier string org.local.rfmapping.tc' \
  'Add :UTExportedTypeDeclarations:2:UTTypeIdentifier string org.local.rfmapping.probe'; do
  assert_file_contains "$BUILD_SCRIPT" "$marker"
done
pass_test "stable builder targets the full viewer and retains its file contracts"

for marker in \
  '$PyInstallerVersion = "6.21.0"' \
  '$TkinterRuntimeHookBackport = Join-Path $HooksDir "rthooks\pyi_rth__tkinter.py"' \
  '$TkinterRuntimeHookPatcher = Join-Path $ScriptDir "patch_pyinstaller_tk9_runtime_hook.py"' \
  '& $BuildPython $TkinterRuntimeHookPatcher $TkinterRuntimeHookBackport' \
  'Assert-NativeSuccess "PyInstaller Tcl/Tk 9 runtime-hook backport"' \
  'function Normalize-WindowsVersionString' \
  'TrimEnd([char[]]@([char]0, [char]32))'; do
  assert_file_contains "$WINDOWS_BUILD_SCRIPT" "$marker"
done
for marker in \
  'EXPECTED_PYINSTALLER_VERSION = "6.21.0"' \
  'EXPECTED_TCL_LIBRARY = "//zipfs:/lib/tcl/tcl_library"' \
  'if not 9.0 <= numeric_tk_version < 10.0:' \
  'installed_digest != EXPECTED_INSTALLED_HOOK_SHA256'; do
  assert_file_contains "$TK9_HOOK_PATCHER" "$marker"
done
assert_file_contains "$TK9_HOOK_BACKPORT" \
  '47745340110001c43d1165693f432521a65fc690'
pass_test "Windows Tcl/Tk 9 backport is pinned and refuses unreviewed environments"

for marker in \
  'RF_MAPPING_RELEASE_CONFIG' \
  'Full)' \
  'CFBundleDocumentTypes:1:CFBundleTypeExtensions:0 json' \
  'UTExportedTypeDeclarations:1:UTTypeIdentifier org.local.rfmapping.tc' \
  'UTExportedTypeDeclarations:2:UTTypeIdentifier org.local.rfmapping.probe'; do
  assert_file_contains "$INSTALLER" "$marker"
done

run_stable_document_contract_fixture() (
  export RF_MAPPING_RELEASE_CONFIG="$RELEASE_CONFIG"
  # shellcheck source=../install_python_macos_app.sh
  source "$INSTALLER"
  plist_value() {
    local key="$2"
    case "$key" in
      CFBundleDocumentTypes:0:CFBundleTypeRole) printf '%s\n' Viewer ;;
      CFBundleDocumentTypes:0:LSHandlerRank) printf '%s\n' Owner ;;
      CFBundleDocumentTypes:0:LSItemContentTypes:0) printf '%s\n' org.local.rfmapping.rfmap ;;
      CFBundleDocumentTypes:0:CFBundleTypeExtensions:0) printf '%s\n' rfmap ;;
      CFBundleDocumentTypes:1:CFBundleTypeRole) printf '%s\n' Viewer ;;
      CFBundleDocumentTypes:1:LSHandlerRank) printf '%s\n' "${STABLE_JSON_RANK:-Alternate}" ;;
      CFBundleDocumentTypes:1:LSItemContentTypes:0) printf '%s\n' public.json ;;
      CFBundleDocumentTypes:1:CFBundleTypeExtensions:0) printf '%s\n' json ;;
      CFBundleDocumentTypes:2) return 1 ;;
      UTExportedTypeDeclarations:0:UTTypeIdentifier) printf '%s\n' org.local.rfmapping.rfmap ;;
      UTExportedTypeDeclarations:1:UTTypeIdentifier) printf '%s\n' org.local.rfmapping.tc ;;
      UTExportedTypeDeclarations:2:UTTypeIdentifier) printf '%s\n' org.local.rfmapping.probe ;;
      UTExportedTypeDeclarations:3) return 1 ;;
      *) return 1 ;;
    esac
  }
  validate_document_contract fixture.app
)

run_stable_document_contract_fixture >/dev/null \
  || fail_test "installer rejected the stable Full document contract"
if STABLE_JSON_RANK=Owner run_stable_document_contract_fixture >/dev/null 2>&1; then
  fail_test "installer accepted an incorrect stable JSON handler rank"
fi
pass_test "installer selects and enforces the stable Full document contract"

# shellcheck source=../build_python_stable_macos_app.sh
source "$BUILD_SCRIPT"
[[ "$ARCHIVE_NAME" == "RF_Map_Viewer-python-1.9.6-full-macos-arm64.zip" ]] \
  || fail_test "stable archive name does not encode component, version, and flavor"
[[ "$CHECKSUM_NAME" == "SHA256SUMS-python-1.9.6-full.txt" ]] \
  || fail_test "stable checksum name does not encode component, version, and flavor"
pass_test "stable artifacts are independently named from the Free-Moving alpha"

python3 "$METADATA_AUDITOR" "$ROOT_DIR" 1.9.6 Full >/dev/null \
  || fail_test "stable metadata auditor rejected the repository source"
FIXTURE_ROOT="$(/usr/bin/mktemp -d /tmp/rfmapping-stable-release-test.XXXXXX)"
/bin/mkdir -p "$FIXTURE_ROOT/source"
/bin/cp "$ROOT_DIR/requirements.txt" "$FIXTURE_ROOT/source/requirements.txt"
printf '%s\n' \
  'APP_VERSION = "1.10.0"' \
  'APP_EDITION = "FreeMovingAlpha"' \
  'DND_SMOKE_ARGUMENT = "--self-test-dnd"' \
  >"$FIXTURE_ROOT/source/rfmapping_gui.py"
if python3 "$METADATA_AUDITOR" "$FIXTURE_ROOT/source" 1.9.6 Full >/dev/null 2>&1; then
  fail_test "stable metadata auditor accepted Free-Moving alpha identity"
fi
pass_test "stable metadata auditor rejects cross-edition source"

echo "1..$TEST_COUNT"

#!/usr/bin/env bash
# Dynamic source paths and literal static-audit strings are intentional.
# shellcheck disable=SC1091,SC2016,SC2034
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd "$TEST_DIR/.." && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_SCRIPT="$SCRIPT_DIR/build_python_macos_app.sh"
RELEASE_CONFIG="$SCRIPT_DIR/python_macos_release.env"
METADATA_AUDITOR="$SCRIPT_DIR/verify_python_release_metadata.py"
PYPROJECT="$ROOT_DIR/pyproject.toml"
REQUIREMENTS="$ROOT_DIR/requirements.txt"
HOOK="$ROOT_DIR/packaging/pyinstaller-hooks/hook-tkinterdnd2.py"
README="$ROOT_DIR/README.md"
SMOKE_GENERATOR="$SCRIPT_DIR/create_fm_smoke_fixture.py"
TEST_PYTHON="${RF_MAPPING_TEST_PYTHON:-$HOME/.virtualenvs/rfmapping/bin/python}"

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
    || fail_test "$file does not contain required release marker: $text"
}

assert_file_not_contains() {
  local file="$1"
  local text="$2"
  if /usr/bin/grep -F -- "$text" "$file" >/dev/null; then
    fail_test "$file contains forbidden release marker: $text"
  fi
}

cleanup() {
  if [[ -n "$FIXTURE_ROOT" && -d "$FIXTURE_ROOT" ]]; then
    case "$FIXTURE_ROOT" in
      /tmp/rfmapping-release-candidate-test.*)
        /bin/rm -rf -- "$FIXTURE_ROOT"
        ;;
      *)
        echo "refusing unsafe fixture cleanup: $FIXTURE_ROOT" >&2
        ;;
    esac
  fi
}
trap cleanup EXIT HUP INT TERM

[[ -x "$TEST_PYTHON" ]] || fail_test "test Python is missing: $TEST_PYTHON"

for required in \
  "$BUILD_SCRIPT" \
  "$RELEASE_CONFIG" \
  "$METADATA_AUDITOR" \
  "$PYPROJECT" \
  "$REQUIREMENTS" \
  "$HOOK" \
  "$README" \
  "$SMOKE_GENERATOR"; do
  [[ -s "$required" ]] || fail_test "required candidate file is missing: $required"
done
/bin/bash -n "$BUILD_SCRIPT" || fail_test "build script failed Bash syntax validation"
python3 - "$METADATA_AUDITOR" "$HOOK" "$SMOKE_GENERATOR" <<'PY' \
  || fail_test "Python release helpers failed syntax validation"
import ast
import pathlib
import sys

for name in sys.argv[1:]:
    path = pathlib.Path(name)
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
PY
pass_test "candidate shell and Python helpers pass syntax validation"

(
  # shellcheck source=../python_macos_release.env
  source "$RELEASE_CONFIG"
  [[ "$RF_MAPPING_APP_NAME" == "Free-Moving RF Viewer" ]]
  [[ "$RF_MAPPING_EXECUTABLE_NAME" == "Free-Moving RF Viewer" ]]
  [[ "$RF_MAPPING_BUNDLE_ID" == "org.local.rfmapping.viewer.freemoving" ]]
  [[ "$RF_MAPPING_APP_VERSION" == "1.10.0.1" ]]
  [[ "$RF_MAPPING_APP_BUILD" == "110001" ]]
  [[ "$RF_MAPPING_RELEASE_EDITION" == "FreeMovingPreview" ]]
  [[ "$RF_MAPPING_RELEASE_FLAVOR" == "freemoving-preview" ]]
  [[ "$RF_MAPPING_APP_ARCHITECTURE" == "arm64" ]]
  [[ "$RF_MAPPING_MINIMUM_MACOS_VERSION" == "14.0" ]]
) || fail_test "canonical free-moving preview metadata is incomplete or unexpected"
pass_test "canonical metadata identifies Python 1.10.0.1 FreeMovingPreview build 110001"

python3 - "$PYPROJECT" "$REQUIREMENTS" <<'PY' \
  || fail_test "package metadata is inconsistent"
import pathlib
import sys
import tomllib

pyproject = tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert pyproject["project"]["version"] == "1.10.0.1"
assert "h5py>=3.16,<4" in pyproject["project"]["dependencies"]
assert "tkinterdnd2==0.6.2" in pyproject["project"]["dependencies"]
requirements = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8").splitlines()
assert "h5py>=3.16,<4" in requirements
assert "tkinterdnd2==0.6.2" in requirements
PY
pass_test "package metadata includes HDF5 and the pinned TkDND runtime"

for marker in \
  'source "$SCRIPT_DIR/python_macos_release.env"' \
  'BUILD_VENV="$(canonical_scoped_build_cache_path "Build virtual environment" "$BUILD_VENV")"' \
  'WORK_DIR="$(canonical_scoped_build_cache_path "Build work directory" "$WORK_DIR")"' \
  'require_safe_removal_target "$APP_BUNDLE"' \
  'require_safe_removal_target "$WORK_DIR"' \
  '[[ ! -L "$dist_parent" ]]' \
  '[[ ! -L "$DIST_DIR" ]]' \
  '# Recheck immediately before destructive cleanup.'; do
  assert_file_contains "$BUILD_SCRIPT" "$marker"
done
pass_test "current distribution and cleanup safety guards are retained"

for marker in \
  'H5PY_VERSION="3.16.0"' \
  'TKINTERDND2_VERSION="0.6.2"' \
  '--additional-hooks-dir "$PYINSTALLER_HOOKS"' \
  '--add-data "$SUPPORT_DOCUMENTATION:."' \
  '"$BUILD_VENV/bin/python" "$SMOKE_FIXTURE_GENERATOR" "$SMOKE_RF"' \
  '"$APP_BINARY" --self-test "$SMOKE_RF"' \
  '"$APP_BINARY" --self-test-dnd' \
  'Add :LSMinimumSystemVersion string $MINIMUM_MACOS_VERSION' \
  'Add :RFMappingReleaseEdition string $RELEASE_EDITION' \
  'Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 string rfmap' \
  'Add :UTExportedTypeDeclarations:0:UTTypeIdentifier string org.local.rfmapping.rfmap' \
  'find "$DATA_SOURCE" -type f -iname '\''*.rfmap'\''' \
  'SIGNING_IDENTITY="${RF_MAPPING_CODESIGN_IDENTITY:-${CODE_SIGN_IDENTITY:--}}"' \
  'SIGN_ARGUMENTS+=(--options runtime --timestamp)' \
  '/usr/bin/xattr -cr "$bundle"' \
  'ditto -c -k --norsrc --keepParent' \
  'verify_archive_metadata "$ARCHIVE_PATH"' \
  'find "$APP_BUNDLE" -type f -print0' \
  'shasum -a 256 -c "$CHECKSUM_NAME"'; do
  assert_file_contains "$BUILD_SCRIPT" "$marker"
done
assert_file_not_contains "$BUILD_SCRIPT" 'Add :CFBundleDocumentTypes:1'
assert_file_contains "$BUILD_SCRIPT" 'verify_plist_missing CFBundleDocumentTypes:1'
assert_file_contains "$BUILD_SCRIPT" 'verify_archive_plist_missing CFBundleDocumentTypes.1'
assert_file_not_contains "$BUILD_SCRIPT" 'Add :UTExportedTypeDeclarations:1'
if /usr/bin/grep -F -- '--sequesterRsrc' "$BUILD_SCRIPT" >/dev/null; then
  fail_test "build script still requests AppleDouble resource-fork entries"
fi
pass_test "FM preview bundle has HDF5 smoke tests, one document type, signing, and clean archives"

# shellcheck source=../build_python_macos_app.sh
source "$BUILD_SCRIPT"
[[ "$ARCHIVE_NAME" == "Free_Moving_RF_Viewer-python-1.10.0.1-freemoving-preview-macos-arm64.zip" ]] \
  || fail_test "archive name does not preserve version and preview flavor"
[[ "$CHECKSUM_NAME" == "SHA256SUMS-python-1.10.0.1-freemoving-preview.txt" ]] \
  || fail_test "checksum name does not preserve version and preview flavor"
pass_test "FM preview artifacts are independently named from the stable viewer"

FIXTURE_ROOT="$(/usr/bin/mktemp -d /tmp/rfmapping-release-candidate-test.XXXXXX)"

AUDIT_FIXTURE="$FIXTURE_ROOT/metadata"
/bin/mkdir -p "$AUDIT_FIXTURE"
/bin/cp "$PYPROJECT" "$AUDIT_FIXTURE/pyproject.toml"
/bin/cp "$REQUIREMENTS" "$AUDIT_FIXTURE/requirements.txt"
printf '%s\n' \
  'APP_VERSION = "1.10.0.1"' \
  'APP_EDITION = "FreeMovingPreview"' \
  'DND_SMOKE_ARGUMENT = "--self-test-dnd"' \
  >"$AUDIT_FIXTURE/rfmapping_fm_gui.py"
python3 "$METADATA_AUDITOR" "$AUDIT_FIXTURE" 1.10.0.1 FreeMovingPreview >/dev/null \
  || fail_test "metadata auditor rejected a matching FM preview"
printf '%s\n' \
  'APP_VERSION = "1.9.0"' \
  'APP_EDITION = "Full"' \
  'DND_SMOKE_ARGUMENT = "--self-test-dnd"' \
  >"$AUDIT_FIXTURE/rfmapping_fm_gui.py"
if python3 "$METADATA_AUDITOR" "$AUDIT_FIXTURE" 1.10.0.1 FreeMovingPreview >/dev/null 2>&1; then
  fail_test "metadata auditor accepted stale Full source as the FM preview"
fi
pass_test "metadata auditor rejects cross-edition or stale source versions"

BUILD_GUARD_FIXTURE="$FIXTURE_ROOT/build-distribution-guard"
/bin/mkdir -p "$BUILD_GUARD_FIXTURE/repo/dist" "$BUILD_GUARD_FIXTURE/outside"
printf 'sentinel\n' >"$BUILD_GUARD_FIXTURE/outside/keep"
/bin/ln -s "$BUILD_GUARD_FIXTURE/outside" "$BUILD_GUARD_FIXTURE/repo/dist/python"
(
  # shellcheck source=../build_python_macos_app.sh
  source "$BUILD_SCRIPT"
  ROOT_DIR="$BUILD_GUARD_FIXTURE/repo"
  DIST_DIR="$ROOT_DIR/dist/python"
  validate_distribution_tree
) >/dev/null 2>&1 && fail_test "symlinked distribution directory was accepted"
[[ -f "$BUILD_GUARD_FIXTURE/outside/keep" ]] \
  || fail_test "distribution guard followed a symlink and damaged its sentinel"
/bin/rm -- "$BUILD_GUARD_FIXTURE/repo/dist/python"
/bin/mkdir "$BUILD_GUARD_FIXTURE/repo/dist/python"
(
  # shellcheck source=../build_python_macos_app.sh
  source "$BUILD_SCRIPT"
  ROOT_DIR="$BUILD_GUARD_FIXTURE/repo"
  DIST_DIR="$ROOT_DIR/dist/python"
  validate_distribution_tree
) >/dev/null || fail_test "physical in-repository dist/python was rejected"
pass_test "distribution fixture rejects symlink escape and accepts physical output"

ARCHIVE_FIXTURE="$FIXTURE_ROOT/archive"
/bin/mkdir -p "$ARCHIVE_FIXTURE"
python3 - "$ARCHIVE_FIXTURE" <<'PY'
import pathlib
import sys
import zipfile

root = pathlib.Path(sys.argv[1])
with zipfile.ZipFile(root / "clean.zip", "w") as archive:
    archive.writestr("Free-Moving RF Viewer.app/Contents/Info.plist", "clean")
with zipfile.ZipFile(root / "appledouble.zip", "w") as archive:
    archive.writestr("Free-Moving RF Viewer.app/Contents/Info.plist", "clean")
    archive.writestr("__MACOSX/Free-Moving RF Viewer.app/Contents/._Info.plist", "bad")
PY
(
  # shellcheck source=../build_python_macos_app.sh
  source "$BUILD_SCRIPT"
  verify_archive_metadata "$ARCHIVE_FIXTURE/clean.zip"
) >/dev/null || fail_test "clean archive fixture was rejected"
if (
  # shellcheck source=../build_python_macos_app.sh
  source "$BUILD_SCRIPT"
  verify_archive_metadata "$ARCHIVE_FIXTURE/appledouble.zip"
) >/dev/null 2>&1; then
  fail_test "AppleDouble archive fixture was accepted"
fi
pass_test "archive fixture rejects __MACOSX and AppleDouble entries"

SMOKE_RF="$FIXTURE_ROOT/release-smoke.rfmap"
"$TEST_PYTHON" "$SMOKE_GENERATOR" "$SMOKE_RF"
"$TEST_PYTHON" - "$SMOKE_RF" <<'PY' \
  || fail_test "synthetic frozen-executable smoke HDF5 is malformed"
import h5py
import sys

with h5py.File(sys.argv[1], "r") as file:
    assert file.attrs["format"] == "rfmapping_fm_hdf5_v1"
    assert file["/rf/rate_hz"].shape == (3, 2, 2, 1)
    assert file["/units/id"][...].reshape(-1).tolist() == [101]
PY
pass_test "release smoke fixture is minimal, structural, and explicitly free-moving"

echo "1..$TEST_COUNT"

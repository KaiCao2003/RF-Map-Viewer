#!/usr/bin/env bash
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd "$TEST_DIR/.." && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALLER="$SCRIPT_DIR/install_python_macos_app.sh"
BUILD_SCRIPT="$SCRIPT_DIR/build_python_macos_app.sh"
RUN_SCRIPT="$SCRIPT_DIR/build_and_run.sh"
SWAP_SOURCE="$SCRIPT_DIR/rename_swap_macos.c"
SWAP_TEST_SHIM="$TEST_DIR/macos_rename_api_shim.h"
RELEASE_CONFIG="$SCRIPT_DIR/python_macos_release.env"

# Load the candidate identity once. Every fixture below derives its candidate
# version/build from this canonical release contract.
# shellcheck source=../python_macos_release.env
source "$RELEASE_CONFIG"
CANDIDATE_VERSION="$RF_MAPPING_APP_VERSION"
CANDIDATE_BUILD="$RF_MAPPING_APP_BUILD"

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
    || fail_test "$file does not contain required safety marker: $text"
}

assert_file_not_contains() {
  local file="$1"
  local text="$2"
  if /usr/bin/grep -F -- "$text" "$file" >/dev/null; then
    fail_test "$file contains forbidden safety marker: $text"
  fi
}

cleanup() {
  if [[ -n "$FIXTURE_ROOT" && -d "$FIXTURE_ROOT" ]]; then
    case "$FIXTURE_ROOT" in
      /tmp/rfmapping-installer-test.*)
        /bin/rm -rf -- "$FIXTURE_ROOT"
        ;;
      *)
        echo "refusing unsafe fixture cleanup: $FIXTURE_ROOT" >&2
        ;;
    esac
  fi
}
trap cleanup EXIT HUP INT TERM

for script in "$INSTALLER" "$BUILD_SCRIPT" "$RUN_SCRIPT"; do
  /bin/bash -n "$script" || fail_test "bash syntax failed for $script"
done
pass_test "release shell scripts pass bash syntax validation"

CC_BIN="$(command -v cc)" || fail_test "a C compiler is required for helper syntax validation"
"$CC_BIN" -Wall -Wextra -Werror -fsyntax-only "$SWAP_SOURCE" \
  || fail_test "non-macOS helper branch failed C syntax validation"
"$CC_BIN" \
  -D__APPLE__ \
  -include "$SWAP_TEST_SHIM" \
  -Wall \
  -Wextra \
  -Werror \
  -fsyntax-only \
  "$SWAP_SOURCE" \
  || fail_test "macOS helper branch failed C syntax validation against the API shim"
pass_test "atomic helper passes non-macOS and macOS-API C syntax validation"

(
  [[ "$RF_MAPPING_APP_NAME" == "Free-Moving RF Viewer" ]]
  [[ "$RF_MAPPING_EXECUTABLE_NAME" == "Free-Moving RF Viewer" ]]
  [[ "$RF_MAPPING_BUNDLE_ID" == "org.local.rfmapping.viewer.freemoving" ]]
  [[ "$RF_MAPPING_APP_VERSION" == "1.10.0" ]]
  [[ "$RF_MAPPING_APP_PRERELEASE" == "alpha.3" ]]
  [[ "$RF_MAPPING_PACKAGE_VERSION" == "1.10.0a3" ]]
  [[ "$RF_MAPPING_APP_BUILD" == "110003" ]]
  [[ "$RF_MAPPING_RELEASE_EDITION" == "FreeMovingAlpha" ]]
  [[ "$RF_MAPPING_RELEASE_FLAVOR" == "freemoving" ]]
  [[ "$RF_MAPPING_APP_ARCHITECTURE" == "arm64" ]]
  [[ "$RF_MAPPING_MINIMUM_MACOS_VERSION" == "14.0" ]]
) || fail_test "release metadata is incomplete or unexpected"
assert_file_contains "$BUILD_SCRIPT" 'source "$SCRIPT_DIR/python_macos_release.env"'
assert_file_contains "$RUN_SCRIPT" 'source "$SCRIPT_DIR/python_macos_release.env"'
assert_file_contains "$INSTALLER" 'source "$SCRIPT_DIR/python_macos_release.env"'
assert_file_contains "$BUILD_SCRIPT" 'BUILD_VENV="$(canonical_scoped_build_cache_path "Build virtual environment" "$BUILD_VENV")"'
assert_file_contains "$BUILD_SCRIPT" 'WORK_DIR="$(canonical_scoped_build_cache_path "Build work directory" "$WORK_DIR")"'
assert_file_contains "$BUILD_SCRIPT" '[[ "$name" == rfmapping-* ]]'
assert_file_contains "$BUILD_SCRIPT" 'require_safe_removal_target "$APP_BUNDLE"'
assert_file_contains "$BUILD_SCRIPT" 'require_safe_removal_target "$WORK_DIR"'
assert_file_contains "$BUILD_SCRIPT" '[[ ! -L "$dist_parent" ]]'
assert_file_contains "$BUILD_SCRIPT" '[[ ! -L "$DIST_DIR" ]]'
assert_file_contains "$BUILD_SCRIPT" '[[ "$dist_physical" == "$root_physical/dist/python" ]]'
assert_file_contains "$BUILD_SCRIPT" '# Recheck immediately before destructive cleanup.'
assert_file_contains "$BUILD_SCRIPT" 'H5PY_VERSION="3.16.0"'
assert_file_contains "$BUILD_SCRIPT" 'TKINTERDND2_VERSION="0.6.2"'
assert_file_contains "$BUILD_SCRIPT" '--additional-hooks-dir "$PYINSTALLER_HOOKS"'
assert_file_contains "$BUILD_SCRIPT" '--add-data "$SUPPORT_DOCUMENTATION:."'
assert_file_contains "$BUILD_SCRIPT" '--stimulus square "$SMOKE_SQUARE_RF"'
assert_file_contains "$BUILD_SCRIPT" '--stimulus bar "$SMOKE_BAR_RF"'
assert_file_contains "$BUILD_SCRIPT" '"$APP_BINARY" --stimulus square --self-test "$SMOKE_SQUARE_RF"'
assert_file_contains "$BUILD_SCRIPT" '"$APP_BINARY" --stimulus bar --self-test "$SMOKE_BAR_RF"'
assert_file_contains "$BUILD_SCRIPT" '"$APP_BINARY" --self-test-dnd'
assert_file_contains "$BUILD_SCRIPT" 'Add :LSMinimumSystemVersion string $MINIMUM_MACOS_VERSION'
assert_file_contains "$BUILD_SCRIPT" 'Add :RFMappingReleaseEdition string $RELEASE_EDITION'
assert_file_contains "$BUILD_SCRIPT" 'Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 string rfmap'
assert_file_not_contains "$BUILD_SCRIPT" 'Add :CFBundleDocumentTypes:1'
assert_file_contains "$BUILD_SCRIPT" 'verify_plist_missing CFBundleDocumentTypes:1'
assert_file_contains "$BUILD_SCRIPT" 'verify_archive_plist_missing CFBundleDocumentTypes.1'
assert_file_contains "$BUILD_SCRIPT" 'Add :UTExportedTypeDeclarations:0:UTTypeIdentifier string org.local.rfmapping.rfmap'
assert_file_not_contains "$BUILD_SCRIPT" 'Add :UTExportedTypeDeclarations:1'
assert_file_contains "$BUILD_SCRIPT" 'find "$DATA_SOURCE" -type f -iname '\''*.rfmap'\'''
assert_file_contains "$BUILD_SCRIPT" 'SIGN_ARGUMENTS+=(--options runtime --timestamp)'
assert_file_contains "$BUILD_SCRIPT" '/usr/bin/xattr -cr "$bundle"'
assert_file_contains "$BUILD_SCRIPT" 'ditto -c -k --norsrc --keepParent'
assert_file_contains "$BUILD_SCRIPT" 'verify_archive_metadata "$ARCHIVE_PATH"'
assert_file_contains "$BUILD_SCRIPT" 'ARCHIVE_NAME="Free_Moving_RF_Viewer-python-$RELEASE_VERSION-$RELEASE_FLAVOR-macos-$APP_ARCHITECTURE.zip"'
if /usr/bin/grep -F -- '--sequesterRsrc' "$BUILD_SCRIPT" >/dev/null; then
  fail_test "build script still requests AppleDouble resource-fork entries"
fi
assert_file_contains "$INSTALLER" '[[ "$TARGET_PARENT" != "/" ]]'
assert_file_contains "$INSTALLER" 'xcrun --sdk macosx --show-sdk-path'
assert_file_contains "$INSTALLER" '-isysroot "$sdk_path"'
assert_file_contains "$INSTALLER" 'EXPECTED_EDITION="$RF_MAPPING_RELEASE_EDITION"'
assert_file_contains "$INSTALLER" 'EXPECTED_MINIMUM_MACOS_VERSION="$RF_MAPPING_MINIMUM_MACOS_VERSION"'
assert_file_contains "$INSTALLER" 'verify_plist_value "$bundle" RFMappingReleaseEdition "$EXPECTED_EDITION"'
assert_file_contains "$INSTALLER" 'verify_plist_value "$bundle" LSMinimumSystemVersion "$EXPECTED_MINIMUM_MACOS_VERSION"'
assert_file_contains "$INSTALLER" 'validate_release_contract "$bundle"'
assert_file_contains "$INSTALLER" 'validate_document_contract "$bundle"'
assert_file_contains "$INSTALLER" 'CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 rfmap'
assert_file_contains "$INSTALLER" 'FreeMovingAlpha)'
assert_file_contains "$INSTALLER" 'verify_plist_missing "$bundle" CFBundleDocumentTypes:1'
pass_test "build, run, and install scripts share canonical release metadata"

assert_file_contains "$INSTALLER" 'ACTION="preflight"'
assert_file_contains "$INSTALLER" 'validate_bundle "$STAGED_BUNDLE" 1'
assert_file_contains "$INSTALLER" 'verify_matching_cdhash "$STAGED_BUNDLE" "$source_cdhash"'
assert_file_contains "$INSTALLER" 'atomic_swap "$STAGED_BUNDLE" "$TARGET_BUNDLE"'
assert_file_contains "$INSTALLER" 'atomic_publish_exclusive "$STAGED_BUNDLE" "$TARGET_BUNDLE"'
assert_file_contains "$INSTALLER" 'rollback_active_transaction'
assert_file_contains "$INSTALLER" 'TRANSACTION_KIND="swap-pending"'
assert_file_contains "$INSTALLER" 'resolve_pending_transaction'
assert_file_contains "$INSTALLER" 'persist_recovery_bundle "$ROLLBACK_BUNDLE" "$backup_path"'
assert_file_contains "$INSTALLER" 'persist_recovery_bundle "$ROLLBACK_BUNDLE" "$replaced_path"'
assert_file_contains "$INSTALLER" 'RECOVERY_PERSIST_PENDING=1'
assert_file_contains "$INSTALLER" 'bundle_has_identity "$TARGET_BUNDLE" "$TRANSACTION_CANDIDATE_ID"'
assert_file_contains "$INSTALLER" '"Requested-backup restoration"'
assert_file_contains "$INSTALLER" '[[ "$parent" == "$TARGET_PARENT" && "$name" == .rfmapping-stage.* ]]'
assert_file_contains "$SWAP_SOURCE" 'flags = RENAME_SWAP;'
assert_file_contains "$SWAP_SOURCE" 'flags = RENAME_EXCL;'
assert_file_contains "$SWAP_SOURCE" 'renamex_np(argv[2], argv[3], flags)'
pass_test "installer statically contains preflight, verification, atomic swap, guarded cleanup, and rollback gates"

FIXTURE_ROOT="$(/usr/bin/mktemp -d /tmp/rfmapping-installer-test.XXXXXX)"

run_release_contract_fixture() (
  local fixture="$1"
  # shellcheck source=../install_python_macos_app.sh
  source "$INSTALLER"
  plist_value() {
    local bundle="$1"
    local key="$2"
    /bin/cat "$bundle/$key"
  }
  validate_release_contract "$fixture"
)

RELEASE_CONTRACT_FIXTURE="$FIXTURE_ROOT/release-contract"
/bin/mkdir -p "$RELEASE_CONTRACT_FIXTURE"
printf '%s\n' "$RF_MAPPING_APP_VERSION" >"$RELEASE_CONTRACT_FIXTURE/CFBundleShortVersionString"
printf '%s\n' "$RF_MAPPING_APP_BUILD" >"$RELEASE_CONTRACT_FIXTURE/CFBundleVersion"
printf '%s\n' "$RF_MAPPING_RELEASE_EDITION" >"$RELEASE_CONTRACT_FIXTURE/RFMappingReleaseEdition"
printf '%s\n' "$RF_MAPPING_MINIMUM_MACOS_VERSION" >"$RELEASE_CONTRACT_FIXTURE/LSMinimumSystemVersion"
run_release_contract_fixture "$RELEASE_CONTRACT_FIXTURE" >/dev/null \
  || fail_test "installer rejected the canonical FM alpha release contract"
printf '%s\n' Full >"$RELEASE_CONTRACT_FIXTURE/RFMappingReleaseEdition"
if run_release_contract_fixture "$RELEASE_CONTRACT_FIXTURE" >/dev/null 2>&1; then
  fail_test "installer accepted Full metadata as the FM alpha candidate"
fi
printf '%s\n' "$RF_MAPPING_RELEASE_EDITION" >"$RELEASE_CONTRACT_FIXTURE/RFMappingReleaseEdition"
printf '%s\n' 13.0 >"$RELEASE_CONTRACT_FIXTURE/LSMinimumSystemVersion"
if run_release_contract_fixture "$RELEASE_CONTRACT_FIXTURE" >/dev/null 2>&1; then
  fail_test "installer accepted an incorrect minimum macOS version"
fi
pass_test "installer rejects cross-edition and incorrect minimum-version candidates"

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
  || fail_test "distribution guard followed the symlink and damaged its sentinel"
/bin/rm -- "$BUILD_GUARD_FIXTURE/repo/dist/python"
/bin/mkdir "$BUILD_GUARD_FIXTURE/repo/dist/python"
(
  # shellcheck source=../build_python_macos_app.sh
  source "$BUILD_SCRIPT"
  ROOT_DIR="$BUILD_GUARD_FIXTURE/repo"
  DIST_DIR="$ROOT_DIR/dist/python"
  validate_distribution_tree
) >/dev/null || fail_test "physical in-repository distribution directory was rejected"
pass_test "build cleanup guard rejects symlink escape and accepts physical dist/python"

(
  # shellcheck source=../install_python_macos_app.sh
  source "$INSTALLER"
  TARGET_PARENT="$FIXTURE_ROOT"
  STAGE_ROOT="$FIXTURE_ROOT/not-a-stage-directory"
  /bin/mkdir -p "$STAGE_ROOT"
  printf 'sentinel\n' >"$STAGE_ROOT/keep"
  safe_stage_cleanup
) >/dev/null 2>&1 && fail_test "unsafe cleanup target was accepted"
[[ -f "$FIXTURE_ROOT/not-a-stage-directory/keep" ]] \
  || fail_test "unsafe cleanup test damaged its sentinel"
pass_test "stage cleanup rejects paths outside its narrow generated prefix"

fixture_bundle() {
  local path="$1"
  local release="$2"
  local version="$3"
  local build="$4"
  /bin/mkdir -p "$path"
  printf '%s\n' "$release" >"$path/RELEASE"
  printf '%s\n' "$version" >"$path/VERSION"
  printf '%s\n' "$build" >"$path/BUILD"
  printf '%s\n' "$release-$version-$build" >"$path/CDHASH"
}

run_fixture_transaction() (
  local fixture="$1"
  local operation="$2"
  local fail_installed_validation="${3:-0}"
  local race_first_install="${4:-0}"
  local fail_atomic_after_swap="${5:-0}"
  local fail_atomic_after_publish="${6:-0}"
  local race_backup_persistence="${7:-0}"
  local fail_persistence_after_publish="${8:-0}"
  local replace_target_before_recovery="${9:-0}"

  # shellcheck source=../install_python_macos_app.sh
  source "$INSTALLER"
  SOURCE_BUNDLE="$fixture/source/$APP_NAME.app"
  TARGET_BUNDLE="$fixture/Applications/$APP_NAME.app"
  FIXTURE_SWAP_COUNT=0
  FIXTURE_FAIL_INSTALLED_VALIDATION="$fail_installed_validation"
  FIXTURE_RACE_FIRST_INSTALL="$race_first_install"
  FIXTURE_FAIL_ATOMIC_AFTER_SWAP="$fail_atomic_after_swap"
  FIXTURE_FAIL_ATOMIC_AFTER_PUBLISH="$fail_atomic_after_publish"
  FIXTURE_RACE_BACKUP_PERSISTENCE="$race_backup_persistence"
  FIXTURE_FAIL_PERSISTENCE_AFTER_PUBLISH="$fail_persistence_after_publish"
  FIXTURE_REPLACE_TARGET_BEFORE_RECOVERY="$replace_target_before_recovery"
  FIXTURE_TARGET_WAS_REPLACED=0

  preflight_platform() { :; }
  ensure_app_is_not_running() { :; }
  compile_swap_tool() { SWAP_TOOL=/bin/true; }
  copy_release_to_stage() { /bin/cp -a "$SOURCE_BUNDLE" "$STAGED_BUNDLE"; }
  plist_value() {
    case "$2" in
      CFBundleShortVersionString) /bin/cat "$1/VERSION" ;;
      CFBundleVersion) /bin/cat "$1/BUILD" ;;
      *) return 1 ;;
    esac
  }
  bundle_version() { /bin/cat "$1/VERSION"; }
  bundle_build() { /bin/cat "$1/BUILD"; }
  bundle_cdhash() { /bin/cat "$1/CDHASH"; }
  path_identity() {
    if [[ "$(/usr/bin/uname -s)" == "Darwin" ]]; then
      /usr/bin/stat -f '%d:%i' "$1"
    else
      /usr/bin/stat -c '%d:%i' "$1"
    fi
  }
  validate_bundle() {
    local bundle="$1"
    local exact="$2"
    [[ -d "$bundle" && -f "$bundle/RELEASE" && -f "$bundle/CDHASH" ]] || return 1
    if [[ "$exact" -eq 1 ]]; then
      [[ "$(/bin/cat "$bundle/RELEASE")" == "new" ]] || return 1
    fi
    if [[ "$FIXTURE_FAIL_INSTALLED_VALIDATION" -eq 1 \
      && -n "$TRANSACTION_KIND" \
      && "$bundle" == "$TARGET_BUNDLE" ]]; then
      return 1
    fi
    if [[ "$FIXTURE_REPLACE_TARGET_BEFORE_RECOVERY" -eq 1 \
      && "$FIXTURE_TARGET_WAS_REPLACED" -eq 0 \
      && -n "$TRANSACTION_KIND" \
      && "$bundle" == "$TARGET_BUNDLE" ]]; then
      FIXTURE_TARGET_WAS_REPLACED=1
      /bin/mv -- "$TARGET_BUNDLE" "$fixture/displaced-published.app"
      fixture_bundle "$TARGET_BUNDLE" concurrent 9.9.9 99999
      return 1
    fi
  }
  atomic_swap() {
    local first="$1"
    local second="$2"
    local temporary="$TARGET_PARENT/.fixture-swap.$$"
    /bin/mv -- "$first" "$temporary"
    /bin/mv -- "$second" "$first"
    /bin/mv -- "$temporary" "$second"
    FIXTURE_SWAP_COUNT=$((FIXTURE_SWAP_COUNT + 1))
    if [[ "$FIXTURE_FAIL_ATOMIC_AFTER_SWAP" -eq 1 && "$FIXTURE_SWAP_COUNT" -eq 1 ]]; then
      return 1
    fi
  }
  atomic_publish_exclusive() {
    local source="$1"
    local destination="$2"
    if [[ "$FIXTURE_RACE_FIRST_INSTALL" -eq 1 \
      && "$TRANSACTION_KIND" == "new-pending" ]]; then
      /bin/mkdir -p "$destination"
      printf 'racer\n' >"$destination/RACER_SENTINEL"
    fi
    if [[ "$FIXTURE_RACE_BACKUP_PERSISTENCE" -eq 1 \
      && "$RECOVERY_PERSIST_PENDING" -eq 1 ]]; then
      /bin/mkdir -p "$destination"
      printf 'racer\n' >"$destination/RACER_SENTINEL"
    fi
    [[ ! -e "$destination" ]] || return 1
    /bin/mv -- "$source" "$destination"
    if [[ "$FIXTURE_FAIL_ATOMIC_AFTER_PUBLISH" -eq 1 \
      && "$TRANSACTION_KIND" == "new-pending" ]]; then
      return 1
    fi
    if [[ "$FIXTURE_FAIL_PERSISTENCE_AFTER_PUBLISH" -eq 1 \
      && "$RECOVERY_PERSIST_PENDING" -eq 1 ]]; then
      FIXTURE_FAIL_PERSISTENCE_AFTER_PUBLISH=0
      return 1
    fi
  }

  trap handle_exit EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM

  case "$operation" in
    install)
      install_release
      ;;
    rollback)
      ROLLBACK_REQUEST="$fixture/Applications/.rfmapping-backups/$APP_NAME-previous-1.8.3-build10803-fixture.app"
      rollback_release
      ;;
    *)
      return 2
      ;;
  esac
)

SUCCESS_FIXTURE="$FIXTURE_ROOT/success"
/bin/mkdir -p "$SUCCESS_FIXTURE/source" "$SUCCESS_FIXTURE/Applications"
fixture_bundle "$SUCCESS_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
fixture_bundle "$SUCCESS_FIXTURE/Applications/Free-Moving RF Viewer.app" old 1.8.3 10803
run_fixture_transaction "$SUCCESS_FIXTURE" install >/dev/null \
  || fail_test "successful replacement fixture failed"
[[ "$(/bin/cat "$SUCCESS_FIXTURE/Applications/Free-Moving RF Viewer.app/RELEASE")" == "new" ]] \
  || fail_test "successful replacement did not publish the new app"
SUCCESS_BACKUPS=("$SUCCESS_FIXTURE"/Applications/.rfmapping-backups/*.app)
[[ "${#SUCCESS_BACKUPS[@]}" -eq 1 \
  && "$(/bin/cat "${SUCCESS_BACKUPS[0]}/RELEASE")" == "old" ]] \
  || fail_test "successful replacement did not retain exactly one old-app backup"
[[ ! -e "$SUCCESS_FIXTURE/Applications/.rfmapping-install.lock" ]] \
  || fail_test "successful replacement left its lock behind"
if compgen -G "$SUCCESS_FIXTURE/Applications/.rfmapping-stage.*" >/dev/null; then
  fail_test "successful replacement left a staging directory behind"
fi
pass_test "fixture replacement publishes the new app and retains the old app"

FAILURE_FIXTURE="$FIXTURE_ROOT/failure"
/bin/mkdir -p "$FAILURE_FIXTURE/source" "$FAILURE_FIXTURE/Applications"
fixture_bundle "$FAILURE_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
fixture_bundle "$FAILURE_FIXTURE/Applications/Free-Moving RF Viewer.app" old 1.8.3 10803
set +e
run_fixture_transaction "$FAILURE_FIXTURE" install 1 >/dev/null 2>&1
FAILURE_STATUS=$?
set -e
[[ "$FAILURE_STATUS" -ne 0 ]] || fail_test "post-swap validation failure unexpectedly succeeded"
[[ "$(/bin/cat "$FAILURE_FIXTURE/Applications/Free-Moving RF Viewer.app/RELEASE")" == "old" ]] \
  || fail_test "post-swap validation failure did not restore the old app"
[[ ! -e "$FAILURE_FIXTURE/Applications/.rfmapping-install.lock" ]] \
  || fail_test "successful automatic rollback left its lock behind"
if compgen -G "$FAILURE_FIXTURE/Applications/.rfmapping-stage.*" >/dev/null; then
  fail_test "successful automatic rollback left a staging directory behind"
fi
pass_test "post-swap validation failure automatically restores the previous app"

ATOMIC_RETURN_FIXTURE="$FIXTURE_ROOT/atomic-return-failure"
/bin/mkdir -p "$ATOMIC_RETURN_FIXTURE/source" "$ATOMIC_RETURN_FIXTURE/Applications"
fixture_bundle "$ATOMIC_RETURN_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
fixture_bundle "$ATOMIC_RETURN_FIXTURE/Applications/Free-Moving RF Viewer.app" old 1.8.3 10803
set +e
run_fixture_transaction "$ATOMIC_RETURN_FIXTURE" install 0 0 1 >/dev/null 2>&1
ATOMIC_RETURN_STATUS=$?
set -e
[[ "$ATOMIC_RETURN_STATUS" -ne 0 ]] || fail_test "post-syscall helper failure unexpectedly succeeded"
[[ "$(/bin/cat "$ATOMIC_RETURN_FIXTURE/Applications/Free-Moving RF Viewer.app/RELEASE")" == "old" ]] \
  || fail_test "pending transaction recovery did not restore the old app"
[[ ! -e "$ATOMIC_RETURN_FIXTURE/Applications/.rfmapping-install.lock" ]] \
  || fail_test "pending transaction recovery left its lock behind"
pass_test "inode-based pending state recovers a swap completed before helper failure"

ROLLBACK_VALIDATION_FAILURE_FIXTURE="$FIXTURE_ROOT/rollback-validation-failure"
/bin/mkdir -p \
  "$ROLLBACK_VALIDATION_FAILURE_FIXTURE/source" \
  "$ROLLBACK_VALIDATION_FAILURE_FIXTURE/Applications/.rfmapping-backups"
fixture_bundle "$ROLLBACK_VALIDATION_FAILURE_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
fixture_bundle "$ROLLBACK_VALIDATION_FAILURE_FIXTURE/Applications/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
ROLLBACK_VALIDATION_REQUEST="$ROLLBACK_VALIDATION_FAILURE_FIXTURE/Applications/.rfmapping-backups/Free-Moving RF Viewer-previous-1.8.3-build10803-fixture.app"
fixture_bundle "$ROLLBACK_VALIDATION_REQUEST" old 1.8.3 10803
set +e
run_fixture_transaction "$ROLLBACK_VALIDATION_FAILURE_FIXTURE" rollback 1 >/dev/null 2>&1
ROLLBACK_VALIDATION_FAILURE_STATUS=$?
set -e
[[ "$ROLLBACK_VALIDATION_FAILURE_STATUS" -ne 0 ]] \
  || fail_test "manual rollback validation failure unexpectedly succeeded"
[[ "$(/bin/cat "$ROLLBACK_VALIDATION_FAILURE_FIXTURE/Applications/Free-Moving RF Viewer.app/RELEASE")" == "new" ]] \
  || fail_test "manual rollback validation failure did not restore the installed app"
[[ "$(/bin/cat "$ROLLBACK_VALIDATION_REQUEST/RELEASE")" == "old" ]] \
  || fail_test "manual rollback validation failure deleted the requested backup"
[[ ! -e "$ROLLBACK_VALIDATION_FAILURE_FIXTURE/Applications/.rfmapping-install.lock" ]] \
  || fail_test "manual rollback validation recovery left its lock behind"
pass_test "manual rollback validation failure restores the app and retains the requested backup"

ROLLBACK_HELPER_FAILURE_FIXTURE="$FIXTURE_ROOT/rollback-helper-return-failure"
/bin/mkdir -p \
  "$ROLLBACK_HELPER_FAILURE_FIXTURE/source" \
  "$ROLLBACK_HELPER_FAILURE_FIXTURE/Applications/.rfmapping-backups"
fixture_bundle "$ROLLBACK_HELPER_FAILURE_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
fixture_bundle "$ROLLBACK_HELPER_FAILURE_FIXTURE/Applications/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
ROLLBACK_HELPER_REQUEST="$ROLLBACK_HELPER_FAILURE_FIXTURE/Applications/.rfmapping-backups/Free-Moving RF Viewer-previous-1.8.3-build10803-fixture.app"
fixture_bundle "$ROLLBACK_HELPER_REQUEST" old 1.8.3 10803
set +e
run_fixture_transaction "$ROLLBACK_HELPER_FAILURE_FIXTURE" rollback 0 0 1 >/dev/null 2>&1
ROLLBACK_HELPER_FAILURE_STATUS=$?
set -e
[[ "$ROLLBACK_HELPER_FAILURE_STATUS" -ne 0 ]] \
  || fail_test "manual rollback helper-return failure unexpectedly succeeded"
[[ "$(/bin/cat "$ROLLBACK_HELPER_FAILURE_FIXTURE/Applications/Free-Moving RF Viewer.app/RELEASE")" == "new" ]] \
  || fail_test "manual rollback helper-return recovery did not restore the installed app"
[[ "$(/bin/cat "$ROLLBACK_HELPER_REQUEST/RELEASE")" == "old" ]] \
  || fail_test "manual rollback helper-return recovery deleted the requested backup"
pass_test "inode recovery after manual rollback helper failure retains the requested backup"

FIRST_INSTALL_FIXTURE="$FIXTURE_ROOT/first-install-failure"
/bin/mkdir -p "$FIRST_INSTALL_FIXTURE/source" "$FIRST_INSTALL_FIXTURE/Applications"
fixture_bundle "$FIRST_INSTALL_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
set +e
run_fixture_transaction "$FIRST_INSTALL_FIXTURE" install 1 >/dev/null 2>&1
FIRST_INSTALL_STATUS=$?
set -e
[[ "$FIRST_INSTALL_STATUS" -ne 0 ]] || fail_test "failed first-install validation unexpectedly succeeded"
[[ ! -e "$FIRST_INSTALL_FIXTURE/Applications/Free-Moving RF Viewer.app" ]] \
  || fail_test "failed first installation left an app at the target"
pass_test "failed first installation withdraws the unverified target"

FIRST_PUBLISH_RETURN_FIXTURE="$FIXTURE_ROOT/first-publish-return-failure"
/bin/mkdir -p "$FIRST_PUBLISH_RETURN_FIXTURE/source" "$FIRST_PUBLISH_RETURN_FIXTURE/Applications"
fixture_bundle "$FIRST_PUBLISH_RETURN_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
set +e
run_fixture_transaction "$FIRST_PUBLISH_RETURN_FIXTURE" install 0 0 0 1 >/dev/null 2>&1
FIRST_PUBLISH_RETURN_STATUS=$?
set -e
[[ "$FIRST_PUBLISH_RETURN_STATUS" -ne 0 ]] \
  || fail_test "post-publication exclusive-helper failure unexpectedly succeeded"
[[ ! -e "$FIRST_PUBLISH_RETURN_FIXTURE/Applications/Free-Moving RF Viewer.app" ]] \
  || fail_test "pending first-publication recovery did not withdraw the candidate"
pass_test "inode-based pending state withdraws a first publication completed before helper failure"

RACE_FIXTURE="$FIXTURE_ROOT/first-install-race"
/bin/mkdir -p "$RACE_FIXTURE/source" "$RACE_FIXTURE/Applications"
fixture_bundle "$RACE_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
set +e
run_fixture_transaction "$RACE_FIXTURE" install 0 1 >/dev/null 2>&1
RACE_STATUS=$?
set -e
[[ "$RACE_STATUS" -ne 0 ]] || fail_test "first-install destination race unexpectedly succeeded"
[[ -f "$RACE_FIXTURE/Applications/Free-Moving RF Viewer.app/RACER_SENTINEL" ]] \
  || fail_test "first-install destination race damaged the competing target"
[[ ! -e "$RACE_FIXTURE/Applications/Free-Moving RF Viewer.app/Free-Moving RF Viewer.app" ]] \
  || fail_test "first-install destination race nested the release inside the competing target"
pass_test "exclusive first publication fails closed when the destination appears concurrently"

BACKUP_RACE_FIXTURE="$FIXTURE_ROOT/backup-publication-race"
/bin/mkdir -p "$BACKUP_RACE_FIXTURE/source" "$BACKUP_RACE_FIXTURE/Applications"
fixture_bundle "$BACKUP_RACE_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
fixture_bundle "$BACKUP_RACE_FIXTURE/Applications/Free-Moving RF Viewer.app" old 1.8.3 10803
set +e
run_fixture_transaction "$BACKUP_RACE_FIXTURE" install 0 0 0 0 1 >/dev/null 2>&1
BACKUP_RACE_STATUS=$?
set -e
[[ "$BACKUP_RACE_STATUS" -ne 0 ]] || fail_test "backup destination race unexpectedly succeeded"
[[ "$(/bin/cat "$BACKUP_RACE_FIXTURE/Applications/Free-Moving RF Viewer.app/RELEASE")" == "old" ]] \
  || fail_test "backup destination race did not restore the previous app"
BACKUP_RACE_SENTINELS=("$BACKUP_RACE_FIXTURE"/Applications/.rfmapping-backups/*.app/RACER_SENTINEL)
[[ "${#BACKUP_RACE_SENTINELS[@]}" -eq 1 && -f "${BACKUP_RACE_SENTINELS[0]}" ]] \
  || fail_test "backup destination race did not preserve the competing directory"
[[ ! -e "${BACKUP_RACE_SENTINELS[0]%/RACER_SENTINEL}/Free-Moving RF Viewer.app" ]] \
  || fail_test "backup destination race nested an app in the competing directory"
pass_test "exclusive backup publication fails closed without mv directory nesting"

BACKUP_PENDING_FIXTURE="$FIXTURE_ROOT/backup-publication-return-failure"
/bin/mkdir -p "$BACKUP_PENDING_FIXTURE/source" "$BACKUP_PENDING_FIXTURE/Applications"
fixture_bundle "$BACKUP_PENDING_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
fixture_bundle "$BACKUP_PENDING_FIXTURE/Applications/Free-Moving RF Viewer.app" old 1.8.3 10803
set +e
run_fixture_transaction "$BACKUP_PENDING_FIXTURE" install 0 0 0 0 0 1 >/dev/null 2>&1
BACKUP_PENDING_STATUS=$?
set -e
[[ "$BACKUP_PENDING_STATUS" -ne 0 ]] \
  || fail_test "post-publication backup helper failure unexpectedly succeeded"
[[ "$(/bin/cat "$BACKUP_PENDING_FIXTURE/Applications/Free-Moving RF Viewer.app/RELEASE")" == "old" ]] \
  || fail_test "backup pending-state recovery did not restore the previous app"
pass_test "inode-based pending state resolves exclusive backup publication before rollback"

ROLLBACK_PERSIST_PENDING_FIXTURE="$FIXTURE_ROOT/rollback-persistence-return-failure"
/bin/mkdir -p \
  "$ROLLBACK_PERSIST_PENDING_FIXTURE/source" \
  "$ROLLBACK_PERSIST_PENDING_FIXTURE/Applications/.rfmapping-backups"
fixture_bundle "$ROLLBACK_PERSIST_PENDING_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
fixture_bundle "$ROLLBACK_PERSIST_PENDING_FIXTURE/Applications/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
ROLLBACK_PERSIST_REQUEST="$ROLLBACK_PERSIST_PENDING_FIXTURE/Applications/.rfmapping-backups/Free-Moving RF Viewer-previous-1.8.3-build10803-fixture.app"
fixture_bundle "$ROLLBACK_PERSIST_REQUEST" old 1.8.3 10803
set +e
run_fixture_transaction "$ROLLBACK_PERSIST_PENDING_FIXTURE" rollback 0 0 0 0 0 1 >/dev/null 2>&1
ROLLBACK_PERSIST_PENDING_STATUS=$?
set -e
[[ "$ROLLBACK_PERSIST_PENDING_STATUS" -ne 0 ]] \
  || fail_test "manual rollback persistence helper failure unexpectedly succeeded"
[[ "$(/bin/cat "$ROLLBACK_PERSIST_PENDING_FIXTURE/Applications/Free-Moving RF Viewer.app/RELEASE")" == "new" ]] \
  || fail_test "manual rollback persistence recovery did not restore the installed app"
[[ "$(/bin/cat "$ROLLBACK_PERSIST_REQUEST/RELEASE")" == "old" ]] \
  || fail_test "manual rollback persistence recovery did not restore the requested backup path"
pass_test "manual rollback persistence failure restores and retains the requested backup"

IDENTITY_RACE_FIXTURE="$FIXTURE_ROOT/identity-change-before-inverse"
/bin/mkdir -p "$IDENTITY_RACE_FIXTURE/source" "$IDENTITY_RACE_FIXTURE/Applications"
fixture_bundle "$IDENTITY_RACE_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
fixture_bundle "$IDENTITY_RACE_FIXTURE/Applications/Free-Moving RF Viewer.app" old 1.8.3 10803
set +e
run_fixture_transaction "$IDENTITY_RACE_FIXTURE" install 0 0 0 0 0 0 1 >/dev/null 2>&1
IDENTITY_RACE_STATUS=$?
set -e
[[ "$IDENTITY_RACE_STATUS" -ne 0 ]] || fail_test "changed target identity unexpectedly recovered"
[[ "$(/bin/cat "$IDENTITY_RACE_FIXTURE/Applications/Free-Moving RF Viewer.app/RELEASE")" == "concurrent" ]] \
  || fail_test "identity mismatch recovery mutated the concurrent target"
[[ -d "$IDENTITY_RACE_FIXTURE/Applications/.rfmapping-install.lock" ]] \
  || fail_test "identity mismatch did not preserve the install lock"
compgen -G "$IDENTITY_RACE_FIXTURE/Applications/.rfmapping-stage.*" >/dev/null \
  || fail_test "identity mismatch did not preserve staging state"
pass_test "inverse rollback fails closed when the live target identity changes"

BACKUP_FAILURE_FIXTURE="$FIXTURE_ROOT/backup-move-failure"
/bin/mkdir -p "$BACKUP_FAILURE_FIXTURE/source" "$BACKUP_FAILURE_FIXTURE/Applications/.rfmapping-backups"
fixture_bundle "$BACKUP_FAILURE_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
fixture_bundle "$BACKUP_FAILURE_FIXTURE/Applications/Free-Moving RF Viewer.app" old 1.8.3 10803
/bin/chmod 500 "$BACKUP_FAILURE_FIXTURE/Applications/.rfmapping-backups"
set +e
run_fixture_transaction "$BACKUP_FAILURE_FIXTURE" install >/dev/null 2>&1
BACKUP_FAILURE_STATUS=$?
set -e
/bin/chmod 700 "$BACKUP_FAILURE_FIXTURE/Applications/.rfmapping-backups"
[[ "$BACKUP_FAILURE_STATUS" -ne 0 ]] || fail_test "unwritable backup directory unexpectedly succeeded"
[[ "$(/bin/cat "$BACKUP_FAILURE_FIXTURE/Applications/Free-Moving RF Viewer.app/RELEASE")" == "old" ]] \
  || fail_test "backup-move failure did not restore the previous app"
pass_test "backup persistence failure automatically restores the previous app"

ROLLBACK_FIXTURE="$FIXTURE_ROOT/manual-rollback"
/bin/mkdir -p "$ROLLBACK_FIXTURE/source" "$ROLLBACK_FIXTURE/Applications/.rfmapping-backups"
fixture_bundle "$ROLLBACK_FIXTURE/source/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
fixture_bundle "$ROLLBACK_FIXTURE/Applications/Free-Moving RF Viewer.app" new "$CANDIDATE_VERSION" "$CANDIDATE_BUILD"
fixture_bundle \
  "$ROLLBACK_FIXTURE/Applications/.rfmapping-backups/Free-Moving RF Viewer-previous-1.8.3-build10803-fixture.app" \
  old \
  1.8.3 \
  10803
run_fixture_transaction "$ROLLBACK_FIXTURE" rollback >/dev/null \
  || fail_test "manual rollback fixture failed"
[[ "$(/bin/cat "$ROLLBACK_FIXTURE/Applications/Free-Moving RF Viewer.app/RELEASE")" == "old" ]] \
  || fail_test "manual rollback did not restore the requested app"
ROLLBACK_NEW_BACKUPS=("$ROLLBACK_FIXTURE"/Applications/.rfmapping-backups/*.app)
[[ "${#ROLLBACK_NEW_BACKUPS[@]}" -eq 1 \
  && "$(/bin/cat "${ROLLBACK_NEW_BACKUPS[0]}/RELEASE")" == "new" ]] \
  || fail_test "manual rollback did not retain the replaced app"
pass_test "manual rollback atomically restores a selected backup and retains the replaced app"

echo "1..$TEST_COUNT"

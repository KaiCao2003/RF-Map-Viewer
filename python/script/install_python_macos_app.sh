#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=python_macos_release.env
source "$SCRIPT_DIR/python_macos_release.env"

APP_NAME="$RF_MAPPING_APP_NAME"
EXECUTABLE_NAME="$RF_MAPPING_EXECUTABLE_NAME"
BUNDLE_ID="$RF_MAPPING_BUNDLE_ID"
EXPECTED_VERSION="$RF_MAPPING_APP_VERSION"
EXPECTED_BUILD="$RF_MAPPING_APP_BUILD"
EXPECTED_EDITION="$RF_MAPPING_RELEASE_EDITION"
EXPECTED_ARCHITECTURE="$RF_MAPPING_APP_ARCHITECTURE"
EXPECTED_MINIMUM_MACOS_VERSION="$RF_MAPPING_MINIMUM_MACOS_VERSION"

ACTION="preflight"
ACTION_WAS_EXPLICIT=0
SOURCE_BUNDLE="$ROOT_DIR/dist/python/$APP_NAME.app"
TARGET_BUNDLE="/Applications/$APP_NAME.app"
ROLLBACK_REQUEST=""

TARGET_PARENT=""
BACKUP_ROOT=""
LOCK_DIR=""
LOCK_TOKEN=""
LOCK_HELD=0
STAGE_ROOT=""
STAGED_BUNDLE=""
SWAP_TOOL=""
TRANSACTION_KIND=""
TRANSACTION_OPERATION=""
TRANSACTION_CANDIDATE_ID=""
TRANSACTION_RECOVERY_ID=""
ROLLBACK_BUNDLE=""
RECOVERY_PERSIST_PENDING=0
RECOVERY_PERSIST_SOURCE=""
RECOVERY_PERSIST_DESTINATION=""
RECOVERY_PERSIST_ID=""
RECOVERY_PERSIST_COMPLETED=0
COMMITTED=0
PRESERVE_TRANSACTION=0
RESULT_BACKUP=""

fail() {
  echo "error: $*" >&2
  exit 1
}

warn() {
  echo "warning: $*" >&2
}

usage() {
  cat <<USAGE
usage:
  $0 [--preflight] [--source APP] [--target APP]
  $0 --install [--source APP] [--target APP]
  $0 --rollback BACKUP_APP [--target APP]

With no action, the script performs a read-only preflight. --install publishes
the verified Python $EXPECTED_VERSION $EXPECTED_EDITION ($EXPECTED_BUILD)
arm64 bundle. Existing
apps are exchanged atomically and retained below the target's sibling
.rfmapping-backups directory. --rollback atomically exchanges one of those
backups with the installed app.
USAGE
}

set_action() {
  local requested="$1"
  if [[ "$ACTION_WAS_EXPLICIT" -eq 1 ]]; then
    fail "Specify exactly one of --preflight, --install, or --rollback"
  fi
  ACTION="$requested"
  ACTION_WAS_EXPLICIT=1
}

parse_arguments() {
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      --preflight)
        set_action preflight
        shift
        ;;
      --install)
        set_action install
        shift
        ;;
      --rollback)
        [[ "$#" -ge 2 ]] || fail "--rollback requires a backup app path"
        set_action rollback
        ROLLBACK_REQUEST="$2"
        shift 2
        ;;
      --source)
        [[ "$#" -ge 2 ]] || fail "--source requires an app path"
        SOURCE_BUNDLE="$2"
        shift 2
        ;;
      --target)
        [[ "$#" -ge 2 ]] || fail "--target requires an app path"
        TARGET_BUNDLE="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "Unknown argument: $1"
        ;;
    esac
  done

  if [[ "$ACTION" == "rollback" && "$SOURCE_BUNDLE" != "$ROOT_DIR/dist/python/$APP_NAME.app" ]]; then
    fail "--source cannot be combined with --rollback"
  fi
}

require_absolute_path() {
  local label="$1"
  local value="${2%/}"
  [[ -n "$value" && "$value" == /* ]] || fail "$label must be an absolute path: $2"
  [[ "$value" != "/" ]] || fail "$label may not be the filesystem root"
}

canonical_bundle_path() {
  local input="${1%/}"
  local parent
  local name
  parent="$(/usr/bin/dirname "$input")"
  name="$(/usr/bin/basename "$input")"
  parent="$(cd -P "$parent" && pwd)"
  printf '%s/%s\n' "$parent" "$name"
}

prepare_target_paths() {
  require_absolute_path "Target app" "$TARGET_BUNDLE"
  [[ "$(/usr/bin/basename "${TARGET_BUNDLE%/}")" == "$APP_NAME.app" ]] \
    || fail "Target basename must be '$APP_NAME.app': $TARGET_BUNDLE"

  TARGET_BUNDLE="$(canonical_bundle_path "$TARGET_BUNDLE")"
  TARGET_PARENT="$(/usr/bin/dirname "$TARGET_BUNDLE")"
  BACKUP_ROOT="$TARGET_PARENT/.rfmapping-backups"
  LOCK_DIR="$TARGET_PARENT/.rfmapping-install.lock"

  [[ -d "$TARGET_PARENT" ]] || fail "Target parent is not a directory: $TARGET_PARENT"
  [[ "$TARGET_PARENT" != "/" ]] || fail "Refusing to install directly below the filesystem root"
  [[ ! -L "$TARGET_BUNDLE" ]] || fail "Refusing to replace a symlink: $TARGET_BUNDLE"
  [[ ! -e "$BACKUP_ROOT" || -d "$BACKUP_ROOT" ]] \
    || fail "Backup path exists but is not a directory: $BACKUP_ROOT"
  [[ ! -L "$BACKUP_ROOT" ]] || fail "Refusing to use a symlink as backup directory: $BACKUP_ROOT"
}

prepare_source_path() {
  require_absolute_path "Source app" "$SOURCE_BUNDLE"
  [[ "$(/usr/bin/basename "${SOURCE_BUNDLE%/}")" == "$APP_NAME.app" ]] \
    || fail "Source basename must be '$APP_NAME.app': $SOURCE_BUNDLE"
  [[ -d "$SOURCE_BUNDLE" && ! -L "$SOURCE_BUNDLE" ]] \
    || fail "Source app is missing, not a directory, or a symlink: $SOURCE_BUNDLE"
  SOURCE_BUNDLE="$(canonical_bundle_path "$SOURCE_BUNDLE")"
  [[ "$SOURCE_BUNDLE" != "$TARGET_BUNDLE" ]] \
    || fail "Source and target resolve to the same app bundle"
}

require_executable() {
  [[ -x "$1" ]] || fail "Required executable not found: $1"
}

preflight_platform() {
  [[ "$(/usr/bin/uname -s)" == "Darwin" ]] \
    || fail "This installer runs only on macOS"
  require_executable /usr/sbin/sysctl
  [[ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null)" == "1" ]] \
    || fail "Apple-silicon hardware is required"

  local tool
  for tool in \
    /bin/chmod \
    /bin/date \
    /bin/mkdir \
    /bin/mv \
    /bin/rm \
    /bin/rmdir \
    /bin/sync \
    /usr/bin/basename \
    /usr/bin/codesign \
    /usr/bin/dirname \
    /usr/bin/ditto \
    /usr/bin/file \
    /usr/bin/find \
    /usr/bin/head \
    /usr/bin/lipo \
    /usr/bin/mktemp \
    /usr/bin/pgrep \
    /usr/bin/plutil \
    /usr/bin/sed \
    /usr/bin/stat \
    /usr/bin/tr \
    /usr/bin/uname \
    /usr/bin/xcrun \
    /usr/libexec/PlistBuddy; do
    require_executable "$tool"
  done

  local clang_bin
  clang_bin="$(/usr/bin/xcrun --find clang 2>/dev/null)" \
    || fail "Xcode Command Line Tools are required for the atomic swap helper"
  [[ -x "$clang_bin" ]] \
    || fail "Xcode Command Line Tools returned a non-executable clang: $clang_bin"
}

plist_value() {
  local bundle="$1"
  local key="$2"
  /usr/libexec/PlistBuddy -c "Print :$key" "$bundle/Contents/Info.plist"
}

verify_plist_value() {
  local bundle="$1"
  local key="$2"
  local expected="$3"
  local actual
  actual="$(plist_value "$bundle" "$key")" \
    || fail "Unable to read Info.plist key $key from $bundle"
  [[ "$actual" == "$expected" ]] \
    || fail "$bundle Info.plist $key is '$actual'; expected '$expected'"
}

verify_plist_missing() {
  local bundle="$1"
  local key="$2"
  if plist_value "$bundle" "$key" >/dev/null 2>&1; then
    fail "$bundle Info.plist unexpectedly contains $key"
  fi
}

bundle_version() {
  plist_value "$1" CFBundleShortVersionString
}

bundle_build() {
  plist_value "$1" CFBundleVersion
}

validate_release_contract() {
  local bundle="$1"
  verify_plist_value "$bundle" CFBundleShortVersionString "$EXPECTED_VERSION"
  verify_plist_value "$bundle" CFBundleVersion "$EXPECTED_BUILD"
  verify_plist_value "$bundle" RFMappingReleaseEdition "$EXPECTED_EDITION"
  verify_plist_value "$bundle" LSMinimumSystemVersion "$EXPECTED_MINIMUM_MACOS_VERSION"
}

bundle_cdhash() {
  local bundle="$1"
  local details
  local cdhash
  details="$(/usr/bin/codesign -d --verbose=4 "$bundle" 2>&1)" \
    || fail "Unable to inspect the code signature for $bundle"
  cdhash="$(printf '%s\n' "$details" | /usr/bin/sed -n 's/^CDHash=//p' | /usr/bin/head -n 1)"
  [[ -n "$cdhash" ]] || fail "Code signature has no CDHash: $bundle"
  printf '%s\n' "$cdhash"
}

path_identity() {
  /usr/bin/stat -f '%d:%i' "$1"
}

validate_arm64_macho_files() {
  local bundle="$1"
  local candidate
  local description
  local architectures
  local macho_count=0
  local -a search_roots=("$bundle/Contents/MacOS")

  [[ -d "$bundle/Contents/MacOS" ]] \
    || fail "Bundle has no Contents/MacOS directory: $bundle"
  if [[ -d "$bundle/Contents/Frameworks" ]]; then
    search_roots+=("$bundle/Contents/Frameworks")
  fi

  while IFS= read -r -d '' candidate; do
    description="$(/usr/bin/file -b "$candidate")"
    [[ "$description" == *"Mach-O"* ]] || continue
    macho_count=$((macho_count + 1))
    architectures="$(/usr/bin/lipo -archs "$candidate" 2>/dev/null)" \
      || fail "Unable to inspect architectures: $candidate"
    [[ "$architectures" == "$EXPECTED_ARCHITECTURE" ]] \
      || fail "Mach-O file is not $EXPECTED_ARCHITECTURE-only: $candidate ($architectures)"
  done < <(/usr/bin/find "${search_roots[@]}" -type f -print0)

  [[ "$macho_count" -gt 0 ]] || fail "Bundle contains no Mach-O files: $bundle"
}

validate_bundle() {
  local bundle="$1"
  local require_release_version="$2"
  local info_plist="$bundle/Contents/Info.plist"
  local executable="$bundle/Contents/MacOS/$EXECUTABLE_NAME"

  [[ -d "$bundle" && ! -L "$bundle" ]] \
    || fail "App bundle is missing, not a directory, or a symlink: $bundle"
  [[ -s "$info_plist" ]] || fail "Info.plist is missing or empty: $info_plist"
  /usr/bin/plutil -lint "$info_plist" >/dev/null \
    || fail "Info.plist validation failed: $info_plist"
  verify_plist_value "$bundle" CFBundleDisplayName "$APP_NAME"
  verify_plist_value "$bundle" CFBundleExecutable "$EXECUTABLE_NAME"
  verify_plist_value "$bundle" CFBundleIdentifier "$BUNDLE_ID"

  if [[ "$require_release_version" -eq 1 ]]; then
    validate_release_contract "$bundle"
    verify_plist_value "$bundle" LSMultipleInstancesProhibited true
    verify_plist_value "$bundle" CFBundleDocumentTypes:0:CFBundleTypeRole Viewer
    verify_plist_value "$bundle" CFBundleDocumentTypes:0:LSItemContentTypes:0 org.local.rfmapping.rfmap
    verify_plist_value "$bundle" CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 rfmap
    verify_plist_value "$bundle" CFBundleDocumentTypes:1:CFBundleTypeExtensions:0 json
    verify_plist_missing "$bundle" CFBundleDocumentTypes:2
    verify_plist_value "$bundle" UTExportedTypeDeclarations:0:UTTypeIdentifier org.local.rfmapping.rfmap
    verify_plist_value "$bundle" UTExportedTypeDeclarations:1:UTTypeIdentifier org.local.rfmapping.tc
    verify_plist_value "$bundle" UTExportedTypeDeclarations:2:UTTypeIdentifier org.local.rfmapping.probe
  else
    [[ -n "$(bundle_version "$bundle")" ]] \
      || fail "Bundle has an empty CFBundleShortVersionString: $bundle"
    [[ -n "$(bundle_build "$bundle")" ]] \
      || fail "Bundle has an empty CFBundleVersion: $bundle"
  fi

  [[ -s "$executable" && -x "$executable" ]] \
    || fail "Bundle executable is missing, empty, or not executable: $executable"
  validate_arm64_macho_files "$bundle"
  /usr/bin/codesign --verify --deep --strict --verbose=2 "$bundle" \
    || fail "Code-signature verification failed: $bundle"
  bundle_cdhash "$bundle" >/dev/null
}

ensure_app_is_not_running() {
  if /usr/bin/pgrep -x "$EXECUTABLE_NAME" >/dev/null 2>&1; then
    fail "Close every running '$APP_NAME' process before installing or rolling back"
  fi
}

verify_target_parent_is_writable() {
  [[ -w "$TARGET_PARENT" ]] \
    || fail "Target parent is not writable: $TARGET_PARENT (run only this installer with appropriate administrator privileges)"
}

run_install_preflight() {
  preflight_platform
  prepare_target_paths
  prepare_source_path
  verify_target_parent_is_writable
  ensure_app_is_not_running
  validate_bundle "$SOURCE_BUNDLE" 1
  if [[ -e "$TARGET_BUNDLE" ]]; then
    validate_bundle "$TARGET_BUNDLE" 0
  fi
}

run_rollback_preflight() {
  preflight_platform
  prepare_target_paths
  verify_target_parent_is_writable
  ensure_app_is_not_running

  require_absolute_path "Rollback app" "$ROLLBACK_REQUEST"
  [[ -d "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]] \
    || fail "Backup directory is missing or unsafe: $BACKUP_ROOT"
  ROLLBACK_REQUEST="$(canonical_bundle_path "$ROLLBACK_REQUEST")"
  [[ "$(/usr/bin/dirname "$ROLLBACK_REQUEST")" == "$BACKUP_ROOT" ]] \
    || fail "Rollback app must be a direct child of $BACKUP_ROOT"
  [[ "$(/usr/bin/basename "$ROLLBACK_REQUEST")" == "$APP_NAME"-*.app ]] \
    || fail "Rollback app has an unexpected name: $ROLLBACK_REQUEST"
  [[ -d "$TARGET_BUNDLE" ]] || fail "Installed target is missing: $TARGET_BUNDLE"
  validate_bundle "$ROLLBACK_REQUEST" 0
  validate_bundle "$TARGET_BUNDLE" 0
}

acquire_install_lock() {
  LOCK_TOKEN="pid=$$;started=$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
  if ! /bin/mkdir -m 700 "$LOCK_DIR" 2>/dev/null; then
    local owner="unknown"
    if [[ -r "$LOCK_DIR/owner" ]]; then
      owner="$(<"$LOCK_DIR/owner")"
    fi
    fail "Another install or an interrupted transaction owns $LOCK_DIR ($owner). Inspect the installed app and any .rfmapping-stage.* directories before removing that lock."
  fi
  printf '%s\n' "$LOCK_TOKEN" >"$LOCK_DIR/owner"
  LOCK_HELD=1
}

release_install_lock() {
  [[ "$LOCK_HELD" -eq 1 ]] || return 0
  if [[ -r "$LOCK_DIR/owner" && "$(<"$LOCK_DIR/owner")" == "$LOCK_TOKEN" ]]; then
    /bin/rm -f -- "$LOCK_DIR/owner"
    /bin/rmdir "$LOCK_DIR"
    LOCK_HELD=0
    return 0
  fi
  warn "Install lock ownership changed; leaving it in place: $LOCK_DIR"
  return 1
}

create_stage() {
  STAGE_ROOT="$(/usr/bin/mktemp -d "$TARGET_PARENT/.rfmapping-stage.XXXXXX")"
  [[ -n "$STAGE_ROOT" && -d "$STAGE_ROOT" ]] \
    || fail "Unable to create a staging directory below $TARGET_PARENT"
  /bin/chmod 700 "$STAGE_ROOT"
  STAGED_BUNDLE="$STAGE_ROOT/$APP_NAME.app"
}

compile_swap_tool() {
  local clang_bin
  local sdk_path
  clang_bin="$(/usr/bin/xcrun --find clang)"
  sdk_path="$(/usr/bin/xcrun --sdk macosx --show-sdk-path 2>/dev/null)" \
    || fail "Unable to locate the macOS SDK for the atomic swap helper"
  [[ -d "$sdk_path" ]] \
    || fail "Xcode returned a missing macOS SDK path: $sdk_path"
  SWAP_TOOL="$STAGE_ROOT/rename-swap"
  "$clang_bin" \
    -isysroot "$sdk_path" \
    -Os \
    -Wall \
    -Wextra \
    -Werror \
    "$SCRIPT_DIR/rename_swap_macos.c" \
    -o "$SWAP_TOOL"
  [[ -x "$SWAP_TOOL" ]] || fail "Atomic swap helper was not created: $SWAP_TOOL"
}

copy_release_to_stage() {
  /usr/bin/ditto "$SOURCE_BUNDLE" "$STAGED_BUNDLE"
}

atomic_swap() {
  local first="$1"
  local second="$2"
  [[ -x "$SWAP_TOOL" ]] || fail "Atomic swap helper is unavailable"
  "$SWAP_TOOL" --swap "$first" "$second"
}

atomic_publish_exclusive() {
  local source="$1"
  local destination="$2"
  [[ -x "$SWAP_TOOL" ]] || fail "Atomic swap helper is unavailable"
  "$SWAP_TOOL" --exclusive "$source" "$destination"
}

safe_stage_cleanup() {
  [[ -n "$STAGE_ROOT" && -e "$STAGE_ROOT" ]] || return 0
  local parent
  local name
  parent="$(cd -P "$(/usr/bin/dirname "$STAGE_ROOT")" && pwd)"
  name="$(/usr/bin/basename "$STAGE_ROOT")"
  [[ "$parent" == "$TARGET_PARENT" && "$name" == .rfmapping-stage.* ]] \
    || fail "Refusing unsafe stage cleanup target: $STAGE_ROOT"
  /bin/rm -rf -- "$STAGE_ROOT"
}

sanitize_backup_component() {
  printf '%s' "$1" | /usr/bin/tr -c 'A-Za-z0-9._-' '_'
}

next_backup_path() {
  local bundle="$1"
  local label="$2"
  local version
  local build
  local timestamp
  local candidate
  local counter=0
  version="$(sanitize_backup_component "$(bundle_version "$bundle")")"
  build="$(sanitize_backup_component "$(bundle_build "$bundle")")"
  timestamp="$(/bin/date -u +%Y%m%dT%H%M%SZ)"
  candidate="$BACKUP_ROOT/$APP_NAME-$label-$version-build$build-$timestamp-$$.app"
  while [[ -e "$candidate" ]]; do
    counter=$((counter + 1))
    candidate="$BACKUP_ROOT/$APP_NAME-$label-$version-build$build-$timestamp-$$-$counter.app"
  done
  printf '%s\n' "$candidate"
}

verify_matching_cdhash() {
  local bundle="$1"
  local expected="$2"
  local actual
  actual="$(bundle_cdhash "$bundle")"
  [[ "$actual" == "$expected" ]] \
    || fail "Installed code hash is '$actual'; expected '$expected' for $bundle"
}

bundle_identity_or_empty() {
  local bundle="$1"
  if [[ ! -d "$bundle" || -L "$bundle" ]]; then
    return 0
  fi
  path_identity "$bundle" 2>/dev/null || true
}

bundle_has_identity() {
  local bundle="$1"
  local expected="$2"
  local actual
  [[ -n "$expected" ]] || return 1
  actual="$(bundle_identity_or_empty "$bundle")"
  [[ "$actual" == "$expected" ]]
}

clear_recovery_persistence_state() {
  RECOVERY_PERSIST_PENDING=0
  RECOVERY_PERSIST_SOURCE=""
  RECOVERY_PERSIST_DESTINATION=""
  RECOVERY_PERSIST_ID=""
}

reset_transaction_state() {
  TRANSACTION_KIND=""
  TRANSACTION_OPERATION=""
  TRANSACTION_CANDIDATE_ID=""
  TRANSACTION_RECOVERY_ID=""
  ROLLBACK_BUNDLE=""
  clear_recovery_persistence_state
  RECOVERY_PERSIST_COMPLETED=0
}

move_exact_bundle_exclusive() {
  local source="$1"
  local destination="$2"
  local expected_identity="$3"
  local label="$4"

  if ! bundle_has_identity "$source" "$expected_identity"; then
    warn "$label source identity changed; preserving transaction state: $source"
    return 1
  fi
  if [[ -e "$destination" || -L "$destination" ]]; then
    warn "$label destination is no longer absent; preserving transaction state: $destination"
    return 1
  fi
  if ! atomic_publish_exclusive "$source" "$destination"; then
    warn "$label failed; preserving both paths for identity-based recovery"
    return 1
  fi
  if [[ -e "$source" || -L "$source" ]] \
    || ! bundle_has_identity "$destination" "$expected_identity"; then
    warn "$label returned success with an unexpected path identity; preserving transaction state"
    return 1
  fi
}

rollback_active_transaction() {
  local failed_candidate
  local recovery_bundle="$ROLLBACK_BUNDLE"

  case "$TRANSACTION_KIND" in
    swap)
      if ! bundle_has_identity "$TARGET_BUNDLE" "$TRANSACTION_CANDIDATE_ID" \
        || ! bundle_has_identity "$recovery_bundle" "$TRANSACTION_RECOVERY_ID"; then
        warn "Cannot automatically roll back: target or recovery identity changed; preserving both paths"
        return 1
      fi
      if ! atomic_swap "$recovery_bundle" "$TARGET_BUNDLE"; then
        warn "Atomic rollback failed; preserve $recovery_bundle, $TARGET_BUNDLE, and $STAGE_ROOT for recovery"
        return 1
      fi
      if ! bundle_has_identity "$TARGET_BUNDLE" "$TRANSACTION_RECOVERY_ID" \
        || ! bundle_has_identity "$recovery_bundle" "$TRANSACTION_CANDIDATE_ID"; then
        warn "Atomic rollback returned success with unexpected identities; preserving transaction state"
        return 1
      fi

      failed_candidate="$recovery_bundle"
      case "$TRANSACTION_OPERATION" in
        install)
          if [[ "$(/usr/bin/dirname "$failed_candidate")" != "$STAGE_ROOT" ]]; then
            if ! move_exact_bundle_exclusive \
              "$failed_candidate" \
              "$STAGE_ROOT/failed-release.app" \
              "$TRANSACTION_CANDIDATE_ID" \
              "Failed-release quarantine"; then
              return 1
            fi
          elif ! bundle_has_identity "$failed_candidate" "$TRANSACTION_CANDIDATE_ID"; then
            warn "Failed install candidate identity changed inside staging; preserving transaction state"
            return 1
          fi
          ;;
        rollback)
          # The candidate is the backup explicitly selected by the user. After
          # the inverse swap it must be retained, never quarantined with the
          # failed install candidate. Restore its original path when recovery
          # persistence had already moved the replaced app elsewhere.
          if [[ "$failed_candidate" != "$ROLLBACK_REQUEST" ]]; then
            if ! move_exact_bundle_exclusive \
              "$failed_candidate" \
              "$ROLLBACK_REQUEST" \
              "$TRANSACTION_CANDIDATE_ID" \
              "Requested-backup restoration"; then
              return 1
            fi
            failed_candidate="$ROLLBACK_REQUEST"
          fi
          if ! bundle_has_identity "$failed_candidate" "$TRANSACTION_CANDIDATE_ID"; then
            warn "Requested rollback backup identity changed; preserving transaction state"
            return 1
          fi
          ;;
        *)
          warn "Unknown transaction operation: $TRANSACTION_OPERATION"
          return 1
          ;;
      esac
      echo "Rollback restored the previous app at $TARGET_BUNDLE" >&2
      ;;
    new)
      if ! bundle_has_identity "$TARGET_BUNDLE" "$TRANSACTION_CANDIDATE_ID"; then
        warn "Cannot withdraw failed first installation: target identity changed; preserving transaction state"
        return 1
      fi
      if ! move_exact_bundle_exclusive \
        "$TARGET_BUNDLE" \
        "$STAGE_ROOT/failed-release.app" \
        "$TRANSACTION_CANDIDATE_ID" \
        "Failed first-install withdrawal"; then
        return 1
      fi
      echo "Rollback removed the failed first installation from $TARGET_BUNDLE" >&2
      ;;
    "")
      ;;
    *)
      warn "Unknown transaction state: $TRANSACTION_KIND"
      return 1
      ;;
  esac

  reset_transaction_state
  return 0
}

resolve_pending_transaction() {
  local target_identity=""
  local candidate_identity=""

  target_identity="$(bundle_identity_or_empty "$TARGET_BUNDLE")"
  candidate_identity="$(bundle_identity_or_empty "$ROLLBACK_BUNDLE")"

  case "$TRANSACTION_KIND" in
    swap-pending)
      if [[ "$target_identity" == "$TRANSACTION_CANDIDATE_ID" \
        && "$candidate_identity" == "$TRANSACTION_RECOVERY_ID" ]]; then
        TRANSACTION_KIND="swap"
        return 0
      fi
      if [[ "$candidate_identity" == "$TRANSACTION_CANDIDATE_ID" \
        && "$target_identity" == "$TRANSACTION_RECOVERY_ID" ]]; then
        reset_transaction_state
        return 0
      fi
      ;;
    new-pending)
      if [[ "$target_identity" == "$TRANSACTION_CANDIDATE_ID" \
        && "$candidate_identity" != "$TRANSACTION_CANDIDATE_ID" ]]; then
        TRANSACTION_KIND="new"
        return 0
      fi
      if [[ "$candidate_identity" == "$TRANSACTION_CANDIDATE_ID" \
        && "$target_identity" != "$TRANSACTION_CANDIDATE_ID" ]]; then
        reset_transaction_state
        return 0
      fi
      ;;
    *)
      return 0
      ;;
  esac

  warn "Atomic publication outcome is ambiguous; preserving target, recovery app, staging directory, and lock"
  return 1
}

resolve_recovery_persistence() {
  local source_identity=""
  local destination_identity=""

  [[ "$RECOVERY_PERSIST_PENDING" -eq 1 ]] || return 0
  source_identity="$(bundle_identity_or_empty "$RECOVERY_PERSIST_SOURCE")"
  destination_identity="$(bundle_identity_or_empty "$RECOVERY_PERSIST_DESTINATION")"

  if [[ "$destination_identity" == "$RECOVERY_PERSIST_ID" \
    && "$source_identity" != "$RECOVERY_PERSIST_ID" ]]; then
    ROLLBACK_BUNDLE="$RECOVERY_PERSIST_DESTINATION"
    RECOVERY_PERSIST_COMPLETED=1
    clear_recovery_persistence_state
    return 0
  fi
  if [[ "$source_identity" == "$RECOVERY_PERSIST_ID" \
    && "$destination_identity" != "$RECOVERY_PERSIST_ID" ]]; then
    ROLLBACK_BUNDLE="$RECOVERY_PERSIST_SOURCE"
    RECOVERY_PERSIST_COMPLETED=0
    clear_recovery_persistence_state
    return 0
  fi

  warn "Recovery-backup publication outcome is ambiguous; preserving source, destination, target, staging directory, and lock"
  return 1
}

persist_recovery_bundle() {
  local source="$1"
  local destination="$2"

  if ! bundle_has_identity "$source" "$TRANSACTION_RECOVERY_ID"; then
    fail "Recovery bundle identity changed before backup persistence: $source"
  fi
  RECOVERY_PERSIST_SOURCE="$source"
  RECOVERY_PERSIST_DESTINATION="$destination"
  RECOVERY_PERSIST_ID="$TRANSACTION_RECOVERY_ID"
  RECOVERY_PERSIST_COMPLETED=0
  RECOVERY_PERSIST_PENDING=1
  atomic_publish_exclusive "$source" "$destination"
  resolve_recovery_persistence
  [[ "$RECOVERY_PERSIST_COMPLETED" -eq 1 ]] \
    || fail "Exclusive recovery-backup publication did not complete: $destination"
}

finish_transaction() {
  COMMITTED=1
  reset_transaction_state
}

refresh_launch_services() {
  local lsregister="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
  [[ -x "$lsregister" ]] || return 0
  if [[ -n "$RESULT_BACKUP" ]]; then
    "$lsregister" -u "$RESULT_BACKUP" >/dev/null 2>&1 \
      || warn "Could not unregister backup from Launch Services: $RESULT_BACKUP"
  fi
  "$lsregister" -f "$TARGET_BUNDLE" >/dev/null 2>&1 \
    || warn "Could not refresh Launch Services registration for $TARGET_BUNDLE"
}

install_release() {
  local source_cdhash
  local backup_path=""

  run_install_preflight
  source_cdhash="$(bundle_cdhash "$SOURCE_BUNDLE")"
  acquire_install_lock
  ensure_app_is_not_running
  /bin/mkdir -p "$BACKUP_ROOT"
  [[ -d "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]] \
    || fail "Backup directory became unsafe: $BACKUP_ROOT"
  create_stage
  compile_swap_tool
  copy_release_to_stage
  validate_bundle "$STAGED_BUNDLE" 1
  verify_matching_cdhash "$STAGED_BUNDLE" "$source_cdhash"
  /bin/sync

  # The initial preflight can be followed by a slow copy. Revalidate the exact
  # path immediately before publication so a concurrent, non-cooperating
  # replacement cannot turn the transaction into a backup of an unknown item.
  [[ ! -L "$TARGET_BUNDLE" ]] || fail "Target became a symlink during staging: $TARGET_BUNDLE"
  if [[ -e "$TARGET_BUNDLE" ]]; then
    validate_bundle "$TARGET_BUNDLE" 0
  fi
  [[ -d "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]] \
    || fail "Backup directory became unsafe during staging: $BACKUP_ROOT"
  ensure_app_is_not_running

  if [[ -d "$TARGET_BUNDLE" ]]; then
    TRANSACTION_OPERATION="install"
    TRANSACTION_CANDIDATE_ID="$(path_identity "$STAGED_BUNDLE")"
    TRANSACTION_RECOVERY_ID="$(path_identity "$TARGET_BUNDLE")"
    TRANSACTION_KIND="swap-pending"
    ROLLBACK_BUNDLE="$STAGED_BUNDLE"
    atomic_swap "$STAGED_BUNDLE" "$TARGET_BUNDLE"
    resolve_pending_transaction
    [[ "$TRANSACTION_KIND" == "swap" ]] \
      || fail "Atomic replacement returned success without publishing the candidate"
  else
    # RENAME_EXCL makes the absent-target decision atomic. A non-cooperating
    # process cannot create the destination between the check and publication
    # and make mv(1) nest the app inside an unexpected directory.
    TRANSACTION_OPERATION="install"
    TRANSACTION_CANDIDATE_ID="$(path_identity "$STAGED_BUNDLE")"
    TRANSACTION_RECOVERY_ID=""
    TRANSACTION_KIND="new-pending"
    ROLLBACK_BUNDLE="$STAGED_BUNDLE"
    atomic_publish_exclusive "$STAGED_BUNDLE" "$TARGET_BUNDLE"
    resolve_pending_transaction
    [[ "$TRANSACTION_KIND" == "new" ]] \
      || fail "Exclusive first publication returned success without publishing the candidate"
  fi

  validate_bundle "$TARGET_BUNDLE" 1
  verify_matching_cdhash "$TARGET_BUNDLE" "$source_cdhash"

  if [[ "$TRANSACTION_KIND" == "swap" ]]; then
    backup_path="$(next_backup_path "$ROLLBACK_BUNDLE" previous)"
    persist_recovery_bundle "$ROLLBACK_BUNDLE" "$backup_path"
    validate_bundle "$ROLLBACK_BUNDLE" 0
    RESULT_BACKUP="$ROLLBACK_BUNDLE"
  fi

  /bin/sync
  finish_transaction
  refresh_launch_services
  echo "Installed $APP_NAME $EXPECTED_VERSION $EXPECTED_EDITION (build $EXPECTED_BUILD) at $TARGET_BUNDLE"
  if [[ -n "$RESULT_BACKUP" ]]; then
    echo "Previous app backup: $RESULT_BACKUP"
  else
    echo "No previous app was present; no backup was created"
  fi
}

rollback_release() {
  local requested_cdhash
  local replaced_path

  run_rollback_preflight
  requested_cdhash="$(bundle_cdhash "$ROLLBACK_REQUEST")"
  acquire_install_lock
  ensure_app_is_not_running
  create_stage
  compile_swap_tool
  TRANSACTION_OPERATION="rollback"
  TRANSACTION_CANDIDATE_ID="$(path_identity "$ROLLBACK_REQUEST")"
  TRANSACTION_RECOVERY_ID="$(path_identity "$TARGET_BUNDLE")"
  TRANSACTION_KIND="swap-pending"
  ROLLBACK_BUNDLE="$ROLLBACK_REQUEST"
  atomic_swap "$ROLLBACK_REQUEST" "$TARGET_BUNDLE"
  resolve_pending_transaction
  [[ "$TRANSACTION_KIND" == "swap" ]] \
    || fail "Atomic rollback publication returned success without publishing the requested backup"

  validate_bundle "$TARGET_BUNDLE" 0
  verify_matching_cdhash "$TARGET_BUNDLE" "$requested_cdhash"
  replaced_path="$(next_backup_path "$ROLLBACK_BUNDLE" replaced)"
  persist_recovery_bundle "$ROLLBACK_BUNDLE" "$replaced_path"
  validate_bundle "$ROLLBACK_BUNDLE" 0
  /bin/sync
  RESULT_BACKUP="$ROLLBACK_BUNDLE"
  finish_transaction
  refresh_launch_services

  echo "Restored $APP_NAME $(bundle_version "$TARGET_BUNDLE") (build $(bundle_build "$TARGET_BUNDLE")) at $TARGET_BUNDLE"
  echo "Replaced app backup: $RESULT_BACKUP"
}

show_preflight_result() {
  local source_cdhash
  run_install_preflight
  source_cdhash="$(bundle_cdhash "$SOURCE_BUNDLE")"
  echo "Preflight passed; no files were changed."
  echo "Candidate: $SOURCE_BUNDLE"
  echo "Candidate version: $EXPECTED_VERSION $EXPECTED_EDITION (build $EXPECTED_BUILD)"
  echo "Candidate minimum macOS: $EXPECTED_MINIMUM_MACOS_VERSION"
  echo "Candidate architecture: $EXPECTED_ARCHITECTURE only"
  echo "Candidate CDHash: $source_cdhash"
  echo "Install target: $TARGET_BUNDLE"
  if [[ -d "$TARGET_BUNDLE" ]]; then
    echo "Installed version: $(bundle_version "$TARGET_BUNDLE") (build $(bundle_build "$TARGET_BUNDLE"))"
    echo "A successful install will retain the existing app under $BACKUP_ROOT"
  else
    echo "Installed version: none"
  fi
  echo "Run '$0 --install' only after reviewing these values."
}

handle_exit() {
  local status=$?
  local rollback_status=0
  local cleanup_status=0
  local lock_status=0

  trap - EXIT HUP INT TERM
  set +e

  if [[ "$status" -ne 0 && "$COMMITTED" -eq 0 \
    && "$RECOVERY_PERSIST_PENDING" -eq 1 ]]; then
    resolve_recovery_persistence
    rollback_status=$?
    if [[ "$rollback_status" -ne 0 ]]; then
      PRESERVE_TRANSACTION=1
      status=1
    fi
  fi

  if [[ "$status" -ne 0 && "$COMMITTED" -eq 0 && "$TRANSACTION_KIND" == *-pending ]]; then
    resolve_pending_transaction
    rollback_status=$?
    if [[ "$rollback_status" -ne 0 ]]; then
      PRESERVE_TRANSACTION=1
      status=1
    fi
  fi

  if [[ "$status" -ne 0 && "$COMMITTED" -eq 0 \
    && "$PRESERVE_TRANSACTION" -eq 0 && -n "$TRANSACTION_KIND" ]]; then
    rollback_active_transaction
    rollback_status=$?
    if [[ "$rollback_status" -ne 0 ]]; then
      PRESERVE_TRANSACTION=1
      status=1
    fi
  fi

  if [[ "$PRESERVE_TRANSACTION" -eq 0 ]]; then
    safe_stage_cleanup
    cleanup_status=$?
    if [[ "$cleanup_status" -ne 0 ]]; then
      warn "Unable to clean staging directory: $STAGE_ROOT"
      status=1
    fi
    release_install_lock
    lock_status=$?
    if [[ "$lock_status" -ne 0 ]]; then
      status=1
    fi
  else
    warn "Transaction state was preserved. Do not remove $LOCK_DIR until the app and recovery bundle have been inspected."
  fi

  exit "$status"
}

main() {
  parse_arguments "$@"
  trap handle_exit EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM

  case "$ACTION" in
    preflight)
      show_preflight_result
      ;;
    install)
      install_release
      ;;
    rollback)
      rollback_release
      ;;
    *)
      fail "Unknown action: $ACTION"
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi

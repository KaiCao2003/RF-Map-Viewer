#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=deploy/lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
Usage: deploy/rollback.sh [--to RELEASE_ID]

Atomically switch to the previous healthy release, or to an explicit immutable
release. The service is restarted and health-checked; a failed rollback restores
the release that was current when this command began.
EOF
}

target_id=""
while (($#)); do
    case "$1" in
        --to)
            (($# >= 2)) || deploy_die "--to requires a release id"
            target_id="$2"
            shift
            ;;
        -h|--help) usage; exit 0 ;;
        *) deploy_die "unknown argument: $1" ;;
    esac
    shift
done

require_hhw9l84
require_kai
require_layout
acquire_deploy_lock

current_release="$(resolve_release_link "${RFMAPPING_DEPLOY_ROOT}/current")" || deploy_die "current release is missing or invalid"

if [[ -n "${target_id}" ]]; then
    validate_release_id "${target_id}"
    target_release="$(validate_release_dir "${RFMAPPING_RELEASES_ROOT}/${target_id}")"
else
    target_release="$(resolve_release_link "${RFMAPPING_DEPLOY_ROOT}/previous")" || deploy_die "previous release is missing or invalid"
fi

[[ "${target_release}" != "${current_release}" ]] || deploy_die "target release is already current"
require_gated_release "${target_release}"
atomic_release_link "${target_release}" "${RFMAPPING_DEPLOY_ROOT}/current"

if restart_rfmapping_service && wait_for_health 30; then
    atomic_release_link "${current_release}" "${RFMAPPING_DEPLOY_ROOT}/previous"
    deploy_note "Rollback complete: $(basename -- "${target_release}")"
    exit 0
fi

deploy_note "Rollback target was unhealthy; restoring $(basename -- "${current_release}")."
atomic_release_link "${current_release}" "${RFMAPPING_DEPLOY_ROOT}/current"
restart_rfmapping_service || true
wait_for_health 30 || deploy_note "WARNING: restored release did not become healthy."
deploy_die "rollback failed; original current symlink was restored"

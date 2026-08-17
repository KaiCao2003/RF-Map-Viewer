#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=deploy/lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
Usage: deploy/release.sh [--source DIR] [--install-deps] [--activate]

Build and test an immutable release under /mnt/ssd4.1/Apps/rfmapping/releases.
The default is stage-only. --activate atomically switches the current symlink,
restarts rfmapping-web.service, verifies /api/health, and automatically restores
the prior release if startup or health validation fails.
EOF
}

SOURCE_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
install_deps=false
activate=false

while (($#)); do
    case "$1" in
        --source)
            (($# >= 2)) || deploy_die "--source requires a directory"
            SOURCE_ROOT="$2"
            shift
            ;;
        --install-deps) install_deps=true ;;
        --activate) activate=true ;;
        -h|--help) usage; exit 0 ;;
        *) deploy_die "unknown argument: $1" ;;
    esac
    shift
done

require_hhw9l84
require_kai
require_layout
load_and_validate_gate_environment

SOURCE_ROOT="$(realpath -e -- "${SOURCE_ROOT}")"
[[ -d "${SOURCE_ROOT}" ]] || deploy_die "source is not a directory: ${SOURCE_ROOT}"
case "${SOURCE_ROOT}" in
    "${RFMAPPING_DEPLOY_ROOT}"|"${RFMAPPING_DEPLOY_ROOT}"/*)
        deploy_die "source must not be inside the deployment root"
        ;;
esac

for required_path in \
    requirements.txt \
    backend/rfmapping_web/app.py \
    backend/rfmapping_web/shared_figure_export.py \
    frontend/package.json \
    frontend/package-lock.json \
    deploy/rfmapping-web.service \
    deploy/nginx-rfmapping-location.conf \
    backend/tests/test_rfmapping_web.py \
    backend/tests/test_access_gate.py \
    backend/tests/test_shared_figure_export.py; do
    [[ -e "${SOURCE_ROOT}/${required_path}" ]] || deploy_die "source is missing ${required_path}"
done

if [[ "${activate}" == true ]]; then
    systemctl --user cat "${RFMAPPING_SERVICE_NAME}" >/dev/null 2>&1 || deploy_die "install the user systemd unit before activation"
    systemctl --user cat "${RFMAPPING_SERVICE_NAME}" \
        | grep -Fq "EnvironmentFile=${RFMAPPING_GATE_ENV}" \
        || deploy_die "install the access-gated user systemd unit before activation"
fi

acquire_deploy_lock

if [[ "${install_deps}" == true ]]; then
    "${RFMAPPING_PIP}" install --requirement "${SOURCE_ROOT}/requirements.txt"
fi
"${RFMAPPING_PYTHON}" -m pip check

git_revision="nogit"
if git -C "${SOURCE_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git_revision="$(git -C "${SOURCE_ROOT}" rev-parse --short=12 HEAD)"
fi
release_id="$(date -u +%Y%m%dT%H%M%S%NZ)-${git_revision}"
validate_release_id "${release_id}"
release_dir="${RFMAPPING_RELEASES_ROOT}/${release_id}"
staging_dir="${RFMAPPING_RELEASES_ROOT}/.${release_id}.staging"

[[ ! -e "${release_dir}" && ! -L "${release_dir}" ]] || deploy_die "release already exists: ${release_dir}"
[[ ! -e "${staging_dir}" && ! -L "${staging_dir}" ]] || deploy_die "staging path already exists: ${staging_dir}"

cleanup_staging() {
    remove_staging_dir "${staging_dir}"
}
trap cleanup_staging EXIT

install -d -m 0750 "${staging_dir}"
rsync -a --exclude '__pycache__/' --exclude '*.pyc' \
    "${SOURCE_ROOT}/backend/rfmapping_web/" "${staging_dir}/rfmapping_web/"
rsync -a --exclude 'node_modules/' --exclude 'dist/' \
    "${SOURCE_ROOT}/frontend/" "${staging_dir}/web/"
rsync -a --exclude '__pycache__/' --exclude '*.pyc' \
    "${SOURCE_ROOT}/backend/tests/" "${staging_dir}/tests/"
rsync -a "${SOURCE_ROOT}/deploy/" "${staging_dir}/deploy/"
install -m 0644 "${SOURCE_ROOT}/requirements.txt" "${staging_dir}/requirements-web.txt"

(
    cd -- "${staging_dir}/web"
    npm ci --no-audit --no-fund
    npm test
    npm run build
)
[[ -r "${staging_dir}/web/dist/index.html" ]] || deploy_die "frontend build did not create web/dist/index.html"
grep -q '/rfmapping/' "${staging_dir}/web/dist/index.html" || deploy_die "frontend build is not rooted at /rfmapping/"
PYTHONDONTWRITEBYTECODE=1 "${RFMAPPING_PYTHON}" \
    "${staging_dir}/deploy/validate_real_data.py" --static-only

(
    cd -- "${staging_dir}"
    PYTHONDONTWRITEBYTECODE=1 "${RFMAPPING_PYTHON}" -m pytest -q \
        tests/test_rfmapping_web.py tests/test_access_gate.py \
        tests/test_shared_figure_export.py
    PYTHONDONTWRITEBYTECODE=1 "${RFMAPPING_PYTHON}" -c 'from rfmapping_web.app import app; assert app is not None'
)

node_modules_dir="${staging_dir}/web/node_modules"
if [[ -d "${node_modules_dir}" ]]; then
    case "${node_modules_dir}" in
        "${RFMAPPING_RELEASES_ROOT}"/.*.staging/web/node_modules) rm -rf -- "${node_modules_dir}" ;;
        *) deploy_die "refusing to remove unexpected node_modules path" ;;
    esac
fi

cat >"${staging_dir}/RELEASE" <<EOF
release_id=${release_id}
created_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
git_revision=${git_revision}
source=${SOURCE_ROOT}
access_gate=${RFMAPPING_GATE_MARKER}
EOF
find "${staging_dir}" -type d -exec chmod 0750 {} +
find "${staging_dir}" -type f -exec chmod u=rw,g=r,o= {} +
find "${staging_dir}/deploy" -maxdepth 1 -type f -name '*.sh' -exec chmod 0750 {} +

mv -- "${staging_dir}" "${release_dir}"
trap - EXIT
deploy_note "Staged release: ${release_dir}"

if [[ "${activate}" != true ]]; then
    deploy_note "No service changes were made. A later --activate run builds and activates a new release."
    exit 0
fi

old_release=""
old_release_is_gated=false
if [[ -e "${RFMAPPING_DEPLOY_ROOT}/current" || -L "${RFMAPPING_DEPLOY_ROOT}/current" ]]; then
    old_release="$(resolve_release_link "${RFMAPPING_DEPLOY_ROOT}/current")" || deploy_die "current is not a valid release link"
    if release_has_access_gate "${old_release}"; then
        old_release_is_gated=true
    fi
fi

atomic_release_link "${release_dir}" "${RFMAPPING_DEPLOY_ROOT}/current"

if restart_rfmapping_service && wait_for_health 30 "${RFMAPPING_EXPECTED_VERSION}"; then
    if [[ -n "${old_release}" && "${old_release_is_gated}" == true ]]; then
        atomic_release_link "${old_release}" "${RFMAPPING_DEPLOY_ROOT}/previous"
    fi
    deploy_note "Activated healthy release: ${release_id}"
    exit 0
fi

deploy_note "Activation failed."
if [[ -n "${old_release}" && "${old_release_is_gated}" == true ]]; then
    deploy_note "Restoring the prior gated release."
    atomic_release_link "${old_release}" "${RFMAPPING_DEPLOY_ROOT}/current"
    restart_rfmapping_service || true
    wait_for_health 30 || deploy_note "WARNING: prior release did not become healthy after restoration."
else
    stop_rfmapping_service || true
    deploy_note "No gated prior release exists; the service remains stopped (fail closed)."
fi
deploy_die "release ${release_id} was staged but not activated"

#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=deploy/lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
    cat <<'EOF'
Usage: deploy/install.sh [--install-deps] [--nginx-file]

Initialize the persistent hhw9l84 deployment layout. Existing environment
configuration is never overwritten.

  --install-deps  Install requirements.txt into ~/.virtualenvs/rfmapping.
  --nginx-file    Install only the Nginx location snippet. This requires cached
                  sudo credentials and does not edit or reload live Nginx.
EOF
}

install_deps=false
install_nginx_file=false

while (($#)); do
    case "$1" in
        --install-deps) install_deps=true ;;
        --nginx-file) install_nginx_file=true ;;
        -h|--help) usage; exit 0 ;;
        *) deploy_die "unknown argument: $1" ;;
    esac
    shift
done

require_hhw9l84
require_kai
load_and_validate_gate_environment

SOURCE_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
[[ -f "${SOURCE_ROOT}/requirements.txt" ]] || deploy_die "requirements.txt is missing from ${SOURCE_ROOT}"
[[ -x "${RFMAPPING_PYTHON}" ]] || deploy_die "remote virtualenv is missing: ${RFMAPPING_PYTHON}"

install -d -m 0750 \
    "${RFMAPPING_DEPLOY_ROOT}" \
    "${RFMAPPING_RELEASES_ROOT}" \
    "${RFMAPPING_DEPLOY_ROOT}/cache" \
    "${RFMAPPING_DEPLOY_ROOT}/exports" \
    "${RFMAPPING_DEPLOY_ROOT}/tmp" \
    "${RFMAPPING_DEPLOY_ROOT}/tmp/runtime" \
    "${RFMAPPING_DEPLOY_ROOT}/shared"

legacy_upload_dir="${RFMAPPING_DEPLOY_ROOT}/tmp/uploads"
if [[ -d "${legacy_upload_dir}" ]]; then
    rmdir -- "${legacy_upload_dir}" \
        || deploy_die "legacy upload directory is not empty; inspect it before removing: ${legacy_upload_dir}"
    deploy_note "Removed the empty legacy upload directory."
fi

if [[ ! -d "/home/kai/.config/systemd/user" ]]; then
    install -d -m 0700 "/home/kai/.config/systemd/user"
fi

acquire_deploy_lock

ENV_TARGET="${RFMAPPING_DEPLOY_ROOT}/shared/rfmapping-web.env"
if [[ -e "${ENV_TARGET}" ]]; then
    deploy_note "Preserved existing environment file: ${ENV_TARGET}"
    if grep -q '^RFMAPPING_UPLOAD_' "${ENV_TARGET}"; then
        sed -i -E \
            -e '/^# Cache and uploads live on hhw9l84/d' \
            -e '/^# Aggregate stored file bytes plus a bounded multipart\/form-data allowance\.$/d' \
            -e '/^RFMAPPING_UPLOAD_[A-Z0-9_]*=/d' \
            "${ENV_TARGET}"
        deploy_note "Removed obsolete upload settings from the existing environment file."
    fi
    if ! grep -q '^RFMAPPING_OUTPUT_ROOT=' "${ENV_TARGET}"; then
        printf '\n# Persistent server-side GUI exports on hhw9l84 local SSD.\nRFMAPPING_OUTPUT_ROOT=%s\n' \
            "${RFMAPPING_DEPLOY_ROOT}/exports" >>"${ENV_TARGET}"
        deploy_note "Added RFMAPPING_OUTPUT_ROOT to the existing environment file."
    fi
    if ! grep -q '^RFMAPPING_FIGURE_EXPORT_ROOT=' "${ENV_TARGET}"; then
        printf '\n# Existing shared directories available to the figure composer.\nRFMAPPING_FIGURE_EXPORT_ROOT=/mnt/senzailab\n' \
            >>"${ENV_TARGET}"
        deploy_note "Added RFMAPPING_FIGURE_EXPORT_ROOT to the existing environment file."
    fi
else
    install -m 0640 "${SCRIPT_DIR}/rfmapping-web.env.example" "${ENV_TARGET}"
    deploy_note "Installed environment file: ${ENV_TARGET}"
fi

if [[ "${install_deps}" == true ]]; then
    "${RFMAPPING_PIP}" install --requirement "${SOURCE_ROOT}/requirements.txt"
    "${RFMAPPING_PYTHON}" -m pip check
else
    deploy_note "Skipped Python dependency installation (use --install-deps on first install)."
fi

install -m 0644 "${SCRIPT_DIR}/rfmapping-web.service" "/home/kai/.config/systemd/user/${RFMAPPING_SERVICE_NAME}"
systemctl --user daemon-reload
deploy_note "Installed the user service without enabling or starting it."

if [[ "${install_nginx_file}" == true ]]; then
    sudo -n true >/dev/null 2>&1 || deploy_die "sudo credentials are not cached; run 'sudo -v' in this interactive SSH session first"
    sudo -n install -m 0644 "${SCRIPT_DIR}/nginx-rfmapping-location.conf" "/etc/nginx/snippets/rfmapping-location.conf"
    deploy_note "Installed the Nginx snippet, but did not edit or reload Nginx."
else
    deploy_note "Skipped the Nginx snippet (use --nginx-file after running sudo -v)."
fi

deploy_note "Install layout is ready at ${RFMAPPING_DEPLOY_ROOT}."
deploy_note "Next: follow deploy/README.md, then run deploy/release.sh --activate."

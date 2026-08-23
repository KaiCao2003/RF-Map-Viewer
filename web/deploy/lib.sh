#!/usr/bin/env bash

# Shared deployment helpers. This file is sourced by the executable scripts.

readonly RFMAPPING_DEPLOY_ROOT="/mnt/ssd4.1/Apps/rfmapping"
readonly RFMAPPING_RELEASES_ROOT="${RFMAPPING_DEPLOY_ROOT}/releases"
readonly RFMAPPING_SERVICE_NAME="rfmapping-web.service"
readonly RFMAPPING_GATE_ENV="/home/kai/.config/lab-access-gate/pi-first-name.env"
readonly RFMAPPING_PYTHON="/home/kai/.virtualenvs/rfmapping/bin/python"
readonly RFMAPPING_PIP="/home/kai/.virtualenvs/rfmapping/bin/pip"
readonly RFMAPPING_HEALTH_URL="http://127.0.0.1:3005/rfmapping/api/health"
readonly RFMAPPING_EXPECTED_VERSION="1.9.5"
readonly RFMAPPING_PROTECTED_URL="http://127.0.0.1:3005/rfmapping/api/fs/list"
readonly RFMAPPING_LOGIN_URL="http://127.0.0.1:3005/rfmapping/login"
readonly RFMAPPING_GATE_MARKER="pi-first-name-v1"

deploy_die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

deploy_note() {
    printf '%s\n' "$*"
}

require_hhw9l84() {
    local short_host
    short_host="$(hostname -s | tr '[:upper:]' '[:lower:]')"
    case "${short_host}" in
        hhw9l84|fsmhhw9l84) ;;
        *) deploy_die "deployment scripts must run on hhw9l84 (found ${short_host})" ;;
    esac
}

require_kai() {
    local current_user
    current_user="$(id -un)"
    [[ "${current_user}" == "kai" ]] || deploy_die "run as kai, not ${current_user}"
}

load_and_validate_gate_environment() {
    local mode
    local owner
    [[ -f "${RFMAPPING_GATE_ENV}" && -r "${RFMAPPING_GATE_ENV}" ]] \
        || deploy_die "private access-gate environment is missing"
    mode="$(stat -c '%a' -- "${RFMAPPING_GATE_ENV}")"
    owner="$(stat -c '%U' -- "${RFMAPPING_GATE_ENV}")"
    [[ "${mode}" == "600" && "${owner}" == "kai" ]] \
        || deploy_die "private access-gate environment must be owned by kai with mode 600"
    if grep -Ev \
        '^(#.*|[[:space:]]*|MOUSELINE_LOGIN_ANSWER=.*|MOUSELINE_AUTH_GENERATION=.*)$' \
        "${RFMAPPING_GATE_ENV}" | grep -q .; then
        deploy_die "private access-gate environment contains an unexpected setting"
    fi
    [[ "$(grep -c '^MOUSELINE_LOGIN_ANSWER=' "${RFMAPPING_GATE_ENV}")" == "1" ]] \
        || deploy_die "private access-gate environment must contain one answer"
    [[ "$(grep -c '^MOUSELINE_AUTH_GENERATION=' "${RFMAPPING_GATE_ENV}")" == "1" ]] \
        || deploy_die "private access-gate environment must contain one generation"
    set -a
    # shellcheck disable=SC1090
    source "${RFMAPPING_GATE_ENV}"
    set +a
    [[ -n "${MOUSELINE_LOGIN_ANSWER:-}" \
        && "${MOUSELINE_LOGIN_ANSWER}" != "replace-with-private-answer" ]] \
        || deploy_die "private access-gate answer is invalid"
    [[ "${MOUSELINE_AUTH_GENERATION:-}" =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$ ]] \
        || deploy_die "private access-gate generation is invalid"
}

require_layout() {
    [[ -d "${RFMAPPING_RELEASES_ROOT}" ]] || deploy_die "run deploy/install.sh first"
    [[ -d "${RFMAPPING_DEPLOY_ROOT}/cache" ]] || deploy_die "cache directory is missing"
    [[ -d "${RFMAPPING_DEPLOY_ROOT}/exports" ]] || deploy_die "export directory is missing"
    [[ -w "${RFMAPPING_DEPLOY_ROOT}/exports" ]] || deploy_die "export directory is not writable"
    [[ -d "${RFMAPPING_DEPLOY_ROOT}/tmp/runtime" ]] || deploy_die "runtime temp directory is missing"
    [[ -r "${RFMAPPING_DEPLOY_ROOT}/shared/rfmapping-web.env" ]] || deploy_die "environment file is missing"
}

acquire_deploy_lock() {
    exec 9>"${RFMAPPING_DEPLOY_ROOT}/.deploy.lock"
    flock -n 9 || deploy_die "another RFmapping install/release/rollback is running"
}

validate_release_id() {
    local release_id="$1"
    [[ "${release_id}" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]] || deploy_die "invalid release id: ${release_id}"
}

validate_release_dir() {
    local release_dir="$1"
    local resolved
    [[ -d "${release_dir}" ]] || deploy_die "release does not exist: ${release_dir}"
    resolved="$(realpath -e -- "${release_dir}")"
    case "${resolved}" in
        "${RFMAPPING_RELEASES_ROOT}"/*) ;;
        *) deploy_die "release resolves outside ${RFMAPPING_RELEASES_ROOT}: ${resolved}" ;;
    esac
    [[ -f "${resolved}/RELEASE" ]] || deploy_die "release marker is missing: ${resolved}"
    printf '%s\n' "${resolved}"
}

resolve_release_link() {
    local link_path="$1"
    local resolved
    [[ -L "${link_path}" ]] || return 1
    resolved="$(realpath -e -- "${link_path}")" || return 1
    validate_release_dir "${resolved}"
}

atomic_release_link() {
    local release_dir="$1"
    local link_path="$2"
    local temp_link

    release_dir="$(validate_release_dir "${release_dir}")"
    case "${link_path}" in
        "${RFMAPPING_DEPLOY_ROOT}/current"|"${RFMAPPING_DEPLOY_ROOT}/previous") ;;
        *) deploy_die "refusing to replace unexpected link: ${link_path}" ;;
    esac

    temp_link="${link_path}.new.$$"
    [[ ! -e "${temp_link}" && ! -L "${temp_link}" ]] || deploy_die "temporary link already exists: ${temp_link}"
    ln -s -- "${release_dir}" "${temp_link}"
    mv -Tf -- "${temp_link}" "${link_path}"
}

remove_staging_dir() {
    local staging_dir="$1"
    case "${staging_dir}" in
        "${RFMAPPING_RELEASES_ROOT}"/.*.staging) ;;
        *) deploy_die "refusing to remove unexpected path: ${staging_dir}" ;;
    esac
    [[ ! -e "${staging_dir}" ]] || rm -rf -- "${staging_dir}"
}

wait_for_health() {
    local attempts="${1:-30}"
    local expected_version="${2:-}"
    local response_file="${RFMAPPING_DEPLOY_ROOT}/tmp/runtime/health.$$.json"
    local login_file="${RFMAPPING_DEPLOY_ROOT}/tmp/runtime/login.$$.html"
    local attempt
    local protected_status

    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if curl --fail --silent --show-error --max-time 5 \
            --output "${response_file}" "${RFMAPPING_HEALTH_URL}"; then
            if "${RFMAPPING_PYTHON}" - "${response_file}" "${expected_version}" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if (
    payload.get("status") not in {"ok", "healthy"}
    or (sys.argv[2] and payload.get("version") != sys.argv[2])
    or payload.get("rfRootAvailable") is not True
):
    raise SystemExit(1)
PY
            then
                protected_status="$(curl --silent --output /dev/null \
                    --write-out '%{http_code}' --max-time 5 \
                    "${RFMAPPING_PROTECTED_URL}" || true)"
                if [[ "${protected_status}" == "401" ]] \
                    && curl --fail --silent --show-error --max-time 5 \
                        --output "${login_file}" "${RFMAPPING_LOGIN_URL}" \
                    && grep -Fq "What's the PI's first name?" "${login_file}"; then
                    rm -f -- "${response_file}" "${login_file}"
                    return 0
                fi
            fi
        fi
        sleep 1
    done

    rm -f -- "${response_file}" "${login_file}"
    return 1
}

release_has_access_gate() {
    local release_dir="$1"
    grep -Fxq "access_gate=${RFMAPPING_GATE_MARKER}" "${release_dir}/RELEASE"
}

require_gated_release() {
    local release_dir="$1"
    release_has_access_gate "${release_dir}" \
        || deploy_die "refusing an RFmapping release without the access gate: ${release_dir}"
}

restart_rfmapping_service() {
    systemctl --user restart "${RFMAPPING_SERVICE_NAME}"
}

stop_rfmapping_service() {
    systemctl --user stop "${RFMAPPING_SERVICE_NAME}"
}

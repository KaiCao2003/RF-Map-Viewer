#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=deploy/lib.sh
source "${SCRIPT_DIR}/lib.sh"

require_hhw9l84
require_kai
load_and_validate_gate_environment
[[ -x "${RFMAPPING_PYTHON}" ]] || deploy_die "remote virtualenv is missing: ${RFMAPPING_PYTHON}"

cd -- "${SCRIPT_DIR}/.."
exec "${RFMAPPING_PYTHON}" "${SCRIPT_DIR}/validate_real_data.py" "$@"

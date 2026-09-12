#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEST_PYTHON="${RF_MAPPING_TEST_PYTHON:-python3}"
source "$SCRIPT_DIR/python_stable_macos_release.env"

cd "$ROOT_DIR"
export PYTHONDONTWRITEBYTECODE=1
"$TEST_PYTHON" -m pytest -c pytest-stable.ini -q "$@"
"$TEST_PYTHON" "$SCRIPT_DIR/verify_python_stable_release_metadata.py" \
  "$ROOT_DIR" "$RF_MAPPING_APP_VERSION" "$RF_MAPPING_RELEASE_EDITION"

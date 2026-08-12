from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

_GATE_TEMP_DIR = TemporaryDirectory(prefix="rfmapping-gate-test-")
os.environ["MOUSELINE_LOGIN_ANSWER"] = "test-only-answer"
os.environ["MOUSELINE_AUTH_GENERATION"] = "test-generation"
os.environ["RFMAPPING_GATE_DB"] = str(
    Path(_GATE_TEMP_DIR.name) / "sessions.sqlite3"
)

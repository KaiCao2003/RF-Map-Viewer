from __future__ import annotations

import json
from pathlib import Path

import pytest

import rfmapping_gui as gui


def test_windowed_smoke_report_records_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = tmp_path / "success.json"
    monkeypatch.setenv(gui.WINDOWED_SMOKE_REPORT_ENV, str(report))
    monkeypatch.setattr(gui, "main", lambda argv: 0)

    assert gui._run_main_entrypoint(["--self-test", "fixture.rfmap"]) == 0
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "argv": ["--self-test", "fixture.rfmap"],
        "exitCode": 0,
        "status": "success",
    }


def test_windowed_smoke_report_converts_unhandled_exception_to_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = tmp_path / "failure.json"
    monkeypatch.setenv(gui.WINDOWED_SMOKE_REPORT_ENV, str(report))

    def fail(_argv: list[str]) -> int:
        raise RuntimeError("packaged dependency failed")

    monkeypatch.setattr(gui, "main", fail)

    assert gui._run_main_entrypoint(["--self-test", "fixture.rfmap"]) == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "error"
    assert payload["argv"] == ["--self-test", "fixture.rfmap"]
    assert payload["exceptionType"] == "RuntimeError"
    assert payload["message"] == "packaged dependency failed"
    assert "RuntimeError: packaged dependency failed" in payload["traceback"]


def test_entrypoint_preserves_normal_unhandled_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(gui.WINDOWED_SMOKE_REPORT_ENV, raising=False)

    def fail(_argv: list[str]) -> int:
        raise RuntimeError("interactive failure")

    monkeypatch.setattr(gui, "main", fail)
    with pytest.raises(RuntimeError, match="interactive failure"):
        gui._run_main_entrypoint([])

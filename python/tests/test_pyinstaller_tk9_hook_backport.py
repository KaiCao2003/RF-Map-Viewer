from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
PATCHER_PATH = PYTHON_ROOT / "script" / "patch_pyinstaller_tk9_runtime_hook.py"
BACKPORT_PATH = (
    PYTHON_ROOT
    / "packaging"
    / "pyinstaller-hooks"
    / "rthooks"
    / "pyi_rth__tkinter.py"
)
WINDOWS_BUILDER_PATH = PYTHON_ROOT / "script" / "build_python_stable_windows_app.ps1"


def _load_patcher():
    spec = importlib.util.spec_from_file_location("tk9_runtime_hook_patcher", PATCHER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checked_in_backport_has_reviewed_upstream_semantics() -> None:
    patcher = _load_patcher()
    source = BACKPORT_PATH.read_text(encoding="utf-8")

    assert "47745340110001c43d1165693f432521a65fc690" in source
    assert 'if os.path.isdir(tcldir):' in source
    assert 'if os.path.isdir(tkdir):' in source
    assert "raise FileNotFoundError" not in source
    assert hashlib.sha256(BACKPORT_PATH.read_bytes()).hexdigest() == (
        patcher.EXPECTED_BACKPORT_SHA256
    )


def test_backport_environment_guard_is_narrow() -> None:
    patcher = _load_patcher()
    canonical = {
        "platform_name": "win32",
        "pyinstaller_version": "6.21.0",
        "tcl_library": "//zipfs:/lib/tcl/tcl_library",
        "tk_version": 9.0,
    }

    patcher._validate_environment(**canonical)

    rejected = (
        {**canonical, "platform_name": "darwin"},
        {**canonical, "pyinstaller_version": "6.22.0"},
        {**canonical, "tcl_library": "C:/Python314/tcl/tcl9.0"},
        {**canonical, "tk_version": 8.6},
        {**canonical, "tk_version": 10.0},
    )
    for candidate in rejected:
        with pytest.raises(patcher.BackportRefused):
            patcher._validate_environment(**candidate)


def test_backport_only_replaces_reviewed_installed_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patcher = _load_patcher()
    installed_hook = tmp_path / "installed" / "pyi_rth__tkinter.py"
    installed_hook.parent.mkdir()
    installed_hook.write_bytes(b"reviewed PyInstaller 6.21 runtime hook\n")
    backport_hook = tmp_path / "repository" / "pyi_rth__tkinter.py"
    backport_hook.parent.mkdir()
    backport_hook.write_bytes(b"reviewed Tcl/Tk 9 backport\n")

    monkeypatch.setattr(
        patcher,
        "EXPECTED_INSTALLED_HOOK_SHA256",
        hashlib.sha256(installed_hook.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        patcher,
        "EXPECTED_BACKPORT_SHA256",
        hashlib.sha256(backport_hook.read_bytes()).hexdigest(),
    )

    assert patcher._install_backport(
        installed_hook=installed_hook, backport_hook=backport_hook
    ) == "installed"
    assert installed_hook.read_bytes() == backport_hook.read_bytes()
    assert patcher._install_backport(
        installed_hook=installed_hook, backport_hook=backport_hook
    ) == "already-installed"

    installed_hook.write_bytes(b"unreviewed hook revision\n")
    with pytest.raises(patcher.BackportRefused):
        patcher._install_backport(
            installed_hook=installed_hook, backport_hook=backport_hook
        )
    assert installed_hook.read_bytes() == b"unreviewed hook revision\n"


def test_windows_builder_applies_backport_before_pyinstaller_build() -> None:
    source = WINDOWS_BUILDER_PATH.read_text(encoding="utf-8")
    patch_call = "& $BuildPython $TkinterRuntimeHookPatcher $TkinterRuntimeHookBackport"
    build_call = "& $BuildPython -m PyInstaller"

    assert '$PyInstallerVersion = "6.21.0"' in source
    assert patch_call in source
    assert source.index(patch_call) < source.index(build_call)
    assert "next PyInstaller version" not in source  # Guard is executable, not a TODO.

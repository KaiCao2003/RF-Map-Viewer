#!/usr/bin/env python3
"""Install the narrow PyInstaller 6.21/Tcl-Tk 9 runtime-hook backport.

PyInstaller commit 47745340110001c43d1165693f432521a65fc690 moved
missing Tcl/Tk data-directory failures out of ``pyi_rth__tkinter.py``.  That
change is required by Python 3.14's Windows Tcl/Tk 9 distribution, whose Tcl
library reports ``//zipfs:/lib/tcl/tcl_library`` because the scripts live in
DLL-embedded zip archives.

This helper deliberately refuses to modify any other PyInstaller version,
runtime layout, or installed hook revision.  Remove it once the packaging pin
contains the upstream change instead of broadening these guards.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys


EXPECTED_PYINSTALLER_VERSION = "6.21.0"
EXPECTED_TCL_LIBRARY = "//zipfs:/lib/tcl/tcl_library"
EXPECTED_INSTALLED_HOOK_SHA256 = (
    "885d0c5011b9bf5f0cca06f5b20aa4bd28053c69a017ecf40e6b6538ada7f431"
)
EXPECTED_BACKPORT_SHA256 = (
    "ebed862e4d937b728f061e0cc3ee5081109e3e07b1854b1513cd9cb8c9417e9b"
)
RUNTIME_HOOK_RELATIVE_PATH = Path("hooks") / "rthooks" / "pyi_rth__tkinter.py"


class BackportRefused(RuntimeError):
    """Raised when the installed environment is outside the narrow backport."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_environment(
    *,
    platform_name: str,
    pyinstaller_version: str,
    tcl_library: str,
    tk_version: object,
) -> None:
    if platform_name != "win32":
        raise BackportRefused(
            f"backport is Windows-only; detected platform {platform_name!r}"
        )
    if pyinstaller_version != EXPECTED_PYINSTALLER_VERSION:
        raise BackportRefused(
            "backport only applies to PyInstaller "
            f"{EXPECTED_PYINSTALLER_VERSION}; detected {pyinstaller_version!r}"
        )
    if tcl_library != EXPECTED_TCL_LIBRARY:
        raise BackportRefused(
            "backport requires the Tcl/Tk 9 DLL zipfs library; "
            f"detected {tcl_library!r}"
        )
    try:
        numeric_tk_version = float(tk_version)
    except (TypeError, ValueError) as exc:
        raise BackportRefused(f"invalid Tk version {tk_version!r}") from exc
    if not 9.0 <= numeric_tk_version < 10.0:
        raise BackportRefused(
            f"backport requires Tk major version 9; detected {tk_version!r}"
        )


def _install_backport(*, installed_hook: Path, backport_hook: Path) -> str:
    if not installed_hook.is_file():
        raise BackportRefused(f"installed runtime hook is missing: {installed_hook}")
    if not backport_hook.is_file():
        raise BackportRefused(f"repository backport is missing: {backport_hook}")

    backport_digest = _sha256(backport_hook)
    if backport_digest != EXPECTED_BACKPORT_SHA256:
        raise BackportRefused(
            "repository backport digest changed: "
            f"{backport_digest}; expected {EXPECTED_BACKPORT_SHA256}"
        )

    installed_digest = _sha256(installed_hook)
    if installed_digest == EXPECTED_BACKPORT_SHA256:
        return "already-installed"
    if installed_digest != EXPECTED_INSTALLED_HOOK_SHA256:
        raise BackportRefused(
            "installed PyInstaller runtime hook is not the reviewed 6.21.0 "
            f"revision: {installed_digest}"
        )

    temporary_hook = installed_hook.with_name(
        f".{installed_hook.name}.rfmapping-{os.getpid()}.tmp"
    )
    try:
        shutil.copyfile(backport_hook, temporary_hook)
        os.replace(temporary_hook, installed_hook)
    finally:
        temporary_hook.unlink(missing_ok=True)

    installed_digest = _sha256(installed_hook)
    if installed_digest != EXPECTED_BACKPORT_SHA256:
        raise BackportRefused(
            f"installed backport digest is incorrect: {installed_digest}"
        )
    return "installed"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "backport_hook",
        type=Path,
        help="repository-owned pyi_rth__tkinter.py backport",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    import PyInstaller
    import tkinter

    tcl = tkinter.Tcl()
    tcl_library = str(tcl.eval("info library"))
    installed_hook = Path(PyInstaller.__file__).resolve().parent / RUNTIME_HOOK_RELATIVE_PATH

    try:
        _validate_environment(
            platform_name=sys.platform,
            pyinstaller_version=PyInstaller.__version__,
            tcl_library=tcl_library,
            tk_version=tkinter.TkVersion,
        )
        result = _install_backport(
            installed_hook=installed_hook,
            backport_hook=args.backport_hook.resolve(),
        )
    except BackportRefused as exc:
        print(f"PyInstaller Tcl/Tk 9 runtime-hook backport refused: {exc}", file=sys.stderr)
        return 1

    print(
        "PyInstaller Tcl/Tk 9 runtime-hook backport "
        f"{result}: {installed_hook} "
        f"(Tcl library {tcl_library}, Tk {tkinter.TkVersion})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

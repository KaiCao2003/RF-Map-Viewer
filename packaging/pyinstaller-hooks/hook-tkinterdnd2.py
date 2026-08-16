"""Collect only the TkDND payload needed by the frozen process."""

from __future__ import annotations

import os
import platform
from pathlib import Path

from PyInstaller.utils.hooks import get_package_paths, logger


def _platform_directory() -> tuple[str | None, str | None]:
    system = platform.system()
    machine = platform.machine()
    if system == "Windows":
        machine = os.environ.get("PROCESSOR_ARCHITECTURE", machine)

    directories = {
        ("Darwin", "arm64"): "osx-arm64",
        ("Linux", "aarch64"): "linux-arm64",
        ("Linux", "x86_64"): "linux-x64",
        ("Windows", "ARM64"): "win-arm64",
        ("Windows", "AMD64"): "win-x64",
        ("Windows", "x86"): "win-x86",
    }
    library_patterns = {
        "Darwin": "*.dylib",
        "Linux": "*.so",
        "Windows": "*.dll",
    }
    return directories.get((system, machine)), library_patterns.get(system)


def _collect_payload() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    directory, library_pattern = _platform_directory()
    if directory is None or library_pattern is None:
        logger.warning("tkinterdnd2 hook: unsupported platform; TkDND payload was not collected")
        return [], []

    _, package_directory = get_package_paths("tkinterdnd2")
    tkdnd_root = Path(package_directory) / "tkdnd"
    datas: list[tuple[str, str]] = []
    binaries: list[tuple[str, str]] = []

    # tkinterdnd2 selects the Tcl 8 or Tcl 9 directory at runtime. Collect
    # both variants for the current CPU when the wheel supplies them.
    for source_name in (directory, f"{directory}-tcl9"):
        source = tkdnd_root / source_name
        if not source.is_dir():
            continue
        destination = str(Path("tkinterdnd2") / "tkdnd" / source_name)
        datas.extend((str(path), destination) for path in source.glob("*.tcl"))
        binaries.extend((str(path), destination) for path in source.glob(library_pattern))

    if not datas or not binaries:
        logger.warning(
            "tkinterdnd2 hook: no complete TkDND payload found for %s", directory
        )
    return datas, binaries


datas, binaries = _collect_payload()

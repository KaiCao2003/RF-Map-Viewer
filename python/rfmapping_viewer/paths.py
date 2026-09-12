"""RF documents, companion discovery, and installed documentation."""

from __future__ import annotations

import sys
from pathlib import Path

from rfmapping_viewer.constants import (
    DEFAULT_JSON,
    DEFAULT_JSON_DIR,
    RF_DOCUMENT_EXTENSIONS,
)


def safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _resolve_existing_file(path: Path) -> Path | None:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser().absolute()
    return resolved if resolved.is_file() else None


def document_kind(path: str | Path) -> str:
    """Classify a file association without inspecting or modifying its data."""

    suffix = Path(path).suffix.lower()
    if suffix == ".tc":
        return "tuning"
    if suffix == ".probe":
        return "probe"
    if suffix in RF_DOCUMENT_EXTENSIONS:
        return "rf"
    return "unsupported"


def discover_json_files(root: Path | None = None, current_path: Path | None = None) -> list[Path]:
    base = (root or Path.cwd()).expanduser()
    candidates: list[Path] = []
    for folder in (base / DEFAULT_JSON_DIR, base):
        if folder.is_dir():
            try:
                candidates.extend(
                    candidate
                    for candidate in folder.iterdir()
                    if candidate.suffix.lower() in RF_DOCUMENT_EXTENSIONS
                    and candidate.name.lower() != "tuning_curves.json"
                )
            except OSError:
                continue
    if current_path is not None:
        candidates.append(current_path)

    unique: dict[str, Path] = {}
    for candidate in candidates:
        resolved = _resolve_existing_file(candidate)
        if resolved is not None:
            unique[str(resolved)] = resolved
    return sorted(unique.values(), key=lambda path: (safe_mtime(path), path.name), reverse=True)


def latest_json_path(root: Path | None = None) -> Path:
    files = discover_json_files(root)
    return files[0] if files else DEFAULT_JSON


def startup_file_dialog_directory() -> Path:
    """Return a stable existing directory for a no-document file picker."""

    documents = Path.home() / "Documents"
    return documents if documents.is_dir() else Path.home()


def support_documentation_path(
    *,
    module_path: Path | None = None,
    executable_path: Path | None = None,
    frozen: bool | None = None,
) -> Path | None:
    """Return the installed local README used by the Help menu."""

    module_path = Path(__file__).parent.parent / "rfmapping_gui.py" if module_path is None else Path(module_path)
    executable_path = (
        Path(sys.executable) if executable_path is None else Path(executable_path)
    )
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    candidates: list[Path] = []
    if frozen:
        executable = executable_path.expanduser().resolve()
        candidates.extend(
            (
                executable.parent.parent / "Resources" / "README.md",
                executable.parent / "Resources" / "README.md",
            )
        )
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            candidates.append(Path(bundle_root) / "README.md")
    candidates.append(module_path.expanduser().resolve().parent / "README.md")

    for candidate in candidates:
        resolved = _resolve_existing_file(candidate)
        if resolved is not None:
            return resolved
    return None

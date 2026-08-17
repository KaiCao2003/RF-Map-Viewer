from __future__ import annotations

import base64
import heapq
import json
import os
from pathlib import Path
from typing import Any, Iterable


class PathAccessError(ValueError):
    """Raised when a requested path is invalid or outside its allowed root."""


FILE_KINDS = {"rf-json", "tuning-json", "positions-csv"}
RF_MAPPING_SUFFIXES = frozenset({".rfmap", ".json"})
TUNING_CURVE_SUFFIXES = frozenset({".tc", ".json"})
PROBE_POSITION_SUFFIXES = frozenset({".probe", ".csv"})


def has_supported_rf_suffix(path: str | Path) -> bool:
    return Path(path).suffix.casefold() in RF_MAPPING_SUFFIXES


def has_supported_tuning_suffix(path: str | Path) -> bool:
    return Path(path).suffix.casefold() in TUNING_CURVE_SUFFIXES


def has_supported_probe_suffix(path: str | Path) -> bool:
    return Path(path).suffix.casefold() in PROBE_POSITION_SUFFIXES


def canonical_root(root: Path) -> Path:
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PathAccessError(f"Data root is unavailable: {root}") from exc
    if not resolved.is_dir():
        raise PathAccessError(f"Data root is not a directory: {root}")
    return resolved


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_under(
    root: Path,
    user_path: str,
    *,
    expect: str | None = None,
) -> Path:
    """Resolve a user path while refusing traversal and escaping symlinks."""

    if not isinstance(user_path, str) or "\x00" in user_path:
        raise PathAccessError("Path must be a string without null bytes")
    root = canonical_root(root)
    candidate_input = Path(user_path)
    if ".." in candidate_input.parts:
        raise PathAccessError("Parent path segments are not allowed")
    candidate = candidate_input if candidate_input.is_absolute() else root / candidate_input
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(user_path) from exc
    except (OSError, RuntimeError) as exc:
        raise PathAccessError(f"Unable to resolve path: {user_path}") from exc
    if not is_within(resolved, root):
        raise PathAccessError("Path is outside the allowed data root")
    if expect == "file" and not resolved.is_file():
        raise PathAccessError("Path is not a file")
    if expect == "directory" and not resolved.is_dir():
        raise PathAccessError("Path is not a directory")
    return resolved


def _entry_key(entry: dict[str, Any]) -> tuple[int, str, str]:
    return (
        0 if entry["type"] == "directory" else 1,
        entry["name"].casefold(),
        entry["name"],
    )


def _encode_cursor(key: tuple[int, str, str]) -> str:
    payload = json.dumps(list(key), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[int, str, str] | None:
    if cursor is None or cursor == "":
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if (
            not isinstance(raw, list)
            or len(raw) != 3
            or not isinstance(raw[0], int)
            or not isinstance(raw[1], str)
            or not isinstance(raw[2], str)
            or raw[0] not in (0, 1)
        ):
            raise ValueError
        return raw[0], raw[1], raw[2]
    except Exception as exc:
        raise PathAccessError("Invalid pagination cursor") from exc


def _matches_file_kind(name: str, kind: str) -> bool:
    folded = name.casefold()
    if kind == "rf-json":
        return has_supported_rf_suffix(name) and folded != "tuning_curves.json"
    if kind == "tuning-json":
        return folded.endswith(".tc") or folded == "tuning_curves.json"
    if kind == "positions-csv":
        return folded.endswith(".probe") or folded == "positions.csv"
    raise PathAccessError(f"Unsupported file kind: {kind}")


def _iter_visible_entries(
    directory: Path, root: Path, kind: str
) -> Iterable[dict[str, Any]]:
    try:
        iterator = os.scandir(directory)
    except OSError as exc:
        raise PathAccessError(f"Unable to list directory: {directory}") from exc

    with iterator:
        for entry in iterator:
            if entry.name.startswith("._"):
                continue
            try:
                target = Path(entry.path).resolve(strict=True)
                if not is_within(target, root):
                    continue
                is_directory = entry.is_dir(follow_symlinks=True)
                is_visible_file = entry.is_file(
                    follow_symlinks=True
                ) and _matches_file_kind(entry.name, kind)
                if not is_directory and not is_visible_file:
                    continue
                stat = entry.stat(follow_symlinks=True)
            except (FileNotFoundError, OSError, RuntimeError, ValueError):
                continue
            yield {
                "name": entry.name,
                "path": str(target),
                "type": "directory" if is_directory else "file",
                "size": None if is_directory else stat.st_size,
                "mtime": stat.st_mtime,
            }


def list_directory(
    root: Path,
    user_path: str,
    *,
    cursor: str | None,
    limit: int,
    kind: str = "rf-json",
) -> dict[str, Any]:
    if kind not in FILE_KINDS:
        raise PathAccessError(f"Unsupported file kind: {kind}")
    root = canonical_root(root)
    directory = resolve_under(root, user_path, expect="directory")
    after = _decode_cursor(cursor)

    def eligible() -> Iterable[dict[str, Any]]:
        for item in _iter_visible_entries(directory, root, kind):
            if after is None or _entry_key(item) > after:
                yield item

    page = heapq.nsmallest(limit + 1, eligible(), key=_entry_key)
    has_more = len(page) > limit
    entries = page[:limit]
    next_cursor = _encode_cursor(_entry_key(entries[-1])) if has_more and entries else None
    return {
        "root": str(root),
        "path": str(directory),
        "entries": entries,
        "nextCursor": next_cursor,
    }

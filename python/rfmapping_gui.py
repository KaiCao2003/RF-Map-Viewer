#!/usr/bin/env python3
"""Native Python/Tk viewer for RF maps and live head-direction data.

The implementation-local validated model, full native viewer, and figure
exporter do not depend on notebook state or a web server.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
import csv
import errno
import hashlib
import ctypes
import json
import math
import os
import queue
import re
import stat
import sys
import tempfile
import threading
import uuid
import webbrowser
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from rfmapping_viewer.figure_export import (
    ExportPage,
    ExportPlan,
    FigureFormat,
    PLOT_KIND_REGISTRY,
    PlotKind,
    PlotSpec,
    export_figures,
    shared_scalar_scale,
    render_live_preview,
)
from rfmapping_viewer.hd_tuning import (
    HDTuningData,
    discover_hd_tuning_path,
    load_hd_tuning,
    probe_name_for_rf,
)
from rfmapping_viewer.rf_dataset import RFMap, RFMapList, load_rf_maps
from rfmapping_viewer.waveform import (
    WaveformArtifactStore,
    WaveformPayload,
    discover_waveform_artifact,
)

try:
    from tkinter import filedialog, messagebox, ttk
    import tkinter as tk
    TK_AVAILABLE = True
except ModuleNotFoundError:
    filedialog = messagebox = ttk = None
    TK_AVAILABLE = False

    class _MissingTk:
        Tk = object
        Toplevel = object
        Misc = object
        TclError = ValueError

    tk = _MissingTk()


DEFAULT_JSON_DIR = Path("data")
DEFAULT_JSON = DEFAULT_JSON_DIR / "unitsSpikeCounts_260701_1.json"
RF_DOCUMENT_EXTENSIONS = (".rfmap", ".json")
TUNING_CURVE_EXTENSIONS = (".tc", ".json")
PROBE_POSITION_EXTENSIONS = (".probe", ".csv")
TUNING_CURVE_FILENAMES = tuple(
    f"tuning_curves{extension}" for extension in TUNING_CURVE_EXTENSIONS
)
PROBE_POSITION_FILENAMES = tuple(
    f"positions{extension}" for extension in PROBE_POSITION_EXTENSIONS
)
RF_DOCUMENT_FILETYPES = (
    ("RF mapping files", "*.rfmap *.json"),
    ("RF Map document", "*.rfmap"),
    ("JSON document", "*.json"),
    ("All files", "*.*"),
)
TUNING_CURVE_FILETYPES = (
    ("Tuning curve files", "*.tc *.json"),
    ("Tuning curve document", "*.tc"),
    ("JSON document", "*.json"),
    ("All files", "*.*"),
)
PROBE_POSITION_FILETYPES = (
    ("Probe position files", "*.probe *.csv"),
    ("Probe position document", "*.probe"),
    ("CSV document", "*.csv"),
    ("All files", "*.*"),
)
APP_VERSION = "1.9.5"
APP_EDITION = "Full"
APP_DISPLAY_VERSION = APP_VERSION
INNER_BLANK_ROWS = 4
POLAR_PAD_ROWS = 1
SINGLETON_Y_REFERENCE_COLUMNS = 30
SINGLETON_Y_REFERENCE_ROWS = 7
STARTUP_EVENT_WAIT_MS = 350
ASYNC_DOCUMENT_LOAD_BYTES = 8 * 1024 * 1024
DEFAULT_RF_SUM_START_MS = 0.0
DEFAULT_RF_SUM_END_MS = 200.0
SETTINGS_SCHEMA_VERSION = 1
HD_RAW_BIN_COUNT = 180
DEFAULT_HD_DISPLAY_BINS = 30
DEFAULT_HD_SMOOTH_SIGMA = 1.5
GAUSSIAN_TRUNCATE = 4.0
MACOS_FULLSCREEN_MAX_SIZE = float.fromhex("0x1.fffffep+127")
HD_BIN_DIVISORS = tuple(
    count for count in range(1, HD_RAW_BIN_COUNT + 1) if HD_RAW_BIN_COUNT % count == 0
)
TUNING_PLOT_MODES = ("Auto", "Polar", "Line")
TUNING_LAYOUTS = ("Side by side", "Stacked")
VALUE_MODE_COUNT = "Spike count"
VALUE_MODE_RATE = "Mean firing rate (Hz)"
VALUE_MODES = (VALUE_MODE_COUNT, VALUE_MODE_RATE)
PALETTES = ("Gray", "Viridis", "Inferno")
POLAR_RADIUS_MODES = ("MATLAB row 1 inner", "Display bottom inner")
WAVEFORM_CHANNEL_MODE_LABELS = {
    "same_x_column": "Same x column",
    "same_shank": "Same shank",
}
_USE_PATH_CSV_PUBLICATION = os.name == "nt"
WAVEFORM_CHANNEL_MODES = tuple(WAVEFORM_CHANNEL_MODE_LABELS)
WAVEFORM_CHANNEL_MODE_BY_LABEL = {
    label: mode for mode, label in WAVEFORM_CHANNEL_MODE_LABELS.items()
}
AxisGroup = tuple[int, int]
CellRef = tuple[int, int, int, int]
PROBE_CLICK_WIDTH_UM = 160.0
PROBE_CLICK_HEIGHT_UM = 75.0
PAIR_SYNC_ALL_FIELDS = frozenset(
    {
        "unit",
        "value_mode",
        "active_time",
        "timeline_selection",
        "rf_range",
        "time_resolution",
        "x_bins",
        "y_bins",
        "smoothing",
        "flip_y",
        "palette",
        "polar_radius",
        "spatial_format",
        "delay_rgb",
        "selected_cell",
        "timeline_scroll",
        "selected_tab",
        "tuning_display",
        "optional_views",
    }
)
_RECORDING_SESSION_RE = re.compile(r"^\d{6}_\d+$")


@dataclass(frozen=True)
class FrozenFileIdentity:
    """Stable identity of a regular input captured while its data is loaded."""

    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    mode: int
    handle_device: int
    handle_inode: int
    handle_size: int
    handle_mtime_ns: int
    handle_mode: int

    @classmethod
    def capture(cls, path: str | Path) -> FrozenFileIdentity:
        source = Path(path).expanduser().resolve(strict=True)
        path_before = os.stat(source, follow_symlinks=False)
        if not stat.S_ISREG(path_before.st_mode):
            raise ValueError(f"Scientific input is not a regular file: {source}")
        descriptor = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            handle_info = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        path_after = os.stat(source, follow_symlinks=False)
        if not stat.S_ISREG(handle_info.st_mode):
            raise ValueError(f"Scientific input is not a regular file: {source}")
        if (
            handle_info.st_size != path_after.st_size
            or cls.path_signature(path_after) != cls.path_signature(path_before)
        ):
            raise ValueError(f"Scientific input changed while it was opened: {source}")
        return cls(
            source,
            int(path_after.st_dev),
            int(path_after.st_ino),
            int(path_after.st_size),
            int(path_after.st_mtime_ns),
            int(path_after.st_ctime_ns),
            int(stat.S_IFMT(path_after.st_mode)),
            int(handle_info.st_dev),
            int(handle_info.st_ino),
            int(handle_info.st_size),
            int(handle_info.st_mtime_ns),
            int(stat.S_IFMT(handle_info.st_mode)),
        )

    @staticmethod
    def path_signature(info: os.stat_result) -> tuple[int, ...]:
        return (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(info.st_ctime_ns) if os.name != "nt" else 0,
            int(stat.S_IFMT(info.st_mode)),
        )

    def matches(self, info: os.stat_result) -> bool:
        stable_fields_match = (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(stat.S_IFMT(info.st_mode)),
        ) == (
            self.device,
            self.inode,
            self.size,
            self.mtime_ns,
            self.mode,
        )
        # Windows reports creation/change timestamps inconsistently between
        # path stat and an open file handle.  Size, mtime, file identity, and
        # the provenance digest still detect scientific-input mutations.
        return stable_fields_match and (
            os.name == "nt" or int(info.st_ctime_ns) == self.ctime_ns
        )

    def matches_open_file(self, info: os.stat_result) -> bool:
        return (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(stat.S_IFMT(info.st_mode)),
        ) == (
            self.handle_device,
            self.handle_inode,
            self.handle_size,
            self.handle_mtime_ns,
            self.handle_mode,
        )

    @staticmethod
    def open_file_signature(info: os.stat_result) -> tuple[int, ...]:
        return (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(stat.S_IFMT(info.st_mode)),
        )

    def verify_path(self) -> None:
        try:
            info = os.stat(self.path, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError(f"Scientific input is no longer available: {self.path}") from exc
        if not self.matches(info):
            raise RuntimeError(
                f"Scientific input changed after it was loaded; reopen it before exporting: {self.path}"
            )

    def metadata(self, sha256: str) -> dict[str, object]:
        return {
            "path": str(self.path),
            "sha256": sha256,
            "sizeBytes": self.size,
            "device": self.device,
            "inode": self.inode,
            "mtimeNs": self.mtime_ns,
            "ctimeNs": self.ctime_ns,
        }


def _hash_frozen_file(
    identity: FrozenFileIdentity,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    """Hash exactly the frozen input, cooperatively aborting stale previews."""

    def check_cancelled() -> None:
        if cancelled is not None and cancelled():
            raise RuntimeError("Preview superseded by a newer recipe")

    check_cancelled()
    identity.verify_path()
    descriptor = os.open(
        identity.path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if not identity.matches_open_file(before):
            raise RuntimeError(
                f"Scientific input changed after it was loaded; reopen it before exporting: {identity.path}"
            )
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            check_cancelled()
            digest.update(chunk)
        check_cancelled()
        after = os.fstat(descriptor)
        if (
            not identity.matches_open_file(after)
            or identity.open_file_signature(after)
            != identity.open_file_signature(before)
        ):
            raise RuntimeError(
                f"Scientific input changed while provenance was computed: {identity.path}"
            )
    finally:
        os.close(descriptor)
    identity.verify_path()
    return digest.hexdigest()


def _export_executor(root: tk.Misc) -> ThreadPoolExecutor:
    executor = getattr(root, "_rfm_export_executor", None)
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rfmap-export")
        root._rfm_export_executor = executor
        root._rfm_export_jobs = {}
        root._rfm_export_jobs_lock = threading.Lock()
    return executor


def _submit_daemon_future(action: Callable[[], object], *, name: str) -> Future:
    """Run cancellable preview work without keeping the interpreter alive."""

    future: Future = Future()

    def run() -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            result = action()
        except BaseException as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)

    threading.Thread(target=run, name=name, daemon=True).start()
    return future


def _register_export_job(root: tk.Misc, viewer: object, future: Future) -> None:
    _export_executor(root)
    with root._rfm_export_jobs_lock:
        root._rfm_export_jobs[future] = viewer


def _unregister_export_job(root: tk.Misc, future: Future | None) -> None:
    if future is None:
        return
    lock = getattr(root, "_rfm_export_jobs_lock", None)
    if lock is None:
        return
    with lock:
        root._rfm_export_jobs.pop(future, None)


def _active_export_jobs(root: tk.Misc, viewer: object | None = None) -> tuple[Future, ...]:
    jobs = getattr(root, "_rfm_export_jobs", {})
    lock = getattr(root, "_rfm_export_jobs_lock", None)
    if lock is None:
        return ()
    with lock:
        return tuple(
            future
            for future, owner in jobs.items()
            if viewer is None or owner is viewer
        )


def _shutdown_export_executor(root: tk.Misc) -> None:
    executor = getattr(root, "_rfm_export_executor", None)
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)
        root._rfm_export_executor = None


def _path_is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _path_stat_signature(result: os.stat_result | None) -> tuple | None:
    if result is None:
        return None
    return (
        result.st_dev,
        result.st_ino,
        stat.S_IFMT(result.st_mode),
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _path_lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_csv_path_backend(
    target: Path,
    write_rows: Callable[[csv.writer], None],
    *,
    before_publish: Callable[[], None] | None,
) -> Path:
    """Windows-safe sibling staging and atomic file replacement."""

    parent = target.parent if str(target.parent) else Path(".")
    parent_before = _path_lstat(parent)
    if (
        parent_before is None
        or _path_is_link_like(parent)
        or not stat.S_ISDIR(parent_before.st_mode)
    ):
        raise ValueError("CSV parent must be a real directory")
    existing = _path_lstat(target)
    if existing is not None and (
        _path_is_link_like(target) or not stat.S_ISREG(existing.st_mode)
    ):
        raise ValueError("CSV destination must be a regular file")
    existing_signature = _path_stat_signature(existing)
    temporary_path = parent / f".{target.name}.tmp-{uuid.uuid4().hex}"
    descriptor: int | None = os.open(
        temporary_path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            descriptor = None
            write_rows(csv.writer(stream))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, 0o660)
        staged = temporary_path.lstat()
        staged_digest = _file_sha256(temporary_path)
        if before_publish is not None:
            before_publish()
        current = _path_lstat(target)
        if _path_stat_signature(current) != existing_signature:
            raise RuntimeError("CSV destination changed while the export was being written")
        parent_now = _path_lstat(parent)
        if (
            parent_now is None
            or _path_is_link_like(parent)
            or (
                parent_now.st_dev,
                parent_now.st_ino,
                stat.S_IFMT(parent_now.st_mode),
            )
            != (
                parent_before.st_dev,
                parent_before.st_ino,
                stat.S_IFMT(parent_before.st_mode),
            )
        ):
            raise RuntimeError("CSV parent directory changed while the export was being written")
        try:
            os.replace(temporary_path, target)
        except OSError:
            published = _path_lstat(target)
            if (
                temporary_path.exists()
                or published is None
                or published.st_size != staged.st_size
                or _file_sha256(target) != staged_digest
            ):
                raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
    return target


def _atomic_write_csv(
    destination: str | Path,
    write_rows: Callable[[csv.writer], None],
    *,
    before_publish: Callable[[], None] | None = None,
) -> Path:
    """Publish one complete CSV atomically.

    Failures before ``os.replace`` preserve the previous destination. A lost
    replace reply is recognized from the staged inode. A later durability
    failure is reported explicitly even though the complete new file is visible.
    """

    target = Path(destination).expanduser()
    if not target.name or target.name in {".", ".."}:
        raise ValueError("CSV destination must name a file")
    if _USE_PATH_CSV_PUBLICATION:
        return _atomic_write_csv_path_backend(
            target,
            write_rows,
            before_publish=before_publish,
        )
    parent = target.parent if str(target.parent) else Path(".")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(parent, directory_flags)
    temporary = f".{target.name}.tmp-{uuid.uuid4().hex}"
    descriptor: int | None = None
    try:
        try:
            existing = os.stat(target.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise ValueError("CSV destination must be a regular file")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            descriptor = None
            write_rows(csv.writer(stream))
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o660)
            os.fsync(stream.fileno())
        if before_publish is not None:
            before_publish()
        try:
            current = os.stat(target.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if (existing is None) != (current is None) or (
            existing is not None
            and current is not None
            and (
                existing.st_dev,
                existing.st_ino,
                stat.S_IFMT(existing.st_mode),
                existing.st_size,
                existing.st_mtime_ns,
                existing.st_ctime_ns,
            )
            != (
                current.st_dev,
                current.st_ino,
                stat.S_IFMT(current.st_mode),
                current.st_size,
                current.st_mtime_ns,
                current.st_ctime_ns,
            )
        ):
            raise RuntimeError("CSV destination changed while the export was being written")
        parent_now = os.stat(parent, follow_symlinks=False)
        parent_open = os.fstat(directory_fd)
        if (parent_now.st_dev, parent_now.st_ino) != (parent_open.st_dev, parent_open.st_ino):
            raise RuntimeError("CSV parent directory changed while the export was being written")
        staged = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
        try:
            os.replace(
                temporary,
                target.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        except OSError:
            # CIFS/NFS can commit the rename and lose only its success reply.
            # Treat that as success iff the complete staged inode is now the
            # destination and the temporary name disappeared.
            try:
                published = os.stat(
                    target.name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                published = None
            try:
                os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
                temporary_still_exists = True
            except FileNotFoundError:
                temporary_still_exists = False
            if (
                published is None
                or temporary_still_exists
                or (published.st_dev, published.st_ino, published.st_size)
                != (staged.st_dev, staged.st_ino, staged.st_size)
            ):
                raise
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            unsupported = {errno.EINVAL, errno.EOPNOTSUPP}
            if hasattr(errno, "ENOTSUP"):
                unsupported.add(errno.ENOTSUP)
            if exc.errno not in unsupported:
                raise RuntimeError(
                    "CSV was atomically published, but directory durability "
                    "could not be confirmed"
                ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(directory_fd)
    return target


def composer_unit_selection_after_click(
    selected_indices: Iterable[int],
    clicked_index: int,
    anchor_index: int | None,
    unit_count: int,
    *,
    command: bool = False,
    shift: bool = False,
) -> tuple[tuple[int, ...], int]:
    """Apply Finder-style row selection and return sorted source indices.

    A plain click replaces the selection, Command-click toggles one row while
    retaining the others, and Shift-click replaces the selection with the
    inclusive range from the stable anchor. Command-Shift-click adds that range.
    Sorting by source index is intentional: downstream exports must always
    follow the JSON ``unitPool`` order, never the order in which rows were
    clicked.
    """

    if unit_count < 0:
        raise ValueError("unit_count must be non-negative")
    if not 0 <= clicked_index < unit_count:
        raise IndexError(f"unit index {clicked_index} is outside 0..{unit_count - 1}")

    selected = {
        int(index)
        for index in selected_indices
        if 0 <= int(index) < unit_count
    }
    valid_anchor = (
        int(anchor_index)
        if anchor_index is not None and 0 <= int(anchor_index) < unit_count
        else None
    )

    if shift:
        anchor = clicked_index if valid_anchor is None else valid_anchor
        first, last = sorted((anchor, clicked_index))
        clicked_range = set(range(first, last + 1))
        selected = selected | clicked_range if command else clicked_range
        return tuple(sorted(selected)), anchor

    if command:
        if clicked_index in selected:
            selected.remove(clicked_index)
        else:
            selected.add(clicked_index)
        return tuple(sorted(selected)), clicked_index

    return (clicked_index,), clicked_index


def composer_unit_checkbox_hit(
    event_x: int,
    row_text_x: int,
    checkbox_hit_width: int,
) -> bool:
    """Return whether a row click landed in its leading checkbox column."""

    if checkbox_hit_width <= 0:
        raise ValueError("checkbox_hit_width must be positive")
    return row_text_x <= event_x < row_text_x + checkbox_hit_width


class PreparedSpatialMatrix(list):
    """A display-grouped matrix that must not be reduced or smoothed again."""

    def __init__(
        self,
        values: list[list[float | None]],
        x_groups: list[AxisGroup],
        y_groups: list[AxisGroup],
    ) -> None:
        super().__init__(values)
        self.x_groups = x_groups
        self.y_groups = y_groups


def timeline_scroll_progress(first: float, last: float) -> float | None:
    """Convert a Tk canvas yview into viewport-independent scroll progress.

    Tk reports fractions of the full scroll region.  The first fraction at the
    bottom therefore depends on how much of that region the current viewport
    can show.  Pairing stores progress through the *scrollable travel* instead.
    ``None`` means the canvas is currently not scrollable, so callers should
    preserve the last meaningful progress for a later draw.
    """

    first = float(first)
    last = float(last)
    visible_span = max(0.0, min(1.0, last - first))
    max_first = max(0.0, 1.0 - visible_span)
    if max_first <= 1e-9:
        return None
    progress = max(0.0, min(1.0, first / max_first))
    if progress <= 1e-9:
        return 0.0
    if progress >= 1.0 - 1e-9:
        return 1.0
    return progress


def timeline_scroll_offset(progress: float, first: float, last: float) -> float | None:
    """Map normalized scroll progress to a target canvas yview offset."""

    visible_span = max(0.0, min(1.0, float(last) - float(first)))
    max_first = max(0.0, 1.0 - visible_span)
    if max_first <= 1e-9:
        return None
    return max(0.0, min(1.0, float(progress))) * max_first


def timeline_position_fraction(
    time_ms: float,
    axis_start_ms: float,
    axis_end_ms: float,
) -> float:
    """Map physical time onto the timeline axis, clamped to its visible span."""

    values = (float(time_ms), float(axis_start_ms), float(axis_end_ms))
    if not all(math.isfinite(value) for value in values):
        return 0.0
    span = values[2] - values[1]
    if span <= 0.0:
        return 0.0
    return max(0.0, min(1.0, (values[0] - values[1]) / span))


def timeline_chart_points(
    values: Sequence[float],
    center_times_ms: Sequence[float],
    axis_range_ms: tuple[float, float],
    high: float,
    chart_rect: tuple[float, float, float, float],
) -> list[float]:
    """Return a Tk polyline whose x coordinates use real bin-center times."""

    chart_x, chart_y, chart_width, chart_height = map(float, chart_rect)
    safe_high = float(high)
    if not math.isfinite(safe_high) or safe_high <= 0.0:
        safe_high = 1.0
    points: list[float] = []
    for value, center_ms in zip(values, center_times_ms):
        numeric = float(value)
        if not math.isfinite(numeric):
            numeric = 0.0
        response_fraction = max(0.0, min(1.0, numeric / safe_high))
        points.extend(
            (
                chart_x
                + chart_width
                * timeline_position_fraction(center_ms, axis_range_ms[0], axis_range_ms[1]),
                chart_y + chart_height - chart_height * response_fraction,
            )
        )
    return points


def timeline_response_high(values: Sequence[float]) -> float:
    """Return one trace's non-negative y-axis maximum."""

    high = 0.0
    for value in values:
        numeric = float(value)
        if math.isfinite(numeric):
            high = max(high, numeric)
    return max(high, 1.0)


def timeline_bin_index(time_ms: float, end_bounds_ms: Sequence[float]) -> int | None:
    """Return the half-open physical-time bin containing ``time_ms``."""

    if not end_bounds_ms:
        return None
    for index, end_ms in enumerate(end_bounds_ms):
        if float(time_ms) < float(end_ms):
            return index
    return len(end_bounds_ms) - 1


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

    module_path = Path(__file__) if module_path is None else Path(module_path)
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


def viewer_settings_path(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the per-user settings path without requiring a GUI."""

    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    if platform == "darwin":
        return home / "Library" / "Application Support" / "RF Map Viewer" / "settings.json"
    if platform.startswith("win"):
        appdata = environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "RF Map Viewer" / "settings.json"
    xdg_config = environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else home / ".config"
    return base / "rf-map-viewer" / "settings.json"


def normalize_hd_bin_count(value: int) -> int:
    """Clamp a requested display count to the greatest divisor of 180 below it."""

    requested = max(1, min(HD_RAW_BIN_COUNT, int(value)))
    return max(divisor for divisor in HD_BIN_DIVISORS if divisor <= requested)


@dataclass(frozen=True)
class ViewerSettings:
    schema_version: int = SETTINGS_SCHEMA_VERSION
    show_tuning_curve: bool = True
    auto_load_tuning_curve: bool = True
    show_waveform: bool = True
    show_probe_layout: bool = True
    auto_load_probe_layout: bool = True
    rf_sum_start_ms: float = DEFAULT_RF_SUM_START_MS
    rf_sum_end_ms: float = DEFAULT_RF_SUM_END_MS
    rf_filter_units_with_zero_bins: bool = True
    rf_zero_bin_threshold: int = 1
    rf_time_resolution_ms: float = 1.0
    rf_value_mode: str = VALUE_MODE_RATE
    rf_x_bins: int = 0
    rf_y_bins: int = 0
    rf_smooth_radius: int = 0
    rf_flip_y: bool = False
    rf_palette: str = "Gray"
    rf_polar_radius: str = POLAR_RADIUS_MODES[1]
    rf_polar_layout: bool = False
    rf_rgb_mode: bool = False
    default_viewer_tab: str = "rf"
    waveform_channel_mode: str = "same_x_column"
    tuning_plot_mode: str = "Auto"
    tuning_layout: str = TUNING_LAYOUTS[0]
    tuning_display_bins: int = DEFAULT_HD_DISPLAY_BINS
    tuning_smoothing: bool = True
    tuning_smooth_sigma: float = DEFAULT_HD_SMOOTH_SIGMA
    tuning_compare_scale: bool = False

    @classmethod
    def from_mapping(cls, payload: object) -> ViewerSettings:
        defaults = cls()
        if not isinstance(payload, Mapping):
            return defaults
        schema = payload.get("schema_version", SETTINGS_SCHEMA_VERSION)
        if type(schema) is not int or schema != SETTINGS_SCHEMA_VERSION:
            return defaults

        def boolean(name: str) -> bool:
            value = payload.get(name, getattr(defaults, name))
            return value if type(value) is bool else getattr(defaults, name)

        def finite_float(name: str, *, positive: bool = False) -> float:
            value = payload.get(name, getattr(defaults, name))
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return getattr(defaults, name)
            result = float(value)
            if not math.isfinite(result) or (positive and result <= 0.0):
                return getattr(defaults, name)
            return result

        def integer(name: str, low: int, high: int) -> int:
            value = payload.get(name, getattr(defaults, name))
            if type(value) is not int:
                return getattr(defaults, name)
            return max(low, min(high, value))

        start_ms = finite_float("rf_sum_start_ms")
        end_ms = finite_float("rf_sum_end_ms")
        if start_ms >= end_ms:
            start_ms = defaults.rf_sum_start_ms
            end_ms = defaults.rf_sum_end_ms

        value_mode = payload.get("rf_value_mode", defaults.rf_value_mode)
        if value_mode not in VALUE_MODES:
            value_mode = defaults.rf_value_mode
        palette = payload.get("rf_palette", defaults.rf_palette)
        if palette not in PALETTES:
            palette = defaults.rf_palette
        polar_radius = payload.get("rf_polar_radius", defaults.rf_polar_radius)
        if polar_radius not in POLAR_RADIUS_MODES:
            polar_radius = defaults.rf_polar_radius
        viewer_tab = payload.get("default_viewer_tab", defaults.default_viewer_tab)
        if viewer_tab not in {"rf", "delay", "timeline"}:
            viewer_tab = defaults.default_viewer_tab
        waveform_channel_mode = payload.get(
            "waveform_channel_mode", defaults.waveform_channel_mode
        )
        if waveform_channel_mode not in WAVEFORM_CHANNEL_MODES:
            waveform_channel_mode = defaults.waveform_channel_mode
        tuning_mode = payload.get("tuning_plot_mode", defaults.tuning_plot_mode)
        if tuning_mode not in TUNING_PLOT_MODES:
            tuning_mode = defaults.tuning_plot_mode
        tuning_layout = payload.get("tuning_layout", defaults.tuning_layout)
        if tuning_layout not in TUNING_LAYOUTS:
            tuning_layout = defaults.tuning_layout
        raw_hd_bins = payload.get("tuning_display_bins", defaults.tuning_display_bins)
        if type(raw_hd_bins) is not int:
            raw_hd_bins = defaults.tuning_display_bins

        return cls(
            show_tuning_curve=boolean("show_tuning_curve"),
            auto_load_tuning_curve=boolean("auto_load_tuning_curve"),
            show_waveform=boolean("show_waveform"),
            show_probe_layout=boolean("show_probe_layout"),
            auto_load_probe_layout=boolean("auto_load_probe_layout"),
            rf_sum_start_ms=start_ms,
            rf_sum_end_ms=end_ms,
            rf_filter_units_with_zero_bins=boolean(
                "rf_filter_units_with_zero_bins"
            ),
            rf_zero_bin_threshold=integer(
                "rf_zero_bin_threshold", 1, 100_000
            ),
            rf_time_resolution_ms=finite_float("rf_time_resolution_ms", positive=True),
            rf_value_mode=value_mode,
            rf_x_bins=integer("rf_x_bins", 0, 100_000),
            rf_y_bins=integer("rf_y_bins", 0, 100_000),
            rf_smooth_radius=integer("rf_smooth_radius", 0, 3),
            rf_flip_y=boolean("rf_flip_y"),
            rf_palette=palette,
            rf_polar_radius=polar_radius,
            rf_polar_layout=boolean("rf_polar_layout"),
            rf_rgb_mode=boolean("rf_rgb_mode"),
            default_viewer_tab=viewer_tab,
            waveform_channel_mode=waveform_channel_mode,
            tuning_plot_mode=tuning_mode,
            tuning_layout=tuning_layout,
            tuning_display_bins=normalize_hd_bin_count(raw_hd_bins),
            tuning_smoothing=boolean("tuning_smoothing"),
            tuning_smooth_sigma=finite_float("tuning_smooth_sigma", positive=True),
            tuning_compare_scale=boolean("tuning_compare_scale"),
        )

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


def load_viewer_settings(path: Path | None = None) -> ViewerSettings:
    settings_path = viewer_settings_path() if path is None else Path(path)
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ViewerSettings()
    return ViewerSettings.from_mapping(payload)


def save_viewer_settings(settings: ViewerSettings, path: Path | None = None) -> Path:
    settings_path = viewer_settings_path() if path is None else Path(path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=settings_path.parent,
            prefix=f".{settings_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            json.dump(settings.to_mapping(), stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)
        os.replace(temporary_path, settings_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return settings_path


@dataclass(frozen=True)
class TuningCurveClassificationProvenance:
    method: str | None = None
    class_0: str | None = None
    class_1: str | None = None
    class_2: str | None = None
    class_null: str | None = None
    rayleigh_alpha: float | None = None
    rayleigh_test: str | None = None
    shuffle_alpha: float | None = None
    num_shuffle: int | None = None
    shuffle_seed: int | None = None


@dataclass(frozen=True)
class TuningCurveTTLProvenance:
    ttl_pulse_count: int | None = None
    first_exposure_s: float | None = None
    last_exposure_s: float | None = None
    median_period_s: float | None = None
    measured_rate_hz: float | None = None
    camera_input_channel: int | None = None
    camera_ttl_threshold: float | None = None
    camera_ttl_active_high: bool | None = None
    motive_frame_count_raw: int | None = None
    matched_motive_frame_count: int | None = None
    dropped_motive_frame_ids: tuple[int, ...] | None = None
    frame_alignment_policy_requested: str | None = None
    frame_alignment_policy_applied: str | None = None
    frame_timestamp_mapping: str | None = None


@dataclass(frozen=True)
class TuningCurveMetadata:
    session: str | None = None
    probe: str | None = None
    kilosort_dir: str | None = None
    timebase: str | None = None
    adc_time_origin_raw_s: float | None = None
    timestamp_reference: str | None = None
    angle_convention_note: str | None = None
    num_angle_bins: int | None = None
    feature_fs_hz: float | None = None
    classification: TuningCurveClassificationProvenance | None = None
    ttl_qc: TuningCurveTTLProvenance | None = None


@dataclass(frozen=True)
class TuningCurveData:
    path: Path
    curves: Mapping[int, tuple[float, ...]]
    spike_counts: Mapping[int, tuple[float, ...]] = field(default_factory=dict)
    occupancy_time_s: tuple[float, ...] | None = None
    hd_classes: Mapping[int, int | None] = field(default_factory=dict)
    metadata: TuningCurveMetadata | None = None

    @classmethod
    def load(cls, path: Path) -> TuningCurveData:
        resolved = Path(path).expanduser().resolve()
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid tuning-curve JSON: {exc}") from exc
        if not isinstance(payload, dict) or not payload:
            raise ValueError("Tuning-curve JSON must be a non-empty cluster mapping.")
        if {
            "unit_id",
            "spike_counts",
            "firing_rate_hz",
            "unit_data",
            "occupancy_time_s",
        }.issubset(payload):
            return cls._load_columnar(resolved)
        if "schema_version" in payload:
            if type(payload["schema_version"]) is not int or payload["schema_version"] != 2:
                raise ValueError(
                    f"Unsupported tuning-curve schema version: {payload['schema_version']!r}."
                )
            return cls._load_schema_v2(resolved, payload)
        return cls._load_legacy(resolved, payload)

    @classmethod
    def _load_columnar(cls, resolved: Path) -> TuningCurveData:
        """Adapt the current columnar HD model to the live-view interface."""

        data = load_hd_tuning(resolved)
        curves = {
            unit.unit_id: tuple(float(value) for value in unit.raw_rates_hz)
            for unit in data
        }
        spike_counts = {
            unit.unit_id: tuple(float(value) for value in unit.spike_counts)
            for unit in data
        }
        hd_classes = {unit.unit_id: unit.hd_class for unit in data}
        try:
            metadata = cls._load_metadata(dict(data.metadata))
        except ValueError:
            # Plot data remain valid even when a newer metadata-only field has
            # no legacy presentation counterpart.
            metadata = None
        return cls(
            path=resolved,
            curves=curves,
            spike_counts=spike_counts,
            occupancy_time_s=tuple(float(value) for value in data.occupancy_time_s),
            hd_classes=hd_classes,
            metadata=metadata,
        )

    @classmethod
    def _load_legacy(cls, resolved: Path, payload: Mapping[object, object]) -> TuningCurveData:
        curves: dict[int, tuple[float, ...]] = {}
        for raw_cluster_id, raw_rates in payload.items():
            try:
                cluster_id = int(raw_cluster_id)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid cluster ID: {raw_cluster_id!r}") from exc
            if cluster_id in curves:
                raise ValueError(f"Duplicate cluster ID after normalization: {cluster_id}")
            if not isinstance(raw_rates, list) or len(raw_rates) != HD_RAW_BIN_COUNT:
                length = len(raw_rates) if isinstance(raw_rates, list) else "non-list"
                raise ValueError(
                    f"Cluster {cluster_id} must contain exactly {HD_RAW_BIN_COUNT} rates; got {length}."
                )
            rates: list[float] = []
            for index, raw_rate in enumerate(raw_rates):
                if isinstance(raw_rate, bool) or not isinstance(raw_rate, (int, float)):
                    raise ValueError(f"Cluster {cluster_id} rate {index + 1} is not numeric.")
                rate = float(raw_rate)
                if not math.isfinite(rate) or rate < 0.0:
                    raise ValueError(
                        f"Cluster {cluster_id} rate {index + 1} must be finite and non-negative."
                    )
                rates.append(rate)
            curves[cluster_id] = tuple(rates)
        return cls(path=resolved, curves=curves)

    @staticmethod
    def _metadata_string(
        payload: Mapping[object, object], key: str, context: str
    ) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Schema v2 {context}.{key} must be a string or null.")
        return value

    @staticmethod
    def _metadata_float(
        payload: Mapping[object, object], key: str, context: str
    ) -> float | None:
        value = payload.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Schema v2 {context}.{key} must be numeric or null.")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"Schema v2 {context}.{key} must be finite.")
        return number

    @staticmethod
    def _metadata_int(
        payload: Mapping[object, object], key: str, context: str
    ) -> int | None:
        value = payload.get(key)
        if value is None:
            return None
        if type(value) is not int:
            raise ValueError(f"Schema v2 {context}.{key} must be an integer or null.")
        return int(value)

    @staticmethod
    def _metadata_bool(
        payload: Mapping[object, object], key: str, context: str
    ) -> bool | None:
        value = payload.get(key)
        if value is None:
            return None
        if type(value) is not bool:
            raise ValueError(f"Schema v2 {context}.{key} must be boolean or null.")
        return bool(value)

    @staticmethod
    def _metadata_int_tuple(
        payload: Mapping[object, object], key: str, context: str
    ) -> tuple[int, ...] | None:
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, list) or any(type(item) is not int for item in value):
            raise ValueError(
                f"Schema v2 {context}.{key} must be an integer list or null."
            )
        return tuple(int(item) for item in value)

    @classmethod
    def _load_metadata(cls, raw_metadata: object) -> TuningCurveMetadata | None:
        if raw_metadata is None:
            return None
        if not isinstance(raw_metadata, dict):
            raise ValueError("Schema v2 metadata must be an object or null.")
        classification_raw = raw_metadata.get("classification")
        if classification_raw is None:
            classification = None
        elif not isinstance(classification_raw, dict):
            raise ValueError(
                "Schema v2 metadata.classification must be an object or null."
            )
        else:
            context = "metadata.classification"
            classification = TuningCurveClassificationProvenance(
                method=cls._metadata_string(classification_raw, "method", context),
                class_0=cls._metadata_string(classification_raw, "class_0", context),
                class_1=cls._metadata_string(classification_raw, "class_1", context),
                class_2=cls._metadata_string(classification_raw, "class_2", context),
                class_null=cls._metadata_string(
                    classification_raw, "class_null", context
                ),
                rayleigh_alpha=cls._metadata_float(
                    classification_raw, "rayleigh_alpha", context
                ),
                rayleigh_test=cls._metadata_string(
                    classification_raw, "rayleigh_test", context
                ),
                shuffle_alpha=cls._metadata_float(
                    classification_raw, "shuffle_alpha", context
                ),
                num_shuffle=cls._metadata_int(
                    classification_raw, "num_shuffle", context
                ),
                shuffle_seed=cls._metadata_int(
                    classification_raw, "shuffle_seed", context
                ),
            )

        ttl_raw = raw_metadata.get("ttl_qc")
        if ttl_raw is None:
            ttl_qc = None
        elif not isinstance(ttl_raw, dict):
            raise ValueError("Schema v2 metadata.ttl_qc must be an object or null.")
        else:
            context = "metadata.ttl_qc"
            ttl_qc = TuningCurveTTLProvenance(
                ttl_pulse_count=cls._metadata_int(
                    ttl_raw, "ttl_pulse_count", context
                ),
                first_exposure_s=cls._metadata_float(
                    ttl_raw, "first_exposure_s", context
                ),
                last_exposure_s=cls._metadata_float(
                    ttl_raw, "last_exposure_s", context
                ),
                median_period_s=cls._metadata_float(
                    ttl_raw, "median_period_s", context
                ),
                measured_rate_hz=cls._metadata_float(
                    ttl_raw, "measured_rate_hz", context
                ),
                camera_input_channel=cls._metadata_int(
                    ttl_raw, "camera_input_channel", context
                ),
                camera_ttl_threshold=cls._metadata_float(
                    ttl_raw, "camera_ttl_threshold", context
                ),
                camera_ttl_active_high=cls._metadata_bool(
                    ttl_raw, "camera_ttl_active_high", context
                ),
                motive_frame_count_raw=cls._metadata_int(
                    ttl_raw, "motive_frame_count_raw", context
                ),
                matched_motive_frame_count=cls._metadata_int(
                    ttl_raw, "matched_motive_frame_count", context
                ),
                dropped_motive_frame_ids=cls._metadata_int_tuple(
                    ttl_raw, "dropped_motive_frame_ids", context
                ),
                frame_alignment_policy_requested=cls._metadata_string(
                    ttl_raw, "frame_alignment_policy_requested", context
                ),
                frame_alignment_policy_applied=cls._metadata_string(
                    ttl_raw, "frame_alignment_policy_applied", context
                ),
                frame_timestamp_mapping=cls._metadata_string(
                    ttl_raw, "frame_timestamp_mapping", context
                ),
            )

        context = "metadata"
        return TuningCurveMetadata(
            session=cls._metadata_string(raw_metadata, "session", context),
            probe=cls._metadata_string(raw_metadata, "probe", context),
            kilosort_dir=cls._metadata_string(raw_metadata, "kilosort_dir", context),
            timebase=cls._metadata_string(raw_metadata, "timebase", context),
            adc_time_origin_raw_s=cls._metadata_float(
                raw_metadata, "adc_time_origin_raw_s", context
            ),
            timestamp_reference=cls._metadata_string(
                raw_metadata, "timestamp_reference", context
            ),
            angle_convention_note=cls._metadata_string(
                raw_metadata, "angle_convention_note", context
            ),
            num_angle_bins=cls._metadata_int(
                raw_metadata, "num_angle_bins", context
            ),
            feature_fs_hz=cls._metadata_float(
                raw_metadata, "feature_fs_hz", context
            ),
            classification=classification,
            ttl_qc=ttl_qc,
        )

    @classmethod
    def _load_schema_v2(cls, resolved: Path, payload: Mapping[object, object]) -> TuningCurveData:
        metadata = cls._load_metadata(payload.get("metadata"))
        raw_edges = payload.get("angle_bin_edges_deg")
        if not isinstance(raw_edges, list) or len(raw_edges) != HD_RAW_BIN_COUNT + 1:
            raise ValueError(
                f"Schema v2 angle_bin_edges_deg must contain {HD_RAW_BIN_COUNT + 1} values."
            )
        edges: list[float] = []
        for index, raw_edge in enumerate(raw_edges):
            if isinstance(raw_edge, bool) or not isinstance(raw_edge, (int, float)):
                raise ValueError(f"Schema v2 angle edge {index + 1} is not numeric.")
            edge = float(raw_edge)
            if not math.isfinite(edge):
                raise ValueError(f"Schema v2 angle edge {index + 1} must be finite.")
            edges.append(edge)
        if not all(after > before for before, after in zip(edges, edges[1:])):
            raise ValueError("Schema v2 angle_bin_edges_deg must be strictly increasing.")
        expected_width = 360.0 / HD_RAW_BIN_COUNT
        if not all(
            math.isclose(edge, index * expected_width, rel_tol=0.0, abs_tol=1e-8)
            for index, edge in enumerate(edges)
        ):
            raise ValueError("Schema v2 angle bins must span 0–360° in 180 equal bins.")

        raw_occupancy = payload.get("occupancy_time_s")
        if not isinstance(raw_occupancy, list) or len(raw_occupancy) != HD_RAW_BIN_COUNT:
            raise ValueError(
                f"Schema v2 occupancy_time_s must contain {HD_RAW_BIN_COUNT} values."
            )
        occupancy: list[float] = []
        for index, raw_value in enumerate(raw_occupancy):
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ValueError(f"Schema v2 occupancy time {index + 1} is not numeric.")
            value = float(raw_value)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(
                    f"Schema v2 occupancy time {index + 1} must be finite and non-negative."
                )
            occupancy.append(value)
        if not any(value > 0.0 for value in occupancy):
            raise ValueError("Schema v2 occupancy_time_s must contain positive occupancy.")

        raw_units = payload.get("units")
        if not isinstance(raw_units, list) or not raw_units:
            raise ValueError("Schema v2 units must be a non-empty list.")
        curves: dict[int, tuple[float, ...]] = {}
        spike_counts: dict[int, tuple[int, ...]] = {}
        hd_classes: dict[int, int | None] = {}
        for unit_index, raw_unit in enumerate(raw_units):
            if not isinstance(raw_unit, dict):
                raise ValueError(f"Schema v2 unit {unit_index + 1} must be an object.")
            raw_unit_id = raw_unit.get("unit_id")
            if type(raw_unit_id) is not int:
                raise ValueError(f"Schema v2 unit {unit_index + 1} has an invalid unit_id.")
            unit_id = int(raw_unit_id)
            if unit_id in curves:
                raise ValueError(f"Duplicate schema v2 unit_id: {unit_id}")

            raw_counts = raw_unit.get("spike_counts")
            raw_rates = raw_unit.get("firing_rate_hz")
            if not isinstance(raw_counts, list) or len(raw_counts) != HD_RAW_BIN_COUNT:
                raise ValueError(
                    f"Unit {unit_id} spike_counts must contain {HD_RAW_BIN_COUNT} values."
                )
            if not isinstance(raw_rates, list) or len(raw_rates) != HD_RAW_BIN_COUNT:
                raise ValueError(
                    f"Unit {unit_id} firing_rate_hz must contain {HD_RAW_BIN_COUNT} values."
                )

            counts: list[int] = []
            rates: list[float] = []
            for bin_index, (raw_count, raw_rate, occupied_s) in enumerate(
                zip(raw_counts, raw_rates, occupancy)
            ):
                if type(raw_count) is not int or raw_count < 0:
                    raise ValueError(
                        f"Unit {unit_id} spike count {bin_index + 1} must be a non-negative integer."
                    )
                count = int(raw_count)
                if occupied_s == 0.0:
                    if count != 0 or raw_rate is not None:
                        raise ValueError(
                            f"Unit {unit_id} bin {bin_index + 1} has zero occupancy and must contain count 0 / rate null."
                        )
                    rate = math.nan
                else:
                    if isinstance(raw_rate, bool) or not isinstance(raw_rate, (int, float)):
                        raise ValueError(
                            f"Unit {unit_id} firing rate {bin_index + 1} is not numeric."
                        )
                    rate = float(raw_rate)
                    expected_rate = count / occupied_s
                    if (
                        not math.isfinite(rate)
                        or rate < 0.0
                        or not math.isclose(rate, expected_rate, rel_tol=1e-7, abs_tol=1e-9)
                    ):
                        raise ValueError(
                            f"Unit {unit_id} firing rate {bin_index + 1} does not match count / occupancy."
                        )
                counts.append(count)
                rates.append(rate)

            hd_class = raw_unit.get("hd_class")
            if hd_class is not None and (type(hd_class) is not int or hd_class not in {0, 1, 2}):
                raise ValueError(f"Unit {unit_id} hd_class must be 0, 1, 2, or null.")
            curves[unit_id] = tuple(rates)
            spike_counts[unit_id] = tuple(counts)
            hd_classes[unit_id] = hd_class
        return cls(
            path=resolved,
            curves=curves,
            spike_counts=spike_counts,
            occupancy_time_s=tuple(occupancy),
            hd_classes=hd_classes,
            metadata=metadata,
        )

    def rates_for(self, cluster_id: int) -> tuple[float, ...] | None:
        return self.curves.get(int(cluster_id))

    def hd_class_for(self, cluster_id: int) -> int | None:
        return self.hd_classes.get(int(cluster_id))

    def processed_for(
        self,
        cluster_id: int,
        display_bins: int,
        *,
        smoothing: bool,
        sigma: float,
    ) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
        cluster_id = int(cluster_id)
        rates = self.rates_for(cluster_id)
        if rates is None:
            return None
        counts = self.spike_counts.get(cluster_id)
        if counts is not None and self.occupancy_time_s is not None:
            display_bins = normalize_hd_bin_count(display_bins)
            if smoothing:
                return smooth_tuning_counts(
                    counts,
                    self.occupancy_time_s,
                    display_bins,
                    sigma,
                )
            return aggregate_tuning_counts(
                counts,
                self.occupancy_time_s,
                display_bins,
            )
        return processed_tuning_curve(
            rates,
            display_bins,
            smoothing=smoothing,
            sigma=sigma,
        )


def discover_tuning_curve_path(rf_json_path: Path) -> Path | None:
    """Find the first session's tuning curve for the RF document's day/probe."""

    rf_json_path = Path(rf_json_path).expanduser()
    probe_name = probe_name_for_json(rf_json_path)
    if probe_name is None:
        return None
    session_pattern = re.compile(r"^(?P<date>\d{6,8})_(?P<index>\d+)$")
    session_dir: Path | None = None
    session_match: re.Match[str] | None = None
    for candidate in (rf_json_path.parent, *rf_json_path.parents):
        match = session_pattern.fullmatch(candidate.name)
        if match is not None:
            session_dir = candidate
            session_match = match
            break
    if session_dir is None or session_match is None:
        return None

    recording_date = session_match.group("date")
    sessions: list[tuple[int, Path]] = []
    try:
        siblings = session_dir.parent.iterdir()
    except OSError:
        return None
    for sibling in siblings:
        if not sibling.is_dir():
            continue
        match = session_pattern.fullmatch(sibling.name)
        if match is None or match.group("date") != recording_date:
            continue
        sessions.append((int(match.group("index")), sibling))
    for _index, session in sorted(sessions):
        directory = session / "data" / "tuning_curves" / probe_name
        for filename in TUNING_CURVE_FILENAMES:
            resolved = _resolve_existing_file(directory / filename)
            if resolved is not None:
                return resolved
    return None


def aggregate_tuning_curve(
    rates: Sequence[float],
    display_bins: int,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Average available raw HD rates and return display centers and rates."""

    if len(rates) != HD_RAW_BIN_COUNT:
        raise ValueError(f"Expected {HD_RAW_BIN_COUNT} raw HD rates; got {len(rates)}.")
    display_bins = normalize_hd_bin_count(display_bins)
    group_size = HD_RAW_BIN_COUNT // display_bins
    values: list[float] = []
    for start in range(0, HD_RAW_BIN_COUNT, group_size):
        group = tuple(
            float(value)
            for value in rates[start : start + group_size]
            if math.isfinite(float(value))
        )
        values.append(sum(group) / len(group) if group else math.nan)
    bin_width_deg = 360.0 / display_bins
    centers = tuple((index + 0.5) * bin_width_deg for index in range(display_bins))
    return centers, tuple(values)


def aggregate_tuning_counts(
    spike_counts: Sequence[int],
    occupancy_time_s: Sequence[float],
    display_bins: int,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Aggregate counts and occupancy before converting to firing rate."""

    centers, counts, occupancy = aggregate_tuning_observations(
        spike_counts,
        occupancy_time_s,
        display_bins,
    )
    return centers, tuple(
        count / occupied_s if occupied_s > 0.0 else math.nan
        for count, occupied_s in zip(counts, occupancy)
    )


def aggregate_tuning_observations(
    spike_counts: Sequence[float],
    occupancy_time_s: Sequence[float],
    display_bins: int,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    """Return grouped angle centers, spike counts, and occupancy seconds."""

    if len(spike_counts) != HD_RAW_BIN_COUNT:
        raise ValueError(f"Expected {HD_RAW_BIN_COUNT} spike-count bins; got {len(spike_counts)}.")
    if len(occupancy_time_s) != HD_RAW_BIN_COUNT:
        raise ValueError(
            f"Expected {HD_RAW_BIN_COUNT} occupancy-time bins; got {len(occupancy_time_s)}."
    )
    display_bins = normalize_hd_bin_count(display_bins)
    group_size = HD_RAW_BIN_COUNT // display_bins
    counts: list[float] = []
    occupancy: list[float] = []
    for start in range(0, HD_RAW_BIN_COUNT, group_size):
        stop = start + group_size
        counts.append(sum(float(value) for value in spike_counts[start:stop]))
        occupancy.append(
            sum(float(value) for value in occupancy_time_s[start:stop])
        )
    bin_width_deg = 360.0 / display_bins
    centers = tuple((index + 0.5) * bin_width_deg for index in range(display_bins))
    return centers, tuple(counts), tuple(occupancy)


def tuning_smoothing_sigma(sigma: float, display_bins: int) -> float:
    """Keep smoothing at a fixed angular width as the display bin count changes."""

    sigma = float(sigma)
    if not math.isfinite(sigma) or sigma <= 0.0:
        raise ValueError("Tuning-curve smoothing sigma must be positive and finite.")
    display_bins = normalize_hd_bin_count(display_bins)
    return sigma * display_bins / DEFAULT_HD_DISPLAY_BINS


@lru_cache(maxsize=64)
def _circular_gaussian_kernel(sigma: float) -> tuple[tuple[int, float], ...]:
    """Return SciPy-compatible order-zero Gaussian weights and offsets."""

    radius = int(GAUSSIAN_TRUNCATE * sigma + 0.5)
    offsets = range(-radius, radius + 1)
    weights = [math.exp(-0.5 * (offset / sigma) ** 2) for offset in offsets]
    weight_total = sum(weights)
    return tuple(
        (offset, weight / weight_total)
        for offset, weight in zip(range(-radius, radius + 1), weights)
    )


def smooth_tuning_curve(rates: Sequence[float], sigma: float) -> tuple[float, ...]:
    sigma = float(sigma)
    if not math.isfinite(sigma) or sigma <= 0.0:
        raise ValueError("Tuning-curve smoothing sigma must be positive and finite.")
    values = tuple(float(value) for value in rates)
    if not values:
        return ()
    kernel = _circular_gaussian_kernel(sigma)
    count = len(values)
    return tuple(
        sum(
            weight * values[(index + offset) % count]
            for offset, weight in kernel
        )
        for index in range(count)
    )


def smooth_tuning_counts(
    spike_counts: Sequence[int],
    occupancy_time_s: Sequence[float],
    display_bins: int,
    sigma: float,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Smooth raw counts and occupancy, aggregate them, then compute rates."""

    sigma_bins = tuning_smoothing_sigma(sigma, HD_RAW_BIN_COUNT)
    smoothed_counts = smooth_tuning_curve(spike_counts, sigma_bins)
    smoothed_occupancy = smooth_tuning_curve(occupancy_time_s, sigma_bins)
    centers, counts, occupancy = aggregate_tuning_observations(
        smoothed_counts,
        smoothed_occupancy,
        display_bins,
    )
    return centers, tuple(
        count / occupied_s if occupied_s > 1e-12 else math.nan
        for count, occupied_s in zip(counts, occupancy)
    )


def smooth_tuning_rates_missing_aware(
    rates: Sequence[float],
    sigma: float,
) -> tuple[float, ...]:
    """Circularly smooth raw rates without treating missing bins as zero Hz."""

    values = tuple(float(value) for value in rates)
    observed = tuple(1.0 if math.isfinite(value) else 0.0 for value in values)
    numerator = smooth_tuning_curve(
        tuple(value if math.isfinite(value) else 0.0 for value in values),
        sigma,
    )
    denominator = smooth_tuning_curve(observed, sigma)
    return tuple(
        value / weight if weight > 1e-12 else math.nan
        for value, weight in zip(numerator, denominator)
    )


def tuning_rate_peak(rates: Sequence[float]) -> float:
    return max(
        (float(rate) for rate in rates if math.isfinite(float(rate))),
        default=0.0,
    )


def processed_tuning_curve(
    rates: Sequence[float],
    display_bins: int,
    *,
    smoothing: bool,
    sigma: float,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    display_bins = normalize_hd_bin_count(display_bins)
    source_rates = (
        smooth_tuning_rates_missing_aware(
            rates,
            tuning_smoothing_sigma(sigma, HD_RAW_BIN_COUNT),
        )
        if smoothing
        else rates
    )
    return aggregate_tuning_curve(source_rates, display_bins)


def center_tuning_curve_on_zero(
    angles_deg: Sequence[float],
    rates: Sequence[float],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Mirror a circular HD curve onto -180..180 with 0 degrees centered."""

    if len(angles_deg) != len(rates):
        raise ValueError("Tuning-curve angles and rates must have the same length.")
    centered = sorted(
        (
            ((-float(angle) + 180.0) % 360.0) - 180.0,
            float(rate),
        )
        for angle, rate in zip(angles_deg, rates)
    )
    return (
        tuple(angle for angle, _rate in centered),
        tuple(rate for _angle, rate in centered),
    )


def head_direction_unit_vector(angle_deg: float) -> tuple[float, float]:
    """Map HD degrees to Canvas coordinates: 0 north, positive counter-clockwise."""

    radians = math.radians(float(angle_deg))
    return -math.sin(radians), -math.cos(radians)


class _NSSize(ctypes.Structure):
    _fields_ = (("width", ctypes.c_double), ("height", ctypes.c_double))


def allow_macos_fullscreen_resize(window: tk.Misc) -> bool:
    """Remove Tk 8.6's initial-display size cap from native full screen."""

    if sys.platform != "darwin":
        return False
    try:
        window.update_idletasks()
        process = ctypes.CDLL(None)
        process.TkMacOSXDrawable.argtypes = (ctypes.c_void_p,)
        process.TkMacOSXDrawable.restype = ctypes.c_void_p
        native_window = process.TkMacOSXDrawable(window.winfo_id())
        if not native_window:
            return False

        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc.sel_registerName.argtypes = (ctypes.c_char_p,)
        objc.sel_registerName.restype = ctypes.c_void_p
        message_address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        if not message_address:
            return False
        send_size = ctypes.CFUNCTYPE(
            None,
            ctypes.c_void_p,
            ctypes.c_void_p,
            _NSSize,
        )(message_address)
        selector = objc.sel_registerName(b"setMaxFullScreenContentSize:")
        maximum = MACOS_FULLSCREEN_MAX_SIZE
        send_size(native_window, selector, _NSSize(maximum, maximum))
    except (AttributeError, OSError, TypeError, tk.TclError):
        return False
    return True


@dataclass(frozen=True)
class UnitMetrics:
    total: list[list[float]]
    peak: list[list[float]]
    peak_bin: list[list[int | None]]
    delay_ms: list[list[float | None]]
    entropy: list[list[float]]
    bin_totals: list[float]
    max_total: float
    max_peak: float
    max_bin_count: float
    total_spikes: float
    best_y: int
    best_x: int


@dataclass(frozen=True, slots=True)
class ProbeChannel:
    """One physical probe channel loaded from a companion ``channels.csv``."""

    channel_id: int
    x_um: float
    y_um: float
    shank_id: int
    channel_index: int = 0
    raw_channel_index: int = 0


@dataclass(frozen=True, slots=True)
class ProbeUnitPosition:
    """One unit location loaded from the required companion ``positions.csv``."""

    unit_id: int
    x_um: float | None
    y_um: float | None
    unit_index: int = 0


@dataclass(frozen=True)
class SpatialRegion:
    """A physical probe-space selection used to filter RF units."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @classmethod
    def from_corners(
        cls, x0: float, y0: float, x1: float, y1: float
    ) -> SpatialRegion:
        return cls(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    @classmethod
    def centered(
        cls,
        x_um: float,
        y_um: float,
        width_um: float = PROBE_CLICK_WIDTH_UM,
        height_um: float = PROBE_CLICK_HEIGHT_UM,
    ) -> SpatialRegion:
        return cls.from_corners(
            x_um - width_um / 2.0,
            y_um - height_um / 2.0,
            x_um + width_um / 2.0,
            y_um + height_um / 2.0,
        )

    def contains(self, x_um: float, y_um: float) -> bool:
        return self.x_min <= x_um <= self.x_max and self.y_min <= y_um <= self.y_max


@dataclass(frozen=True, slots=True)
class ProbeGeometry:
    """Immutable probe geometry captured for a figure-composer session."""

    probe_name: str
    positions_path: Path
    channels_path: Path | None
    channels: tuple[ProbeChannel, ...]
    units: tuple[ProbeUnitPosition, ...]

    @property
    def units_by_id(self) -> dict[int, ProbeUnitPosition]:
        return {unit.unit_id: unit for unit in self.units}

    @property
    def positioned_units(self) -> tuple[ProbeUnitPosition, ...]:
        return tuple(
            unit
            for unit in self.units
            if unit.x_um is not None and unit.y_um is not None
        )

    def unit_ids_in_region(
        self,
        region: SpatialRegion,
        available_ids: Sequence[int],
    ) -> list[int]:
        positions = self.units_by_id
        return [
            int(unit_id)
            for unit_id in available_ids
            if int(unit_id) in positions
            and positions[int(unit_id)].x_um is not None
            and positions[int(unit_id)].y_um is not None
            and region.contains(
                float(positions[int(unit_id)].x_um),
                float(positions[int(unit_id)].y_um),
            )
        ]


def _finite_csv_float(value: str | None, label: str) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def _probe_unit_coordinates(
    x_value: str | None,
    y_value: str | None,
) -> tuple[float | None, float | None]:
    """Accept only a finite position or SpikeInterface's explicit nan,nan."""

    try:
        x_um = float(x_value)  # type: ignore[arg-type]
        y_um = float(y_value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("unit x_um and y_um must be numeric") from exc
    if math.isnan(x_um) and math.isnan(y_um):
        return None, None
    if not math.isfinite(x_um) or not math.isfinite(y_um):
        raise ValueError(
            "unit x_um and y_um must both be finite or both be nan"
        )
    return x_um, y_um


def _csv_integer(value: str | None, label: str) -> int:
    parsed = _finite_csv_float(value, label)
    if not parsed.is_integer():
        raise ValueError(f"{label} must be an integer")
    return int(parsed)


def _read_probe_csv(
    path: Path,
    required_columns: tuple[str, ...],
) -> tuple[dict[str, int], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        if fieldnames is None:
            raise ValueError(f"{path.name} is missing a header")
        duplicate_columns = sorted(
            {name for name in fieldnames if fieldnames.count(name) > 1}
        )
        if duplicate_columns:
            raise ValueError(
                f"{path.name} contains duplicate columns: {', '.join(duplicate_columns)}"
            )
        missing = [column for column in required_columns if column not in fieldnames]
        if missing:
            raise ValueError(
                f"{path.name} is missing required columns: {', '.join(missing)}"
            )
        rows = list(reader)
    return {name: index for index, name in enumerate(fieldnames)}, rows


def probe_name_for_json(path: Path) -> str | None:
    """Infer ProbeA/ProbeB from RF filenames and containing directories."""

    filename_match = re.search(r"(?:^|[\s_-])([ab])$", path.stem, re.IGNORECASE)
    if filename_match:
        return f"Probe{filename_match.group(1).upper()}"
    for part in (path.name, *(parent.name for parent in path.parents)):
        match = re.search(r"probe[\s_-]*([ab])(?:\b|[_-])", part, re.IGNORECASE)
        if match:
            return f"Probe{match.group(1).upper()}"
    return None


def probe_name_for_rf(path: str | Path) -> str | None:
    """Use the full legacy/current filename vocabulary for probe inference."""

    return probe_name_for_json(Path(path))


def _geometry_path_pairs(
    base: Path, probe_name: str
) -> tuple[tuple[Path, Path | None], ...]:
    layouts = (
        (
            base / "spike_position" / probe_name,
            base / "waveform" / probe_name / "channels.csv",
        ),
        (base / probe_name, base / probe_name / "channels.csv"),
        (base, base / "channels.csv"),
    )
    return tuple(
        (directory / filename, channels)
        for directory, channels in layouts
        for filename in PROBE_POSITION_FILENAMES
    )


def _probe_geometry_search_roots(
    json_path: Path,
    data_root: Path | None,
) -> list[Path]:
    roots: list[Path] = []
    if data_root is not None:
        roots.append(data_root.expanduser())
    elif configured := os.environ.get("RF_MAPPING_PROBE_DATA_ROOT"):
        roots.append(Path(configured).expanduser())

    source = json_path.expanduser()
    parents = tuple(source.parents)
    session = next(
        (parent for parent in parents if _RECORDING_SESSION_RE.fullmatch(parent.name)),
        None,
    )
    if session is not None:
        boundary = next(
            (
                parent
                for parent in parents
                if parent.name == "data" and parent.parent == session
            ),
            session,
        )
        for parent in parents:
            roots.append(parent)
            if parent == boundary:
                break
    elif data_boundary := next(
        (parent for parent in parents if parent.name == "data"),
        None,
    ):
        for parent in parents:
            roots.append(parent)
            if parent == data_boundary:
                break
    else:
        # Compact fixtures and manual exports may keep geometry one or two
        # directory levels above the JSON, but never require a walk to root.
        roots.extend(parents[:2])

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def discover_probe_geometry_paths(
    rf_path: str | Path,
    *,
    data_root: Path | None = None,
) -> tuple[str, Path, Path | None] | None:
    """Discover session probe geometry beside an RF mapping JSON.

    The active pipeline stores unit positions below ``data/spike_position``
    and physical channels below ``data/waveform``. Both ``positions.probe``
    and the legacy ``positions.csv`` name are accepted in every supported
    layout; ``channels.csv`` remains the optional physical-channel companion.
    """

    source = Path(rf_path).expanduser()
    probe_name = probe_name_for_json(source)
    if probe_name is None:
        return None
    first_malformed: tuple[str, Path, Path | None] | None = None
    for base in _probe_geometry_search_roots(source, data_root):
        for positions_path, channels_path in _geometry_path_pairs(base, probe_name):
            resolved_positions = _resolve_existing_file(positions_path)
            if resolved_positions is None:
                continue
            candidate = (
                probe_name,
                resolved_positions,
                _resolve_existing_file(channels_path) if channels_path else None,
            )
            try:
                _read_probe_csv(
                    resolved_positions,
                    ("unit_index", "unit_id", "x_um", "y_um"),
                )
            except (OSError, ValueError):
                # Keep the first malformed candidate so RFMappingData can
                # surface its precise validation error if no valid fallback
                # exists, while still allowing a later trusted root to win.
                if first_malformed is None:
                    first_malformed = candidate
                continue
            return candidate
    return first_malformed


def load_probe_geometry(
    probe_name_or_positions: str | Path,
    positions_path: Path | None = None,
    channels_path: Path | None = None,
    *,
    probe_name: str | None = None,
    infer_sibling_channels: bool = True,
) -> ProbeGeometry:
    """Load validated unit positions and optional channel sites from CSV."""

    if probe_name is not None:
        # Legacy API: load_probe_geometry(positions, channels, probe_name=...).
        if channels_path is not None:
            raise TypeError("channels_path was provided twice")
        channels_path = positions_path
        positions_path = Path(probe_name_or_positions)
        normalized_probe_name = probe_name
    else:
        # Canonical API: load_probe_geometry(probe_name, positions, channels).
        if positions_path is None:
            # Compact legacy API without an explicit probe name.
            positions_path = Path(probe_name_or_positions)
            normalized_probe_name = "Probe"
        else:
            normalized_probe_name = str(probe_name_or_positions)

    positions_resolved = _resolve_existing_file(positions_path)
    if positions_resolved is None:
        raise ValueError(f"CSV file not found: {positions_path}")
    if channels_path is None and infer_sibling_channels:
        sibling = positions_resolved.with_name("channels.csv")
        channels_path = sibling if sibling.is_file() else None

    _fields, position_rows = _read_probe_csv(
        positions_resolved,
        ("unit_index", "unit_id", "x_um", "y_um"),
    )
    units: list[ProbeUnitPosition] = []
    seen_unit_ids: set[int] = set()
    for row_number, row in enumerate(position_rows, start=2):
        try:
            unit_index = _csv_integer(row.get("unit_index"), "unit_index")
            unit_id = _csv_integer(row.get("unit_id"), "unit_id")
            x_um, y_um = _probe_unit_coordinates(
                row.get("x_um"), row.get("y_um")
            )
        except ValueError as exc:
            raise ValueError(
                f"Invalid positions.csv value on row {row_number}: {exc}"
            ) from exc
        if unit_id in seen_unit_ids:
            raise ValueError(f"Duplicate unit_id {unit_id} in positions.csv")
        seen_unit_ids.add(unit_id)
        units.append(ProbeUnitPosition(unit_id, x_um, y_um, unit_index))

    channels: list[ProbeChannel] = []
    validated_channels_path = (
        _resolve_existing_file(channels_path) if channels_path is not None else None
    )
    if validated_channels_path is not None:
        try:
            _fields, channel_rows = _read_probe_csv(
                validated_channels_path,
                (
                    "channel_index",
                    "channel_id",
                    "raw_channel_index",
                    "x_um",
                    "y_um",
                    "shank_id",
                ),
            )
            for row_number, row in enumerate(channel_rows, start=2):
                try:
                    channel_index = _csv_integer(row.get("channel_index"), "channel_index")
                    raw_channel_index = _csv_integer(row.get("raw_channel_index"), "raw_channel_index")
                    channel_id = _csv_integer(row.get("channel_id"), "channel_id")
                    x_um = _finite_csv_float(row.get("x_um"), "channel x_um")
                    y_um = _finite_csv_float(row.get("y_um"), "channel y_um")
                    shank_id = _csv_integer(row.get("shank_id"), "shank_id")
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid channels.csv value on row {row_number}: {exc}"
                    ) from exc
                channels.append(
                    ProbeChannel(
                        channel_id,
                        x_um,
                        y_um,
                        shank_id,
                        channel_index,
                        raw_channel_index,
                    )
                )
        except (OSError, ValueError):
            # Unit positions are sufficient for a useful probe plot.  An
            # optional stale/malformed channel file must not suppress them.
            channels = []
            validated_channels_path = None

    return ProbeGeometry(
        probe_name=normalized_probe_name,
        positions_path=positions_resolved,
        channels_path=validated_channels_path,
        channels=tuple(channels),
        units=tuple(units),
    )


def discover_probe_geometry(
    json_path: Path,
    *,
    data_root: Path | None = None,
) -> ProbeGeometry | None:
    """Load probe geometry using the bounded current-session policy.

    ``data_root`` remains an explicit opt-in for compact fixtures and manually
    curated layouts; normal discovery never walks outside the RF data scope.
    """

    if data_root is not None:
        probe_name = probe_name_for_rf(json_path)
        if probe_name is None:
            return None
        for positions, channels in _geometry_path_pairs(data_root, probe_name):
            if positions.is_file():
                return load_probe_geometry(
                    probe_name,
                    positions.resolve(),
                    channels.resolve() if channels.is_file() else None,
                )
        return None

    probe_name = probe_name_for_json(json_path)
    if probe_name is None:
        return None
    positions_only: ProbeGeometry | None = None
    for root in _probe_geometry_search_roots(json_path, data_root):
        for positions, channels in _geometry_path_pairs(root, probe_name):
            positions_resolved = _resolve_existing_file(positions)
            if positions_resolved is None:
                continue
            channels_resolved = (
                _resolve_existing_file(channels) if channels is not None else None
            )
            try:
                geometry = load_probe_geometry(
                    probe_name,
                    positions_resolved,
                    channels_resolved,
                )
            except (OSError, ValueError):
                if channels_resolved is None:
                    continue
                try:
                    geometry = load_probe_geometry(
                        probe_name,
                        positions_resolved,
                        None,
                        infer_sibling_channels=False,
                    )
                except (OSError, ValueError):
                    continue
            if geometry.channels:
                return geometry
            if positions_only is None:
                positions_only = geometry
    return positions_only


@dataclass(frozen=True)
class SpatialGroupObservations:
    count: float
    occupancy_time_s: float
    source_pixel_count: int


@dataclass(frozen=True)
class SpatialGroupTemporalMetrics:
    mean_total_count: float
    peak_group_index: int | None
    delay_ms: float | None
    entropy: float


@dataclass(frozen=True)
class ViewerSyncState:
    """Persistent viewer controls shared by paired windows.

    The selected unit is stored by cluster ID rather than by its per-file
    array index so windows with different unit lists can still be paired.
    Time selections are stored in physical milliseconds so files with
    different time axes or display-group resolutions remain synchronized. A
    selected spatial cell is represented by its source-index midpoint for the
    same reason.
    """

    unit_id: int
    value_mode: str
    timeline_bin_center_ms: float
    timeline_selection_start_ms: float
    timeline_selection_end_ms: float
    timeline_anchor_center_ms: float | None
    rf_start_ms: float
    rf_end_ms: float
    time_resolution_ms: float
    x_bins: int
    y_bins: int
    smooth_radius: int
    flip_y: bool
    palette: str
    polar_radius: str
    polar_layout: bool
    rgb_mode: bool
    selected_cell_y_midpoint: float | None
    selected_cell_x_midpoint: float | None
    timeline_scroll_fraction: float
    selected_tab: str
    tuning_plot_mode: str = "Auto"
    tuning_display_bins: int = DEFAULT_HD_DISPLAY_BINS
    tuning_smoothing: bool = True
    tuning_smooth_sigma: float = DEFAULT_HD_SMOOTH_SIGMA
    tuning_compare_scale: bool = False
    show_tuning_curve: bool = True
    show_waveform: bool = True
    show_probe_layout: bool = True

    def changed_fields(self, baseline: ViewerSyncState) -> frozenset[str]:
        fields: set[str] = set()
        if self.unit_id != baseline.unit_id:
            fields.add("unit")
        if self.value_mode != baseline.value_mode:
            fields.add("value_mode")
        if self.timeline_bin_center_ms != baseline.timeline_bin_center_ms:
            fields.add("active_time")
        if (
            self.timeline_selection_start_ms != baseline.timeline_selection_start_ms
            or self.timeline_selection_end_ms != baseline.timeline_selection_end_ms
            or self.timeline_anchor_center_ms != baseline.timeline_anchor_center_ms
        ):
            fields.add("timeline_selection")
        if self.rf_start_ms != baseline.rf_start_ms or self.rf_end_ms != baseline.rf_end_ms:
            fields.add("rf_range")
        if self.time_resolution_ms != baseline.time_resolution_ms:
            fields.add("time_resolution")
        if self.x_bins != baseline.x_bins:
            fields.add("x_bins")
        if self.y_bins != baseline.y_bins:
            fields.add("y_bins")
        if self.smooth_radius != baseline.smooth_radius:
            fields.add("smoothing")
        if self.flip_y != baseline.flip_y:
            fields.add("flip_y")
        if self.palette != baseline.palette:
            fields.add("palette")
        if self.polar_radius != baseline.polar_radius:
            fields.add("polar_radius")
        if self.polar_layout != baseline.polar_layout:
            fields.add("spatial_format")
        if self.rgb_mode != baseline.rgb_mode:
            fields.add("delay_rgb")
        if (
            self.selected_cell_y_midpoint != baseline.selected_cell_y_midpoint
            or self.selected_cell_x_midpoint != baseline.selected_cell_x_midpoint
        ):
            fields.add("selected_cell")
        if abs(self.timeline_scroll_fraction - baseline.timeline_scroll_fraction) > 1e-6:
            fields.add("timeline_scroll")
        if self.selected_tab != baseline.selected_tab:
            fields.add("selected_tab")
        if (
            self.tuning_plot_mode != baseline.tuning_plot_mode
            or self.tuning_display_bins != baseline.tuning_display_bins
            or self.tuning_smoothing != baseline.tuning_smoothing
            or self.tuning_smooth_sigma != baseline.tuning_smooth_sigma
            or self.tuning_compare_scale != baseline.tuning_compare_scale
        ):
            fields.add("tuning_display")
        if (
            self.show_tuning_curve != baseline.show_tuning_curve
            or self.show_waveform != baseline.show_waveform
            or self.show_probe_layout != baseline.show_probe_layout
        ):
            fields.add("optional_views")
        return frozenset(fields)

    def merging(
        self,
        incoming: ViewerSyncState,
        fields: frozenset[str],
    ) -> ViewerSyncState:
        updates: dict[str, object] = {}
        if "unit" in fields:
            updates["unit_id"] = incoming.unit_id
        if "value_mode" in fields:
            updates["value_mode"] = incoming.value_mode
        if "active_time" in fields:
            updates["timeline_bin_center_ms"] = incoming.timeline_bin_center_ms
        if "timeline_selection" in fields:
            updates.update(
                timeline_selection_start_ms=incoming.timeline_selection_start_ms,
                timeline_selection_end_ms=incoming.timeline_selection_end_ms,
                timeline_anchor_center_ms=incoming.timeline_anchor_center_ms,
            )
        if "rf_range" in fields:
            updates.update(rf_start_ms=incoming.rf_start_ms, rf_end_ms=incoming.rf_end_ms)
        if "time_resolution" in fields:
            updates["time_resolution_ms"] = incoming.time_resolution_ms
        if "x_bins" in fields:
            updates["x_bins"] = incoming.x_bins
        if "y_bins" in fields:
            updates["y_bins"] = incoming.y_bins
        if "smoothing" in fields:
            updates["smooth_radius"] = incoming.smooth_radius
        if "flip_y" in fields:
            updates["flip_y"] = incoming.flip_y
        if "palette" in fields:
            updates["palette"] = incoming.palette
        if "polar_radius" in fields:
            updates["polar_radius"] = incoming.polar_radius
        if "spatial_format" in fields:
            updates["polar_layout"] = incoming.polar_layout
        if "delay_rgb" in fields:
            updates["rgb_mode"] = incoming.rgb_mode
        if "selected_cell" in fields:
            updates.update(
                selected_cell_y_midpoint=incoming.selected_cell_y_midpoint,
                selected_cell_x_midpoint=incoming.selected_cell_x_midpoint,
            )
        if "timeline_scroll" in fields:
            updates["timeline_scroll_fraction"] = incoming.timeline_scroll_fraction
        if "selected_tab" in fields:
            updates["selected_tab"] = incoming.selected_tab
        if "tuning_display" in fields:
            updates.update(
                tuning_plot_mode=incoming.tuning_plot_mode,
                tuning_display_bins=incoming.tuning_display_bins,
                tuning_smoothing=incoming.tuning_smoothing,
                tuning_smooth_sigma=incoming.tuning_smooth_sigma,
                tuning_compare_scale=incoming.tuning_compare_scale,
            )
        if "optional_views" in fields:
            updates.update(
                show_tuning_curve=incoming.show_tuning_curve,
                show_waveform=incoming.show_waveform,
                show_probe_layout=incoming.show_probe_layout,
            )
        return replace(self, **updates)


class RFMappingData:
    """GUI adapter around the implementation-local RF JSON model."""

    def __init__(self, path: Path):
        source_identity = FrozenFileIdentity.capture(path)
        self.path = source_identity.path
        self.rf_maps: RFMapList = load_rf_maps(self.path)
        source_identity.verify_path()
        self.source_identity = source_identity
        first = self.rf_maps[0]
        self.n_units = len(self.rf_maps)
        self.n_y = first.n_y
        self.n_x = first.n_x
        self.n_bins = first.n_time_bins
        self.size = (self.n_units, self.n_y, self.n_x, self.n_bins)
        self.counts = [rf_map.spike_counts for rf_map in self.rf_maps]
        self.unit_pool = list(self.rf_maps.unit_ids)
        self.x_positions = first.x_positions.tolist()
        self.y_positions = first.y_positions.tolist()
        self.time_bin_edges = first.time_bin_edges_s.tolist()
        self.occupancy_time_s = first.occupancy_time_s.tolist()
        self._metrics_cache: dict[int, UnitMetrics] = {}
        self._best_cell_cache: dict[int, tuple[int, int]] = {}
        self._zero_spike_bin_count_cache: dict[tuple[int, int, int], int] = {}
        self._hd_tuning_lock = threading.Lock()
        self._hd_tuning_checked = False
        self._hd_tuning: HDTuningData | TuningCurveData | None = None
        self._hd_tuning_error: str | None = None
        self._hd_tuning_identity: FrozenFileIdentity | None = None
        self._probe_geometry_lock = threading.Lock()
        self._probe_geometry_checked = False
        self._probe_geometry: ProbeGeometry | None = None
        self._probe_geometry_error: str | None = None
        self._probe_file_identities: tuple[FrozenFileIdentity, ...] = ()
        self._waveform_lock = threading.Lock()
        self._waveform_checked = False
        self._waveform_store: WaveformArtifactStore | None = None
        self._waveform_error: str | None = None
        self._waveform_file_identities: tuple[FrozenFileIdentity, ...] = ()

    def rf_map(self, unit_idx: int) -> RFMap:
        """Return one unit by its original JSON array index."""

        return self.rf_maps.by_index(unit_idx)

    def rf_map_by_unit_id(self, unit_id: int) -> RFMap:
        """Return a per-unit object by its recorded cluster/unit ID."""

        return self.rf_maps.by_unit_id(unit_id)

    @property
    def spatial_bin_count(self) -> int:
        return self.n_y * self.n_x

    def zero_spike_spatial_bin_count(
        self,
        unit_idx: int,
        start: int,
        end: int,
    ) -> int:
        """Return native RF bins with zero spikes in an inclusive time range."""

        requested_start, requested_end = min(start, end), max(start, end)
        start = max(0, min(self.n_bins - 1, requested_start))
        end = max(0, min(self.n_bins - 1, requested_end))
        key = (int(unit_idx), start, end)
        cached = self._zero_spike_bin_count_cache.get(key)
        if cached is not None:
            return cached
        result = self.rf_map(unit_idx).zero_spike_spatial_bin_count(
            self.time_bin_edges[start],
            self.time_bin_edges[end + 1],
        )
        self._zero_spike_bin_count_cache[key] = result
        return result

    def hd_tuning(self) -> HDTuningData | TuningCurveData | None:
        """Lazily discover and validate the companion HD tuning JSON."""

        if self._hd_tuning_checked:
            return self._hd_tuning
        # Preview rendering runs on Tk's main thread while final export runs on
        # a worker.  Publish the checked flag only after discovery/loading is
        # complete so another caller can never observe a false "missing" state.
        with self._hd_tuning_lock:
            if self._hd_tuning_checked:
                return self._hd_tuning
            tuning: HDTuningData | TuningCurveData | None = None
            error: str | None = None
            tuning_path = discover_hd_tuning_path(self.path)
            identity: FrozenFileIdentity | None = None
            if tuning_path is not None:
                try:
                    identity = FrozenFileIdentity.capture(tuning_path)
                    try:
                        tuning = load_hd_tuning(identity.path)
                    except (KeyError, TypeError, ValueError):
                        # 1.8 numeric-key and nested schema-v2 documents remain
                        # valid live/export companions in the full viewer.
                        tuning = TuningCurveData.load(identity.path)
                    identity.verify_path()
                except Exception as exc:
                    error = str(exc)
            self._hd_tuning = tuning
            self._hd_tuning_identity = identity
            self._hd_tuning_error = error
            self._hd_tuning_checked = True
        return self._hd_tuning

    def attach_hd_tuning(self, path: Path) -> TuningCurveData:
        """Atomically attach one manually selected HD document."""

        identity = FrozenFileIdentity.capture(path)
        tuning = TuningCurveData.load(identity.path)
        identity.verify_path()
        with self._hd_tuning_lock:
            self._hd_tuning = tuning
            self._hd_tuning_identity = identity
            self._hd_tuning_error = None
            self._hd_tuning_checked = True
        return tuning

    @property
    def hd_tuning_error(self) -> str | None:
        self.hd_tuning()
        return self._hd_tuning_error

    def probe_geometry(self) -> ProbeGeometry | None:
        """Lazily discover and validate companion probe geometry CSV files."""

        if self._probe_geometry_checked:
            return self._probe_geometry
        with self._probe_geometry_lock:
            if self._probe_geometry_checked:
                return self._probe_geometry
            geometry: ProbeGeometry | None = None
            error: str | None = None
            discovered = discover_probe_geometry_paths(self.path)
            identities: tuple[FrozenFileIdentity, ...] = ()
            if discovered is not None:
                probe_name, positions_path, channels_path = discovered
                try:
                    positions_identity = FrozenFileIdentity.capture(positions_path)
                    channels_identity = (
                        FrozenFileIdentity.capture(channels_path)
                        if channels_path is not None
                        else None
                    )
                    identities = tuple(
                        identity
                        for identity in (positions_identity, channels_identity)
                        if identity is not None
                    )
                    geometry = load_probe_geometry(
                        probe_name,
                        positions_identity.path,
                        channels_identity.path if channels_identity is not None else None,
                    )
                    for identity in identities:
                        identity.verify_path()
                    rf_unit_ids = set(self.unit_pool)
                    matching_units = tuple(
                        unit for unit in geometry.units if unit.unit_id in rf_unit_ids
                    )
                    if not matching_units:
                        raise ValueError(
                            "positions.csv contains no unit IDs from this RF "
                            "dataset's unitPool"
                        )
                    # A positions.csv can contain a broader sorting result than
                    # the selected RF export.  Never draw those unrelated units
                    # as though they belonged to this RF payload.
                    geometry = replace(geometry, units=matching_units)
                except Exception as exc:
                    geometry = None
                    error = str(exc)
            self._probe_geometry = geometry
            self._probe_geometry_error = error
            self._probe_file_identities = identities
            # Publish only after the immutable geometry/error state is ready;
            # previews and final exports may request it from different threads.
            self._probe_geometry_checked = True
        return self._probe_geometry

    def attach_probe_geometry(
        self,
        positions_path: Path,
        channels_path: Path | None = None,
        *,
        probe_name: str | None = None,
    ) -> ProbeGeometry:
        """Atomically attach validated probe inputs and freeze provenance."""

        positions_identity = FrozenFileIdentity.capture(positions_path)
        channels_identity = (
            FrozenFileIdentity.capture(channels_path)
            if channels_path is not None
            else None
        )
        identities = tuple(
            identity
            for identity in (positions_identity, channels_identity)
            if identity is not None
        )
        geometry = load_probe_geometry(
            probe_name
            or probe_name_for_json(self.path)
            or positions_identity.path.parent.name,
            positions_identity.path,
            channels_identity.path if channels_identity is not None else None,
        )
        for identity in identities:
            identity.verify_path()
        rf_unit_ids = set(self.unit_pool)
        matching_units = tuple(
            unit for unit in geometry.units if unit.unit_id in rf_unit_ids
        )
        if not matching_units:
            raise ValueError(
                "positions.csv contains no unit IDs from this RF dataset's unitPool"
            )
        geometry = replace(geometry, units=matching_units)
        with self._probe_geometry_lock:
            self._probe_geometry = geometry
            self._probe_geometry_error = None
            self._probe_file_identities = identities
            self._probe_geometry_checked = True
        return geometry

    @property
    def probe_geometry_error(self) -> str | None:
        self.probe_geometry()
        return self._probe_geometry_error

    def waveform_store(self) -> WaveformArtifactStore | None:
        """Lazily discover the read-only schema-v4 waveform artifact."""

        if self._waveform_checked:
            return self._waveform_store
        with self._waveform_lock:
            if self._waveform_checked:
                return self._waveform_store
            store: WaveformArtifactStore | None = None
            error: str | None = None
            try:
                artifact = discover_waveform_artifact(self.path)
                if artifact is not None:
                    store = WaveformArtifactStore.open(artifact)
            except Exception as exc:
                error = str(exc)
            self._waveform_store = store
            self._waveform_error = error
            self._waveform_checked = True
        return self._waveform_store

    @property
    def waveform_error(self) -> str | None:
        self.waveform_store()
        return self._waveform_error

    def waveform_payload(
        self,
        unit_id: int,
        channel_mode: str,
    ) -> WaveformPayload:
        store = self.waveform_store()
        if store is None:
            if self._waveform_error:
                raise ValueError(
                    f"Waveform artifact could not be loaded: {self._waveform_error}"
                )
            raise ValueError(
                "No companion data/waveform/Probe*/manifest.json was found "
                "for this RF dataset."
            )
        try:
            return store.payload_for(
                int(unit_id),
                mode=channel_mode,
                local_channel_count=5,
                baseline_end_ms=-0.25,
            )
        except KeyError as exc:
            raise ValueError(
                f"Waveform is unavailable for RF unit {int(unit_id)}."
            ) from exc

    def waveform_plot_payload(
        self,
        unit_id: int,
        channel_mode: str,
    ) -> dict[str, object]:
        """Return one shared immutable-data contract for Tk and Pillow."""

        payload = self.waveform_payload(unit_id, channel_mode)
        summary = payload.summary
        return {
            "matrix": payload.matrix,
            "times_ms": payload.times_ms,
            "time_edges_ms": payload.time_edges_ms,
            "channel_labels": tuple(
                f"ch {channel.channel_id} · x {channel.x_um:g} y {channel.y_um:g} · s{channel.shank_id}"
                for channel in payload.channels
            ),
            "best_channel_row": payload.best_channel_row,
            "best_channel_index": payload.best_channel_index,
            "amplitude_limit_uv": payload.amplitude_limit_uv,
            "unit_id": int(summary.unit_id),
            "max_ptp_uv": float(summary.max_ptp_uv),
            "channel_mode": payload.mode,
        }

    def capture_waveform_inputs(
        self,
        unit_ids: Iterable[int],
    ) -> tuple[FrozenFileIdentity, ...]:
        """Freeze metadata and selected templates for export provenance."""

        store = self.waveform_store()
        if store is None:
            self._waveform_file_identities = ()
            return ()
        paths: dict[Path, None] = {}
        for unit_id in unit_ids:
            try:
                source_paths = store.source_paths_for_unit(int(unit_id))
            except KeyError:
                continue
            for path in source_paths:
                paths[Path(path).expanduser().resolve()] = None
        identities = tuple(FrozenFileIdentity.capture(path) for path in paths)
        self._waveform_file_identities = identities
        return identities

    def display_y_indices(self, flip_y: bool = True) -> list[int]:
        if flip_y:
            return list(range(self.n_y - 1, -1, -1))
        return list(range(self.n_y))

    def cluster_id(self, unit_idx: int) -> int:
        return self.rf_map(unit_idx).unit_id

    def bin_label(self, bin_idx: int) -> str:
        start = self.time_bin_edges[bin_idx] * 1000.0
        end = self.time_bin_edges[bin_idx + 1] * 1000.0
        return f"{bin_idx}: {start:.0f}-{end:.0f} ms"

    def bin_center_ms(self, bin_idx: int) -> float:
        return (self.time_bin_edges[bin_idx] + self.time_bin_edges[bin_idx + 1]) * 500.0

    def infer_total_deg(self) -> float:
        if self.n_x <= 1:
            return 360.0
        diffs = [self.x_positions[i + 1] - self.x_positions[i] for i in range(self.n_x - 1)]
        step = sum(diffs) / len(diffs)
        if all(abs(d - step) < 1e-6 for d in diffs) and abs(step) > 1e-9:
            return abs(step) * self.n_x
        return abs(self.x_positions[-1] - self.x_positions[0])

    def metrics(self, unit_idx: int) -> UnitMetrics:
        cached = self._metrics_cache.get(unit_idx)
        if cached is not None:
            return cached

        unit = self.counts[unit_idx]
        total: list[list[float]] = []
        peak: list[list[float]] = []
        peak_bin: list[list[int | None]] = []
        delay_ms: list[list[float | None]] = []
        entropy: list[list[float]] = []
        bin_totals = [0.0 for _ in range(self.n_bins)]

        max_total = 0.0
        max_peak = 0.0
        max_bin_count = 0.0
        total_spikes = 0.0
        best_y = 0
        best_x = 0
        best_rate = -1.0

        for y_idx in range(self.n_y):
            total_row: list[float] = []
            peak_row: list[float] = []
            peak_bin_row: list[int | None] = []
            delay_row: list[float | None] = []
            entropy_row: list[float] = []
            for x_idx in range(self.n_x):
                hist = [float(v) for v in unit[y_idx][x_idx]]
                cell_total = sum(hist)
                cell_peak = max(hist) if hist else 0.0
                if cell_total > 0:
                    best_bin = max(range(self.n_bins), key=lambda i: hist[i])
                    delay = self.bin_center_ms(best_bin)
                    ent = 0.0
                    for count in hist:
                        if count > 0:
                            p = count / cell_total
                            ent -= p * math.log(p)
                    ent = ent / math.log(self.n_bins) if self.n_bins > 1 else 0.0
                else:
                    best_bin = None
                    delay = None
                    ent = 0.0

                for bin_idx, count in enumerate(hist):
                    bin_totals[bin_idx] += count
                    if count > max_bin_count:
                        max_bin_count = count

                if cell_total > max_total:
                    max_total = cell_total
                occupancy = self.occupancy_time_s[y_idx][x_idx]
                cell_rate = cell_total / occupancy if occupancy > 0.0 else -1.0
                if cell_rate > best_rate:
                    best_rate = cell_rate
                    best_y = y_idx
                    best_x = x_idx
                if cell_peak > max_peak:
                    max_peak = cell_peak

                total_spikes += cell_total
                total_row.append(cell_total)
                peak_row.append(cell_peak)
                peak_bin_row.append(best_bin)
                delay_row.append(delay)
                entropy_row.append(ent)

            total.append(total_row)
            peak.append(peak_row)
            peak_bin.append(peak_bin_row)
            delay_ms.append(delay_row)
            entropy.append(entropy_row)

        metrics = UnitMetrics(
            total=total,
            peak=peak,
            peak_bin=peak_bin,
            delay_ms=delay_ms,
            entropy=entropy,
            bin_totals=bin_totals,
            max_total=max_total,
            max_peak=max_peak,
            max_bin_count=max_bin_count,
            total_spikes=total_spikes,
            best_y=best_y,
            best_x=best_x,
        )
        self._metrics_cache[unit_idx] = metrics
        self._best_cell_cache[unit_idx] = (best_y, best_x)
        return metrics

    def best_cell(self, unit_idx: int) -> tuple[int, int]:
        """Return the strongest occupancy-normalized cell without full metrics.

        RF navigation only needs a sensible default cell.  Keeping this path
        separate avoids calculating every cell's peak, delay, and entropy the
        first time each unit is visited, while avoiding a bias toward cells
        with longer stimulus occupancy.
        """

        cached = self._best_cell_cache.get(unit_idx)
        if cached is not None:
            return cached
        unit = self.counts[unit_idx]
        best_y = 0
        best_x = 0
        best_rate = -1.0
        for y_idx, row in enumerate(unit):
            for x_idx, histogram in enumerate(row):
                total = sum(float(value) for value in histogram)
                occupancy = self.occupancy_time_s[y_idx][x_idx]
                rate = total / occupancy if occupancy > 0.0 else -1.0
                if rate > best_rate:
                    best_rate = rate
                    best_y = y_idx
                    best_x = x_idx
        result = (best_y, best_x)
        self._best_cell_cache[unit_idx] = result
        return result

    def aggregate_matrix(
        self,
        unit_idx: int,
        mode: str,
        bin_idx: int,
        range_start: int,
        range_end: int,
    ) -> list[list[float]]:
        if mode == "Total":
            metrics = self.metrics(unit_idx)
            return clone_matrix(metrics.total)
        if mode == "Peak":
            metrics = self.metrics(unit_idx)
            return clone_matrix(metrics.peak)

        unit = self.counts[unit_idx]
        if mode == "Bin":
            return [
                [float(unit[y_idx][x_idx][bin_idx]) for x_idx in range(self.n_x)]
                for y_idx in range(self.n_y)
            ]
        if mode == "Range sum":
            start = max(0, min(range_start, range_end))
            end = min(self.n_bins - 1, max(range_start, range_end))
            summed = self.rf_map(unit_idx).sum(
                self.time_bin_edges[start],
                self.time_bin_edges[end + 1],
            )
            return summed.spike_counts[..., 0].astype(float).tolist()
        raise ValueError(f"Unknown RF mode: {mode}")

    def supports_value_mode(self, value_mode: str) -> bool:
        return value_mode in VALUE_MODES

    def time_span_seconds(self, start: int, end: int) -> float:
        requested_start, requested_end = min(start, end), max(start, end)
        start = max(0, min(self.n_bins - 1, requested_start))
        end = max(0, min(self.n_bins - 1, requested_end))
        return self.time_bin_edges[end + 1] - self.time_bin_edges[start]

    def response_value(
        self,
        unit_idx: int,
        y_idx: int,
        x_idx: int,
        start: int,
        end: int,
        value_mode: str,
    ) -> float | None:
        requested_start, requested_end = min(start, end), max(start, end)
        start = max(0, min(self.n_bins - 1, requested_start))
        end = max(0, min(self.n_bins - 1, requested_end))
        count = float(sum(self.counts[unit_idx][y_idx][x_idx][start : end + 1]))
        occupancy_time_s = self.occupancy_time_s[y_idx][x_idx]
        if occupancy_time_s <= 0:
            return None
        if value_mode == VALUE_MODE_COUNT:
            return count
        if value_mode not in VALUE_MODES:
            raise ValueError(f"Unknown value mode: {value_mode}")
        if value_mode == VALUE_MODE_RATE:
            return count / occupancy_time_s
        raise ValueError(f"Unknown value mode: {value_mode}")

    def response_matrix(
        self,
        unit_idx: int,
        start: int,
        end: int,
        value_mode: str,
    ) -> list[list[float | None]]:
        requested_start, requested_end = min(start, end), max(start, end)
        start = max(0, min(self.n_bins - 1, requested_start))
        end = max(0, min(self.n_bins - 1, requested_end))
        count_matrix = self.aggregate_matrix(unit_idx, "Range sum", 0, start, end)
        if value_mode == VALUE_MODE_COUNT:
            return [
                [
                    None
                    if self.occupancy_time_s[y_idx][x_idx] <= 0
                    else count_matrix[y_idx][x_idx]
                    for x_idx in range(self.n_x)
                ]
                for y_idx in range(self.n_y)
            ]
        if value_mode not in VALUE_MODES:
            raise ValueError(f"Unknown value mode: {value_mode}")
        return [
            [
                None
                if self.occupancy_time_s[y_idx][x_idx] <= 0
                else count_matrix[y_idx][x_idx] / self.occupancy_time_s[y_idx][x_idx]
                for x_idx in range(self.n_x)
            ]
            for y_idx in range(self.n_y)
        ]

    def spatial_group_observations(
        self,
        unit_idx: int,
        y_group: AxisGroup,
        x_group: AxisGroup,
        start: int,
        end: int,
    ) -> SpatialGroupObservations:
        """Pool raw observations for one displayed spatial cell.

        ``occupancyTimeSec`` is exposure metadata for each source position. A
        displayed cell that combines positions therefore has one pooled
        numerator and one pooled exposure; averaging already-normalized source
        rates would give briefly occupied positions too much weight.
        """

        y_start = max(0, min(self.n_y - 1, min(y_group)))
        y_end = max(0, min(self.n_y - 1, max(y_group)))
        x_start = max(0, min(self.n_x - 1, min(x_group)))
        x_end = max(0, min(self.n_x - 1, max(x_group)))
        requested_start, requested_end = min(start, end), max(start, end)
        start = max(0, min(self.n_bins - 1, requested_start))
        end = max(0, min(self.n_bins - 1, requested_end))
        source_indices = [
            (y_idx, x_idx)
            for y_idx in range(y_start, y_end + 1)
            for x_idx in range(x_start, x_end + 1)
            if self.occupancy_time_s[y_idx][x_idx] > 0
        ]
        counts = [
            float(sum(self.counts[unit_idx][y_idx][x_idx][start : end + 1]))
            for y_idx, x_idx in source_indices
        ]
        occupancy_time_s = sum(
            float(self.occupancy_time_s[y_idx][x_idx])
            for y_idx, x_idx in source_indices
        )
        return SpatialGroupObservations(
            count=sum(counts),
            occupancy_time_s=occupancy_time_s,
            source_pixel_count=len(counts),
        )

    def spatial_group_response_value(
        self,
        unit_idx: int,
        y_group: AxisGroup,
        x_group: AxisGroup,
        start: int,
        end: int,
        value_mode: str,
    ) -> float | None:
        observations = self.spatial_group_observations(
            unit_idx,
            y_group,
            x_group,
            start,
            end,
        )
        if value_mode == VALUE_MODE_COUNT:
            if observations.source_pixel_count <= 0:
                return None
            return observations.count / observations.source_pixel_count
        if value_mode not in VALUE_MODES:
            raise ValueError(f"Unknown value mode: {value_mode}")
        if observations.occupancy_time_s <= 0:
            return None
        return observations.count / observations.occupancy_time_s

    def spatial_group_response_matrix(
        self,
        unit_idx: int,
        start: int,
        end: int,
        value_mode: str,
        y_groups: list[AxisGroup],
        x_groups: list[AxisGroup],
    ) -> list[list[float | None]]:
        return [
            [
                self.spatial_group_response_value(
                    unit_idx,
                    y_group,
                    x_group,
                    start,
                    end,
                    value_mode,
                )
                for x_group in x_groups
            ]
            for y_group in y_groups
        ]

    def spatial_group_count_histogram(
        self,
        unit_idx: int,
        y_group: AxisGroup,
        x_group: AxisGroup,
    ) -> list[float]:
        y_start = max(0, min(self.n_y - 1, min(y_group)))
        y_end = max(0, min(self.n_y - 1, max(y_group)))
        x_start = max(0, min(self.n_x - 1, min(x_group)))
        x_end = max(0, min(self.n_x - 1, max(x_group)))
        return [
            sum(
                float(self.counts[unit_idx][y_idx][x_idx][bin_idx])
                for y_idx in range(y_start, y_end + 1)
                for x_idx in range(x_start, x_end + 1)
                if self.occupancy_time_s[y_idx][x_idx] > 0
            )
            for bin_idx in range(self.n_bins)
        ]

    def spatial_group_source_pixel_count(
        self,
        y_group: AxisGroup,
        x_group: AxisGroup,
    ) -> int:
        """Return source bins with positive stimulus occupancy."""

        y_start = max(0, min(self.n_y - 1, min(y_group)))
        y_end = max(0, min(self.n_y - 1, max(y_group)))
        x_start = max(0, min(self.n_x - 1, min(x_group)))
        x_end = max(0, min(self.n_x - 1, max(x_group)))
        return sum(
            1
            for y_idx in range(y_start, y_end + 1)
            for x_idx in range(x_start, x_end + 1)
            if self.occupancy_time_s[y_idx][x_idx] > 0
        )

    def spatial_group_temporal_metrics(
        self,
        unit_idx: int,
        y_group: AxisGroup,
        x_group: AxisGroup,
        time_groups: list[AxisGroup],
    ) -> SpatialGroupTemporalMetrics:
        """Derive delay and entropy after pooling the full count histogram."""

        hist = self.spatial_group_count_histogram(unit_idx, y_group, x_group)
        source_pixel_count = self.spatial_group_source_pixel_count(y_group, x_group)
        return self.temporal_metrics_from_histogram(
            hist,
            time_groups,
            source_pixel_count=source_pixel_count,
        )

    def temporal_metrics_from_histogram(
        self,
        hist: Sequence[float],
        time_groups: list[AxisGroup],
        *,
        source_pixel_count: int = 1,
    ) -> SpatialGroupTemporalMetrics:
        if len(hist) != self.n_bins:
            raise ValueError(
                f"Expected {self.n_bins} temporal count bins; got {len(hist)}."
            )
        hist = [float(value) for value in hist]
        total = sum(hist)
        grouped: list[tuple[int, int, float, float]] = []
        for raw_start, raw_end in time_groups:
            start = max(0, min(self.n_bins - 1, min(raw_start, raw_end)))
            end = max(0, min(self.n_bins - 1, max(raw_start, raw_end)))
            count = sum(hist[start : end + 1])
            duration_s = self.time_bin_edges[end + 1] - self.time_bin_edges[start]
            grouped.append((start, end, count, count / duration_s))
        if total > 0 and grouped:
            peak_group_index = max(
                range(len(grouped)),
                key=lambda index: grouped[index][3],
            )
            group_start, group_end, _count, _rate = grouped[peak_group_index]
            delay_ms = (
                self.time_bin_edges[group_start]
                + self.time_bin_edges[group_end + 1]
            ) * 500.0
            entropy = -sum(
                (count / total) * math.log(count / total)
                for count in hist
                if count > 0
            )
            if self.n_bins > 1:
                entropy /= math.log(self.n_bins)
        else:
            peak_group_index = None
            delay_ms = None
            entropy = 0.0
        return SpatialGroupTemporalMetrics(
            mean_total_count=total / max(1, int(source_pixel_count)),
            peak_group_index=peak_group_index,
            delay_ms=delay_ms,
            entropy=entropy,
        )


def clone_matrix(matrix: list[list[float]]) -> list[list[float]]:
    return [row[:] for row in matrix]


def display_matrix(
    matrix: list[list[float | None]],
    data: RFMappingData,
    flip_y: bool,
) -> list[list[float | None]]:
    return [matrix[y_idx][:] for y_idx in data.display_y_indices(flip_y)]


def axis_groups_for_target(source_count: int, target_count: int) -> list[AxisGroup]:
    target = max(1, min(source_count, int(target_count)))
    groups: list[AxisGroup] = []
    for group_idx in range(target):
        start = group_idx * source_count // target
        end = ((group_idx + 1) * source_count // target) - 1
        groups.append((start, max(start, end)))
    return groups


def physical_time_groups(
    edges_ms: Sequence[float],
    target_duration_ms: float,
) -> list[AxisGroup]:
    """Group native bins by measured timestamps around a target duration.

    Starting at each native edge, the next boundary is the available edge
    nearest ``target_duration_ms`` later. Exact ties choose the earlier edge so
    the requested target is not silently exceeded. The final residual interval
    is retained. Uniform edges with an integer-bin target therefore reproduce
    fixed-count grouping exactly.
    """

    edges = tuple(float(edge) for edge in edges_ms)
    if len(edges) < 2:
        return []
    source_bin_count = len(edges) - 1
    target = float(target_duration_ms)
    if not math.isfinite(target) or target <= 0.0:
        target = max(edges[1] - edges[0], math.ulp(0.0))

    groups: list[AxisGroup] = []
    start = 0
    while start < source_bin_count:
        target_edge = edges[start] + target
        upper = bisect_left(
            edges,
            target_edge,
            lo=start + 1,
            hi=source_bin_count + 1,
        )
        upper = min(source_bin_count, upper)
        lower = max(start + 1, upper - 1)
        end_exclusive = (
            lower
            if abs(edges[lower] - target_edge) <= abs(edges[upper] - target_edge)
            else upper
        )
        groups.append((start, end_exclusive - 1))
        start = end_exclusive
    return groups


def display_group_index_for_source_bin(groups: list[AxisGroup], source_bin: int) -> int:
    """Return the display group containing a source bin, clamped at the ends."""
    if not groups:
        return 0
    for index, (start, end) in enumerate(groups):
        if start <= source_bin <= end:
            return index
    return 0 if source_bin < groups[0][0] else len(groups) - 1


def x_groups_for_count(n_x: int, group_size: int) -> list[AxisGroup]:
    group_size = max(1, min(n_x, int(group_size)))
    return [(start, min(start + group_size - 1, n_x - 1)) for start in range(0, n_x, group_size)]


def reduce_x_matrix(
    matrix: list[list[float | None]],
    x_groups: list[AxisGroup],
) -> list[list[float | None]]:
    reduced: list[list[float | None]] = []
    for row in matrix:
        out_row: list[float | None] = []
        for start, end in x_groups:
            values = [
                float(row[x_idx])
                for x_idx in range(start, end + 1)
                if row[x_idx] is not None and math.isfinite(float(row[x_idx]))
            ]
            out_row.append(sum(values) / len(values) if values else None)
        reduced.append(out_row)
    return reduced


def reduce_matrix_xy(
    matrix: list[list[float | None]],
    y_groups: list[AxisGroup],
    x_groups: list[AxisGroup],
) -> list[list[float | None]]:
    reduced: list[list[float | None]] = []
    for y_start, y_end in y_groups:
        out_row: list[float | None] = []
        for x_start, x_end in x_groups:
            values: list[float] = []
            for y_idx in range(y_start, y_end + 1):
                row = matrix[y_idx]
                for x_idx in range(x_start, x_end + 1):
                    value = row[x_idx]
                    if value is not None and math.isfinite(float(value)):
                        values.append(float(value))
            out_row.append(sum(values) / len(values) if values else None)
        reduced.append(out_row)
    return reduced


def smooth_matrix(
    matrix: list[list[float | None]],
    radius: int,
) -> list[list[float | None]]:
    radius = max(0, int(radius))
    if radius <= 0:
        return [row[:] for row in matrix]
    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    current = [row[:] for row in matrix]
    for _ in range(radius):
        out: list[list[float | None]] = []
        for y in range(rows):
            out_row: list[float | None] = []
            for x in range(cols):
                center = current[y][x]
                if center is None or not math.isfinite(float(center)):
                    out_row.append(None)
                    continue
                total = 0.0
                weight_total = 0.0
                for dy in (-1, 0, 1):
                    yy = y + dy
                    if yy < 0 or yy >= rows:
                        continue
                    for dx in (-1, 0, 1):
                        xx = x + dx
                        if xx < 0 or xx >= cols:
                            continue
                        value = current[yy][xx]
                        if value is None or not math.isfinite(float(value)):
                            continue
                        weight = 4.0 if dx == 0 and dy == 0 else (2.0 if dx == 0 or dy == 0 else 1.0)
                        total += float(value) * weight
                        weight_total += weight
                out_row.append(total / weight_total if weight_total else None)
            out.append(out_row)
        current = out
    return current


def finite_min_max(matrix: list[list[float | None]]) -> tuple[float, float]:
    values = [
        float(value)
        for row in matrix
        for value in row
        if value is not None and math.isfinite(float(value))
    ]
    if not values:
        return 0.0, 1.0
    low = min(values)
    high = max(values)
    if abs(high - low) < 1e-12:
        high = low + 1.0
    return low, high


def nonnegative_response_range(
    matrix: Sequence[Sequence[float | None]],
) -> tuple[float, float]:
    """Use a truthful zero baseline for non-negative response estimands."""

    peak = max(
        (
            max(0.0, float(value))
            for row in matrix
            for value in row
            if value is not None and math.isfinite(float(value))
        ),
        default=0.0,
    )
    return 0.0, peak


def palette_response_range(
    matrix: list[list[float | None]],
    palette: str,
) -> tuple[float, float]:
    """Return the response range used by each display palette.

    Gray retains the previous Python viewer's contrast-stretched range, while
    color palettes keep the explicit zero baseline.
    """

    if palette == "Gray":
        return finite_min_max(matrix)
    return nonnegative_response_range(matrix)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def hex_color(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def shade_hex(color: str, factor: float) -> str:
    color = color.lstrip("#")
    r = clamp(int(color[0:2], 16) * factor, 0, 255)
    g = clamp(int(color[2:4], 16) * factor, 0, 255)
    b = clamp(int(color[4:6], 16) * factor, 0, 255)
    return hex_color((int(round(r)), int(round(g)), int(round(b))))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        int(round(lerp(a[0], b[0], t))),
        int(round(lerp(a[1], b[1], t))),
        int(round(lerp(a[2], b[2], t))),
    )


def palette_color(value: float | None, low: float, high: float, palette: str) -> str:
    if value is None or not math.isfinite(float(value)):
        return "#e6e8eb"
    t = clamp((float(value) - low) / (high - low if high != low else 1.0))
    if palette == "Gray":
        shade = int(round(18 + t * 232))
        return hex_color((shade, shade, shade))
    if palette == "Inferno":
        return gradient_color(
            t,
            (
                (0.0, (22, 11, 57)),
                (0.25, (90, 18, 110)),
                (0.50, (190, 54, 85)),
                (0.75, (249, 140, 10)),
                (1.0, (252, 255, 164)),
            ),
        )
    return gradient_color(
        t,
        (
            (0.0, (68, 1, 84)),
            (0.25, (59, 82, 139)),
            (0.50, (33, 145, 140)),
            (0.75, (94, 201, 98)),
            (1.0, (253, 231, 37)),
        ),
    )


def delay_color(value: float | None, low: float = 0.0, high: float = 100.0) -> str:
    if value is None:
        return "#eceff2"
    t = clamp((float(value) - low) / (high - low if high != low else 1.0))
    return gradient_color(
        t,
        (
            (0.0, (47, 88, 167)),
            (0.35, (44, 171, 184)),
            (0.68, (246, 204, 89)),
            (1.0, (203, 71, 45)),
        ),
    )


def waveform_color(value: float | None, amplitude_limit_uv: float) -> str:
    """Return the notebook's red-white-blue diverging waveform color."""

    if value is None or not math.isfinite(float(value)):
        return "#e6e8eb"
    limit = max(float(amplitude_limit_uv), 1e-12)
    t = clamp((float(value) + limit) / (2.0 * limit))
    return gradient_color(
        t,
        (
            (0.0, (5, 48, 97)),
            (0.25, (67, 147, 195)),
            (0.50, (247, 247, 247)),
            (0.75, (214, 96, 77)),
            (1.0, (103, 0, 31)),
        ),
    )


def gradient_color(t: float, stops: tuple[tuple[float, tuple[int, int, int]], ...]) -> str:
    t = clamp(t)
    for i in range(len(stops) - 1):
        left_t, left_c = stops[i]
        right_t, right_c = stops[i + 1]
        if left_t <= t <= right_t:
            local = (t - left_t) / (right_t - left_t if right_t != left_t else 1.0)
            return hex_color(lerp_color(left_c, right_c, local))
    return hex_color(stops[-1][1])


def text_color_for(fill: str) -> str:
    fill = fill.lstrip("#")
    r = int(fill[0:2], 16)
    g = int(fill[2:4], 16)
    b = int(fill[4:6], 16)
    luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    return "#0f172a" if luminance > 0.58 else "#f8fafc"


def point_in_polygon(x: float, y: float, points: tuple[tuple[float, float], ...]) -> bool:
    inside = False
    j = len(points) - 1
    for i in range(len(points)):
        xi, yi = points[i]
        xj, yj = points[j]
        intersects = (yi > y) != (yj > y)
        if intersects:
            x_cross = (xj - xi) * (y - yi) / (yj - yi if yj != yi else 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def format_pos(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}"


def format_ms(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def value_mode_unit(value_mode: str) -> str:
    if value_mode == VALUE_MODE_COUNT:
        return "spikes"
    if value_mode == VALUE_MODE_RATE:
        return "Hz"
    raise ValueError(f"Unknown value mode: {value_mode}")


def value_mode_slug(value_mode: str) -> str:
    if value_mode == VALUE_MODE_COUNT:
        return "spike_count"
    if value_mode == VALUE_MODE_RATE:
        return "mean_firing_rate_hz"
    raise ValueError(f"Unknown value mode: {value_mode}")


def value_mode_suffix(value_mode: str) -> str:
    if value_mode == VALUE_MODE_COUNT:
        return " spikes"
    if value_mode == VALUE_MODE_RATE:
        return " Hz"
    raise ValueError(f"Unknown value mode: {value_mode}")


def format_response_value(value: float | None, value_mode: str) -> str:
    if value is None:
        return "n/a"
    if value_mode == VALUE_MODE_COUNT:
        return f"{value:.0f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def spatial_grid_dimensions(
    available_width: float,
    available_height: float,
    columns: int,
    rows: int,
    *,
    minimum_cell_width: float = 0.0,
) -> tuple[float, float, float, float]:
    """Fit a spatial grid and keep singleton-y maps near the legacy 30:7 shape.

    Multi-row RF maps retain square cells.  A singleton y axis has no physical
    height increment to preserve, so stretching only that display row avoids
    turning vertical-bar datasets into an unreadable strip without changing
    their data or hit-test groups.
    """

    columns = max(1, int(columns))
    rows = max(1, int(rows))
    width = max(0.0, float(available_width))
    height = max(0.0, float(available_height))
    if rows == 1:
        aspect = SINGLETON_Y_REFERENCE_COLUMNS / SINGLETON_Y_REFERENCE_ROWS
        grid_width = min(width, height * aspect)
        cell_width = max(float(minimum_cell_width), grid_width / columns)
        grid_width = cell_width * columns
        grid_height = grid_width / aspect
        return cell_width, grid_height, grid_width, grid_height

    cell = max(
        float(minimum_cell_width),
        min(width / columns, height / rows),
    )
    return cell, cell, cell * columns, cell * rows


def polar_ring_span(rows: int) -> float:
    """Return the visual radial width of one scientific y row."""

    return float(SINGLETON_Y_REFERENCE_ROWS if int(rows) == 1 else 1)


def matrix_ppm_data(
    matrix: list[list[float | None]],
    width: int,
    height: int,
    color_for_value: Callable[[float | None], str],
) -> bytes:
    """Rasterize a matrix into a binary PPM image using nearest-neighbor cells."""
    width = max(1, int(width))
    height = max(1, int(height))
    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    if rows == 0 or cols == 0:
        return f"P6\n{width} {height}\n255\n".encode("ascii") + bytes([230, 232, 235]) * (width * height)

    rgb_by_cell: list[list[tuple[int, int, int]]] = []
    for row in matrix:
        if len(row) != cols:
            raise ValueError("Cannot rasterize a ragged matrix")
        rgb_row: list[tuple[int, int, int]] = []
        for value in row:
            color = color_for_value(value).lstrip("#")
            if len(color) != 6:
                raise ValueError(f"Expected #RRGGBB color, got {color!r}")
            rgb_row.append(tuple(int(color[index : index + 2], 16) for index in (0, 2, 4)))
        rgb_by_cell.append(rgb_row)

    pixels = bytearray(width * height * 3)
    offset = 0
    for pixel_y in range(height):
        source_y = min(rows - 1, pixel_y * rows // height)
        for pixel_x in range(width):
            source_x = min(cols - 1, pixel_x * cols // width)
            red, green, blue = rgb_by_cell[source_y][source_x]
            pixels[offset : offset + 3] = bytes((red, green, blue))
            offset += 3
    return f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(pixels)


class SettingsValidationError(ValueError):
    """Validation failure associated with one Settings tab."""

    def __init__(self, tab_name: str, message: str):
        super().__init__(message)
        self.tab_name = tab_name


def matrix_atlas_ppm_data(
    tiles: list[
        tuple[list[list[float | None]], float, float, float]
        | tuple[list[list[float | None]], float, float, float, float]
    ],
    width: int,
    height: int,
    color_for_value: Callable[[float | None], str],
) -> bytes:
    """Rasterize many equally-scaled matrices into one white PPM atlas."""
    width = max(1, int(width))
    height = max(1, int(height))
    pixels = bytearray(b"\xff" * (width * height * 3))
    color_cache: dict[str, bytes] = {}
    value_color_cache: dict[float | None, bytes] = {}

    for tile in tiles:
        if len(tile) == 4:
            matrix, origin_x, origin_y, cell_width = tile
            cell_height = cell_width
        else:
            matrix, origin_x, origin_y, cell_width, cell_height = tile
        rows = len(matrix)
        cols = len(matrix[0]) if rows else 0
        if any(len(row) != cols for row in matrix):
            raise ValueError("Cannot rasterize a ragged matrix")
        cell_width = float(cell_width)
        cell_height = float(cell_height)
        if cell_width <= 0.0 or cell_height <= 0.0:
            continue
        x_ranges: list[tuple[int, int]] = []
        for col_idx in range(cols):
            x0 = max(0, min(width, int(round(origin_x + col_idx * cell_width))))
            x1 = max(
                x0,
                min(width, int(round(origin_x + (col_idx + 1) * cell_width))),
            )
            x_ranges.append((x0, x1))
        for row_idx, row in enumerate(matrix):
            y0 = max(0, min(height, int(round(origin_y + row_idx * cell_height))))
            y1 = max(
                y0,
                min(height, int(round(origin_y + (row_idx + 1) * cell_height))),
            )
            if y1 <= y0:
                continue
            scanlines: list[tuple[int, bytes]] = []
            scanline_start: int | None = None
            scanline_end = 0
            scanline_parts: list[bytes] = []
            for col_idx, value in enumerate(row):
                x0, x1 = x_ranges[col_idx]
                if x1 <= x0:
                    continue
                value_key = None if value is None else float(value)
                rgb = value_color_cache.get(value_key)
                if rgb is None:
                    color = color_for_value(value).lower()
                    rgb = color_cache.get(color)
                    if rgb is None:
                        raw = color.lstrip("#")
                        if len(raw) != 6:
                            raise ValueError(f"Expected #RRGGBB color, got {color!r}")
                        rgb = bytes(
                            int(raw[index : index + 2], 16)
                            for index in (0, 2, 4)
                        )
                        color_cache[color] = rgb
                    value_color_cache[value_key] = rgb
                if scanline_start is not None and x0 != scanline_end:
                    scanlines.append((scanline_start, b"".join(scanline_parts)))
                    scanline_start = None
                    scanline_parts = []
                if scanline_start is None:
                    scanline_start = x0
                scanline_end = x1
                scanline_parts.append(rgb * (x1 - x0))
            if scanline_start is not None:
                scanlines.append((scanline_start, b"".join(scanline_parts)))
            for x0, scanline in scanlines:
                for pixel_y in range(y0, y1):
                    offset = (pixel_y * width + x0) * 3
                    pixels[offset : offset + len(scanline)] = scanline

    return f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(pixels)


@lru_cache(maxsize=256)
def _polar_tile_pixel_runs(
    origin_x_fraction: float,
    origin_y_fraction: float,
    scale: float,
    total_deg: float,
    rows: int,
    cols: int,
    ring_span: float = 1.0,
) -> tuple[tuple[int, int, int, int, int], ...]:
    """Map one polar tile's scanlines to ring/column runs for reuse."""

    ring_span = max(float(ring_span), 1e-9)
    radius_units = INNER_BLANK_ROWS + rows * ring_span
    diameter = 2.0 * radius_units * scale
    center_x = origin_x_fraction + diameter / 2.0
    center_y = origin_y_fraction + diameter / 2.0
    local_width = int(math.ceil(origin_x_fraction + diameter))
    local_height = int(math.ceil(origin_y_fraction + diameter))
    column_span = total_deg / cols
    theta_start = 90.0 + total_deg / 2.0
    theta_end = 90.0 - total_deg / 2.0
    runs: list[tuple[int, int, int, int, int]] = []

    for pixel_y in range(local_height):
        dy = (center_y - (pixel_y + 0.5)) / scale
        run_start: int | None = None
        run_value: tuple[int, int] | None = None
        for pixel_x in range(local_width):
            dx = ((pixel_x + 0.5) - center_x) / scale
            radius = math.hypot(dx, dy)
            value: tuple[int, int] | None = None
            if INNER_BLANK_ROWS <= radius < radius_units:
                ring_idx = int((radius - INNER_BLANK_ROWS) / ring_span)
                if 0 <= ring_idx < rows:
                    theta_deg = math.degrees(math.atan2(dy, dx))
                    if total_deg >= 359.999:
                        relative = (theta_start - theta_deg) % 360.0
                    else:
                        while theta_deg > theta_start:
                            theta_deg -= 360.0
                        while theta_deg < theta_end:
                            theta_deg += 360.0
                        if theta_end <= theta_deg <= theta_start:
                            relative = theta_start - theta_deg
                        else:
                            relative = None
                    if relative is not None:
                        column = max(
                            0,
                            min(cols - 1, int(relative / column_span)),
                        )
                        value = ring_idx, column

            if value == run_value:
                continue
            if run_value is not None and run_start is not None:
                runs.append(
                    (pixel_y, run_start, pixel_x, run_value[0], run_value[1])
                )
            run_start = pixel_x if value is not None else None
            run_value = value
        if run_value is not None and run_start is not None:
            runs.append(
                (pixel_y, run_start, local_width, run_value[0], run_value[1])
            )
    return tuple(runs)


def polar_matrix_atlas_ppm_data(
    tiles: list[
        tuple[
            list[list[float | None]],
            float,
            float,
            float,
            float,
            list[int],
        ]
        | tuple[
            list[list[float | None]],
            float,
            float,
            float,
            float,
            list[int],
            float,
        ]
    ],
    width: int,
    height: int,
    color_for_value: Callable[[float | None], str],
) -> bytes:
    """Rasterize polar matrices into one white PPM atlas.

    Keeping the timeline previews in a single image avoids creating thousands
    of individual Tk canvas polygons when the source contains many time bins.
    """
    width = max(1, int(width))
    height = max(1, int(height))
    pixels = bytearray(b"\xff" * (width * height * 3))
    color_cache: dict[str, bytes] = {}
    value_color_cache: dict[float | None, bytes] = {}

    for tile in tiles:
        if len(tile) == 6:
            matrix, origin_x, origin_y, scale, total_deg, ring_rows = tile
            ring_span = 1.0
        else:
            (
                matrix,
                origin_x,
                origin_y,
                scale,
                total_deg,
                ring_rows,
                ring_span,
            ) = tile
        rows = len(matrix)
        cols = len(matrix[0]) if rows else 0
        if rows == 0 or cols == 0:
            continue
        if any(len(row) != cols for row in matrix):
            raise ValueError("Cannot rasterize a ragged polar matrix")
        if len(ring_rows) != rows:
            raise ValueError("Polar ring order must match matrix rows")

        rgb_by_cell: list[list[bytes]] = []
        for row in matrix:
            rgb_row: list[bytes] = []
            for value in row:
                value_key = None if value is None else float(value)
                rgb = value_color_cache.get(value_key)
                if rgb is None:
                    color = color_for_value(value).lower()
                    rgb = color_cache.get(color)
                    if rgb is None:
                        raw = color.lstrip("#")
                        if len(raw) != 6:
                            raise ValueError(f"Expected #RRGGBB color, got {color!r}")
                        rgb = bytes(
                            int(raw[index : index + 2], 16)
                            for index in (0, 2, 4)
                        )
                        color_cache[color] = rgb
                    value_color_cache[value_key] = rgb
                rgb_row.append(rgb)
            rgb_by_cell.append(rgb_row)

        scale = max(float(scale), 1e-9)
        origin_x_floor = math.floor(origin_x)
        origin_y_floor = math.floor(origin_y)
        runs = _polar_tile_pixel_runs(
            float(origin_x - origin_x_floor),
            float(origin_y - origin_y_floor),
            scale,
            float(total_deg),
            rows,
            cols,
            float(ring_span),
        )
        for local_y, local_x0, local_x1, ring_idx, column in runs:
            pixel_y = origin_y_floor + local_y
            if not (0 <= pixel_y < height):
                continue
            pixel_x0 = max(0, origin_x_floor + local_x0)
            pixel_x1 = min(width, origin_x_floor + local_x1)
            if pixel_x1 <= pixel_x0:
                continue
            rgb = rgb_by_cell[ring_rows[ring_idx]][column]
            offset = (pixel_y * width + pixel_x0) * 3
            scanline = rgb * (pixel_x1 - pixel_x0)
            pixels[offset : offset + len(scanline)] = scanline

    return f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(pixels)


class SettingsWindow(tk.Toplevel):
    """Single native-style settings window shared by all viewer windows."""

    TAB_NAMES = ("General", "RF Map", "Waveform", "Tuning Curve")

    def __init__(self, owner: RFMViewer):
        self.owner = owner
        self._app_root = owner._app_root
        super().__init__(self._app_root)
        self.title("RF Map Viewer Settings")
        self.geometry("680x720")
        self.minsize(620, 640)
        self.transient(owner)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._create_variables(owner._app_root._rfm_settings)
        self._build()
        self._select_remembered_tab()
        self._sync_dependent_controls()

    def transient(self, master: tk.Misc | None = None) -> str | None:
        """Normalize Tk's queried window object to its stable path string."""

        result = super().transient(master)
        if master is None and result:
            return str(result)
        return result

    def _create_variables(self, settings: ViewerSettings) -> None:
        self.show_tuning_curve_var = tk.BooleanVar(value=settings.show_tuning_curve)
        self.auto_load_tuning_curve_var = tk.BooleanVar(value=settings.auto_load_tuning_curve)
        self.show_waveform_var = tk.BooleanVar(value=settings.show_waveform)
        self.show_probe_layout_var = tk.BooleanVar(value=settings.show_probe_layout)
        self.auto_load_probe_layout_var = tk.BooleanVar(value=settings.auto_load_probe_layout)
        self.rf_sum_start_var = tk.StringVar(value=format_ms(settings.rf_sum_start_ms))
        self.rf_sum_end_var = tk.StringVar(value=format_ms(settings.rf_sum_end_ms))
        self.rf_filter_units_with_zero_bins_var = tk.BooleanVar(
            value=settings.rf_filter_units_with_zero_bins
        )
        self.rf_zero_bin_threshold_var = tk.StringVar(
            value=str(settings.rf_zero_bin_threshold)
        )
        self.rf_time_resolution_var = tk.StringVar(value=format_ms(settings.rf_time_resolution_ms))
        self.rf_value_mode_var = tk.StringVar(value=settings.rf_value_mode)
        self.rf_x_bins_var = tk.StringVar(
            value="Native" if settings.rf_x_bins == 0 else str(settings.rf_x_bins)
        )
        self.rf_y_bins_var = tk.StringVar(
            value="Native" if settings.rf_y_bins == 0 else str(settings.rf_y_bins)
        )
        self.rf_smooth_radius_var = tk.IntVar(value=settings.rf_smooth_radius)
        self.rf_flip_y_var = tk.BooleanVar(value=settings.rf_flip_y)
        self.rf_palette_var = tk.StringVar(value=settings.rf_palette)
        self.rf_polar_radius_var = tk.StringVar(value=settings.rf_polar_radius)
        self.rf_layout_var = tk.StringVar(
            value="Polar" if settings.rf_polar_layout else "Rectangle"
        )
        self.rf_rgb_mode_var = tk.BooleanVar(value=settings.rf_rgb_mode)
        viewer_tab_labels = {
            "rf": "RF",
            "delay": "Delay / RGB",
            "timeline": "Timeline",
        }
        self.default_viewer_tab_var = tk.StringVar(
            value=viewer_tab_labels.get(settings.default_viewer_tab, "RF")
        )
        self.waveform_channel_mode_var = tk.StringVar(
            value=WAVEFORM_CHANNEL_MODE_LABELS.get(
                settings.waveform_channel_mode,
                WAVEFORM_CHANNEL_MODE_LABELS["same_x_column"],
            )
        )
        self.tuning_plot_mode_var = tk.StringVar(value=settings.tuning_plot_mode)
        self.tuning_layout_var = tk.StringVar(value=settings.tuning_layout)
        self.tuning_display_bins_var = tk.StringVar(value=str(settings.tuning_display_bins))
        self.tuning_smoothing_var = tk.BooleanVar(value=settings.tuning_smoothing)
        self.tuning_compare_scale_var = tk.BooleanVar(value=settings.tuning_compare_scale)
        self.tuning_smooth_sigma_var = tk.StringVar(
            value=f"{settings.tuning_smooth_sigma * 360.0 / DEFAULT_HD_DISPLAY_BINS:g}"
        )
        self.error_var = tk.StringVar(value="")
        self._tab_error_vars = {
            name: tk.StringVar(value="") for name in self.TAB_NAMES
        }

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        outer = ttk.Frame(self, padding=(16, 14, 16, 12))
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.notebook.enable_traversal()
        self._tab_name_by_widget: dict[str, str] = {}
        self._tab_widget_by_name: dict[str, str] = {}
        general = self._new_tab("General")
        rf_map = self._new_tab("RF Map")
        waveform = self._new_tab("Waveform")
        tuning = self._new_tab("Tuning Curve")
        self._build_general_tab(general)
        self._build_rf_tab(rf_map)
        self._build_waveform_tab(waveform)
        self._build_tuning_tab(tuning)
        self.notebook.bind("<<NotebookTabChanged>>", self._remember_selected_tab)

        footer = ttk.Frame(outer)
        footer.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer,
            textvariable=self.error_var,
            foreground="#b42318",
            wraplength=280,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Cancel", command=self._close).grid(
            row=0, column=1, padx=(12, 8)
        )
        ttk.Button(footer, text="Save", command=self._save).grid(row=0, column=2)

        for variable in (
            self.show_tuning_curve_var,
            self.show_waveform_var,
            self.show_probe_layout_var,
            self.rf_filter_units_with_zero_bins_var,
            self.tuning_smoothing_var,
        ):
            variable.trace_add("write", lambda *_args: self._sync_dependent_controls())

    def _new_tab(self, name: str) -> ttk.Frame:
        tab = ttk.Frame(self.notebook, padding=(18, 16))
        # Keep forms anchored to the leading edge instead of centering their
        # controls in the available Settings width.
        tab.columnconfigure(0, minsize=164)
        tab.columnconfigure(1, weight=0)
        tab.columnconfigure(2, weight=1)
        self.notebook.add(tab, text=name)
        self._tab_name_by_widget[str(tab)] = name
        self._tab_widget_by_name[name] = str(tab)
        ttk.Label(
            tab,
            textvariable=self._tab_error_vars[name],
            foreground="#b42318",
            wraplength=500,
            justify="left",
        ).grid(row=99, column=0, columnspan=2, sticky="w", pady=(16, 0))
        return tab

    @staticmethod
    def _section_label(parent: ttk.Frame, text: str, row: int) -> None:
        ttk.Label(
            parent,
            text=text,
            font=("TkDefaultFont", 11, "bold"),
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8 if row else 0, 8))

    def _build_general_tab(self, tab: ttk.Frame) -> None:
        self._section_label(tab, "Views and loading", 0)
        ttk.Checkbutton(
            tab,
            text="Show HD tuning curve beside the RF map",
            variable=self.show_tuning_curve_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.auto_tuning_check = ttk.Checkbutton(
            tab,
            text="Automatically find and load tuning_curves.tc or .json",
            variable=self.auto_load_tuning_curve_var,
        )
        self.auto_tuning_check.grid(row=2, column=0, columnspan=2, sticky="w", padx=(22, 0), pady=(0, 14))
        ttk.Checkbutton(
            tab,
            text="Show probe layout in the sidebar",
            variable=self.show_probe_layout_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.auto_probe_check = ttk.Checkbutton(
            tab,
            text="Automatically find and load probe geometry",
            variable=self.auto_load_probe_layout_var,
        )
        self.auto_probe_check.grid(row=4, column=0, columnspan=2, sticky="w", padx=(22, 0))
        ttk.Label(
            tab,
            text=(
                "Hidden views are not discovered, read, or rendered. Turning off automatic "
                "loading does not remove a file that is already attached."
            ),
            foreground="#667085",
            wraplength=500,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(20, 0))

    def _labeled_entry(
        self,
        tab: ttk.Frame,
        row: int,
        label: str,
        variable: tk.Variable,
        *,
        width: int = 12,
    ) -> ttk.Entry:
        ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=5)
        entry = ttk.Entry(tab, textvariable=variable, width=width)
        entry.grid(row=row, column=1, sticky="w", pady=5)
        return entry

    def _labeled_combo(
        self,
        tab: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        values: Sequence[str],
        *,
        width: int = 24,
    ) -> ttk.Combobox:
        ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=5)
        combo = ttk.Combobox(
            tab,
            state="readonly",
            values=tuple(values),
            textvariable=variable,
            width=width,
        )
        combo.grid(row=row, column=1, sticky="w", pady=5)
        return combo

    def _build_rf_tab(self, tab: ttk.Frame) -> None:
        self._section_label(tab, "Timing", 0)
        range_frame = ttk.Frame(tab)
        range_frame.grid(row=1, column=1, sticky="w", pady=5)
        ttk.Label(tab, text="Default RF sum range (ms)").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(range_frame, textvariable=self.rf_sum_start_var, width=8).grid(row=0, column=0)
        ttk.Label(range_frame, text="to").grid(row=0, column=1, padx=6)
        ttk.Entry(range_frame, textvariable=self.rf_sum_end_var, width=8).grid(row=0, column=2)
        self._labeled_entry(tab, 2, "Target time width (ms)", self.rf_time_resolution_var)
        self._labeled_combo(tab, 3, "Value", self.rf_value_mode_var, VALUE_MODES)

        self._section_label(tab, "Unit filtering", 4)
        ttk.Checkbutton(
            tab,
            text="Hide units with zero-spike RF bins in the current RF window",
            variable=self.rf_filter_units_with_zero_bins_var,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(tab, text="Hide at this many zero bins").grid(
            row=6, column=0, sticky="w", pady=5
        )
        self.rf_zero_bin_threshold_entry = ttk.Entry(
            tab,
            textvariable=self.rf_zero_bin_threshold_var,
            width=12,
        )
        self.rf_zero_bin_threshold_entry.grid(row=6, column=1, sticky="w", pady=5)
        ttk.Label(
            tab,
            text=(
                "Counts native spatial RF bins before display rebinning or smoothing. "
                "The filter follows the RF window in the main viewer."
            ),
            foreground="#667085",
            wraplength=440,
            justify="left",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self._section_label(tab, "Spatial display", 8)
        bins_frame = ttk.Frame(tab)
        bins_frame.grid(row=9, column=1, sticky="w", pady=5)
        ttk.Label(tab, text="Display bins").grid(row=9, column=0, sticky="w", pady=5)
        ttk.Label(bins_frame, text="X").grid(row=0, column=0)
        ttk.Entry(bins_frame, textvariable=self.rf_x_bins_var, width=8).grid(row=0, column=1, padx=(4, 12))
        ttk.Label(bins_frame, text="Y").grid(row=0, column=2)
        ttk.Entry(bins_frame, textvariable=self.rf_y_bins_var, width=8).grid(row=0, column=3, padx=(4, 0))
        self._labeled_combo(tab, 10, "Layout", self.rf_layout_var, ("Rectangle", "Polar"))
        self._labeled_combo(tab, 11, "Palette", self.rf_palette_var, PALETTES)
        self._labeled_combo(tab, 12, "Polar radius", self.rf_polar_radius_var, POLAR_RADIUS_MODES)
        ttk.Label(tab, text="RF smoothing radius").grid(row=13, column=0, sticky="w", pady=5)
        ttk.Spinbox(
            tab,
            from_=0,
            to=3,
            increment=1,
            textvariable=self.rf_smooth_radius_var,
            width=10,
        ).grid(row=13, column=1, sticky="w", pady=5)
        toggles = ttk.Frame(tab)
        toggles.grid(row=14, column=1, sticky="w", pady=5)
        ttk.Checkbutton(toggles, text="Flip Y", variable=self.rf_flip_y_var).grid(row=0, column=0, padx=(0, 18))
        ttk.Checkbutton(toggles, text="RGB composite", variable=self.rf_rgb_mode_var).grid(row=0, column=1)
        self._labeled_combo(
            tab,
            15,
            "Initial tab",
            self.default_viewer_tab_var,
            ("RF", "Delay / RGB", "Timeline"),
        )
        ttk.Label(
            tab,
            text="Use “Native” for all source X or Y bins.",
            foreground="#667085",
        ).grid(row=16, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_waveform_tab(self, tab: ttk.Frame) -> None:
        self._section_label(tab, "Local average waveform", 0)
        ttk.Checkbutton(
            tab,
            text="Show a compact waveform below the HD tuning curve",
            variable=self.show_waveform_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.waveform_channel_mode_combo = self._labeled_combo(
            tab,
            2,
            "Nearby channels",
            self.waveform_channel_mode_var,
            tuple(WAVEFORM_CHANNEL_MODE_LABELS.values()),
        )
        ttk.Label(
            tab,
            text=(
                "The display follows the notebook: the best-PTP channel plus the "
                "four nearest channels matching this rule, ordered from larger to "
                "smaller probe Y. It is not forced to two channels above and two below."
            ),
            foreground="#667085",
            wraplength=500,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _build_tuning_tab(self, tab: ttk.Frame) -> None:
        self._section_label(tab, "Head-direction display", 0)
        self._labeled_combo(
            tab,
            1,
            "Plot style",
            self.tuning_plot_mode_var,
            TUNING_PLOT_MODES,
        )
        ttk.Label(
            tab,
            text="Auto follows the RF map's Rectangle or Polar layout.",
            foreground="#667085",
            wraplength=440,
        ).grid(row=2, column=1, sticky="w", pady=(0, 10))
        self._labeled_combo(
            tab,
            3,
            "RF + tuning arrangement",
            self.tuning_layout_var,
            TUNING_LAYOUTS,
        )
        self._labeled_entry(tab, 4, "Displayed HD bins", self.tuning_display_bins_var)
        ttk.Label(
            tab,
            text="On Save, the value is rounded down to a divisor of 180 (for example, 8 → 6).",
            foreground="#667085",
            wraplength=440,
            justify="left",
        ).grid(row=5, column=1, sticky="w", pady=(0, 12))
        ttk.Checkbutton(
            tab,
            text="Compare cells in this file on one shared 0–peak Hz scale",
            variable=self.tuning_compare_scale_var,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Checkbutton(
            tab,
            text="Smooth the 180-bin source curve",
            variable=self.tuning_smoothing_var,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(tab, text="Gaussian σ (degrees)").grid(
            row=8,
            column=0,
            sticky="w",
            pady=5,
        )
        self.tuning_sigma_entry = ttk.Entry(
            tab,
            textvariable=self.tuning_smooth_sigma_var,
            width=12,
        )
        self.tuning_sigma_entry.grid(row=8, column=1, sticky="w", pady=5)
        ttk.Label(
            tab,
            text=(
                "Circular Gaussian smoothing uses mode=wrap on the raw 180-bin curve "
                "before display aggregation, preserving one angular width at every resolution."
            ),
            foreground="#667085",
            wraplength=440,
            justify="left",
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(14, 0))

    def _select_remembered_tab(self) -> None:
        remembered = getattr(self._app_root, "_rfm_settings_tab", "General")
        for tab_id in self.notebook.tabs():
            if self._tab_name_by_widget.get(str(tab_id)) == remembered:
                self.notebook.select(tab_id)
                return

    def _remember_selected_tab(self, _event: object | None = None) -> None:
        selected = str(self.notebook.select())
        self._app_root._rfm_settings_tab = self._tab_name_by_widget.get(
            selected, "General"
        )
        self.after_idle(self._refresh_selected_tab_text)

    def _refresh_selected_tab_text(self) -> None:
        """Work around stale controls in initially hidden ttk tabs on macOS Tk."""

        try:
            selected = self.nametowidget(self.notebook.select())
        except (KeyError, tk.TclError):
            return
        pending = list(selected.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, (ttk.Label, ttk.Checkbutton)):
                try:
                    if not widget.cget("textvariable"):
                        widget.configure(text=widget.cget("text"))
                except tk.TclError:
                    continue
            elif isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Spinbox)):
                try:
                    # Aqua occasionally leaves a previously hidden field blank
                    # until it receives focus. Re-applying the variable asks the
                    # native theme to paint the current value immediately.
                    variable = widget.cget("textvariable")
                    if variable:
                        widget.configure(textvariable=variable)
                except tk.TclError:
                    continue
        try:
            selected.update_idletasks()
        except tk.TclError:
            pass

    def _clear_tab_errors(self) -> None:
        for name, variable in self._tab_error_vars.items():
            variable.set("")
            tab_id = self._tab_widget_by_name.get(name)
            if tab_id is not None:
                self.notebook.tab(tab_id, text=name)

    def _show_validation_error(self, error: SettingsValidationError) -> None:
        self._clear_tab_errors()
        tab_name = error.tab_name if error.tab_name in self.TAB_NAMES else "General"
        self._tab_error_vars[tab_name].set(str(error))
        tab_id = self._tab_widget_by_name[tab_name]
        self.notebook.tab(tab_id, text=f"{tab_name} •")
        self.notebook.select(tab_id)

    def _sync_dependent_controls(self) -> None:
        self.auto_tuning_check.state(
            ["!disabled"] if self.show_tuning_curve_var.get() else ["disabled"]
        )
        self.auto_probe_check.state(
            ["!disabled"] if self.show_probe_layout_var.get() else ["disabled"]
        )
        self.waveform_channel_mode_combo.state(
            ["!disabled"] if self.show_waveform_var.get() else ["disabled"]
        )
        self.rf_zero_bin_threshold_entry.state(
            ["!disabled"]
            if self.rf_filter_units_with_zero_bins_var.get()
            else ["disabled"]
        )
        self.tuning_sigma_entry.state(
            ["!disabled"] if self.tuning_smoothing_var.get() else ["disabled"]
        )

    @staticmethod
    def _positive_float(raw: str, label: str) -> float:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{label} must be a number.") from exc
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{label} must be positive and finite.")
        return value

    @staticmethod
    def _native_or_positive_int(raw: str, label: str) -> int:
        cleaned = raw.strip()
        if cleaned.casefold() in {"native", "auto"}:
            return 0
        try:
            value = int(cleaned)
        except ValueError as exc:
            raise ValueError(f"{label} must be “Native” or a positive integer.") from exc
        if value <= 0:
            raise ValueError(f"{label} must be “Native” or a positive integer.")
        return value

    def _validated_settings(self) -> ViewerSettings:
        try:
            start_ms = float(self.rf_sum_start_var.get())
            end_ms = float(self.rf_sum_end_var.get())
        except ValueError as exc:
            raise SettingsValidationError(
                "RF Map", "RF sum range must contain two numbers."
            ) from exc
        if not math.isfinite(start_ms) or not math.isfinite(end_ms) or start_ms >= end_ms:
            raise SettingsValidationError(
                "RF Map", "RF sum range must be finite and start before end."
            )
        try:
            time_resolution = self._positive_float(
                self.rf_time_resolution_var.get(), "Time resolution"
            )
            x_bins = self._native_or_positive_int(self.rf_x_bins_var.get(), "X bins")
            y_bins = self._native_or_positive_int(self.rf_y_bins_var.get(), "Y bins")
            smooth_radius = max(0, min(3, int(self.rf_smooth_radius_var.get())))
        except (tk.TclError, ValueError) as exc:
            raise SettingsValidationError("RF Map", str(exc)) from exc
        try:
            zero_bin_threshold = int(self.rf_zero_bin_threshold_var.get().strip())
        except ValueError as exc:
            raise SettingsValidationError(
                "RF Map", "Zero-bin threshold must be a positive integer."
            ) from exc
        if zero_bin_threshold <= 0:
            raise SettingsValidationError(
                "RF Map", "Zero-bin threshold must be a positive integer."
            )
        active = self.owner._active_viewer()
        maximum_zero_bins = active.data.spatial_bin_count
        if zero_bin_threshold > maximum_zero_bins:
            raise SettingsValidationError(
                "RF Map",
                f"Zero-bin threshold is too large; max is {maximum_zero_bins}.",
            )

        value_mode = self.rf_value_mode_var.get()
        palette = self.rf_palette_var.get()
        polar_radius = self.rf_polar_radius_var.get()
        layout = self.rf_layout_var.get()
        tab_keys = {
            "RF": "rf",
            "Delay / RGB": "delay",
            "Timeline": "timeline",
        }
        initial_tab = self.default_viewer_tab_var.get()
        waveform_channel_mode = WAVEFORM_CHANNEL_MODE_BY_LABEL.get(
            self.waveform_channel_mode_var.get()
        )
        if value_mode not in VALUE_MODES:
            raise SettingsValidationError("RF Map", "Choose a supported RF value mode.")
        if palette not in PALETTES:
            raise SettingsValidationError("RF Map", "Choose a supported RF palette.")
        if polar_radius not in POLAR_RADIUS_MODES:
            raise SettingsValidationError("RF Map", "Choose a supported polar-radius mode.")
        if layout not in {"Rectangle", "Polar"}:
            raise SettingsValidationError("RF Map", "Choose Rectangle or Polar layout.")
        if initial_tab not in tab_keys:
            raise SettingsValidationError("RF Map", "Choose a supported initial tab.")
        if waveform_channel_mode is None:
            raise SettingsValidationError(
                "Waveform", "Choose Same x column or Same shank."
            )

        smoothing = bool(self.tuning_smoothing_var.get())
        try:
            sigma_degrees = self._positive_float(
                self.tuning_smooth_sigma_var.get(), "Tuning smoothing sigma"
            )
            sigma = sigma_degrees * DEFAULT_HD_DISPLAY_BINS / 360.0
        except ValueError as exc:
            if smoothing:
                raise SettingsValidationError("Tuning Curve", str(exc)) from exc
            current = getattr(self._app_root, "_rfm_settings", ViewerSettings())
            sigma = float(current.tuning_smooth_sigma)
            if not math.isfinite(sigma) or sigma <= 0.0:
                sigma = ViewerSettings().tuning_smooth_sigma
            self.tuning_smooth_sigma_var.set(
                f"{sigma * 360.0 / DEFAULT_HD_DISPLAY_BINS:g}"
            )
        try:
            raw_hd_bins = int(self.tuning_display_bins_var.get().strip())
        except ValueError as exc:
            raise SettingsValidationError(
                "Tuning Curve", "Displayed HD bins must be an integer."
            ) from exc
        if raw_hd_bins <= 0:
            raise SettingsValidationError(
                "Tuning Curve", "Displayed HD bins must be a positive integer."
            )
        hd_bins = normalize_hd_bin_count(raw_hd_bins)
        self.tuning_display_bins_var.set(str(hd_bins))
        tuning_mode = self.tuning_plot_mode_var.get()
        if tuning_mode not in TUNING_PLOT_MODES:
            raise SettingsValidationError(
                "Tuning Curve", "Choose Auto, Polar, or Line plot style."
            )
        tuning_layout = self.tuning_layout_var.get()
        if tuning_layout not in TUNING_LAYOUTS:
            raise SettingsValidationError(
                "Tuning Curve", "Choose Side by side or Stacked arrangement."
            )
        return ViewerSettings(
            show_tuning_curve=bool(self.show_tuning_curve_var.get()),
            auto_load_tuning_curve=bool(self.auto_load_tuning_curve_var.get()),
            show_waveform=bool(self.show_waveform_var.get()),
            show_probe_layout=bool(self.show_probe_layout_var.get()),
            auto_load_probe_layout=bool(self.auto_load_probe_layout_var.get()),
            rf_sum_start_ms=start_ms,
            rf_sum_end_ms=end_ms,
            rf_filter_units_with_zero_bins=bool(
                self.rf_filter_units_with_zero_bins_var.get()
            ),
            rf_zero_bin_threshold=zero_bin_threshold,
            rf_time_resolution_ms=time_resolution,
            rf_value_mode=value_mode,
            rf_x_bins=x_bins,
            rf_y_bins=y_bins,
            rf_smooth_radius=smooth_radius,
            rf_flip_y=bool(self.rf_flip_y_var.get()),
            rf_palette=palette,
            rf_polar_radius=polar_radius,
            rf_polar_layout=layout == "Polar",
            rf_rgb_mode=bool(self.rf_rgb_mode_var.get()),
            default_viewer_tab=tab_keys[initial_tab],
            waveform_channel_mode=waveform_channel_mode,
            tuning_plot_mode=tuning_mode,
            tuning_layout=tuning_layout,
            tuning_display_bins=hd_bins,
            tuning_smoothing=smoothing,
            tuning_smooth_sigma=sigma,
            tuning_compare_scale=bool(self.tuning_compare_scale_var.get()),
        )

    def _commit(self, *, close: bool) -> None:
        self.error_var.set("")
        self._clear_tab_errors()
        try:
            settings = self._validated_settings()
        except SettingsValidationError as exc:
            self._show_validation_error(exc)
            return
        except (KeyError, tk.TclError, ValueError) as exc:
            selected = self._tab_name_by_widget.get(
                str(self.notebook.select()), "General"
            )
            self._show_validation_error(SettingsValidationError(selected, str(exc)))
            return
        active = self.owner._active_viewer()
        if not getattr(active, "_viewer_ready", False):
            self.error_var.set("The viewer is still opening. Try again when it is ready.")
            return
        if not active._apply_viewer_settings(settings, persist=True, broadcast=True):
            self.error_var.set("Settings could not be saved.")
            return
        self.error_var.set("")
        if close:
            self._close()

    def _save(self) -> None:
        self._commit(close=True)

    def _close(self) -> None:
        if getattr(self._app_root, "_rfm_settings_window", None) is self:
            self._app_root._rfm_settings_window = None
        try:
            self.destroy()
        except tk.TclError:
            pass


class RFMViewer(tk.Toplevel):
    def __init__(
        self,
        data: RFMappingData | None = None,
        *,
        startup_path: Path | None = None,
        master: tk.Misc | None = None,
    ):
        if data is not None and startup_path is not None:
            raise ValueError("Provide at most one of data or startup_path")

        if master is None:
            master = tk.Tk()
            master.withdraw()
        self._app_root = master.winfo_toplevel()
        if not hasattr(self._app_root, "_rfm_settings_path"):
            self._app_root._rfm_settings_path = viewer_settings_path()
        if not hasattr(self._app_root, "_rfm_settings"):
            self._app_root._rfm_settings = load_viewer_settings(
                self._app_root._rfm_settings_path
            )
        if not hasattr(self._app_root, "_rfm_settings_window"):
            self._app_root._rfm_settings_window = None
            self._app_root._rfm_settings_tab = "General"
        if not hasattr(self._app_root, "_rfm_tuning_cache"):
            self._app_root._rfm_tuning_cache = {}
        self.settings: ViewerSettings = self._app_root._rfm_settings
        super().__init__(self._app_root)
        windows = getattr(self._app_root, "_rfm_viewer_windows", None)
        if windows is None:
            windows = []
            self._app_root._rfm_viewer_windows = windows
        windows.append(self)
        if not hasattr(self._app_root, "_rfm_pairing_enabled"):
            self._app_root._rfm_pairing_enabled = False
            self._app_root._rfm_pairing_state = None
            self._app_root._rfm_pairing_broadcasting = False
        self._quitting = False
        self._viewer_ready = False
        self._pair_apply_in_progress = False
        self._pair_last_local_state: ViewerSyncState | None = None
        self._startup_after: str | None = None
        self._startup_poll_after: str | None = None
        self._startup_generation = 0
        self._startup_result_queue: queue.SimpleQueue[
            tuple[int, Path, RFMappingData | None, Exception | None]
        ] = queue.SimpleQueue()
        self._startup_loading_frame: ttk.Frame | None = None
        self._startup_progress: ttk.Progressbar | None = None
        self._startup_chooser_frame: ttk.Frame | None = None
        self._optional_autoload_after: str | None = None
        self._optional_poll_after: str | None = None
        self._optional_autoload_generation = 0
        self._optional_result_queue: queue.SimpleQueue[dict[str, object]] = (
            queue.SimpleQueue()
        )
        self._waveform_poll_after: str | None = None
        self._waveform_generation = 0
        self._waveform_result_queue: queue.SimpleQueue[dict[str, object]] = (
            queue.SimpleQueue()
        )
        self._redraw_after: str | None = None
        self._focus_after: str | None = None
        self._optional_redraw_after: str | None = None
        self._optional_redraw_dirty: set[str] = set()
        self._pending_open_documents: list[Path] = []
        self._show_settings_when_ready = False
        self.title(f"RF Map Viewer {APP_DISPLAY_VERSION}")
        self.withdraw()
        self._install_application_handlers()

        if data is not None:
            self._initialize_viewer(data)
        elif startup_path is not None:
            self._show_startup_loading_shell(startup_path)
            self._startup_after = self.after(
                STARTUP_EVENT_WAIT_MS,
                lambda path=startup_path: self._load_startup_document(path),
            )
        else:
            self._show_startup_chooser_shell()
            # Give Finder's OpenDocument Apple event a short chance to replace
            # this callback before a direct launch opens the modal chooser.
            self._startup_after = self.after(200, self._open_startup_file_dialog)

    def _initialize_viewer(self, data: RFMappingData) -> None:
        self._remove_startup_chooser_shell()
        self._remove_startup_loading_shell()
        self.data = data
        self.settings = self._app_root._rfm_settings
        self.title(f"{data.path.name} — RF Map Viewer {APP_DISPLAY_VERSION}")
        self.geometry("1440x900")
        self.minsize(1120, 720)

        self.unit_idx = tk.IntVar(value=0)
        self._selected_unit_id = data.unit_pool[0]
        self._last_supported_unit_id = data.unit_pool[0]
        value_mode = self.settings.rf_value_mode
        if not data.supports_value_mode(value_mode):
            value_mode = VALUE_MODE_RATE
        self.value_mode_var = tk.StringVar(value=value_mode)
        self.bin_var = tk.IntVar(value=0)
        self.range_start_var = tk.IntVar(value=0)
        self.range_end_var = tk.IntVar(value=data.n_bins - 1)
        plot_start_ms, plot_end_ms = self._default_plot_time_bounds_ms()
        self.range_start_ms_var = tk.StringVar(value=format_ms(plot_start_ms))
        self.range_end_ms_var = tk.StringVar(value=format_ms(plot_end_ms))
        self.flip_y_var = tk.BooleanVar(value=self.settings.rf_flip_y)
        self.palette_var = tk.StringVar(value=self.settings.rf_palette)
        self.polar_radius_var = tk.StringVar(value=self.settings.rf_polar_radius)
        self.polar_layout_var = tk.BooleanVar(value=self.settings.rf_polar_layout)
        self.rgb_mode_var = tk.BooleanVar(value=self.settings.rf_rgb_mode)
        self.pair_windows_var = tk.BooleanVar(
            value=bool(getattr(self._app_root, "_rfm_pairing_enabled", False))
        )
        self.x_bins_var = tk.IntVar(
            value=min(data.n_x, self.settings.rf_x_bins or data.n_x)
        )
        self.y_bins_var = tk.IntVar(
            value=min(data.n_y, self.settings.rf_y_bins or data.n_y)
        )
        self.time_res_ms_var = tk.StringVar(
            value=format_ms(max(self._base_bin_ms(), self.settings.rf_time_resolution_ms))
        )
        self._last_time_group_count = data.n_bins
        self._last_time_groups = [(index, index) for index in range(data.n_bins)]
        self.smooth_radius_var = tk.IntVar(value=self.settings.rf_smooth_radius)
        self.show_tuning_curve_var = tk.BooleanVar(value=self.settings.show_tuning_curve)
        self.show_waveform_var = tk.BooleanVar(value=self.settings.show_waveform)
        self.show_probe_layout_var = tk.BooleanVar(value=self.settings.show_probe_layout)
        self.tuning_plot_mode_var = tk.StringVar(value=self.settings.tuning_plot_mode)
        self.tuning_layout_var = tk.StringVar(value=self.settings.tuning_layout)
        self.tuning_display_bins_var = tk.IntVar(value=self.settings.tuning_display_bins)
        self.tuning_smoothing_var = tk.BooleanVar(value=self.settings.tuning_smoothing)
        self.tuning_smooth_sigma_var = tk.DoubleVar(value=self.settings.tuning_smooth_sigma)
        self.tuning_compare_scale_var = tk.BooleanVar(
            value=self.settings.tuning_compare_scale
        )
        self.waveform_channel_mode_var = tk.StringVar(
            value=self.settings.waveform_channel_mode
        )
        self.selected_cell: CellRef | None = None
        self.hover_cell: CellRef | None = None
        self.json_paths: list[Path] = []
        self._json_choice_to_path: dict[str, Path] = {}
        self._canvas_layouts: dict[str, dict[str, object]] = {}
        self._timeline_cells: list[dict[str, object]] = []
        self._timeline_cells_by_bin: dict[int, dict[str, object]] = {}
        self._timeline_preview_cache_key: tuple[object, ...] | None = None
        self._timeline_preview_images: dict[int, object] = {}
        self._timeline_preview_high = 1.0
        self._timeline_range_anchor: int | None = None
        self._timeline_scroll_fraction = 0.0
        self._restoring_timeline_scroll = False
        self._tab_keys: dict[str, str] = {}
        self._hover_signature: tuple[object, ...] | None = None
        self._hover_tooltip_text = ""
        self.probe_geometry: ProbeGeometry | None = None
        self.tuning_curve_data: TuningCurveData | None = None
        self._tuning_curve_error: str | None = None
        self._tuning_curve_candidate: Path | None = None
        self._tuning_processed_cache: tuple[object, ...] | None = None
        self._tuning_scale_cache: tuple[object, ...] | None = None
        self._probe_static_signature: tuple[object, ...] | None = None
        self.waveform_payload: Mapping[str, object] | None = None
        self._waveform_payload_key: tuple[int, str] | None = None
        self._waveform_loading_key: tuple[int, str] | None = None
        self._waveform_error: str | None = None
        self._waveform_error_key: tuple[int, str] | None = None
        self.spatial_region: SpatialRegion | None = None
        self._probe_drag_start: tuple[float, float] | None = None
        self._probe_drag_moved = False
        self._probe_canvas_transform: tuple[float, float, float, float] | None = None
        self.probe_collapsed_var = tk.BooleanVar(value=False)
        self.tuning_collapsed_var = tk.BooleanVar(value=False)
        self.display_expanded_var = tk.BooleanVar(value=False)

        self._build_style()
        self._build_layout()
        self._build_menu()
        self._wire_events()
        self._sync_optional_view_visibility(redraw=False)
        self._sync_json_menu()
        self._sync_unit_combo()
        self._select_tab_key(self.settings.default_viewer_tab)
        self._update_all()
        self._viewer_ready = True
        self._pair_ready_viewer_set_changed(adopt_viewer=self)
        self.deiconify()
        allow_macos_fullscreen_resize(self)
        self.lift()
        self._focus_after = self.after_idle(self._focus_rf_canvas)
        self._schedule_optional_autoload()
        pending_documents = tuple(self._pending_open_documents)
        self._pending_open_documents.clear()
        for pending_path in pending_documents:
            self._open_external_companion(pending_path)
        if self._show_settings_when_ready:
            self._show_settings_when_ready = False
            self.after_idle(self._show_settings)

    def _focus_rf_canvas(self) -> None:
        self._focus_after = None
        try:
            if self.winfo_exists() and self.canvases["rf"].winfo_exists():
                self.canvases["rf"].focus_set()
        except tk.TclError:
            pass

    def _load_startup_document(self, path: Path) -> None:
        self._startup_after = None
        if self._quitting or self._viewer_ready:
            return
        self._startup_generation += 1
        generation = self._startup_generation
        path = Path(path).expanduser()
        self._remove_startup_chooser_shell()
        self._show_startup_loading_shell(path)

        def decode_document() -> None:
            try:
                data = RFMappingData(path)
            except Exception as exc:
                self._startup_result_queue.put((generation, path, None, exc))
            else:
                self._startup_result_queue.put((generation, path, data, None))

        threading.Thread(
            target=decode_document,
            name=f"rf-map-load-{generation}",
            daemon=True,
        ).start()
        self._schedule_startup_result_poll()

    def _show_startup_loading_shell(self, path: Path) -> None:
        if self._startup_loading_frame is None:
            self.geometry("560x190")
            self.minsize(480, 170)
            frame = ttk.Frame(self, padding=24)
            frame.pack(fill="both", expand=True)
            frame.columnconfigure(0, weight=1)
            ttk.Label(
                frame,
                text="Opening RF mapping data",
                font=("TkDefaultFont", 15, "bold"),
            ).grid(row=0, column=0, sticky="w")
            self._startup_path_label = ttk.Label(
                frame,
                text="",
                foreground="#667085",
                wraplength=500,
            )
            self._startup_path_label.grid(row=1, column=0, sticky="ew", pady=(8, 16))
            progress = ttk.Progressbar(frame, mode="indeterminate")
            progress.grid(row=2, column=0, sticky="ew")
            progress.start(12)
            ttk.Label(
                frame,
                text="Decoding and validating counts off the interface thread…",
                foreground="#667085",
            ).grid(row=3, column=0, sticky="w", pady=(10, 0))
            self._startup_loading_frame = frame
            self._startup_progress = progress
        self._startup_path_label.configure(text=path.name)
        self.title(f"Opening {path.name} — RF Map Viewer {APP_DISPLAY_VERSION}")
        self.deiconify()
        self.lift()

    def _show_startup_chooser_shell(self) -> None:
        """Show the no-document landing view behind the native file chooser."""

        if self._startup_chooser_frame is not None:
            return
        self.geometry("560x230")
        self.minsize(480, 210)
        frame = ttk.Frame(self, padding=28)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        ttk.Label(
            frame,
            text="Open RF mapping data",
            font=("TkDefaultFont", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            frame,
            text=(
                "Choose a current .rfmap or JSON result. The viewer never "
                "loads sample data when opened without a document."
            ),
            foreground="#667085",
            wraplength=500,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(10, 18))
        ttk.Button(
            frame,
            text="Open RF Map…",
            command=self._open_json,
        ).grid(row=2, column=0, sticky="w")
        self._startup_chooser_frame = frame
        self.title(f"RF Map Viewer {APP_DISPLAY_VERSION}")
        self.deiconify()
        self.lift()

    def _remove_startup_chooser_shell(self) -> None:
        if self._startup_chooser_frame is not None:
            self._startup_chooser_frame.destroy()
            self._startup_chooser_frame = None

    def _open_startup_file_dialog(self) -> None:
        self._startup_after = None
        if not self._quitting and not self._viewer_ready:
            self._open_json()

    def _remove_startup_loading_shell(self) -> None:
        if self._startup_progress is not None:
            self._startup_progress.stop()
            self._startup_progress = None
        if self._startup_loading_frame is not None:
            self._startup_loading_frame.destroy()
            self._startup_loading_frame = None

    def _schedule_startup_result_poll(self) -> None:
        if self._startup_poll_after is None:
            self._startup_poll_after = self.after(30, self._poll_startup_result)

    def _poll_startup_result(self) -> None:
        self._startup_poll_after = None
        matching: tuple[int, Path, RFMappingData | None, Exception | None] | None = None
        while True:
            try:
                candidate = self._startup_result_queue.get_nowait()
            except queue.Empty:
                break
            if candidate[0] == self._startup_generation:
                matching = candidate
        if matching is None:
            if not self._quitting and not self._viewer_ready:
                self._schedule_startup_result_poll()
            return
        _generation, _path, data, error = matching
        if error is not None:
            messagebox.showerror("Could not open RF map", str(error), parent=self)
            self._quit_application()
            return
        assert data is not None
        self._initialize_viewer(data)

    def _cancel_startup_callback(self) -> None:
        self._startup_generation = getattr(self, "_startup_generation", 0) + 1
        if self._startup_after is None:
            pass
        else:
            try:
                self.after_cancel(self._startup_after)
            except tk.TclError:
                pass
            self._startup_after = None
        if getattr(self, "_startup_poll_after", None) is not None:
            try:
                self.after_cancel(self._startup_poll_after)
            except tk.TclError:
                pass
            self._startup_poll_after = None

    def destroy(self) -> None:
        if (
            not getattr(self._app_root, "_rfm_quitting", False)
            and _active_export_jobs(self._app_root, self)
        ):
            messagebox.showinfo(
                "Export is running",
                "Wait for this window's export to finish before closing it.",
                parent=self,
            )
            return
        self._cancel_startup_callback()
        if self._optional_autoload_after is not None:
            try:
                self.after_cancel(self._optional_autoload_after)
            except tk.TclError:
                pass
            self._optional_autoload_after = None
        self._optional_autoload_generation += 1
        if self._optional_poll_after is not None:
            try:
                self.after_cancel(self._optional_poll_after)
            except tk.TclError:
                pass
            self._optional_poll_after = None
        self._waveform_generation += 1
        if self._waveform_poll_after is not None:
            try:
                self.after_cancel(self._waveform_poll_after)
            except tk.TclError:
                pass
            self._waveform_poll_after = None
        if self._redraw_after is not None:
            try:
                self.after_cancel(self._redraw_after)
            except tk.TclError:
                pass
            self._redraw_after = None
        if self._optional_redraw_after is not None:
            try:
                self.after_cancel(self._optional_redraw_after)
            except tk.TclError:
                pass
            self._optional_redraw_after = None
        if self._focus_after is not None:
            try:
                self.after_cancel(self._focus_after)
            except tk.TclError:
                pass
            self._focus_after = None
        windows = getattr(self._app_root, "_rfm_viewer_windows", [])
        if self in windows:
            windows.remove(self)
        if not getattr(self._app_root, "_rfm_quitting", False):
            self._pair_ready_viewer_set_changed()
        try:
            super().destroy()
        except tk.TclError:
            return
        if getattr(self._app_root, "_rfm_quitting", False):
            return
        if windows:
            windows[-1]._install_application_handlers()
            return
        try:
            self._app_root._rfm_quitting = True
            _shutdown_export_executor(self._app_root)
            self._app_root.destroy()
        except tk.TclError:
            pass

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if sys.platform == "darwin" and "aqua" in style.theme_names():
            style.theme_use("aqua")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f5f5f7")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Sidebar.TFrame", background="#eef0f4")
        style.configure("Toolbar.TFrame", background="#f5f5f7")
        style.configure("TLabel", background="#f5f5f7", foreground="#1d1d1f")
        style.configure("Panel.TLabel", background="#ffffff", foreground="#1d1d1f")
        style.configure("Sidebar.TLabel", background="#eef0f4", foreground="#1d1d1f")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#6e6e73")
        style.configure("SidebarMuted.TLabel", background="#eef0f4", foreground="#6e6e73")
        style.configure(
            "Section.TLabel",
            background="#eef0f4",
            foreground="#6e6e73",
            font=("TkDefaultFont", 10, "bold"),
        )
        style.configure(
            "Title.TLabel",
            background="#ffffff",
            foreground="#1d1d1f",
            font=("TkDefaultFont", 13, "bold"),
        )
        style.configure(
            "Value.TLabel",
            background="#ffffff",
            foreground="#1d1d1f",
            font=("TkDefaultFont", 11, "bold"),
        )
        style.configure(
            "Status.TLabel",
            background="#f5f5f7",
            foreground="#6e6e73",
            font=("TkDefaultFont", 10),
        )
        style.configure(
            "HDClass1.TLabel",
            background="#fff3c4",
            foreground="#805b00",
            font=("TkDefaultFont", 10, "bold"),
            padding=(6, 2),
        )
        style.configure(
            "HDClass2.TLabel",
            background="#dff5e8",
            foreground="#08783f",
            font=("TkDefaultFont", 10, "bold"),
            padding=(6, 2),
        )
        style.configure("TButton", padding=(7, 4))
        style.configure("TNotebook", background="#ffffff", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 7))
        self._pane_icons = {
            placement: self._make_pane_icon(placement)
            for placement in ("leading", "trailing", "bottom")
        }

    def _make_pane_icon(self, placement: str) -> tk.PhotoImage:
        """Draw a compact sidebar/split-pane icon without font glyph arrows."""

        image = tk.PhotoImage(master=self, width=18, height=18)
        outline = "#667085"
        panel = "#98a2b3"
        interior = "#f8fafc"
        image.put(outline, to=(2, 3, 16, 15))
        image.put(interior, to=(3, 4, 15, 14))
        if placement == "leading":
            image.put(panel, to=(3, 4, 7, 14))
            image.put(outline, to=(7, 4, 8, 14))
        elif placement == "trailing":
            image.put(panel, to=(11, 4, 15, 14))
            image.put(outline, to=(10, 4, 11, 14))
        elif placement == "bottom":
            image.put(panel, to=(3, 11, 15, 14))
            image.put(outline, to=(3, 10, 15, 11))
        else:
            raise ValueError(f"Unsupported pane icon placement: {placement}")
        return image

    def _build_menu(self) -> None:
        menu = tk.Menu(self)

        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(
            label="Open RF Map in New Window…",
            accelerator="⌘O" if sys.platform == "darwin" else "Ctrl+O",
            command=self._open_json,
        )
        self._discovered_json_menu = tk.Menu(file_menu, tearoff=False)
        file_menu.add_cascade(
            label="Open Discovered RF Map",
            menu=self._discovered_json_menu,
        )
        file_menu.add_command(
            label="Attach Probe Geometry…",
            command=self._attach_probe_geometry,
        )
        file_menu.add_command(
            label="Export Figures…",
            accelerator="⌘E" if sys.platform == "darwin" else "Ctrl+E",
            command=self._open_figure_exporter,
        )
        file_menu.add_command(
            label="Attach Tuning Curves…",
            command=self._attach_tuning_curve,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Export Displayed Data CSV…",
            accelerator="⇧⌘E" if sys.platform == "darwin" else "Ctrl+Shift+E",
            command=self._export_current_matrix,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Close Window",
            accelerator="⌘W" if sys.platform == "darwin" else "Ctrl+W",
            command=self._close_window,
        )
        menu.add_cascade(label="File", menu=file_menu)
        self._file_menu = file_menu

        navigate_menu = tk.Menu(menu, tearoff=False)
        navigate_menu.add_command(label="Previous Unit", accelerator="←  or  [", command=lambda: self._step_unit(-1))
        navigate_menu.add_command(label="Next Unit", accelerator="→  or  ]", command=lambda: self._step_unit(1))
        navigate_menu.add_separator()
        navigate_menu.add_command(label="Previous Timeline Bin", accelerator="↑", command=lambda: self._step_timeline_bin(-1))
        navigate_menu.add_command(label="Next Timeline Bin", accelerator="↓", command=lambda: self._step_timeline_bin(1))
        navigate_menu.add_command(
            label="Decrease Time Resolution",
            accelerator="⇧,",
            command=lambda: self._step_time_resolution(1.0),
        )
        navigate_menu.add_command(
            label="Increase Time Resolution",
            accelerator="⇧.",
            command=lambda: self._step_time_resolution(-1.0),
        )
        navigate_menu.add_separator()
        navigate_menu.add_command(
            label="Show Full Timeline Range",
            accelerator="Esc",
            command=self._clear_timeline_selection,
        )
        menu.add_cascade(label="Navigate", menu=navigate_menu)
        self._navigate_menu = navigate_menu

        view_menu = tk.Menu(menu, tearoff=False)
        for tab_index, title in enumerate(("RF", "Delay / RGB", "Timeline")):
            view_menu.add_command(
                label=title,
                accelerator=str(tab_index + 1),
                command=lambda index=tab_index: self._select_tab(index),
            )
        view_menu.add_separator()
        view_menu.add_command(label="Invert Y", accelerator="F", command=self._toggle_flip_y)
        view_menu.add_command(label="Cycle Palette", accelerator="P", command=self._cycle_palette)
        if sys.platform != "darwin":
            view_menu.add_separator()
            view_menu.add_command(
                label="Settings…",
                accelerator="Ctrl+,",
                command=self._show_settings,
            )
        menu.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menu, name="help", tearoff=False)
        help_menu.add_command(label="Keyboard Shortcuts", accelerator="?", command=self._show_shortcuts)
        help_menu.add_separator()
        help_menu.add_command(
            label="Support Documentation",
            command=self._open_support_documentation,
        )
        menu.add_cascade(label="Help", menu=help_menu)
        self._help_menu = help_menu
        self.configure(menu=menu)
        self._menu = menu

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 10))
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.columnconfigure(0, weight=1)
        self.sidebar_panel = sidebar
        self.sidebar_frame = sidebar
        self.sidebar_collapsed_rail = ttk.Frame(
            self, style="Sidebar.TFrame", padding=(4, 10)
        )
        ttk.Button(
            self.sidebar_collapsed_rail,
            image=self._pane_icons["leading"],
            text="Show sidebar",
            width=2,
            command=self._toggle_probe_collapsed,
        ).grid(row=0, column=0, sticky="n")
        self.sidebar_collapsed_rail.grid(row=0, column=0, sticky="ns")
        self.sidebar_collapsed_rail.grid_remove()

        main = ttk.Frame(self, style="Panel.TFrame")
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        self._build_sidebar(sidebar)
        self._build_main(main)

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        row = 0
        ttk.Label(parent, text="Windows", style="Section.TLabel").grid(
            row=row, column=0, sticky="w", pady=(0, 5)
        )
        row += 1

        ttk.Label(parent, text="Window pairing", style="Panel.TLabel").grid(
            row=row, column=0, sticky="w", pady=(2, 0)
        )
        row += 1
        self.pair_windows_toggle = ttk.Checkbutton(
            parent,
            text="Sync viewer windows",
            variable=self.pair_windows_var,
            command=self._on_pair_windows_toggled,
        )
        self.pair_windows_toggle.grid(row=row, column=0, sticky="w", pady=(0, 5))
        row += 1
        self.pair_status_label = ttk.Label(
            parent,
            text="Open another loaded viewer window to enable sync.",
            style="SidebarMuted.TLabel",
            wraplength=220,
            justify="left",
        )
        self.pair_status_label.grid(row=row, column=0, sticky="ew", pady=(2, 8))
        self.pair_status_label.grid_remove()
        row += 1

        self.probe_section = ttk.Frame(parent, style="Sidebar.TFrame")
        self.probe_section.grid(row=row, column=0, sticky="nsew", pady=(10, 0))
        self.probe_section.columnconfigure(0, weight=1)
        self.probe_section.rowconfigure(1, weight=1)
        self._probe_section_row = row
        parent.rowconfigure(row, weight=1)

        probe_header = ttk.Frame(self.probe_section, style="Sidebar.TFrame")
        probe_header.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        probe_header.columnconfigure(1, weight=1)
        self.probe_fold_button = ttk.Button(
            probe_header,
            image=self._pane_icons["leading"],
            text="Hide sidebar",
            width=2,
            command=self._toggle_probe_collapsed,
        )
        self.probe_fold_button.grid(row=0, column=0, sticky="w", padx=(0, 5))
        ttk.Label(probe_header, text="Probe", style="Section.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        self.clear_spatial_button = ttk.Button(
            probe_header,
            text="Clear",
            width=6,
            command=self._clear_spatial_filter,
        )
        self.clear_spatial_button.grid(row=0, column=2, sticky="e")
        row += 1

        self.probe_canvas = tk.Canvas(
            self.probe_section,
            width=220,
            height=330,
            background="#ffffff",
            highlightthickness=1,
            highlightbackground="#d7d9de",
        )
        self.probe_canvas.grid(row=1, column=0, sticky="nsew")
        self.probe_attach_button = ttk.Button(
            self.probe_canvas,
            text="Choose positions.probe or .csv…",
            command=self._attach_probe_geometry,
        )
        self.spatial_status_label = ttk.Label(
            self.probe_section,
            text="",
            style="SidebarMuted.TLabel",
            wraplength=220,
            justify="left",
        )
        self.spatial_status_label.grid(row=2, column=0, sticky="ew", pady=(5, 10))
        row += 1

        ttk.Label(parent, text="Selection", style="Section.TLabel").grid(
            row=row, column=0, sticky="w", pady=(8, 5)
        )
        row += 1
        self.cell_label = ttk.Label(
            parent,
            text="",
            style="SidebarMuted.TLabel",
            font=("TkFixedFont", 10),
            wraplength=220,
            justify="left",
        )
        self.cell_label.grid(row=row, column=0, sticky="ew")
        row += 1
        self.unit_stats_label = ttk.Label(
            parent,
            text="",
            style="SidebarMuted.TLabel",
            wraplength=220,
            justify="left",
        )
        self.unit_stats_label.grid(row=row, column=0, sticky="ew", pady=(2, 0))

    def _build_main(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent, style="Toolbar.TFrame", padding=(10, 7))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(4, weight=1)

        self.previous_unit_button = ttk.Button(
            toolbar,
            text="‹",
            width=3,
            command=lambda: self._step_unit(-1),
        )
        self.previous_unit_button.grid(row=0, column=0, padx=(0, 4))
        self.unit_combo = ttk.Combobox(toolbar, state="readonly", width=27)
        self.unit_combo.grid(row=0, column=1, sticky="w")
        self.next_unit_button = ttk.Button(
            toolbar,
            text="›",
            width=3,
            command=lambda: self._step_unit(1),
        )
        self.next_unit_button.grid(row=0, column=2, padx=(4, 0))
        ttk.Separator(toolbar, orient="vertical").grid(
            row=0, column=3, sticky="ns", padx=10
        )

        # Kept as a data-bearing widget for the update path; the unit picker
        # already exposes the same context, so repeating it would add chrome.
        self.header_label = ttk.Label(toolbar, text="", style="Status.TLabel")
        self.open_toolbar_button = ttk.Button(
            toolbar,
            text="Open…",
            command=self._open_json,
        )
        self.open_toolbar_button.grid(row=0, column=5, padx=(8, 4))
        self.export_toolbar_button = ttk.Button(
            toolbar,
            text="Figures…",
            command=self._open_figure_exporter,
        )
        self.export_toolbar_button.grid(row=0, column=6)

        self._build_plot_controls(parent)

        self.notebook = ttk.Notebook(parent)
        self.notebook.grid(row=2, column=0, sticky="nsew")

        self.status_label = ttk.Label(
            parent,
            text="",
            style="Status.TLabel",
            anchor="w",
            padding=(10, 4),
        )
        self.status_label.grid(row=3, column=0, sticky="ew")

        self.canvases: dict[str, tk.Canvas] = {}
        self._tab_keys = {}
        for key, title in (
            ("rf", "RF"),
            ("delay", "Delay / RGB"),
            ("timeline", "Timeline"),
        ):
            frame = ttk.Frame(self.notebook)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            if key == "rf":
                self.rf_tab_frame = frame
                self.rf_split_container = ttk.Frame(frame)
                self.rf_split_container.grid(row=0, column=0, sticky="nsew")
                self._rf_split_responsive_stacked = False
                self.rf_split_container.bind(
                    "<Configure>",
                    self._on_rf_split_configure,
                    add="+",
                )
                self.rf_map_pane = ttk.Frame(
                    self.rf_split_container,
                    style="Panel.TFrame",
                )
                self.rf_map_pane.columnconfigure(0, weight=1)
                self.rf_map_pane.rowconfigure(1, weight=1)
                rf_header = ttk.Frame(
                    self.rf_map_pane,
                    style="Panel.TFrame",
                    padding=(12, 9),
                )
                rf_header.grid(row=0, column=0, sticky="ew")
                rf_header.columnconfigure(0, weight=1)
                ttk.Label(
                    rf_header,
                    text="RF Map",
                    style="Title.TLabel",
                ).grid(row=0, column=0, sticky="w")
                self.rf_map_subtitle_label = ttk.Label(
                    rf_header,
                    text="",
                    style="Muted.TLabel",
                    font=("TkDefaultFont", 10),
                )
                self.rf_map_subtitle_label.grid(
                    row=1, column=0, sticky="w", pady=(2, 0)
                )
                canvas = tk.Canvas(
                    self.rf_map_pane,
                    background="#ffffff",
                    highlightthickness=0,
                )
                canvas.grid(row=1, column=0, sticky="nsew")

                self.tuning_curve_pane = ttk.Frame(
                    self.rf_split_container,
                    style="Panel.TFrame",
                )
                self.tuning_curve_pane.columnconfigure(0, weight=1)
                self.tuning_curve_section = ttk.Frame(
                    self.tuning_curve_pane,
                    style="Panel.TFrame",
                )
                self.tuning_curve_section.columnconfigure(0, weight=1)
                self.tuning_curve_section.rowconfigure(1, weight=1)
                tuning_header = ttk.Frame(
                    self.tuning_curve_section,
                    style="Panel.TFrame",
                    padding=(12, 9),
                )
                tuning_header.grid(row=0, column=0, sticky="ew")
                tuning_header.columnconfigure(0, weight=1)
                ttk.Label(
                    tuning_header,
                    text="HD Tuning Curve",
                    style="Title.TLabel",
                ).grid(row=0, column=0, sticky="w")
                self.tuning_cluster_label = ttk.Label(
                    tuning_header,
                    text="",
                    style="Muted.TLabel",
                    font=("TkDefaultFont", 10),
                )
                self.tuning_cluster_label.grid(
                    row=1, column=0, sticky="w", pady=(2, 0)
                )
                self.tuning_hd_class_label = ttk.Label(
                    tuning_header,
                    text="",
                    style="Panel.TLabel",
                    width=2,
                    anchor="center",
                )
                self.tuning_hd_class_label.grid(
                    row=0,
                    column=1,
                    sticky="e",
                    padx=(6, 4),
                )
                self.tuning_hd_class_label.grid_remove()
                self.tuning_provenance_button = ttk.Button(
                    tuning_header,
                    text="Info",
                    width=4,
                    command=self._show_tuning_provenance,
                )
                self.tuning_provenance_button.grid(
                    row=0,
                    column=2,
                    sticky="e",
                    padx=(4, 4),
                )
                self.tuning_provenance_button.grid_remove()
                self.tuning_fold_button = ttk.Button(
                    tuning_header,
                    image=self._pane_icons["trailing"],
                    text="Collapse HD tuning curve",
                    width=2,
                    command=self._toggle_tuning_collapsed,
                )
                self.tuning_fold_button.grid(row=0, column=3, sticky="e")
                self.tuning_curve_canvas = tk.Canvas(
                    self.tuning_curve_section,
                    background="#ffffff",
                    highlightthickness=0,
                )
                self.tuning_curve_canvas.grid(row=1, column=0, sticky="nsew")
                self.tuning_attach_button = ttk.Button(
                    self.tuning_curve_canvas,
                    text="Choose tuning_curves.tc or .json…",
                    command=self._attach_tuning_curve,
                )
                self.tuning_curve_status_label = ttk.Label(
                    self.tuning_curve_section,
                    text="",
                    style="Muted.TLabel",
                    wraplength=360,
                    justify="left",
                )
                self.tuning_curve_status_label.grid(
                    row=2,
                    column=0,
                    sticky="ew",
                    padx=12,
                    pady=(5, 7),
                )

                self.waveform_pane = ttk.Frame(
                    self.tuning_curve_pane,
                    style="Panel.TFrame",
                )
                self.waveform_pane.columnconfigure(0, weight=1)
                self.waveform_pane.rowconfigure(2, weight=1)
                ttk.Separator(self.waveform_pane, orient="horizontal").grid(
                    row=0, column=0, sticky="ew"
                )
                waveform_header = ttk.Frame(
                    self.waveform_pane,
                    style="Panel.TFrame",
                    padding=(12, 7),
                )
                waveform_header.grid(row=1, column=0, sticky="ew")
                waveform_header.columnconfigure(0, weight=1)
                ttk.Label(
                    waveform_header,
                    text="Local Average Waveform",
                    style="Title.TLabel",
                ).grid(row=0, column=0, sticky="w")
                self.waveform_subtitle_label = ttk.Label(
                    waveform_header,
                    text="",
                    style="Muted.TLabel",
                    font=("TkDefaultFont", 9),
                    wraplength=330,
                    justify="left",
                )
                self.waveform_subtitle_label.grid(
                    row=1, column=0, sticky="w", pady=(1, 0)
                )
                self.waveform_fold_button = ttk.Button(
                    waveform_header,
                    image=self._pane_icons["trailing"],
                    text="Collapse auxiliary plots",
                    width=2,
                    command=self._toggle_tuning_collapsed,
                )
                self.waveform_fold_button.grid(row=0, column=1, sticky="e")
                self.waveform_canvas = tk.Canvas(
                    self.waveform_pane,
                    background="#ffffff",
                    highlightthickness=0,
                    # Header, subtitle, and separator bring the complete pane
                    # to roughly 180 px, leaving TC larger on short displays.
                    height=100,
                )
                self.waveform_canvas.grid(row=2, column=0, sticky="nsew")
                self.canvases["waveform"] = self.waveform_canvas
                self.tuning_collapsed_rail = ttk.Frame(
                    self.rf_split_container,
                    style="Panel.TFrame",
                    padding=(4, 6),
                )
                self.tuning_collapsed_rail.columnconfigure(0, weight=1)
                self.tuning_restore_button = ttk.Button(
                    self.tuning_collapsed_rail,
                    image=self._pane_icons["trailing"],
                    text="Restore HD tuning curve",
                    width=2,
                    command=self._toggle_tuning_collapsed,
                )
                self.tuning_restore_button.grid(row=0, column=0, sticky="n")
                self._sync_auxiliary_sections()
                self._layout_rf_and_tuning()
            else:
                canvas = tk.Canvas(frame, background="#ffffff", highlightthickness=0)
                canvas.grid(row=0, column=0, sticky="nsew")
            if key == "timeline":
                frame.columnconfigure(1, weight=0)
                self.timeline_scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self._timeline_yview)
                self.timeline_scrollbar.grid(row=0, column=1, sticky="ns")
                canvas.configure(yscrollcommand=self._timeline_scroll_set)
            self.notebook.add(frame, text=title)
            self.canvases[key] = canvas
            self._tab_keys[str(frame)] = key

    def _sync_auxiliary_sections(self) -> None:
        """Stack the enabled companion plots, with waveform below tuning."""

        if not hasattr(self, "tuning_curve_section"):
            return
        tuning_visible = bool(self.show_tuning_curve_var.get())
        waveform_visible = bool(self.show_waveform_var.get())
        self.tuning_curve_section.grid_remove()
        self.waveform_pane.grid_remove()
        for index in range(2):
            self.tuning_curve_pane.rowconfigure(index, weight=0, minsize=0)

        if tuning_visible:
            self.tuning_curve_section.grid(row=0, column=0, sticky="nsew")
            self.tuning_curve_pane.rowconfigure(0, weight=1)
            self.tuning_fold_button.grid()
        if waveform_visible:
            waveform_row = 1 if tuning_visible else 0
            self.waveform_pane.grid(row=waveform_row, column=0, sticky="nsew")
            self.tuning_curve_pane.rowconfigure(
                waveform_row,
                weight=0,
                minsize=180,
            )
            if tuning_visible:
                self.waveform_fold_button.grid_remove()
            else:
                self.waveform_fold_button.grid()
                # Keep an enabled waveform compact even when HD is hidden.
                self.tuning_curve_pane.rowconfigure(1, weight=1)

    def _layout_rf_and_tuning(self) -> None:
        """Place RF and the optional tuning/waveform companion stack."""

        if not hasattr(self, "rf_split_container"):
            return
        container = self.rf_split_container
        self.rf_map_pane.grid_forget()
        self.tuning_curve_pane.grid_forget()
        self.tuning_collapsed_rail.grid_forget()
        for index in range(2):
            container.columnconfigure(index, weight=0, uniform="")
            container.rowconfigure(index, weight=0, uniform="")

        auxiliary_visible = bool(
            self.show_tuning_curve_var.get() or self.show_waveform_var.get()
        )
        collapsed = bool(self.tuning_collapsed_var.get())
        stacked = (
            self.tuning_layout_var.get() == "Stacked"
            or bool(getattr(self, "_rf_split_responsive_stacked", False))
        )
        self.rf_map_pane.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        if not auxiliary_visible:
            return
        if collapsed:
            if stacked:
                self.tuning_collapsed_rail.grid(
                    row=1, column=0, sticky="ew", pady=(2, 0)
                )
                self.tuning_restore_button.configure(
                    image=self._pane_icons["bottom"],
                    text="Restore auxiliary plots",
                )
            else:
                self.tuning_collapsed_rail.grid(
                    row=0, column=1, sticky="ns", padx=(2, 0)
                )
                self.tuning_restore_button.configure(
                    image=self._pane_icons["trailing"],
                    text="Restore auxiliary plots",
                )
            return
        if stacked:
            self.tuning_curve_pane.grid(
                row=1, column=0, sticky="nsew", pady=(1, 0)
            )
            container.rowconfigure(0, weight=5, uniform="rf-hd-rows")
            container.rowconfigure(1, weight=3, uniform="rf-hd-rows")
            self.tuning_fold_button.configure(
                image=self._pane_icons["bottom"],
                text="Collapse auxiliary plots",
            )
            self.waveform_fold_button.configure(
                image=self._pane_icons["bottom"],
                text="Collapse auxiliary plots",
            )
        else:
            self.tuning_curve_pane.grid(
                row=0, column=1, sticky="nsew", padx=(1, 0)
            )
            container.columnconfigure(0, weight=5, uniform="rf-hd-columns")
            container.columnconfigure(1, weight=2, uniform="rf-hd-columns")
            self.tuning_fold_button.configure(
                image=self._pane_icons["trailing"],
                text="Collapse auxiliary plots",
            )
            self.waveform_fold_button.configure(
                image=self._pane_icons["trailing"],
                text="Collapse auxiliary plots",
            )

    def _on_rf_split_configure(self, event: tk.Event) -> None:
        # At narrow window widths a side-by-side HD pane would be smaller than
        # its scientific axes. Switch arrangement without changing the saved
        # user preference, then restore it when space returns.
        responsive_stacked = int(event.width) < 1050
        if responsive_stacked == getattr(self, "_rf_split_responsive_stacked", False):
            return
        self._rf_split_responsive_stacked = responsive_stacked
        self._layout_rf_and_tuning()

    def _toggle_probe_collapsed(self) -> None:
        self.probe_collapsed_var.set(not self.probe_collapsed_var.get())
        self._sync_probe_collapsed_state()

    def _sync_probe_collapsed_state(self, *, schedule_redraw: bool = True) -> None:
        if not hasattr(self, "probe_canvas"):
            return
        collapsed = bool(self.probe_collapsed_var.get())
        self.probe_fold_button.configure(
            image=self._pane_icons["leading"],
            text="Hide sidebar",
        )
        if collapsed:
            self.sidebar_panel.grid_remove()
            self.sidebar_collapsed_rail.grid()
        else:
            self.sidebar_collapsed_rail.grid_remove()
            self.sidebar_panel.grid()
            if schedule_redraw and self.__dict__.get("_viewer_ready", False):
                self._schedule_optional_redraw("probe")

    def _toggle_tuning_collapsed(self) -> None:
        self.tuning_collapsed_var.set(not self.tuning_collapsed_var.get())
        self._sync_tuning_collapsed_state()

    def _sync_tuning_collapsed_state(self, *, schedule_redraw: bool = True) -> None:
        if not hasattr(self, "tuning_curve_canvas"):
            return
        collapsed = bool(self.tuning_collapsed_var.get())
        self._sync_auxiliary_sections()
        self._layout_rf_and_tuning()
        if (
            not collapsed
            and schedule_redraw
            and self.__dict__.get("_viewer_ready", False)
        ):
            if self.show_tuning_curve_var.get():
                self._schedule_optional_redraw("tuning")
            if self.show_waveform_var.get() and self._active_tab_key() == "rf":
                self._schedule_redraw()

    def _build_plot_controls(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent, style="Panel.TFrame", padding=(10, 6))
        controls.grid(row=1, column=0, sticky="ew")
        controls.columnconfigure(6, weight=1)
        self.plot_controls_frame = controls

        ttk.Label(controls, text="Metric", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        self.value_mode_combo = ttk.Combobox(
            controls,
            state="readonly",
            values=VALUE_MODES,
            textvariable=self.value_mode_var,
            width=18,
        )
        self.value_mode_combo.grid(row=0, column=1, sticky="w", padx=(0, 10))

        ttk.Separator(controls, orient="vertical").grid(
            row=0, column=2, sticky="ns", padx=(0, 10)
        )
        ttk.Label(controls, text="Target width", style="Panel.TLabel").grid(
            row=0, column=3, sticky="w", padx=(0, 6)
        )
        self.time_res_spin = ttk.Spinbox(
            controls,
            from_=self._base_bin_ms(),
            to=self._total_time_ms(),
            increment=self._base_bin_ms(),
            width=6,
            textvariable=self.time_res_ms_var,
            command=self._on_time_resolution_changed,
        )
        self.time_res_spin.grid(row=0, column=4, sticky="w")
        ttk.Label(controls, text="ms", style="Panel.TLabel").grid(
            row=0, column=5, sticky="w", padx=(4, 12)
        )

        range_controls = ttk.Frame(controls, style="Panel.TFrame")
        range_controls.grid(row=0, column=7, sticky="w")
        self.range_controls_frame = range_controls

        ttk.Label(range_controls, text="RF window", style="Panel.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 6),
        )
        self.range_start_spin = ttk.Spinbox(
            range_controls,
            from_=self._time_axis_start_ms(),
            to=self._time_axis_end_ms(),
            increment=self._base_bin_ms(),
            width=6,
            textvariable=self.range_start_ms_var,
            command=self._on_range_changed,
        )
        self.range_start_spin.grid(row=0, column=1, sticky="w")
        ttk.Label(range_controls, text="–", style="Panel.TLabel").grid(
            row=0, column=2, padx=5
        )
        self.range_end_spin = ttk.Spinbox(
            range_controls,
            from_=self._time_axis_start_ms(),
            to=self._time_axis_end_ms(),
            increment=self._base_bin_ms(),
            width=6,
            textvariable=self.range_end_ms_var,
            command=self._on_range_changed,
        )
        self.range_end_spin.grid(row=0, column=3, sticky="w")
        ttk.Label(range_controls, text="ms", style="Panel.TLabel").grid(
            row=0, column=4, sticky="w", padx=(4, 8)
        )

        self.reset_plot_range_button = ttk.Button(
            range_controls,
            text="Reset",
            command=self._reset_plot_range,
        )
        self.reset_plot_range_button.grid(row=0, column=5, sticky="w")

        self.delay_controls_frame = ttk.Frame(controls, style="Panel.TFrame")
        self.rgb_mode_toggle = ttk.Checkbutton(
            self.delay_controls_frame,
            text="RGB composite",
            variable=self.rgb_mode_var,
            command=self._on_control_changed,
        )
        self.rgb_mode_toggle.grid(row=0, column=0, sticky="w")

        self.timeline_context_frame = ttk.Frame(controls, style="Panel.TFrame")
        ttk.Label(
            self.timeline_context_frame,
            text="Full physical time axis",
            style="Panel.TLabel",
        ).grid(row=0, column=0, sticky="w")

        self.display_toggle_button = ttk.Button(
            controls,
            text="Display Options",
            command=self._toggle_display_controls,
        )
        self.display_toggle_button.grid(row=0, column=8, sticky="e", padx=(10, 0))
        self.display_controls_frame = ttk.Frame(controls, style="Panel.TFrame")
        display = self.display_controls_frame
        for column in (1, 3, 5):
            display.columnconfigure(column, weight=1)

        ttk.Label(display, text="X bins", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.x_bins_spin = ttk.Spinbox(
            display,
            from_=1,
            to=self.data.n_x,
            increment=1,
            width=8,
            textvariable=self.x_bins_var,
            command=self._on_control_changed,
        )
        self.x_bins_spin.grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(display, text="Y bins", style="Panel.TLabel").grid(row=0, column=2, sticky="w")
        self.y_bins_spin = ttk.Spinbox(
            display,
            from_=1,
            to=self.data.n_y,
            increment=1,
            width=8,
            textvariable=self.y_bins_var,
            command=self._on_control_changed,
        )
        self.y_bins_spin.grid(row=0, column=3, sticky="w", padx=(6, 18))
        ttk.Label(display, text="Smooth", style="Panel.TLabel").grid(row=0, column=4, sticky="w")
        self.smooth_spin = ttk.Spinbox(
            display,
            from_=0,
            to=3,
            increment=1,
            width=8,
            textvariable=self.smooth_radius_var,
            command=self._on_control_changed,
        )
        self.smooth_spin.grid(row=0, column=5, sticky="w", padx=(6, 18))

        ttk.Label(display, text="Palette", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Combobox(
            display,
            state="readonly",
            values=PALETTES,
            textvariable=self.palette_var,
            width=12,
        ).grid(row=1, column=1, sticky="w", padx=(6, 18), pady=(8, 0))
        ttk.Label(display, text="Polar radius", style="Panel.TLabel").grid(
            row=1, column=2, sticky="w", pady=(8, 0)
        )
        ttk.Combobox(
            display,
            state="readonly",
            values=POLAR_RADIUS_MODES,
            textvariable=self.polar_radius_var,
            width=18,
        ).grid(row=1, column=3, sticky="w", padx=(6, 18), pady=(8, 0))
        self.polar_layout_toggle = ttk.Checkbutton(
            display,
            text="Polar layout",
            variable=self.polar_layout_var,
            command=self._on_spatial_format_changed,
        )
        self.polar_layout_toggle.grid(row=1, column=4, sticky="w", pady=(8, 0))

    def _wire_events(self) -> None:
        self.unit_combo.bind("<<ComboboxSelected>>", self._on_unit_selected)
        self.value_mode_combo.bind("<<ComboboxSelected>>", self._on_value_mode_changed)
        self.range_start_spin.bind("<Return>", self._on_range_changed)
        self.range_end_spin.bind("<Return>", self._on_range_changed)
        self.range_start_spin.bind("<FocusOut>", self._on_range_changed)
        self.range_end_spin.bind("<FocusOut>", self._on_range_changed)
        self.time_res_spin.bind("<Return>", self._on_time_resolution_changed)
        self.time_res_spin.bind("<FocusOut>", self._on_time_resolution_changed)
        self.x_bins_spin.bind("<Return>", self._on_control_changed)
        self.y_bins_spin.bind("<Return>", self._on_control_changed)
        self.smooth_spin.bind("<Return>", self._on_control_changed)
        self.x_bins_spin.bind("<FocusOut>", self._on_control_changed)
        self.y_bins_spin.bind("<FocusOut>", self._on_control_changed)
        self.smooth_spin.bind("<FocusOut>", self._on_control_changed)
        self.palette_var.trace_add("write", lambda *_: self._on_control_changed())
        self.polar_radius_var.trace_add("write", lambda *_: self._on_control_changed())
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.bind("<FocusIn>", self._on_window_focus, add="+")
        self.bind("<Left>", lambda event: self._run_navigation_shortcut(event, self._step_unit, -1))
        self.bind("<Right>", lambda event: self._run_navigation_shortcut(event, self._step_unit, 1))
        self.bind("<bracketleft>", lambda event: self._run_navigation_shortcut(event, self._step_unit, -1))
        self.bind("<bracketright>", lambda event: self._run_navigation_shortcut(event, self._step_unit, 1))
        self.bind("<Up>", lambda event: self._run_navigation_shortcut(event, self._step_timeline_bin, -1))
        self.bind("<Down>", lambda event: self._run_navigation_shortcut(event, self._step_timeline_bin, 1))
        self.bind("<less>", lambda event: self._run_navigation_shortcut(event, self._step_time_resolution, -1.0))
        self.bind("<greater>", lambda event: self._run_navigation_shortcut(event, self._step_time_resolution, 1.0))
        self.bind("<Escape>", lambda event: self._run_navigation_shortcut(event, self._handle_escape))
        self.bind("<KeyPress-f>", lambda event: self._run_navigation_shortcut(event, self._toggle_flip_y))
        self.bind("<KeyPress-p>", lambda event: self._run_navigation_shortcut(event, self._cycle_palette))
        self.bind("<question>", lambda event: self._run_navigation_shortcut(event, self._show_shortcuts))
        for tab_index in range(3):
            self.bind(
                f"<KeyPress-{tab_index + 1}>",
                lambda event, index=tab_index: self._run_navigation_shortcut(event, self._select_tab, index),
            )
        self.bind("<Control-e>", lambda _event: self._open_figure_exporter())
        self.bind("<Control-Shift-E>", lambda _event: self._export_current_matrix())
        self.bind("<Control-w>", lambda _event: self._close_window())
        if sys.platform == "darwin":
            self.bind("<Command-e>", lambda _event: self._open_figure_exporter())
            self.bind("<Command-Shift-E>", lambda _event: self._export_current_matrix())
            self.bind("<Command-w>", lambda _event: self._close_window())
        for key, canvas in self.canvases.items():
            canvas.bind("<Configure>", self._schedule_redraw)
            canvas.bind("<Motion>", lambda event, k=key: self._on_canvas_motion(k, event))
            canvas.bind("<Button-1>", lambda event, k=key: self._on_canvas_click(k, event))
            canvas.bind("<Leave>", lambda _event: self._clear_hover())
        self.canvases["timeline"].bind("<MouseWheel>", self._on_timeline_mousewheel)
        self.canvases["timeline"].bind("<Button-4>", self._on_timeline_mousewheel)
        self.canvases["timeline"].bind("<Button-5>", self._on_timeline_mousewheel)
        self.probe_canvas.bind(
            "<Configure>", lambda _event: self._schedule_optional_redraw("probe")
        )
        self.probe_canvas.bind("<ButtonPress-1>", self._on_probe_press)
        self.probe_canvas.bind("<B1-Motion>", self._on_probe_drag)
        self.probe_canvas.bind("<ButtonRelease-1>", self._on_probe_release)
        self.tuning_curve_canvas.bind(
            "<Configure>", lambda _event: self._schedule_optional_redraw("tuning")
        )
        self.tuning_curve_canvas.bind("<Button-1>", self._on_tuning_curve_click)
        self._install_optional_drop_targets()

    def _toggle_display_controls(self) -> None:
        expanded = not self.display_expanded_var.get()
        self.display_expanded_var.set(expanded)
        self.display_toggle_button.configure(
            text="Hide Display Options" if expanded else "Display Options"
        )
        if expanded:
            self.display_controls_frame.grid(
                row=1, column=0, columnspan=9, sticky="ew", pady=(7, 0)
            )
        else:
            self.display_controls_frame.grid_remove()

    def _handle_escape(self) -> None:
        if self.spatial_region is not None:
            self._clear_spatial_filter()
        else:
            self._clear_timeline_selection()

    def _on_window_focus(self, _event: object | None = None) -> None:
        self._app_root._rfm_active_viewer = self

    def _shortcut_uses_editing_widget(self, event: object) -> bool:
        widget = getattr(event, "widget", None)
        return isinstance(widget, (tk.Entry, tk.Text, ttk.Entry, ttk.Spinbox, ttk.Combobox))

    def _run_navigation_shortcut(
        self,
        event: object,
        action: Callable[..., object],
        *args: object,
    ) -> str | None:
        if self._shortcut_uses_editing_widget(event):
            return None
        action(*args)
        return "break"

    def _select_tab(self, tab_index: int) -> None:
        if not hasattr(self, "notebook"):
            return
        tabs = self.notebook.tabs()
        if 0 <= tab_index < len(tabs):
            self.notebook.select(tab_index)

    def _toggle_flip_y(self) -> None:
        self.flip_y_var.set(not self.flip_y_var.get())
        self._on_control_changed()

    def _cycle_palette(self) -> None:
        try:
            index = PALETTES.index(self.palette_var.get())
        except ValueError:
            index = 0
        self.palette_var.set(PALETTES[(index + 1) % len(PALETTES)])

    def _show_shortcuts(self) -> None:
        primary = "Command" if sys.platform == "darwin" else "Ctrl"
        messagebox.showinfo(
            "Keyboard Shortcuts",
            "← / →   Previous / next unit\n"
            "↑ / ↓   Previous / next timeline bin\n"
            "Shift+, / Shift+.   Coarser / finer by one source bin\n"
            "1–3   Switch plot tab\n"
            "F   Invert Y\n"
            "P   Cycle palette\n"
            "Esc   Show Full Timeline Range\n"
            "[ / ]   Previous / next unit (legacy)\n"
            "Command-O   Open an RF map in a new window\n"
            "Command-E   Open figure exporter\n"
            "Shift-Command-E   Export displayed data CSV\n"
            "Command-W   Close current window",
            parent=self,
        )

    def _open_support_documentation(self) -> None:
        path = support_documentation_path()
        if path is None:
            messagebox.showerror(
                "Support Documentation",
                "The local README.md could not be found in this installation.",
                parent=self,
            )
            return
        try:
            opened = webbrowser.open(path.as_uri())
        except (OSError, webbrowser.Error) as exc:
            messagebox.showerror(
                "Support Documentation",
                f"Could not open {path.name}:\n\n{exc}",
                parent=self,
            )
            return
        if not opened:
            messagebox.showerror(
                "Support Documentation",
                f"Could not open the local documentation:\n\n{path}",
                parent=self,
            )

    def _install_application_handlers(self) -> None:
        self.protocol("WM_DELETE_WINDOW", self._close_window)
        self._app_root._rfm_active_viewer = self
        self.bind_all("<Control-o>", self._dispatch_open_json)
        settings_callback = getattr(self, "_dispatch_settings", lambda *_args: None)
        help_callback = getattr(
            self, "_open_support_documentation", lambda *_args: None
        )
        self.bind_all("<Control-comma>", settings_callback)

        if sys.platform != "darwin":
            return
        try:
            self.bind_all("<Command-o>", self._dispatch_open_json)
            self.bind_all("<Command-comma>", settings_callback)
            self.tk.createcommand("::tk::mac::OpenDocument", self._dispatch_macos_open_documents)
            self.tk.createcommand("::tk::mac::Quit", self._quit_application)
            self.tk.createcommand("::tk::mac::ShowPreferences", settings_callback)
            self.tk.createcommand("::tk::mac::ShowHelp", help_callback)
        except tk.TclError:
            # The in-app Open button and window close protocol remain usable
            # if this Tk build does not expose the macOS application callbacks.
            return

    def _active_viewer(self) -> RFMViewer:
        active = getattr(self._app_root, "_rfm_active_viewer", None)
        windows = getattr(self._app_root, "_rfm_viewer_windows", [])
        return active if active in windows else (windows[-1] if windows else self)

    def _ready_pairing_viewers(self) -> list[RFMViewer]:
        windows = getattr(self._app_root, "_rfm_viewer_windows", [])
        return [
            window
            for window in windows
            if getattr(window, "_viewer_ready", False) and hasattr(window, "data")
        ]

    def _pairing_unit_ids(
        self,
        ready: list[RFMViewer] | None = None,
    ) -> list[int]:
        viewers = self._ready_pairing_viewers() if ready is None else ready
        return sorted(
            {
                int(unit_id)
                for window in viewers
                for unit_id in window.data.unit_pool
            }
        )

    @staticmethod
    def _unit_lists_match(ready: list[RFMViewer]) -> bool:
        if len(ready) < 2:
            return True
        first_units = tuple(int(unit_id) for unit_id in ready[0].data.unit_pool)
        return all(
            tuple(int(unit_id) for unit_id in window.data.unit_pool) == first_units
            for window in ready[1:]
        )

    @staticmethod
    def _next_union_unit_id(unit_ids: list[int], requested: int) -> int:
        if not unit_ids:
            raise ValueError("Cannot select a unit from an empty unit union")
        requested = int(requested)
        if requested in unit_ids:
            return requested
        return next((unit_id for unit_id in unit_ids if unit_id > requested), unit_ids[0])

    def _local_unit_index(self, unit_id: int) -> int | None:
        lookup = getattr(self.data, "rf_map_by_unit_id", None)
        if callable(lookup):
            try:
                return lookup(int(unit_id)).unit_index
            except KeyError:
                return None
        try:
            return self.data.unit_pool.index(int(unit_id))
        except ValueError:
            return None

    def _selected_unit_id_value(self) -> int:
        selected = self.__dict__.get("_selected_unit_id")
        if selected is not None:
            return int(selected)
        local_index = int(self.unit_idx.get())
        if 0 <= local_index < self.data.n_units:
            return int(self.data.cluster_id(local_index))
        return int(self.data.unit_pool[0])

    def _selected_local_unit_index(self) -> int | None:
        if not hasattr(self, "settings"):
            unit_id = self._selected_unit_id_value()
            local_index = self._local_unit_index(unit_id)
            if local_index is None:
                return None
            if int(self.unit_idx.get()) != local_index:
                self.unit_idx.set(local_index)
            return local_index
        navigation_ids = RFMViewer._unit_navigation_ids(self)
        if not navigation_ids:
            if int(self.unit_idx.get()) != -1:
                self.unit_idx.set(-1)
            return None
        unit_id = self._selected_unit_id_value()
        if unit_id not in navigation_ids or not self._local_unit_passes_quality_filter(
            unit_id
        ):
            if int(self.unit_idx.get()) != -1:
                self.unit_idx.set(-1)
            return None
        local_index = self._local_unit_index(unit_id)
        if local_index is None:
            return None
        if int(self.unit_idx.get()) != local_index:
            self.unit_idx.set(local_index)
        return local_index

    def _set_selected_unit_id(self, unit_id: int) -> None:
        unit_id = int(unit_id)
        self._selected_unit_id = unit_id
        local_index = self._local_unit_index(unit_id)
        if local_index is None:
            self.unit_idx.set(-1)
        else:
            self.unit_idx.set(local_index)
            self._last_supported_unit_id = unit_id
        if hasattr(self, "unit_combo"):
            self._sync_unit_combo()

    def _quality_filter_status(self, unit_id: int | None = None) -> str | None:
        if not self.settings.rf_filter_units_with_zero_bins:
            return None
        visible = self._local_quality_visible_unit_ids()
        if not visible:
            return (
                "No units pass the zero-spike RF-bin filter for the current "
                "RF window. Change the window or filter in Settings."
            )
        if unit_id is not None and self._local_unit_index(unit_id) is not None:
            if not self._local_unit_passes_quality_filter(unit_id):
                return (
                    f"Cluster {unit_id} is hidden by the zero-spike RF-bin "
                    "filter for the current RF window."
                )
        return None

    def _restore_local_unit_selection(self) -> None:
        local_units = RFMViewer._local_quality_visible_unit_ids(self)
        if not local_units:
            self.unit_idx.set(-1)
            if hasattr(self, "unit_combo"):
                self._sync_unit_combo()
            return
        selected = self._selected_unit_id_value()
        if selected in local_units:
            target = selected
        else:
            last_supported = self.__dict__.get("_last_supported_unit_id")
            target = int(last_supported) if last_supported in local_units else local_units[0]
        changed = target != selected or self._selected_local_unit_index() is None
        self._set_selected_unit_id(target)
        if changed and self.__dict__.get("_viewer_ready", False):
            self.selected_cell = None
            self._update_all()

    def _local_quality_visible_unit_ids(self) -> list[int]:
        unit_ids = [int(unit_id) for unit_id in self.data.unit_pool]
        settings = self.__dict__.get("settings", ViewerSettings())
        if (
            not settings.rf_filter_units_with_zero_bins
            or not hasattr(self.data, "zero_spike_spatial_bin_count")
            or not hasattr(self.data, "rf_map_by_unit_id")
            or not hasattr(self, "range_start_ms_var")
        ):
            return unit_ids
        start, end = self._source_bins_for_time_controls()
        threshold = settings.rf_zero_bin_threshold
        return [
            unit_id
            for unit_id in unit_ids
            if self.data.zero_spike_spatial_bin_count(
                self.data.rf_map_by_unit_id(unit_id).unit_index,
                start,
                end,
            )
            < threshold
        ]

    def _local_unit_passes_quality_filter(self, unit_id: int) -> bool:
        if not hasattr(self, "settings") or not self.settings.rf_filter_units_with_zero_bins:
            return True
        local_index = self._local_unit_index(unit_id)
        if local_index is None:
            return False
        start, end = self._source_bins_for_time_controls()
        return (
            self.data.zero_spike_spatial_bin_count(local_index, start, end)
            < self.settings.rf_zero_bin_threshold
        )

    def _quality_filtered_pairing_unit_ids(
        self,
        ready: list[RFMViewer],
    ) -> list[int]:
        return sorted(
            {
                unit_id
                for window in ready
                for unit_id in RFMViewer._local_quality_visible_unit_ids(window)
            }
        )

    def _unit_navigation_ids(self) -> list[int]:
        if getattr(self._app_root, "_rfm_pairing_enabled", False):
            ready, eligible = self._pairing_eligibility()
            if eligible:
                unit_ids = RFMViewer._quality_filtered_pairing_unit_ids(self, ready)
            else:
                unit_ids = RFMViewer._local_quality_visible_unit_ids(self)
        else:
            unit_ids = RFMViewer._local_quality_visible_unit_ids(self)
        region = self.__dict__.get("spatial_region")
        geometry = self.__dict__.get("probe_geometry")
        if region is not None and geometry is not None:
            return geometry.unit_ids_in_region(region, unit_ids)
        return unit_ids

    def _reconcile_unit_filter_selection(self) -> None:
        """Keep selection valid as the RF sum window or filter settings change."""

        unit_ids = self._unit_navigation_ids()
        selected = self._selected_unit_id_value()
        if not unit_ids:
            self.unit_idx.set(-1)
            if hasattr(self, "unit_combo"):
                self._sync_unit_combo()
            return
        if selected not in unit_ids:
            self.selected_cell = None
            self._set_selected_unit_id(self._next_union_unit_id(unit_ids, selected))
            return
        local_index = self._local_unit_index(selected)
        if local_index is None or not self._local_unit_passes_quality_filter(selected):
            self.unit_idx.set(-1)
        elif int(self.unit_idx.get()) != local_index:
            self.unit_idx.set(local_index)
        if hasattr(self, "unit_combo"):
            self._sync_unit_combo()

    def _pairing_eligibility(self) -> tuple[list[RFMViewer], bool]:
        ready = self._ready_pairing_viewers()
        return ready, len(ready) >= 2

    def _refresh_pairing_controls(self) -> None:
        ready, eligible = self._pairing_eligibility()
        active = bool(getattr(self._app_root, "_rfm_pairing_enabled", False) and eligible)
        matching_units = self._unit_lists_match(ready)
        if len(ready) < 2:
            status = "Open another loaded viewer window to enable sync."
        elif not matching_units:
            prefix = f"{len(ready)} windows paired. " if active else f"{len(ready)} windows ready. "
            status = (
                prefix
                + "Unit lists differ; these files may be from different sessions. "
                "Missing units display N/A."
            )
        elif active:
            status = (
                f"{len(ready)} windows paired. Changes in any paired window sync to the others."
            )
        else:
            status = f"{len(ready)} loaded windows have matching unit lists."

        windows = getattr(self._app_root, "_rfm_viewer_windows", [])
        for window in windows:
            if not hasattr(window, "pair_windows_var"):
                continue
            try:
                window.pair_windows_var.set(active)
                if hasattr(window, "pair_windows_toggle"):
                    window.pair_windows_toggle.state(
                        ["!disabled"] if eligible else ["disabled"]
                    )
                if hasattr(window, "pair_status_label"):
                    window.pair_status_label.configure(text=status)
                if getattr(window, "_viewer_ready", False) and hasattr(window, "_sync_unit_combo"):
                    window._sync_unit_combo()
            except tk.TclError:
                continue

    def _disable_window_pairing(self) -> None:
        self._app_root._rfm_pairing_enabled = False
        self._app_root._rfm_pairing_state = None
        self._app_root._rfm_pairing_broadcasting = False
        for window in self._ready_pairing_viewers():
            window._pair_last_local_state = None
            if hasattr(window, "_restore_local_unit_selection"):
                window._restore_local_unit_selection()
        self._refresh_pairing_controls()

    def _pair_ready_viewer_set_changed(
        self,
        *,
        adopt_viewer: RFMViewer | None = None,
    ) -> None:
        ready, eligible = self._pairing_eligibility()
        if not getattr(self._app_root, "_rfm_pairing_enabled", False):
            self._refresh_pairing_controls()
            return
        if not eligible:
            self._disable_window_pairing()
            return

        state = getattr(self._app_root, "_rfm_pairing_state", None)
        if state is None:
            source = ready[0]
            state = source._capture_pairing_state()
            source._pair_last_local_state = state
            self._app_root._rfm_pairing_state = state
        unit_ids = RFMViewer._quality_filtered_pairing_unit_ids(self, ready)
        if not unit_ids:
            for window in ready:
                window._set_selected_unit_id(state.unit_id)
                window.unit_idx.set(-1)
                window._sync_unit_combo()
                window._update_all()
            self._refresh_pairing_controls()
            return
        normalized_unit_id = self._next_union_unit_id(unit_ids, state.unit_id)
        unit_changed = normalized_unit_id != state.unit_id
        if unit_changed:
            state = replace(state, unit_id=normalized_unit_id)
            self._app_root._rfm_pairing_state = state

        recipients = ready if unit_changed else (
            [adopt_viewer] if adopt_viewer is not None and adopt_viewer in ready else []
        )
        if recipients:
            self._app_root._rfm_pairing_broadcasting = True
            try:
                for window in recipients:
                    if unit_changed and window is not adopt_viewer:
                        window._apply_pairing_state(state, frozenset({"unit"}))
                    else:
                        window._apply_pairing_state(state)
            finally:
                self._app_root._rfm_pairing_broadcasting = False
        self._refresh_pairing_controls()

    def _on_pair_windows_toggled(self) -> None:
        if not self.pair_windows_var.get():
            self._disable_window_pairing()
            return

        ready, eligible = self._pairing_eligibility()
        if not eligible or self not in ready:
            self._disable_window_pairing()
            return

        state = self._capture_pairing_state()
        self._app_root._rfm_pairing_enabled = True
        self._app_root._rfm_pairing_state = state
        self._pair_last_local_state = state
        self._app_root._rfm_pairing_broadcasting = True
        try:
            for window in ready:
                if window is not self:
                    window._apply_pairing_state(state)
        finally:
            self._app_root._rfm_pairing_broadcasting = False
        self._refresh_pairing_controls()

    def _capture_pairing_state(self) -> ViewerSyncState:
        self._normalize_control_values()
        timeline_start_ms, timeline_end_ms = self._timeline_selected_time_bounds_ms()
        rf_start_ms, rf_end_ms = self._selected_time_bounds_ms()
        current_bin = max(0, min(self._time_group_count() - 1, self.bin_var.get()))
        anchor_center_ms = (
            self._time_group_center_ms(self._timeline_range_anchor)
            if self._timeline_range_anchor is not None
            else None
        )
        selected_y_midpoint: float | None = None
        selected_x_midpoint: float | None = None
        if self.selected_cell is not None:
            y_start, y_end, x_start, x_end = self.selected_cell
            selected_y_midpoint = (float(y_start) + float(y_end)) / 2.0
            selected_x_midpoint = (float(x_start) + float(x_end)) / 2.0

        value_mode = self.value_mode_var.get()
        if value_mode not in VALUE_MODES or not self.data.supports_value_mode(value_mode):
            value_mode = VALUE_MODE_RATE
        palette = self.palette_var.get()
        if palette not in PALETTES:
            palette = PALETTES[0]
        polar_radius = self.polar_radius_var.get()
        if polar_radius not in POLAR_RADIUS_MODES:
            polar_radius = POLAR_RADIUS_MODES[1]
        selected_tab = self._active_tab_key()
        if selected_tab not in {"rf", "delay", "timeline"}:
            selected_tab = "rf"

        return ViewerSyncState(
            unit_id=self._selected_unit_id_value(),
            value_mode=value_mode,
            timeline_bin_center_ms=self._time_group_center_ms(current_bin),
            timeline_selection_start_ms=timeline_start_ms,
            timeline_selection_end_ms=timeline_end_ms,
            timeline_anchor_center_ms=anchor_center_ms,
            rf_start_ms=rf_start_ms,
            rf_end_ms=rf_end_ms,
            time_resolution_ms=float(self.time_res_ms_var.get()),
            x_bins=self._x_target_bins(),
            y_bins=self._y_target_bins(),
            smooth_radius=self._smooth_radius(),
            flip_y=bool(self.flip_y_var.get()),
            palette=palette,
            polar_radius=polar_radius,
            polar_layout=bool(self.polar_layout_var.get()),
            rgb_mode=bool(self.rgb_mode_var.get()),
            selected_cell_y_midpoint=selected_y_midpoint,
            selected_cell_x_midpoint=selected_x_midpoint,
            timeline_scroll_fraction=round(
                max(0.0, min(1.0, float(self._timeline_scroll_fraction))), 9
            ),
            selected_tab=selected_tab,
            tuning_plot_mode=self.tuning_plot_mode_var.get(),
            tuning_display_bins=normalize_hd_bin_count(
                self.tuning_display_bins_var.get()
            ),
            tuning_smoothing=bool(self.tuning_smoothing_var.get()),
            tuning_smooth_sigma=float(self.tuning_smooth_sigma_var.get()),
            tuning_compare_scale=bool(self.tuning_compare_scale_var.get()),
            show_tuning_curve=bool(self.show_tuning_curve_var.get()),
            show_waveform=bool(self.show_waveform_var.get()),
            show_probe_layout=bool(self.show_probe_layout_var.get()),
        )

    def _time_group_index_for_ms(self, time_ms: float) -> int:
        groups = self._time_groups()
        bounds = [self._time_group_bounds_ms(index) for index in range(len(groups))]
        for index, (start_ms, end_ms) in enumerate(bounds):
            if start_ms <= time_ms < end_ms or (
                index == len(bounds) - 1 and time_ms == end_ms
            ):
                return index
        return min(
            range(len(bounds)),
            key=lambda index: abs((bounds[index][0] + bounds[index][1]) / 2.0 - time_ms),
        )

    def _time_group_range_for_ms(self, start_ms: float, end_ms: float) -> AxisGroup:
        if start_ms > end_ms:
            start_ms, end_ms = end_ms, start_ms
        groups = self._time_groups()
        bounds = [self._time_group_bounds_ms(index) for index in range(len(groups))]
        if math.isclose(start_ms, end_ms):
            index = self._time_group_index_for_ms(start_ms)
            return index, index
        overlapping = [
            index
            for index, (group_start, group_end) in enumerate(bounds)
            if group_end > start_ms and group_start < end_ms
        ]
        if overlapping:
            return overlapping[0], overlapping[-1]
        return (
            self._time_group_index_for_ms(start_ms),
            self._time_group_index_for_ms(end_ms),
        )

    @staticmethod
    def _axis_group_for_midpoint(groups: list[AxisGroup], midpoint: float) -> AxisGroup:
        if not groups:
            return 0, 0
        axis_start = min(group[0] for group in groups)
        axis_end = max(group[1] for group in groups)
        midpoint = max(float(axis_start), min(float(axis_end), float(midpoint)))
        source_index = max(axis_start, min(axis_end, int(math.floor(midpoint + 0.5))))
        return next(
            (group for group in groups if group[0] <= source_index <= group[1]),
            min(groups, key=lambda group: abs((group[0] + group[1]) / 2.0 - midpoint)),
        )

    def _cell_for_pairing_midpoint(
        self,
        y_midpoint: float | None,
        x_midpoint: float | None,
    ) -> CellRef | None:
        if y_midpoint is None or x_midpoint is None:
            return None
        y_start, y_end = self._axis_group_for_midpoint(
            self._display_y_groups(), y_midpoint
        )
        x_start, x_end = self._axis_group_for_midpoint(self._x_groups(), x_midpoint)
        return y_start, y_end, x_start, x_end

    def _select_tab_key(self, key: str) -> None:
        if not hasattr(self, "notebook"):
            return
        for tab in self.notebook.tabs():
            if self._tab_keys.get(str(tab)) == key:
                self.notebook.select(tab)
                return

    def _apply_pairing_state(
        self,
        state: ViewerSyncState,
        fields: frozenset[str] = PAIR_SYNC_ALL_FIELDS,
    ) -> None:
        if not self._viewer_ready:
            return
        self._pair_apply_in_progress = True
        try:
            preserved_active_time_ms: float | None = None
            preserved_timeline_bounds_ms: tuple[float, float] | None = None
            preserved_anchor_time_ms: float | None = None
            if "time_resolution" in fields:
                if "active_time" not in fields:
                    preserved_active_time_ms = self._time_group_center_ms(self.bin_var.get())
                if "timeline_selection" not in fields:
                    preserved_timeline_bounds_ms = self._timeline_selected_time_bounds_ms()
                    if self._timeline_range_anchor is not None:
                        preserved_anchor_time_ms = self._time_group_center_ms(
                            self._timeline_range_anchor
                        )
            preserved_cell_midpoint: tuple[float, float] | None = None
            if (
                fields.intersection({"x_bins", "y_bins"})
                and "selected_cell" not in fields
                and self.selected_cell is not None
            ):
                y_start, y_end, x_start, x_end = self.selected_cell
                preserved_cell_midpoint = (
                    (float(y_start) + float(y_end)) / 2.0,
                    (float(x_start) + float(x_end)) / 2.0,
                )
            if "unit" in fields:
                if (
                    self.__dict__.get("spatial_region") is not None
                    and int(state.unit_id) not in self._unit_navigation_ids()
                ):
                    self.spatial_region = None
                self._set_selected_unit_id(state.unit_id)
            if "value_mode" in fields:
                value_mode = state.value_mode
                if (
                    value_mode not in VALUE_MODES
                    or not self.data.supports_value_mode(value_mode)
                ):
                    value_mode = VALUE_MODE_RATE
                self.value_mode_var.set(value_mode)
            if "time_resolution" in fields:
                self.time_res_ms_var.set(format_ms(state.time_resolution_ms))
            if "x_bins" in fields:
                self.x_bins_var.set(max(1, min(self.data.n_x, int(state.x_bins))))
            if "y_bins" in fields:
                self.y_bins_var.set(max(1, min(self.data.n_y, int(state.y_bins))))
            if "smoothing" in fields:
                self.smooth_radius_var.set(max(0, min(3, int(state.smooth_radius))))
            if "flip_y" in fields:
                self.flip_y_var.set(bool(state.flip_y))
            if "palette" in fields:
                self.palette_var.set(
                    state.palette if state.palette in PALETTES else PALETTES[0]
                )
            if "polar_radius" in fields:
                self.polar_radius_var.set(
                    state.polar_radius
                    if state.polar_radius in POLAR_RADIUS_MODES
                    else POLAR_RADIUS_MODES[1]
                )
            if "spatial_format" in fields:
                self.polar_layout_var.set(bool(state.polar_layout))
            if "delay_rgb" in fields:
                self.rgb_mode_var.set(bool(state.rgb_mode))
            if "rf_range" in fields:
                self.range_start_ms_var.set(format_ms(state.rf_start_ms))
                self.range_end_ms_var.set(format_ms(state.rf_end_ms))
            if "timeline_scroll" in fields:
                self._timeline_scroll_fraction = max(
                    0.0, min(1.0, float(state.timeline_scroll_fraction))
                )
            if "tuning_display" in fields:
                self.tuning_plot_mode_var.set(
                    state.tuning_plot_mode
                    if state.tuning_plot_mode in TUNING_PLOT_MODES
                    else "Auto"
                )
                self.tuning_display_bins_var.set(
                    normalize_hd_bin_count(state.tuning_display_bins)
                )
                self.tuning_smoothing_var.set(bool(state.tuning_smoothing))
                self.tuning_smooth_sigma_var.set(
                    state.tuning_smooth_sigma
                    if math.isfinite(state.tuning_smooth_sigma)
                    and state.tuning_smooth_sigma > 0.0
                    else DEFAULT_HD_SMOOTH_SIGMA
                )
                self.tuning_compare_scale_var.set(bool(state.tuning_compare_scale))
                self._tuning_processed_cache = None
                self._tuning_scale_cache = None
            if "optional_views" in fields:
                self.show_tuning_curve_var.set(bool(state.show_tuning_curve))
                self.show_waveform_var.set(bool(state.show_waveform))
                self.show_probe_layout_var.set(bool(state.show_probe_layout))
                self._sync_optional_view_visibility(redraw=False)
                if (
                    state.show_probe_layout
                    and self.probe_geometry is None
                    and self.settings.auto_load_probe_layout
                ):
                    self.probe_geometry = discover_probe_geometry(self.data.path)
                    self._probe_static_signature = None
                if (
                    state.show_tuning_curve
                    and self.tuning_curve_data is None
                    and self.settings.auto_load_tuning_curve
                ):
                    candidate = discover_tuning_curve_path(self.data.path)
                    if candidate is not None:
                        self._load_tuning_curve_path(
                            candidate, show_error=False, redraw=False
                        )

            self._normalize_control_values()
            if "active_time" in fields:
                self.bin_var.set(
                    self._time_group_index_for_ms(state.timeline_bin_center_ms)
                )
            elif preserved_active_time_ms is not None:
                self.bin_var.set(self._time_group_index_for_ms(preserved_active_time_ms))
            if "timeline_selection" in fields:
                timeline_start, timeline_end = self._time_group_range_for_ms(
                    state.timeline_selection_start_ms,
                    state.timeline_selection_end_ms,
                )
                self.range_start_var.set(timeline_start)
                self.range_end_var.set(timeline_end)
                self._timeline_range_anchor = (
                    self._time_group_index_for_ms(state.timeline_anchor_center_ms)
                    if state.timeline_anchor_center_ms is not None
                    else None
                )
            elif preserved_timeline_bounds_ms is not None:
                timeline_start, timeline_end = self._time_group_range_for_ms(
                    *preserved_timeline_bounds_ms
                )
                self.range_start_var.set(timeline_start)
                self.range_end_var.set(timeline_end)
                self._timeline_range_anchor = (
                    self._time_group_index_for_ms(preserved_anchor_time_ms)
                    if preserved_anchor_time_ms is not None
                    else None
                )
            if "selected_cell" in fields:
                self.selected_cell = self._cell_for_pairing_midpoint(
                    state.selected_cell_y_midpoint,
                    state.selected_cell_x_midpoint,
                )
            elif preserved_cell_midpoint is not None:
                self.selected_cell = self._cell_for_pairing_midpoint(
                    *preserved_cell_midpoint
                )
            if "selected_tab" in fields:
                self._select_tab_key(state.selected_tab)
            if fields.intersection(
                {
                    "unit",
                    "value_mode",
                    "time_resolution",
                    "x_bins",
                    "y_bins",
                    "smoothing",
                    "flip_y",
                    "palette",
                    "polar_radius",
                    "spatial_format",
                    "delay_rgb",
                    "rf_range",
                    "tuning_display",
                    "optional_views",
                }
            ):
                self._timeline_preview_cache_key = None
                self._timeline_preview_images = {}
            self._update_all()
            if "timeline_scroll" in fields:
                self._restore_timeline_scroll()
            self._pair_last_local_state = self._capture_pairing_state()
        finally:
            self._pair_apply_in_progress = False

    def _apply_pairing_scroll_fraction(self, fraction: float) -> None:
        if not self._viewer_ready:
            return
        self._pair_apply_in_progress = True
        try:
            fraction = max(0.0, min(1.0, float(fraction)))
            self._timeline_scroll_fraction = fraction
            canvas = self.canvases.get("timeline") if hasattr(self, "canvases") else None
            if canvas is not None:
                try:
                    first, last = canvas.yview()
                except (tk.TclError, TypeError, ValueError):
                    offset = None
                else:
                    offset = timeline_scroll_offset(fraction, first, last)
                if offset is not None:
                    self._restoring_timeline_scroll = True
                    try:
                        canvas.yview_moveto(offset)
                    finally:
                        self._restoring_timeline_scroll = False
            baseline = self._pair_last_local_state or self._capture_pairing_state()
            self._pair_last_local_state = replace(
                baseline,
                timeline_scroll_fraction=round(fraction, 9),
            )
        finally:
            self._pair_apply_in_progress = False

    def _publish_pairing_state_if_changed(self) -> None:
        if not self.__dict__.get("_viewer_ready", False):
            return
        if self.__dict__.get("_pair_apply_in_progress", False):
            return
        if not getattr(self._app_root, "_rfm_pairing_enabled", False):
            return
        if getattr(self._app_root, "_rfm_pairing_broadcasting", False):
            return

        ready, eligible = self._pairing_eligibility()
        if not eligible or self not in ready:
            self._disable_window_pairing()
            return
        state = self._capture_pairing_state()
        previous = self._pair_last_local_state
        if previous is not None:
            changed_fields = state.changed_fields(previous)
        else:
            changed_fields = PAIR_SYNC_ALL_FIELDS
        self._pair_last_local_state = state
        if not changed_fields:
            return
        canonical = getattr(self._app_root, "_rfm_pairing_state", None)
        self._app_root._rfm_pairing_state = (
            canonical.merging(state, changed_fields) if canonical is not None else state
        )

        self._app_root._rfm_pairing_broadcasting = True
        try:
            for window in ready:
                if window is self:
                    continue
                if changed_fields == frozenset({"timeline_scroll"}):
                    window._apply_pairing_scroll_fraction(state.timeline_scroll_fraction)
                else:
                    window._apply_pairing_state(state, changed_fields)
        finally:
            self._app_root._rfm_pairing_broadcasting = False

    def _dispatch_open_json(self, _event: object | None = None) -> None:
        self._active_viewer()._open_json()

    def _dispatch_settings(self, _event: object | None = None) -> None:
        self._active_viewer()._show_settings()

    def _show_settings(self) -> None:
        active = self._active_viewer()
        if not getattr(active, "_viewer_ready", False):
            active._show_settings_when_ready = True
            return
        existing = getattr(self._app_root, "_rfm_settings_window", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.owner = active
                    existing.transient(active)
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
        window = SettingsWindow(active)
        self._app_root._rfm_settings_window = window
        window.lift()

    def _apply_viewer_settings(
        self,
        settings: ViewerSettings,
        *,
        persist: bool,
        broadcast: bool,
    ) -> bool:
        previous = self.settings
        if persist:
            try:
                save_viewer_settings(settings, self._app_root._rfm_settings_path)
            except OSError as exc:
                messagebox.showerror("Could not save settings", str(exc), parent=self)
                return False
            self._app_root._rfm_settings = settings
        self.settings = settings

        old_show_probe = bool(self.show_probe_layout_var.get())
        old_show_tuning = bool(self.show_tuning_curve_var.get())
        old_show_waveform = bool(self.show_waveform_var.get())
        needs_optional_autoload = False
        previous_pair_apply = self._pair_apply_in_progress
        self._pair_apply_in_progress = True
        try:
            self.show_probe_layout_var.set(settings.show_probe_layout)
            self.show_tuning_curve_var.set(settings.show_tuning_curve)
            self.show_waveform_var.set(settings.show_waveform)
            value_mode = settings.rf_value_mode
            if not self.data.supports_value_mode(value_mode):
                value_mode = VALUE_MODE_RATE
            self.value_mode_var.set(value_mode)
            self.range_start_ms_var.set(format_ms(settings.rf_sum_start_ms))
            self.range_end_ms_var.set(format_ms(settings.rf_sum_end_ms))
            self.time_res_ms_var.set(format_ms(settings.rf_time_resolution_ms))
            self.x_bins_var.set(min(self.data.n_x, settings.rf_x_bins or self.data.n_x))
            self.y_bins_var.set(min(self.data.n_y, settings.rf_y_bins or self.data.n_y))
            self.smooth_radius_var.set(settings.rf_smooth_radius)
            self.flip_y_var.set(settings.rf_flip_y)
            self.palette_var.set(settings.rf_palette)
            self.polar_radius_var.set(settings.rf_polar_radius)
            self.polar_layout_var.set(settings.rf_polar_layout)
            self.rgb_mode_var.set(settings.rf_rgb_mode)
            self.tuning_plot_mode_var.set(settings.tuning_plot_mode)
            self.tuning_layout_var.set(settings.tuning_layout)
            self.tuning_display_bins_var.set(settings.tuning_display_bins)
            self.tuning_smoothing_var.set(settings.tuning_smoothing)
            self.tuning_smooth_sigma_var.set(settings.tuning_smooth_sigma)
            self.tuning_compare_scale_var.set(settings.tuning_compare_scale)
            mode_changed = (
                self.waveform_channel_mode_var.get()
                != settings.waveform_channel_mode
            )
            self.waveform_channel_mode_var.set(settings.waveform_channel_mode)
            if mode_changed or (
                old_show_waveform and not settings.show_waveform
            ):
                self.waveform_payload = None
                self._waveform_payload_key = None
                self._waveform_error = None
                self._waveform_error_key = None
                self._waveform_loading_key = None
                self._waveform_generation += 1
            self._tuning_processed_cache = None
            self._tuning_scale_cache = None

            self._sync_optional_view_visibility(redraw=False)
            if settings.show_probe_layout and self.probe_geometry is None:
                should_load_probe = settings.auto_load_probe_layout and (
                    not old_show_probe or not previous.auto_load_probe_layout
                )
                if should_load_probe:
                    needs_optional_autoload = True
            if settings.show_tuning_curve and self.tuning_curve_data is None:
                should_load_tuning = settings.auto_load_tuning_curve and (
                    not old_show_tuning or not previous.auto_load_tuning_curve
                )
                if should_load_tuning:
                    needs_optional_autoload = True
            self._normalize_control_values()
            self._timeline_preview_cache_key = None
            self._timeline_preview_images = {}
            self._sync_unit_combo()
            self._update_all()
        finally:
            self._pair_apply_in_progress = previous_pair_apply

        if needs_optional_autoload:
            self._schedule_optional_autoload()

        if (
            broadcast
            and getattr(self._app_root, "_rfm_pairing_enabled", False)
            and not getattr(self._app_root, "_rfm_pairing_broadcasting", False)
        ):
            self._app_root._rfm_pairing_broadcasting = True
            try:
                ready = self._ready_pairing_viewers()
                for window in ready:
                    if window is not self:
                        window._apply_viewer_settings(
                            settings,
                            persist=False,
                            broadcast=False,
                        )
                # Settings are global, but each file has its own RF counts.
                # Reconcile the shared unit only after every paired window has
                # adopted the new filter so no window observes a mixed old/new
                # visible-unit union.
                visible_union = self._quality_filtered_pairing_unit_ids(ready)
                selected = self._selected_unit_id_value()
                target = (
                    self._next_union_unit_id(visible_union, selected)
                    if visible_union
                    else selected
                )
                for window in ready:
                    if (
                        window.spatial_region is not None
                        and target not in window._unit_navigation_ids()
                    ):
                        window.spatial_region = None
                    window._set_selected_unit_id(target)
                    window.selected_cell = None
                    window._update_all()
            finally:
                self._app_root._rfm_pairing_broadcasting = False
            state = self._capture_pairing_state()
            self._app_root._rfm_pairing_state = state
            for window in self._ready_pairing_viewers():
                window._pair_last_local_state = window._capture_pairing_state()
        return True

    def _dispatch_macos_open_documents(self, *paths: str) -> None:
        self._active_viewer()._on_macos_open_documents(*paths)

    def _close_window(self, _event: object | None = None) -> None:
        if _active_export_jobs(self._app_root, self):
            messagebox.showinfo(
                "Export is running",
                "Wait for this window's export to finish before closing it.",
                parent=self,
            )
            return
        self.destroy()

    def _quit_application(self, _event: object | None = None) -> None:
        if getattr(self._app_root, "_rfm_quitting", False):
            return
        if _active_export_jobs(self._app_root):
            messagebox.showinfo(
                "Export is running",
                "Wait for all figure exports to finish before quitting RF Map Viewer.",
                parent=self,
            )
            return
        self._quitting = True
        self._app_root._rfm_quitting = True
        _shutdown_export_executor(self._app_root)
        self._app_root.destroy()

    def _open_json_window(self, path: Path) -> RFMViewer | None:
        path = Path(path).expanduser()
        try:
            use_background_load = path.stat().st_size >= ASYNC_DOCUMENT_LOAD_BYTES
        except OSError:
            use_background_load = False
        if use_background_load:
            window = RFMViewer(startup_path=path, master=self._app_root)
            window._cancel_startup_callback()
            window._startup_after = window.after_idle(
                lambda: window._load_startup_document(path)
            )
            window.lift()
            return window
        try:
            data = RFMappingData(path)
        except Exception as exc:
            messagebox.showerror("Could not open RF map", str(exc), parent=self)
            return None
        window = RFMViewer(data, master=self._app_root)
        window.lift()
        return window

    def _open_json(self, _event: object | None = None) -> None:
        initial_dir = (
            self.data.path.parent
            if self._viewer_ready
            else startup_file_dialog_directory()
        )
        path = filedialog.askopenfilename(
            parent=self,
            title="Open RF mapping file",
            initialdir=str(initial_dir),
            filetypes=RF_DOCUMENT_FILETYPES,
        )
        if path:
            if self._viewer_ready:
                self._open_json_window(Path(path))
            else:
                self._cancel_startup_callback()
                self._remove_startup_chooser_shell()
                self._startup_after = self.after_idle(
                    lambda selected=Path(path): self._load_startup_document(selected)
                )

    def _open_external_companion(self, path: Path) -> bool:
        """Attach a Finder-opened companion to this RF document window."""

        if not self._viewer_ready:
            self._pending_open_documents.append(path)
            return True
        kind = document_kind(path)
        if kind == "tuning":
            return self._load_tuning_curve_path(path)
        if kind == "probe":
            return self._load_probe_geometry_path(path)
        raise ValueError(f"Not a companion document: {path}")

    def _on_macos_open_documents(self, *paths: str) -> None:
        documents = [Path(raw_path).expanduser() for raw_path in paths]
        if not documents:
            return
        rf_documents = [path for path in documents if document_kind(path) == "rf"]
        companions = [
            path
            for path in documents
            if document_kind(path) in {"tuning", "probe"}
        ]
        if not self._viewer_ready:
            pending = getattr(self, "_pending_open_documents", None)
            if pending is None:
                pending = []
                self._pending_open_documents = pending
            pending.extend(companions)
            if not rf_documents:
                return
            self._cancel_startup_callback()
            selected, *additional = rf_documents

            def load_documents() -> None:
                self._load_startup_document(selected)
                for path in additional:
                    self._open_json_window(path)

            self._startup_after = self.after_idle(
                load_documents
            )
            return

        companion_target = self
        for index, path in enumerate(rf_documents):
            opened = self._open_json_window(path)
            if index == 0 and opened is not None:
                companion_target = opened
        for path in companions:
            companion_target._open_external_companion(path)

    def _json_choice_label(self, path: Path) -> str:
        try:
            rel = path.relative_to(Path.cwd())
        except ValueError:
            rel = path
        modified = safe_mtime(path)
        stamp = ""
        if modified > 0:
            try:
                from datetime import datetime

                stamp = datetime.fromtimestamp(modified).strftime("  %Y-%m-%d %H:%M")
            except (OSError, ValueError):
                stamp = ""
        return f"{rel}{stamp}"

    def _sync_json_menu(self) -> None:
        current = _resolve_existing_file(self.data.path) or self.data.path
        self.json_paths = discover_json_files(current_path=current)
        if current not in self.json_paths:
            self.json_paths.insert(0, current)
        labels = [self._json_choice_label(path) for path in self.json_paths]
        self._json_choice_to_path = dict(zip(labels, self.json_paths))
        menu = getattr(self, "_discovered_json_menu", None)
        if menu is None:
            return
        menu.delete(0, "end")
        if not labels:
            menu.add_command(label="No RF mapping files found", state="disabled")
            return
        for label, path in zip(labels, self.json_paths):
            menu.add_command(
                label=label,
                command=lambda selected=path: self._open_json_window(selected),
            )

    def _sync_json_combo(self) -> None:
        """Compatibility alias retained for the minimal 1.9 call surface."""

        self._sync_json_menu()

    def _on_json_selected(self, _event: object | None = None) -> None:
        combo = getattr(self, "json_combo", None)
        if combo is None:
            return
        choice = combo.get()
        path = self._json_choice_to_path.get(choice)
        if path is None:
            return
        if _resolve_existing_file(self.data.path) == path:
            return
        self._open_json_window(path)

    def _autoload_optional_resources(self, *, redraw: bool = True) -> None:
        if self.show_probe_layout_var.get() and self.settings.auto_load_probe_layout:
            self.probe_geometry = self.data.probe_geometry()
            self._probe_static_signature = None
        if self.show_tuning_curve_var.get() and self.settings.auto_load_tuning_curve:
            candidate = discover_tuning_curve_path(self.data.path)
            if candidate is not None:
                self._load_tuning_curve_path(
                    candidate, show_error=False, redraw=False
                )
        if redraw:
            self._draw_probe_canvas()
            if self._active_tab_key() == "rf":
                self._draw_tuning_curve()

    def _autoload_optional_resources_deferred(self, generation: int) -> None:
        self._optional_autoload_after = None
        if (
            generation != self._optional_autoload_generation
            or not self.__dict__.get("_viewer_ready", False)
            or self._quitting
        ):
            return
        snapshot = {
            "generation": generation,
            "data": self.data,
            "data_path": self.data.path,
            "load_probe": bool(
                self.show_probe_layout_var.get()
                and self.settings.auto_load_probe_layout
            ),
            "load_tuning": bool(
                self.show_tuning_curve_var.get()
                and self.settings.auto_load_tuning_curve
            ),
            "cluster_id": self._selected_unit_id_value(),
            "tuning_bins": normalize_hd_bin_count(
                self.tuning_display_bins_var.get()
            ),
            "tuning_smoothing": bool(self.tuning_smoothing_var.get()),
            "tuning_sigma": float(self.tuning_smooth_sigma_var.get()),
        }
        threading.Thread(
            target=self._optional_autoload_worker,
            args=(snapshot,),
            name=f"rfmapping-optional-{generation}",
            daemon=True,
        ).start()
        self._schedule_optional_result_poll()

    def _optional_autoload_worker(self, snapshot: Mapping[str, object]) -> None:
        """Discover and parse optional files without blocking Tk's UI thread."""

        result: dict[str, object] = {
            "generation": snapshot.get("generation"),
            "data_path": snapshot.get("data_path"),
            "probe_geometry": None,
            "tuning_path": None,
            "tuning_signature": None,
            "tuning_data": None,
            "tuning_error": None,
            "worker_error": None,
            "processed": None,
            "processed_cluster": snapshot.get("cluster_id"),
            "processed_bins": snapshot.get("tuning_bins"),
            "processed_smoothing": snapshot.get("tuning_smoothing"),
            "processed_sigma": snapshot.get("tuning_sigma"),
        }
        try:
            data = snapshot.get("data", self.data)
            if not isinstance(data, RFMappingData):
                raise TypeError("Optional-load snapshot lost its RF data owner")
            data_path = data.path
            result["data_path"] = data_path
            if snapshot["load_probe"]:
                geometry = discover_probe_geometry(data_path)
                if geometry is not None:
                    geometry = data.attach_probe_geometry(
                        geometry.positions_path,
                        geometry.channels_path,
                        probe_name=geometry.probe_name,
                    )
                result["probe_geometry"] = geometry
            if snapshot["load_tuning"]:
                candidate = discover_tuning_curve_path(data_path)
                result["tuning_path"] = candidate
                if candidate is not None:
                    tuning_data = data.attach_hd_tuning(candidate)
                    identity = data._hd_tuning_identity
                    result["tuning_signature"] = (
                        (identity.mtime_ns, identity.size)
                        if identity is not None
                        else None
                    )
                    result["tuning_data"] = tuning_data
                    raw_rates = tuning_data.rates_for(int(snapshot["cluster_id"]))
                    if raw_rates is not None:
                        result["processed"] = tuning_data.processed_for(
                            int(snapshot["cluster_id"]),
                            int(snapshot["tuning_bins"]),
                            smoothing=bool(snapshot["tuning_smoothing"]),
                            sigma=float(snapshot["tuning_sigma"]),
                        )
        except Exception as exc:
            result["worker_error"] = f"{type(exc).__name__}: {exc}"
            if snapshot.get("load_tuning"):
                if isinstance(exc, (ImportError, OSError, ValueError)):
                    result["tuning_error"] = str(exc)
                else:
                    result["tuning_error"] = "Could not auto-load tuning curves."
        finally:
            # Always end the matching poll generation, even if mounted-volume
            # discovery or optional preprocessing fails unexpectedly.
            self._optional_result_queue.put(result)

    def _schedule_optional_result_poll(self) -> None:
        if self._optional_poll_after is not None:
            return
        self._optional_poll_after = self.after(30, self._poll_optional_results)

    def _poll_optional_results(self) -> None:
        self._optional_poll_after = None
        current_result: dict[str, object] | None = None
        while True:
            try:
                candidate = self._optional_result_queue.get_nowait()
            except queue.Empty:
                break
            if candidate.get("generation") == self._optional_autoload_generation:
                current_result = candidate
        if current_result is None:
            if not self._quitting:
                self._schedule_optional_result_poll()
            return
        if (
            current_result.get("data_path") != self.data.path
            or not self.__dict__.get("_viewer_ready", False)
        ):
            return

        if (
            self.show_probe_layout_var.get()
            and self.settings.auto_load_probe_layout
            and self.probe_geometry is None
        ):
            geometry = current_result.get("probe_geometry")
            if geometry is None or isinstance(geometry, ProbeGeometry):
                self.probe_geometry = geometry
                self._probe_static_signature = None

        if (
            self.show_tuning_curve_var.get()
            and self.settings.auto_load_tuning_curve
            and self.tuning_curve_data is None
        ):
            tuning_path = current_result.get("tuning_path")
            tuning_data = current_result.get("tuning_data")
            if isinstance(tuning_path, Path):
                self._tuning_curve_candidate = tuning_path
            if isinstance(tuning_data, TuningCurveData):
                self.tuning_curve_data = tuning_data
                self._tuning_curve_error = None
                self._tuning_scale_cache = None
                self.tuning_collapsed_var.set(False)
                self._sync_tuning_collapsed_state(schedule_redraw=False)
                signature = current_result.get("tuning_signature")
                if (
                    isinstance(signature, tuple)
                    and len(signature) == 2
                    and isinstance(tuning_path, Path)
                ):
                    self._app_root._rfm_tuning_cache[str(tuning_path)] = (
                        *signature,
                        tuning_data,
                    )
                processed = current_result.get("processed")
                processed_cluster = int(current_result["processed_cluster"])
                if (
                    isinstance(processed, tuple)
                    and len(processed) == 2
                    and processed_cluster == self._selected_unit_id_value()
                    and int(current_result["processed_bins"])
                    == normalize_hd_bin_count(self.tuning_display_bins_var.get())
                    and bool(current_result["processed_smoothing"])
                    == bool(self.tuning_smoothing_var.get())
                    and math.isclose(
                        float(current_result["processed_sigma"]),
                        float(self.tuning_smooth_sigma_var.get()),
                    )
                ):
                    key = (
                        tuning_data.path,
                        processed_cluster,
                        int(current_result["processed_bins"]),
                        bool(current_result["processed_smoothing"]),
                        float(current_result["processed_sigma"]),
                    )
                    self._tuning_processed_cache = (key, processed[0], processed[1])
            elif current_result.get("tuning_error"):
                self._tuning_curve_error = str(current_result["tuning_error"])
            elif (
                self.settings.auto_load_tuning_curve
                and current_result.get("tuning_path") is None
                and not self.show_waveform_var.get()
            ):
                # A missing optional file should not reserve two fifths of the
                # RF tab. Keep a small, explicit HD restore control instead.
                self.tuning_collapsed_var.set(True)
                self._sync_tuning_collapsed_state(schedule_redraw=False)

        self._draw_probe_canvas()
        if self._active_tab_key() == "rf":
            self._draw_tuning_curve()

    def _schedule_optional_autoload(self) -> None:
        if self._optional_autoload_after is not None:
            try:
                self.after_cancel(self._optional_autoload_after)
            except tk.TclError:
                pass
        self._optional_autoload_generation += 1
        generation = self._optional_autoload_generation
        # Give Tk a chance to map and paint the RF window before starting the
        # mounted-volume discovery worker.
        self._optional_autoload_after = self.after(
            100,
            lambda: self._autoload_optional_resources_deferred(generation),
        )

    def _load_tuning_curve_path(
        self,
        path: Path,
        *,
        show_error: bool = True,
        redraw: bool = True,
    ) -> bool:
        resolved = Path(path).expanduser().resolve()
        previous_data = self.tuning_curve_data
        previous_error = self._tuning_curve_error
        previous_candidate = self._tuning_curve_candidate
        try:
            data = self.data.attach_hd_tuning(resolved)
            identity = self.data._hd_tuning_identity
            if identity is not None:
                self._app_root._rfm_tuning_cache[str(resolved)] = (
                    identity.mtime_ns,
                    identity.size,
                    data,
                )
        except (OSError, ValueError) as exc:
            if show_error:
                self.tuning_curve_data = previous_data
                self._tuning_curve_error = previous_error
                self._tuning_curve_candidate = previous_candidate
                messagebox.showerror("Could not attach tuning curves", str(exc), parent=self)
            else:
                self.tuning_curve_data = None
                self._tuning_curve_error = str(exc)
                self._tuning_curve_candidate = resolved
                self._tuning_processed_cache = None
                self._tuning_scale_cache = None
            if redraw:
                self._draw_tuning_curve()
            return False
        self.tuning_curve_data = data
        self._tuning_curve_error = None
        self._tuning_curve_candidate = resolved
        self._tuning_processed_cache = None
        self._tuning_scale_cache = None
        self.tuning_collapsed_var.set(False)
        self._sync_tuning_collapsed_state(schedule_redraw=False)
        if redraw:
            self._draw_tuning_curve()
        return True

    def _attach_tuning_curve(self) -> None:
        if not self.show_tuning_curve_var.get():
            return
        initial_dir = (
            self._tuning_curve_candidate.parent
            if self._tuning_curve_candidate is not None
            else self.data.path.parent
        )
        path = filedialog.askopenfilename(
            parent=self,
            title="Attach tuning curves",
            initialdir=str(initial_dir),
            filetypes=TUNING_CURVE_FILETYPES,
        )
        if path:
            self._load_tuning_curve_path(Path(path))

    def _clear_tuning_curve(self) -> None:
        with self.data._hd_tuning_lock:
            self.data._hd_tuning = None
            self.data._hd_tuning_identity = None
            self.data._hd_tuning_error = None
            self.data._hd_tuning_checked = True
        self.tuning_curve_data = None
        self._tuning_curve_error = None
        self._tuning_curve_candidate = None
        self._tuning_processed_cache = None
        self._tuning_scale_cache = None
        self._draw_tuning_curve()

    def _on_tuning_curve_click(self, _event: object | None = None) -> None:
        if self.show_tuning_curve_var.get() and self.tuning_curve_data is None:
            self._attach_tuning_curve()

    def _load_probe_geometry_path(
        self,
        positions: Path,
        *,
        show_error: bool = True,
        redraw: bool = True,
    ) -> bool:
        previous_geometry = self.probe_geometry
        try:
            geometry = self.data.attach_probe_geometry(
                positions,
                self._infer_attached_channels_path(positions),
                probe_name=probe_name_for_json(self.data.path) or positions.parent.name,
            )
        except (OSError, ValueError) as exc:
            self.probe_geometry = previous_geometry
            if show_error:
                messagebox.showerror("Could not attach probe geometry", str(exc), parent=self)
            if redraw:
                self._draw_probe_canvas()
            return False
        self.probe_geometry = geometry
        self._probe_static_signature = None
        self.spatial_region = None
        self._sync_unit_combo()
        if redraw:
            self._draw_probe_canvas()
        return True

    def _install_optional_drop_targets(self) -> None:
        self._optional_drop_available = False
        self._dnd_copy_action = "copy"
        self._dnd_refuse_action = "refuse_drop"
        try:
            from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP, TkinterDnD

            TkinterDnD.require(self._app_root)
            self._dnd_copy_action = COPY
            self._dnd_refuse_action = REFUSE_DROP
            for widget, resource in (
                (self.probe_canvas, "probe"),
                (self.tuning_curve_canvas, "tuning"),
            ):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind(
                    "<<Drop>>",
                    lambda event, target=resource: self._on_optional_file_drop(
                        target, event
                    ),
                )
        except (ImportError, RuntimeError, tk.TclError):
            return
        self._optional_drop_available = True

    def _on_optional_file_drop(self, resource: str, event: object) -> str:
        copy_action = getattr(self, "_dnd_copy_action", "copy")
        refuse_action = getattr(self, "_dnd_refuse_action", "refuse_drop")
        if resource not in {"probe", "tuning"}:
            return refuse_action
        visible = (
            self.show_probe_layout_var.get()
            if resource == "probe"
            else self.show_tuning_curve_var.get()
        )
        if not visible:
            return refuse_action
        raw_data = getattr(event, "data", "")
        try:
            paths = tuple(Path(value) for value in self.tk.splitlist(raw_data))
        except tk.TclError:
            paths = ()
        if len(paths) != 1:
            messagebox.showerror(
                "Could not attach file",
                "Drop exactly one file at a time.",
                parent=self,
            )
            return refuse_action
        if resource == "probe":
            loaded = self._load_probe_geometry_path(paths[0])
        else:
            loaded = self._load_tuning_curve_path(paths[0])
        return copy_action if loaded else refuse_action

    def _sync_optional_menu_states(self) -> None:
        menu = getattr(self, "_file_menu", None)
        if menu is None:
            return
        try:
            menu.entryconfigure(
                "Attach Probe Geometry…",
                state="normal" if self.show_probe_layout_var.get() else "disabled",
            )
            menu.entryconfigure(
                "Attach Tuning Curves…",
                state="normal" if self.show_tuning_curve_var.get() else "disabled",
            )
        except tk.TclError:
            return

    def _sync_optional_view_visibility(self, *, redraw: bool = True) -> None:
        if not self.show_probe_layout_var.get():
            self.spatial_region = None
            self.probe_geometry = None
            self._probe_static_signature = None
            self.probe_section.grid_remove()
            self.sidebar_frame.rowconfigure(self._probe_section_row, weight=0)
        else:
            self.probe_section.grid()
            self._sync_probe_collapsed_state(schedule_redraw=False)

        if not self.show_tuning_curve_var.get():
            self.tuning_curve_data = None
            self._tuning_curve_error = None
            self._tuning_curve_candidate = None
            self._tuning_processed_cache = None
            self._tuning_scale_cache = None
        if not self.show_waveform_var.get():
            had_waveform_state = any(
                value is not None
                for value in (
                    self.waveform_payload,
                    self._waveform_payload_key,
                    self._waveform_loading_key,
                    self._waveform_error,
                    self._waveform_error_key,
                )
            )
            if had_waveform_state:
                self._waveform_generation += 1
            self.waveform_payload = None
            self._waveform_payload_key = None
            self._waveform_loading_key = None
            self._waveform_error = None
            self._waveform_error_key = None
            self.waveform_subtitle_label.configure(text="")
            self.waveform_canvas.delete("all")
        self._sync_auxiliary_sections()
        self._sync_tuning_collapsed_state(schedule_redraw=False)
        self._layout_rf_and_tuning()
        self._sync_optional_menu_states()
        if redraw:
            self._draw_probe_canvas()
            self._draw_tuning_curve()
            if self.show_waveform_var.get() and self._active_tab_key() == "rf":
                self._draw_waveform()

    def _effective_tuning_plot_mode(self) -> str:
        mode = self.tuning_plot_mode_var.get()
        if mode == "Auto":
            return "Polar" if self.polar_layout_var.get() else "Line"
        return mode if mode in {"Polar", "Line"} else "Line"

    def _processed_tuning_values(
        self,
        cluster_id: int,
        rates: Sequence[float],
    ) -> tuple[tuple[float, ...], tuple[float, ...]]:
        bins = normalize_hd_bin_count(self.tuning_display_bins_var.get())
        smoothing = bool(self.tuning_smoothing_var.get())
        sigma = float(self.tuning_smooth_sigma_var.get())
        key = (
            self.tuning_curve_data.path if self.tuning_curve_data is not None else None,
            int(cluster_id),
            bins,
            smoothing,
            sigma,
        )
        cached = self._tuning_processed_cache
        if cached is not None and cached[0] == key:
            return cached[1], cached[2]
        processed = (
            self.tuning_curve_data.processed_for(
                cluster_id,
                bins,
                smoothing=smoothing,
                sigma=sigma,
            )
            if self.tuning_curve_data is not None
            else None
        )
        if processed is None:
            centers, values = processed_tuning_curve(
                rates,
                bins,
                smoothing=smoothing,
                sigma=sigma,
            )
        else:
            centers, values = processed
        self._tuning_processed_cache = (key, centers, values)
        return centers, values

    def _shared_tuning_scale_high(self) -> float:
        data = self.tuning_curve_data
        if data is None:
            return 0.0
        bins = normalize_hd_bin_count(self.tuning_display_bins_var.get())
        smoothing = bool(self.tuning_smoothing_var.get())
        sigma = float(self.tuning_smooth_sigma_var.get())
        cached = self._tuning_scale_cache
        if (
            cached is not None
            and cached[0] is data
            and cached[1:4] == (bins, smoothing, sigma)
        ):
            return float(cached[4])

        high = 0.0
        for cluster_id in data.curves:
            processed = data.processed_for(
                cluster_id,
                bins,
                smoothing=smoothing,
                sigma=sigma,
            )
            if processed is not None:
                high = max(high, tuning_rate_peak(processed[1]))
        self._tuning_scale_cache = (data, bins, smoothing, sigma, high)
        return high

    def _set_tuning_hd_class_label(self, hd_class: int | None) -> None:
        if not hasattr(self, "tuning_hd_class_label"):
            return
        if hd_class == 1:
            self.tuning_hd_class_label.configure(text="1", style="HDClass1.TLabel")
            self.tuning_hd_class_label.grid()
        elif hd_class == 2:
            self.tuning_hd_class_label.configure(text="2", style="HDClass2.TLabel")
            self.tuning_hd_class_label.grid()
        else:
            self.tuning_hd_class_label.configure(text="", style="Panel.TLabel")
            self.tuning_hd_class_label.grid_remove()

    def _show_tuning_provenance(self) -> None:
        data = self.tuning_curve_data
        metadata = data.metadata if data is not None else None
        if metadata is None:
            return

        rows = [
            ("Schema", "2"),
            ("Timestamp", metadata.timestamp_reference or "Not recorded"),
            ("Timebase", metadata.timebase or "Not recorded"),
            ("Direction", metadata.angle_convention_note or "Not recorded"),
        ]
        if metadata.feature_fs_hz is not None:
            rows.append(("Tracking", f"{metadata.feature_fs_hz:g} Hz"))
        classification = metadata.classification
        if classification is not None:
            rows.append(("Classification", classification.method or "Not recorded"))
            if classification.rayleigh_alpha is not None:
                rows.append(("Rayleigh α", f"{classification.rayleigh_alpha:g}"))
            if classification.shuffle_alpha is not None:
                rows.append(("Shuffle α", f"{classification.shuffle_alpha:g}"))
            if classification.num_shuffle is not None:
                rows.append(("Shuffles", str(classification.num_shuffle)))
        ttl_qc = metadata.ttl_qc
        if ttl_qc is not None:
            if ttl_qc.ttl_pulse_count is not None:
                rows.append(("Motive trigger TTLs", str(ttl_qc.ttl_pulse_count)))
            if ttl_qc.measured_rate_hz is not None:
                rows.append(("Measured rate", f"{ttl_qc.measured_rate_hz:g} Hz"))
            if ttl_qc.median_period_s is not None:
                rows.append(("Median period", f"{ttl_qc.median_period_s:g} s"))
            if ttl_qc.camera_input_channel is not None:
                rows.append(("Camera input", str(ttl_qc.camera_input_channel)))
            if ttl_qc.camera_ttl_threshold is not None:
                rows.append(("TTL threshold", f"{ttl_qc.camera_ttl_threshold:g}"))
            if ttl_qc.camera_ttl_active_high is not None:
                rows.append(
                    (
                        "TTL polarity",
                        "Active high" if ttl_qc.camera_ttl_active_high else "Active low",
                    )
                )
            if (
                ttl_qc.matched_motive_frame_count is not None
                and ttl_qc.motive_frame_count_raw is not None
            ):
                rows.append(
                    (
                        "Matched frames",
                        f"{ttl_qc.matched_motive_frame_count} / {ttl_qc.motive_frame_count_raw}",
                    )
                )
            if ttl_qc.frame_alignment_policy_applied is not None:
                rows.append(
                    ("Alignment", ttl_qc.frame_alignment_policy_applied)
                )
            if ttl_qc.dropped_motive_frame_ids:
                rows.append(
                    (
                        "Dropped frame IDs",
                        ", ".join(str(value) for value in ttl_qc.dropped_motive_frame_ids),
                    )
                )
            if ttl_qc.frame_timestamp_mapping is not None:
                rows.append(("Frame mapping", ttl_qc.frame_timestamp_mapping))
        label_width = max(len(label) for label, _value in rows)
        messagebox.showinfo(
            "Tuning Provenance",
            "\n".join(f"{label:<{label_width}}   {value}" for label, value in rows),
            parent=self,
        )

    def _draw_tuning_placeholder(self, title: str, detail: str) -> None:
        canvas = self.tuning_curve_canvas
        width = max(canvas.winfo_width(), 280)
        height = max(canvas.winfo_height(), 220)
        offers_attach = title in {"No tuning curves", "Could not load tuning curves"}
        canvas.create_text(
            width / 2,
            height / 2 - 28 if offers_attach else height / 2 - 12,
            text=title,
            fill="#1d1d1f",
            font=("TkDefaultFont", 13, "bold"),
        )
        canvas.create_text(
            width / 2,
            height / 2 + 4 if offers_attach else height / 2 + 22,
            text=detail,
            justify="center",
            fill="#6e6e73",
            font=("TkDefaultFont", 10),
        )
        if offers_attach and hasattr(self, "tuning_attach_button"):
            canvas.create_window(
                width / 2,
                height / 2 + 48,
                window=self.tuning_attach_button,
            )

    def _draw_tuning_curve(self) -> None:
        if (
            not hasattr(self, "tuning_curve_canvas")
            or not self.show_tuning_curve_var.get()
            or self.tuning_collapsed_var.get()
        ):
            return
        self._set_tuning_hd_class_label(None)
        canvas = self.tuning_curve_canvas
        canvas.delete("all")
        cluster_id = self._selected_unit_id_value()
        if hasattr(self, "tuning_cluster_label"):
            self.tuning_cluster_label.configure(text=f"Cluster {cluster_id}")
        filter_status = self._quality_filter_status(cluster_id)
        if filter_status is not None and not self._local_unit_passes_quality_filter(
            cluster_id
        ):
            if hasattr(self, "tuning_provenance_button"):
                self.tuning_provenance_button.grid_remove()
            self._draw_tuning_placeholder("Unit filtered", filter_status)
            self.tuning_curve_status_label.configure(text=filter_status)
            return
        data = self.tuning_curve_data
        if data is None:
            if hasattr(self, "tuning_provenance_button"):
                self.tuning_provenance_button.grid_remove()
            detail = (
                "No tuning_curves.tc or tuning_curves.json was found automatically for this "
                "recording date. Generate it with the analysis pipeline, "
                "or attach a matching file.\nAttach head-direction data "
                "for the selected RF unit."
            )
            if self._optional_drop_available:
                detail += "\nYou can also drop a .tc or tuning JSON file here."
            if self._tuning_curve_error:
                self._draw_tuning_placeholder("Could not load tuning curves", detail)
                self.tuning_curve_status_label.configure(text=self._tuning_curve_error)
            else:
                self._draw_tuning_placeholder("No tuning curves", detail)
                self.tuning_curve_status_label.configure(text="Tuning curves optional")
            return

        if hasattr(self, "tuning_provenance_button"):
            if data.metadata is None:
                self.tuning_provenance_button.grid_remove()
            else:
                self.tuning_provenance_button.grid()

        if getattr(self._app_root, "_rfm_pairing_enabled", False):
            ready, eligible = self._pairing_eligibility()
            rf_unit_ids = (
                set(self._quality_filtered_pairing_unit_ids(ready))
                if eligible
                else set(self._local_quality_visible_unit_ids())
            )
        else:
            rf_unit_ids = set(self._local_quality_visible_unit_ids())
        if cluster_id not in rf_unit_ids:
            self._draw_tuning_placeholder(
                f"Cluster {cluster_id} skipped",
                "No open RF map contains this cluster.",
            )
            self.tuning_curve_status_label.configure(text=data.path.name)
            return
        raw_rates = data.rates_for(cluster_id)
        if raw_rates is None:
            self._draw_tuning_placeholder(
                f"No tuning curve for cluster {cluster_id}",
                "The selected RF unit is not present in this tuning file.",
            )
            self.tuning_curve_status_label.configure(text=data.path.name)
            return
        self._set_tuning_hd_class_label(data.hd_class_for(cluster_id))
        try:
            angles_deg, rates = self._processed_tuning_values(cluster_id, raw_rates)
            scale_high = (
                self._shared_tuning_scale_high()
                if self.tuning_compare_scale_var.get()
                else tuning_rate_peak(rates)
            )
        except (ImportError, ValueError) as exc:
            self._draw_tuning_placeholder("Could not plot tuning curve", str(exc))
            self.tuning_curve_status_label.configure(text=data.path.name)
            return

        if self._effective_tuning_plot_mode() == "Polar":
            self._draw_tuning_polar(angles_deg, rates, cluster_id, scale_high)
        else:
            self._draw_tuning_line(angles_deg, rates, cluster_id, scale_high)
        sigma_deg = (
            float(self.tuning_smooth_sigma_var.get())
            * 360.0
            / DEFAULT_HD_DISPLAY_BINS
        )
        smooth_label = (
            f" · smoothed σ={sigma_deg:g}°"
            if self.tuning_smoothing_var.get()
            else " · smoothing off"
        )
        scale_label = (
            f" · shared within file: 0–{format_response_value(scale_high, VALUE_MODE_RATE)} Hz"
            if self.tuning_compare_scale_var.get()
            else " · per-cell 0–peak Hz scale"
        )
        missing_bins = sum(not math.isfinite(float(rate)) for rate in rates)
        missing_label = f" · {missing_bins} bins without occupancy" if missing_bins else ""
        legacy_label = ""
        if data.occupancy_time_s is None:
            legacy_label = " · legacy schema (timing/occupancy provenance unavailable)"
            if len(rates) < HD_RAW_BIN_COUNT:
                legacy_label += " · rebinned rates averaged"
        self.tuning_curve_status_label.configure(
            text=(
                f"{data.path.name} · {len(rates)} bins{smooth_label}"
                f"{scale_label}{missing_label}{legacy_label}"
            )
        )

    def _draw_tuning_line(
        self,
        angles_deg: Sequence[float],
        rates: Sequence[float],
        cluster_id: int,
        scale_high: float | None = None,
    ) -> None:
        canvas = self.tuning_curve_canvas
        width = max(canvas.winfo_width(), 280)
        height = max(canvas.winfo_height(), 220)
        left, right, top, bottom = 54.0, width - 16.0, 18.0, height - 44.0
        plot_width = max(1.0, right - left)
        plot_height = max(1.0, bottom - top)
        current_high = tuning_rate_peak(rates)
        high = max(
            current_high,
            float(scale_high) if scale_high is not None else current_high,
        )
        denominator = high if high > 1e-12 else 1.0
        centered_angles, centered_rates = center_tuning_curve_on_zero(
            angles_deg,
            rates,
        )
        canvas.create_line(left, top, left, bottom, right, bottom, fill="#98a2b3")
        for angle, label in zip(
            (-180, -90, 0, 90, 180),
            ("180", "90", "0", "270", "180"),
        ):
            x = left + plot_width * (angle + 180.0) / 360.0
            canvas.create_line(x, bottom, x, bottom + 4, fill="#98a2b3")
            canvas.create_text(x, bottom + 16, text=label, fill="#667085", font=("TkDefaultFont", 10))
        tick_fractions = (0.0, 0.5, 1.0) if high > 1e-12 else (0.0,)
        for fraction in tick_fractions:
            y = bottom - plot_height * fraction
            if fraction:
                canvas.create_line(left, y, right, y, fill="#eaecf0", dash=(3, 3))
            canvas.create_text(
                left - 7,
                y,
                anchor="e",
                text=f"{high * fraction:.3g}",
                fill="#667085",
                font=("TkDefaultFont", 10),
            )
        segments: list[list[float]] = []
        points: list[float] = []
        for angle, rate in zip(centered_angles, centered_rates):
            if not math.isfinite(float(rate)):
                if points:
                    segments.append(points)
                    points = []
                continue
            normalized = max(0.0, float(rate)) / denominator
            points.extend(
                (
                    left + plot_width * (float(angle) + 180.0) / 360.0,
                    bottom - plot_height * normalized,
                )
            )
        if points:
            segments.append(points)
        for points in segments:
            if len(points) >= 4:
                canvas.create_line(*points, fill="#1570ef", width=2, joinstyle="round")
            elif len(points) == 2:
                x, y = points
                canvas.create_oval(
                    x - 3,
                    y - 3,
                    x + 3,
                    y + 3,
                    fill="#1570ef",
                    outline="",
                )
        canvas.create_text(
            (left + right) / 2,
            height - 10,
            text="Head direction (deg)",
            fill="#475467",
            font=("TkDefaultFont", 10),
        )
        canvas.create_text(
            12,
            (top + bottom) / 2,
            text="Hz",
            angle=90,
            fill="#475467",
            font=("TkDefaultFont", 10),
        )

    def _draw_tuning_polar(
        self,
        angles_deg: Sequence[float],
        rates: Sequence[float],
        cluster_id: int,
        scale_high: float | None = None,
    ) -> None:
        canvas = self.tuning_curve_canvas
        width = max(canvas.winfo_width(), 280)
        height = max(canvas.winfo_height(), 220)
        center_x = width / 2.0
        center_y = height / 2.0 + 8.0
        radius = max(30.0, min(width, height) / 2.0 - 40.0)
        current_high = tuning_rate_peak(rates)
        radial_high = max(
            current_high,
            float(scale_high) if scale_high is not None else current_high,
        )
        denominator = radial_high if radial_high > 1e-12 else 1.0
        canvas.create_oval(
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
            outline="#c7c9ce",
        )
        for angle, label in ((0, "0°"), (90, "90°"), (180, "180°"), (270, "270°")):
            vector_x, vector_y = head_direction_unit_vector(angle)
            canvas.create_line(
                center_x,
                center_y,
                center_x + vector_x * radius,
                center_y + vector_y * radius,
                fill="#eaecf0",
            )
            canvas.create_text(
                center_x + vector_x * (radius + 15),
                center_y + vector_y * (radius + 15),
                text=label,
                fill="#667085",
                font=("TkDefaultFont", 10),
            )

        # A single labelled radial axis states the scale without implying
        # that decorative rings are measured contours.
        scale_x, scale_y = head_direction_unit_vector(315.0)
        normal_x, normal_y = -scale_y, scale_x
        canvas.create_line(
            center_x,
            center_y,
            center_x + scale_x * radius,
            center_y + scale_y * radius,
            fill="#d2d3d7",
        )
        tick_fractions = (0.0, 0.5, 1.0) if radial_high > 1e-12 else (0.0,)
        for fraction in tick_fractions:
            tick_x = center_x + scale_x * radius * fraction
            tick_y = center_y + scale_y * radius * fraction
            canvas.create_line(
                tick_x - normal_x * 3,
                tick_y - normal_y * 3,
                tick_x + normal_x * 3,
                tick_y + normal_y * 3,
                fill="#8e8e93",
            )
            canvas.create_text(
                tick_x + normal_x * 8,
                tick_y + normal_y * 8,
                anchor="w",
                text=f"{radial_high * fraction:.3g} Hz",
                fill="#6e6e73",
                font=("TkDefaultFont", 10),
            )
        points: list[tuple[float, float] | None] = []
        for angle, rate in zip(angles_deg, rates):
            if not math.isfinite(float(rate)):
                points.append(None)
                continue
            vector_x, vector_y = head_direction_unit_vector(angle)
            scaled = radius * max(0.0, float(rate)) / denominator
            points.append((center_x + vector_x * scaled, center_y + vector_y * scaled))
        finite_points = [point for point in points if point is not None]
        if radial_high <= 1e-12 and finite_points:
            canvas.create_oval(
                center_x - 3,
                center_y - 3,
                center_x + 3,
                center_y + 3,
                fill="#1570ef",
                outline="",
            )
        elif len(finite_points) >= 3 and len(finite_points) == len(points):
            flattened = [coordinate for point in finite_points for coordinate in point]
            canvas.create_line(
                *flattened,
                *finite_points[0],
                fill="#1570ef",
                width=2,
                joinstyle="round",
            )
        elif len(finite_points) <= 2:
            for x, y in finite_points:
                canvas.create_oval(
                    x - 3,
                    y - 3,
                    x + 3,
                    y + 3,
                    fill="#1570ef",
                    outline="",
                )
        else:
            segments: list[list[tuple[float, float]]] = []
            segment: list[tuple[float, float]] = []
            for point in points:
                if point is None:
                    if segment:
                        segments.append(segment)
                        segment = []
                else:
                    segment.append(point)
            if segment:
                segments.append(segment)
            if points[0] is not None and points[-1] is not None and len(segments) > 1:
                segments[0] = segments[-1] + segments[0]
                segments.pop()
            for segment in segments:
                flattened = [coordinate for point in segment for coordinate in point]
                if len(segment) >= 2:
                    canvas.create_line(
                        *flattened,
                        fill="#1570ef",
                        width=2,
                        joinstyle="round",
                    )
                else:
                    x, y = segment[0]
                    canvas.create_oval(
                        x - 3,
                        y - 3,
                        x + 3,
                        y + 3,
                        fill="#1570ef",
                        outline="",
                    )

    def _infer_attached_channels_path(self, positions_path: Path) -> Path | None:
        sibling = positions_path.with_name("channels.csv")
        if sibling.is_file():
            return sibling
        probe_name = positions_path.parent.name
        for ancestor in positions_path.parents:
            if ancestor.name == "spike_position":
                candidate = ancestor.parent / "waveform" / probe_name / "channels.csv"
                return candidate if candidate.is_file() else None
        return None

    def _attach_probe_geometry(self) -> None:
        if not self.show_probe_layout_var.get():
            return
        path = filedialog.askopenfilename(
            parent=self,
            title="Attach probe positions",
            initialdir=str(self.data.path.parent),
            filetypes=PROBE_POSITION_FILETYPES,
        )
        if not path:
            return
        self._load_probe_geometry_path(Path(path))

    def _probe_to_canvas(self, x_um: float, y_um: float) -> tuple[float, float] | None:
        transform = self._probe_canvas_transform
        if transform is None:
            return None
        x_min, y_min, x_scale, y_scale = transform
        height = max(self.probe_canvas.winfo_height(), 2)
        margin = 14.0
        return margin + (x_um - x_min) * x_scale, height - margin - (y_um - y_min) * y_scale

    def _canvas_to_probe(self, canvas_x: float, canvas_y: float) -> tuple[float, float] | None:
        transform = self._probe_canvas_transform
        if transform is None:
            return None
        x_min, y_min, x_scale, y_scale = transform
        if x_scale <= 0 or y_scale <= 0:
            return None
        height = max(self.probe_canvas.winfo_height(), 2)
        margin = 14.0
        return (
            x_min + (canvas_x - margin) / x_scale,
            y_min + (height - margin - canvas_y) / y_scale,
        )

    def _draw_probe_canvas(self) -> None:
        if not hasattr(self, "probe_canvas"):
            return
        if self.probe_collapsed_var.get():
            return
        canvas = self.probe_canvas
        if not self.show_probe_layout_var.get():
            return
        geometry = self.probe_geometry
        compact = geometry is None
        requested_height = 170 if compact else 330
        self.probe_section.rowconfigure(1, weight=0 if compact else 1)
        self.sidebar_frame.rowconfigure(
            self._probe_section_row,
            weight=0 if compact else 1,
        )
        if int(float(canvas.cget("height"))) != requested_height:
            canvas.configure(height=requested_height)
        width = max(canvas.winfo_width(), 220)
        height = max(canvas.winfo_height(), 200)
        if geometry is None:
            self._probe_canvas_transform = None
            detail = "Geometry is optional"
            if getattr(self, "_optional_drop_available", False):
                detail += " · drop is supported"
            signature = ("missing", width, height, detail)
            if signature != self._probe_static_signature:
                canvas.delete("all")
                canvas.create_text(
                    width / 2,
                    height / 2 - 34,
                    text="No probe geometry",
                    justify="center",
                    fill="#1d1d1f",
                    font=("TkDefaultFont", 12, "bold"),
                    tags=("probe-static",),
                )
                canvas.create_text(
                    width / 2,
                    height / 2 - 8,
                    text=detail,
                    justify="center",
                    fill="#6e6e73",
                    font=("TkDefaultFont", 10),
                    tags=("probe-static",),
                )
                if hasattr(self, "probe_attach_button"):
                    canvas.create_window(
                        width / 2,
                        height / 2 + 30,
                        window=self.probe_attach_button,
                        tags=("probe-static",),
                    )
                self._probe_static_signature = signature
            self.spatial_status_label.configure(text="Geometry optional")
            self.clear_spatial_button.state(["disabled"])
            return

        available = set(self._local_quality_visible_unit_ids())
        units = [unit for unit in geometry.units if unit.unit_id in available]
        points = [(channel.x_um, channel.y_um) for channel in geometry.channels]
        positioned_units = [
            unit
            for unit in units
            if unit.x_um is not None and unit.y_um is not None
        ]
        points.extend(
            (float(unit.x_um), float(unit.y_um))
            for unit in positioned_units
        )
        selected_id = self._selected_unit_id_value()
        selected = (
            geometry.units_by_id.get(selected_id)
            if selected_id in available
            else None
        )
        if not points:
            self._probe_canvas_transform = None
            signature = ("no-matches", id(geometry), width, height)
            if signature != self._probe_static_signature:
                canvas.delete("all")
                canvas.create_text(
                    width / 2,
                    height / 2 + 42,
                    text=(
                        "Geometry has no finite positions"
                        if units
                        else "Geometry has no visible units"
                    ),
                    fill="#667085",
                    tags=("probe-static",),
                )
                self._probe_static_signature = signature
            canvas.delete("probe-selection")
            if (
                self.spatial_region is None
                and selected is not None
                and selected.x_um is None
                and selected.y_um is None
            ):
                canvas.create_text(
                    width / 2,
                    height / 2,
                    text="NaN",
                    fill="#b42318",
                    font=("TkDefaultFont", 24, "bold"),
                    tags=("probe-selection",),
                )
            if self.spatial_region is None:
                self.spatial_status_label.configure(
                    text=f"{geometry.probe_name} · 0/{len(units)} units positioned"
                )
                self.clear_spatial_button.state(["disabled"])
            else:
                self.spatial_status_label.configure(text="No units in region")
                self.clear_spatial_button.state(["!disabled"])
            return
        region_ids = set(self._unit_navigation_ids()) if self.spatial_region is not None else set()
        signature = (
            id(geometry),
            id(self.data),
            width,
            height,
            self.spatial_region,
            frozenset(region_ids),
        )
        if signature != self._probe_static_signature:
            canvas.delete("all")
            xs, ys = zip(*points)
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            if x_max <= x_min:
                x_min, x_max = x_min - 1.0, x_max + 1.0
            if y_max <= y_min:
                y_min, y_max = y_min - 1.0, y_max + 1.0
            margin = 14.0
            self._probe_canvas_transform = (
                x_min,
                y_min,
                (width - margin * 2.0) / (x_max - x_min),
                (height - margin * 2.0) / (y_max - y_min),
            )
            shank_colors = ("#98a2b3", "#7f8ea3", "#667085", "#475467")
            for channel in geometry.channels:
                point = self._probe_to_canvas(channel.x_um, channel.y_um)
                if point is None:
                    continue
                x, y = point
                color = shank_colors[channel.shank_id % len(shank_colors)]
                canvas.create_rectangle(
                    x - 2,
                    y - 2,
                    x + 2,
                    y + 2,
                    fill=color,
                    outline="",
                    tags=("probe-static",),
                )
            for unit in positioned_units:
                point = self._probe_to_canvas(
                    float(unit.x_um), float(unit.y_um)
                )
                if point is None:
                    continue
                x, y = point
                in_region = unit.unit_id in region_ids
                fill = "#f79009" if in_region else "#2e90fa"
                radius = 4 if in_region else 3
                canvas.create_oval(
                    x - radius,
                    y - radius,
                    x + radius,
                    y + radius,
                    fill=fill,
                    outline="#ffffff",
                    tags=("probe-static",),
                )
            if self.spatial_region is not None:
                top_left = self._probe_to_canvas(
                    self.spatial_region.x_min, self.spatial_region.y_max
                )
                bottom_right = self._probe_to_canvas(
                    self.spatial_region.x_max, self.spatial_region.y_min
                )
                if top_left is not None and bottom_right is not None:
                    canvas.create_rectangle(
                        *top_left,
                        *bottom_right,
                        outline="#f04438",
                        width=2,
                        dash=(5, 3),
                        tags=("probe-static",),
                    )
            self._probe_static_signature = signature

        canvas.delete("probe-selection")
        if (
            selected is not None
            and selected.x_um is not None
            and selected.y_um is not None
            and (
            self.spatial_region is None or selected_id in region_ids
            )
        ):
            point = self._probe_to_canvas(
                float(selected.x_um), float(selected.y_um)
            )
            if point is not None:
                x, y = point
                canvas.create_oval(
                    x - 7,
                    y - 7,
                    x + 7,
                    y + 7,
                    outline="#d92d20",
                    width=2,
                    tags=("probe-selection",),
                )
        if (
            self.spatial_region is None
            and selected is not None
            and selected.x_um is None
            and selected.y_um is None
        ):
            canvas.create_text(
                width / 2,
                height / 2,
                text="NaN",
                fill="#b42318",
                font=("TkDefaultFont", 24, "bold"),
                tags=("probe-selection",),
            )
        count = (
            len(region_ids)
            if self.spatial_region is not None
            else len(positioned_units)
        )
        if self.spatial_region is None:
            status = (
                f"{geometry.probe_name} · {count}/{len(units)} units positioned"
            )
            self.clear_spatial_button.state(["disabled"])
        elif count:
            status = f"{count} unit{'s' if count != 1 else ''} in region"
            self.clear_spatial_button.state(["!disabled"])
        else:
            status = "No units in region"
            self.clear_spatial_button.state(["!disabled"])
        self.spatial_status_label.configure(text=status)

    def _on_probe_press(self, event: tk.Event) -> None:
        point = self._canvas_to_probe(float(event.x), float(event.y))
        self._probe_drag_start = point
        self._probe_press_canvas = (float(event.x), float(event.y))
        self._probe_drag_moved = False

    def _on_probe_drag(self, event: tk.Event) -> None:
        start_canvas = getattr(self, "_probe_press_canvas", None)
        if start_canvas is None:
            return
        self._probe_drag_moved = math.hypot(event.x - start_canvas[0], event.y - start_canvas[1]) >= 4.0

    def _on_probe_release(self, event: tk.Event) -> None:
        start = self._probe_drag_start
        end = self._canvas_to_probe(float(event.x), float(event.y))
        self._probe_drag_start = None
        if self.probe_geometry is None and not self._probe_drag_moved:
            self._attach_probe_geometry()
            return
        if start is None or end is None or self.probe_geometry is None:
            return
        if self._probe_drag_moved:
            region = SpatialRegion.from_corners(start[0], start[1], end[0], end[1])
        else:
            nearest: tuple[float, ProbeChannel] | None = None
            for channel in self.probe_geometry.channels:
                point = self._probe_to_canvas(channel.x_um, channel.y_um)
                if point is None:
                    continue
                distance = math.hypot(event.x - point[0], event.y - point[1])
                if nearest is None or distance < nearest[0]:
                    nearest = distance, channel
            if nearest is None or nearest[0] > 14.0:
                return
            channel = nearest[1]
            region = SpatialRegion.centered(channel.x_um, channel.y_um)
        self._apply_spatial_region(region)

    def _apply_spatial_region(self, region: SpatialRegion) -> None:
        self.spatial_region = region
        eligible = self._unit_navigation_ids()
        if eligible:
            selected = self._selected_unit_id_value()
            if selected not in eligible:
                center_x = (region.x_min + region.x_max) / 2.0
                center_y = (region.y_min + region.y_max) / 2.0
                positions = self.probe_geometry.units_by_id if self.probe_geometry is not None else {}
                target = min(
                    eligible,
                    key=lambda unit_id: (
                        (float(positions[unit_id].x_um) - center_x) ** 2
                        + (float(positions[unit_id].y_um) - center_y) ** 2
                        if unit_id in positions
                        and positions[unit_id].x_um is not None
                        and positions[unit_id].y_um is not None
                        else math.inf
                    ),
                )
                self._set_selected_unit_id(target)
        else:
            self.unit_idx.set(-1)
        self.selected_cell = None
        self._sync_unit_combo()
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _clear_spatial_filter(self) -> None:
        if self.spatial_region is None:
            return
        self.spatial_region = None
        self._reconcile_unit_filter_selection()
        self._sync_unit_combo()
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _sync_unit_combo(self) -> None:
        unit_ids = self._unit_navigation_ids()
        self._unit_combo_unit_ids = unit_ids
        values: list[str] = []
        for unit_id in unit_ids:
            local_index = self._local_unit_index(unit_id)
            if local_index is None:
                values.append(f"N/A  cluster {unit_id} — not in this session")
            elif not self._local_unit_passes_quality_filter(unit_id):
                values.append(f"N/A  cluster {unit_id} — hidden by RF-bin filter")
            else:
                values.append(f"{local_index:03d}  cluster {unit_id}")
        self.unit_combo.configure(values=values)
        selected_unit_id = self._selected_unit_id_value()
        try:
            selected_index = unit_ids.index(selected_unit_id)
        except ValueError:
            self.unit_combo.set("")
        else:
            self.unit_combo.current(selected_index)

    def _on_unit_selected(self, _event: object | None = None) -> None:
        combo_index = self.unit_combo.current()
        unit_ids = self.__dict__.get("_unit_combo_unit_ids", [])
        if 0 <= combo_index < len(unit_ids):
            self._set_selected_unit_id(unit_ids[combo_index])
            self.selected_cell = None
            self._update_all()
            self._publish_pairing_state_if_changed()

    def _step_unit(self, delta: int) -> None:
        unit_ids = self._unit_navigation_ids()
        if not unit_ids:
            return
        selected_unit_id = self._selected_unit_id_value()
        try:
            current_index = unit_ids.index(selected_unit_id)
        except ValueError:
            selected_unit_id = self._next_union_unit_id(unit_ids, selected_unit_id)
            current_index = unit_ids.index(selected_unit_id)
        target_unit_id = unit_ids[(current_index + int(delta)) % len(unit_ids)]
        self._set_selected_unit_id(target_unit_id)
        self.selected_cell = None
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _step_timeline_bin(self, delta: int) -> None:
        max_bin = max(0, self._time_group_count() - 1)
        target = max(0, min(max_bin, self.bin_var.get() + delta))
        self.bin_var.set(target)
        self.range_start_var.set(target)
        self.range_end_var.set(target)
        self._timeline_range_anchor = target
        self._sync_time_range_controls()
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _step_time_resolution(self, delta_groups: float) -> None:
        try:
            current = float(self.time_res_ms_var.get())
        except (tk.TclError, TypeError, ValueError):
            current = self._base_bin_ms()
        source_bin_ms = self._base_bin_ms()
        target = max(
            source_bin_ms,
            min(self._total_time_ms(), current + delta_groups * source_bin_ms),
        )
        self.time_res_ms_var.set(format_ms(target))
        self._on_time_resolution_changed()

    def _clear_timeline_selection(self) -> None:
        self._timeline_range_anchor = None
        self.bin_var.set(0)
        self.range_start_var.set(0)
        self.range_end_var.set(max(0, self._time_group_count() - 1))
        self._sync_time_range_controls()
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _on_value_mode_changed(self, _event: object | None = None) -> None:
        value_mode = self.value_mode_var.get()
        if not self.data.supports_value_mode(value_mode):
            self.value_mode_var.set(VALUE_MODE_RATE)
            return
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _on_range_changed(self, _event: object | None = None) -> None:
        self._normalize_control_values()
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _reset_plot_range(self) -> None:
        start_ms, end_ms = self._default_plot_time_bounds_ms()
        self.range_start_ms_var.set(format_ms(start_ms))
        self.range_end_ms_var.set(format_ms(end_ms))
        self._on_range_changed()

    def _on_time_resolution_changed(self, _event: object | None = None) -> None:
        previous_groups = list(getattr(self, "_last_time_groups", ()))
        if not previous_groups:
            previous_groups = [(index, index) for index in range(self.data.n_bins)]
        previous_count = len(previous_groups)
        previous_start = max(
            0,
            min(
                previous_count - 1,
                min(self.range_start_var.get(), self.range_end_var.get()),
            ),
        )
        previous_end = max(
            0,
            min(
                previous_count - 1,
                max(self.range_start_var.get(), self.range_end_var.get()),
            ),
        )
        source_start = previous_groups[previous_start][0]
        source_end = previous_groups[previous_end][1]
        previous_bin = max(0, min(previous_count - 1, self.bin_var.get()))
        active_source_group = previous_groups[previous_bin]
        active_source_bin = (active_source_group[0] + active_source_group[1]) // 2
        was_full_timeline = (
            previous_start == 0
            and previous_end == previous_count - 1
        )
        self._timeline_range_anchor = None
        self._normalize_control_values()
        new_groups = list(self._last_time_groups)
        if was_full_timeline:
            self.range_start_var.set(0)
            self.range_end_var.set(len(new_groups) - 1)
        else:
            self.range_start_var.set(display_group_index_for_source_bin(new_groups, source_start))
            self.range_end_var.set(display_group_index_for_source_bin(new_groups, source_end))
        self.bin_var.set(display_group_index_for_source_bin(new_groups, active_source_bin))
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _on_control_changed(self, _event: object | None = None) -> None:
        if self.__dict__.get("_pair_apply_in_progress", False):
            return
        self._normalize_control_values()
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _on_spatial_format_changed(self) -> None:
        self._timeline_preview_cache_key = None
        self._timeline_preview_images = {}
        self._on_control_changed()

    def _on_tab_changed(self, _event: object | None = None) -> None:
        self._sync_context_controls()
        self._on_control_changed()

    def _sync_context_controls(self) -> None:
        if not hasattr(self, "rgb_mode_toggle"):
            return
        for frame in (
            self.range_controls_frame,
            self.delay_controls_frame,
            self.timeline_context_frame,
        ):
            frame.grid_remove()
        tab = self._active_tab_key()
        if tab == "delay":
            self.delay_controls_frame.grid(row=0, column=7, sticky="w")
            self.rgb_mode_toggle.state(["!disabled"])
        elif tab == "timeline":
            self.timeline_context_frame.grid(row=0, column=7, sticky="w")
            self.rgb_mode_toggle.state(["disabled"])
        else:
            self.range_controls_frame.grid(row=0, column=7, sticky="w")
            self.rgb_mode_toggle.state(["disabled"])

    def _schedule_redraw(self, _event: object | None = None) -> None:
        if self._redraw_after is not None:
            self.after_cancel(self._redraw_after)
        self._redraw_after = self.after(40, self._run_scheduled_redraw)

    def _run_scheduled_redraw(self) -> None:
        self._redraw_after = None
        self._draw_active_tab()

    def _schedule_optional_redraw(self, view: str) -> None:
        if view not in {"probe", "tuning"}:
            return
        self._optional_redraw_dirty.add(view)
        if self._optional_redraw_after is not None:
            try:
                self.after_cancel(self._optional_redraw_after)
            except tk.TclError:
                pass
        self._optional_redraw_after = self.after(
            60, self._run_scheduled_optional_redraw
        )

    def _run_scheduled_optional_redraw(self) -> None:
        self._optional_redraw_after = None
        dirty = set(self._optional_redraw_dirty)
        self._optional_redraw_dirty.clear()
        if "probe" in dirty:
            self._draw_probe_canvas()
        if "tuning" in dirty and self._active_tab_key() == "rf":
            self._draw_tuning_curve()

    def _timeline_scroll_set(self, first: str, last: str) -> None:
        if hasattr(self, "timeline_scrollbar"):
            self.timeline_scrollbar.set(first, last)
        if self._restoring_timeline_scroll:
            return
        try:
            first_value = float(first)
            last_value = float(last)
        except ValueError:
            return
        progress = timeline_scroll_progress(first_value, last_value)
        if progress is not None:
            self._timeline_scroll_fraction = progress

    def _timeline_yview(self, *args: object) -> None:
        canvas = self.canvases.get("timeline")
        if canvas is None:
            return
        canvas.yview(*args)
        self._remember_timeline_scroll()
        self._publish_pairing_state_if_changed()

    def _remember_timeline_scroll(self) -> None:
        canvas = self.canvases.get("timeline")
        if canvas is None:
            return
        try:
            first, last = canvas.yview()
        except tk.TclError:
            return
        progress = timeline_scroll_progress(first, last)
        if progress is not None:
            self._timeline_scroll_fraction = progress

    def _restore_timeline_scroll(self) -> None:
        canvas = self.canvases.get("timeline")
        if canvas is None:
            return
        try:
            first, last = canvas.yview()
        except tk.TclError:
            return
        offset = timeline_scroll_offset(self._timeline_scroll_fraction, first, last)
        if offset is None:
            return
        self._restoring_timeline_scroll = True
        try:
            canvas.yview_moveto(offset)
        finally:
            self._restoring_timeline_scroll = False

    def _on_timeline_mousewheel(self, event: tk.Event) -> str:
        canvas = self.canvases.get("timeline")
        if canvas is None:
            return "break"
        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return "break"
            units = -1 * (delta // 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
            units *= 3
        canvas.yview_scroll(units, "units")
        self._remember_timeline_scroll()
        self._publish_pairing_state_if_changed()
        return "break"

    def _normalize_control_values(self) -> None:
        time_groups = self._time_groups()
        time_count = max(1, len(time_groups))
        max_bin = max(0, time_count - 1)
        for var in (self.bin_var, self.range_start_var, self.range_end_var):
            try:
                value = int(var.get())
            except (tk.TclError, ValueError):
                value = 0
            var.set(max(0, min(max_bin, value)))
        self._source_bins_for_time_controls()
        if self._timeline_range_anchor is not None:
            self._timeline_range_anchor = max(0, min(max_bin, self._timeline_range_anchor))
        self._x_target_bins()
        self._y_target_bins()
        self._smooth_radius()
        self._sync_time_control_ranges()
        self._last_time_group_count = time_count
        self._last_time_groups = list(time_groups)
        selected_cell = self.__dict__.get("selected_cell")
        if selected_cell is not None:
            y_start, y_end, x_start, x_end = selected_cell
            self.selected_cell = self._cell_for_pairing_midpoint(
                (float(y_start) + float(y_end)) / 2.0,
                (float(x_start) + float(x_end)) / 2.0,
            )

    def _parse_time_control(self, variable: tk.StringVar, fallback: float) -> float:
        try:
            return float(variable.get())
        except (tk.TclError, TypeError, ValueError):
            return fallback

    def _default_plot_time_bounds_ms(self) -> tuple[float, float]:
        settings = self.__dict__.get("settings", ViewerSettings())
        start, end = self._snap_time_range_to_bins(
            settings.rf_sum_start_ms,
            settings.rf_sum_end_ms,
        )
        return (
            self.data.time_bin_edges[start] * 1000.0,
            self.data.time_bin_edges[end + 1] * 1000.0,
        )

    def _snap_time_range_to_bins(self, requested_start: float, requested_end: float) -> AxisGroup:
        edges_ms = [edge * 1000.0 for edge in self.data.time_bin_edges]
        axis_start, axis_end = edges_ms[0], edges_ms[-1]
        requested_start = max(axis_start, min(axis_end, requested_start))
        requested_end = max(axis_start, min(axis_end, requested_end))
        if requested_start > requested_end:
            requested_start, requested_end = requested_end, requested_start

        start_edge = min(
            range(self.data.n_bins),
            key=lambda index: abs(edges_ms[index] - requested_start),
        )
        end_edge = min(
            range(1, self.data.n_bins + 1),
            key=lambda index: abs(edges_ms[index] - requested_end),
        )
        if end_edge <= start_edge:
            if requested_start >= axis_end:
                start_edge, end_edge = self.data.n_bins - 1, self.data.n_bins
            elif requested_end <= axis_start:
                start_edge, end_edge = 0, 1
            else:
                end_edge = min(self.data.n_bins, start_edge + 1)
        return start_edge, end_edge - 1

    def _source_bins_for_time_controls(self) -> AxisGroup:
        edges_ms = [edge * 1000.0 for edge in self.data.time_bin_edges]
        axis_start, axis_end = edges_ms[0], edges_ms[-1]
        requested_start = self._parse_time_control(self.range_start_ms_var, axis_start)
        requested_end = self._parse_time_control(self.range_end_ms_var, axis_end)
        start, end = self._snap_time_range_to_bins(requested_start, requested_end)
        start_edge, end_edge = start, end + 1
        self.range_start_ms_var.set(format_ms(edges_ms[start_edge]))
        self.range_end_ms_var.set(format_ms(edges_ms[end_edge]))
        return start, end

    def _sync_time_range_controls(self) -> None:
        # Timeline selection is intentionally independent of the RF sum range
        # shown in the top bar.
        count = self._time_group_count()
        max_bin = max(0, count - 1)
        self.range_start_var.set(max(0, min(max_bin, self.range_start_var.get())))
        self.range_end_var.set(max(0, min(max_bin, self.range_end_var.get())))

    def _active_tab_key(self) -> str:
        if not hasattr(self, "notebook"):
            return "rf"
        selected = self.notebook.select()
        return self._tab_keys.get(str(selected), "rf")

    def _draw_active_tab(self) -> None:
        key = self._active_tab_key()
        if self._selected_local_unit_index() is None:
            self._draw_unavailable_unit(key)
            if key == "rf":
                self._draw_tuning_curve()
                if (
                    self.show_waveform_var.get()
                    and not self.tuning_collapsed_var.get()
                ):
                    self.waveform_subtitle_label.configure(
                        text=f"Cluster {self._selected_unit_id_value()} · waveform unavailable"
                    )
                    self._draw_unavailable_unit("waveform")
            return
        if key == "rf":
            self._draw_rf()
            self._draw_tuning_curve()
            if (
                self.show_waveform_var.get()
                and not self.tuning_collapsed_var.get()
            ):
                self._draw_waveform()
        elif key == "delay":
            self._draw_rgb() if self.rgb_mode_var.get() else self._draw_delay()
        elif key == "timeline":
            self._draw_timeline()
        elif key == "waveform":
            self._draw_waveform()

    def _request_waveform_payload(self) -> None:
        if (
            not self.show_waveform_var.get()
            or self._active_tab_key() != "rf"
            or self.tuning_collapsed_var.get()
        ):
            return
        key = (
            self._selected_unit_id_value(),
            self.waveform_channel_mode_var.get(),
        )
        if (
            self._waveform_loading_key == key
            or self._waveform_payload_key == key
            or getattr(self, "_waveform_error_key", None) == key
        ):
            return
        self._waveform_generation += 1
        generation = self._waveform_generation
        self._waveform_loading_key = key
        data = self.data

        def load() -> None:
            result: dict[str, object] = {
                "generation": generation,
                "data_path": data.path,
                "key": key,
                "payload": None,
                "error": None,
            }
            try:
                result["payload"] = data.waveform_plot_payload(
                    key[0], key[1]
                )
            except Exception as exc:
                result["error"] = str(exc)
            finally:
                self._waveform_result_queue.put(result)

        threading.Thread(
            target=load,
            name=f"rfmapping-waveform-{generation}",
            daemon=True,
        ).start()
        self._schedule_waveform_result_poll()

    def _schedule_waveform_result_poll(self) -> None:
        if self._waveform_poll_after is None:
            self._waveform_poll_after = self.after(
                30, self._poll_waveform_results
            )

    def _poll_waveform_results(self) -> None:
        self._waveform_poll_after = None
        current: dict[str, object] | None = None
        while True:
            try:
                result = self._waveform_result_queue.get_nowait()
            except queue.Empty:
                break
            if result.get("generation") == self._waveform_generation:
                current = result
        if current is None:
            if self._waveform_loading_key is not None and not self._quitting:
                self._schedule_waveform_result_poll()
            return
        if (
            current.get("data_path") != self.data.path
            or not self.__dict__.get("_viewer_ready", False)
        ):
            return
        raw_key = current.get("key")
        if not (
            isinstance(raw_key, tuple)
            and len(raw_key) == 2
            and isinstance(raw_key[0], int)
            and isinstance(raw_key[1], str)
        ):
            return
        key = (raw_key[0], raw_key[1])
        self._waveform_loading_key = None
        payload = current.get("payload")
        error = current.get("error")
        if isinstance(payload, Mapping):
            self.waveform_payload = payload
            self._waveform_payload_key = key
            self._waveform_error = None
            self._waveform_error_key = None
        else:
            self.waveform_payload = None
            self._waveform_payload_key = None
            self._waveform_error = str(error or "Waveform data is unavailable.")
            self._waveform_error_key = key
        if (
            self.show_waveform_var.get()
            and self._active_tab_key() == "rf"
            and not self.tuning_collapsed_var.get()
        ):
            self._draw_waveform()

    @staticmethod
    def _draw_waveform_message(
        canvas: tk.Canvas,
        heading: str,
        detail: str,
    ) -> None:
        width = max(canvas.winfo_width(), 260)
        height = max(canvas.winfo_height(), 165)
        canvas.create_text(
            width / 2,
            height / 2 - 14,
            text=heading,
            fill="#667085",
            font=("TkDefaultFont", 16, "bold"),
        )
        canvas.create_text(
            width / 2,
            height / 2 + 25,
            text=detail,
            fill="#667085",
            font=("TkDefaultFont", 9),
            width=max(180, width - 60),
            justify="center",
        )

    def _draw_waveform(self) -> None:
        canvas = self.canvases["waveform"]
        canvas.delete("all")
        if not self.show_waveform_var.get():
            self.waveform_subtitle_label.configure(text="")
            return
        key = (
            self._selected_unit_id_value(),
            self.waveform_channel_mode_var.get(),
        )
        if self._waveform_payload_key != key:
            if getattr(self, "_waveform_error_key", None) == key:
                self.waveform_subtitle_label.configure(
                    text=f"Cluster {key[0]} · waveform unavailable"
                )
                self._draw_waveform_message(
                    canvas,
                    "N/A",
                    self._waveform_error or "Waveform data is unavailable.",
                )
                return
            self._request_waveform_payload()
            self.waveform_subtitle_label.configure(
                text=f"Cluster {key[0]} · loading waveform artifact…"
            )
            self._draw_waveform_message(
                canvas,
                "Loading…",
                "Reading the selected unit's precomputed average template.",
            )
            return

        payload = self.waveform_payload
        if payload is None:
            self._draw_waveform_message(
                canvas, "N/A", "Waveform data is unavailable."
            )
            return
        matrix_raw = payload.get("matrix")
        times_raw = payload.get("times_ms", payload.get("time_ms"))
        labels_raw = payload.get("channel_labels")
        if hasattr(matrix_raw, "tolist"):
            matrix_raw = matrix_raw.tolist()
        if hasattr(times_raw, "tolist"):
            times_raw = times_raw.tolist()
        if hasattr(labels_raw, "tolist"):
            labels_raw = labels_raw.tolist()
        if not (
            isinstance(matrix_raw, Sequence)
            and not isinstance(matrix_raw, (str, bytes))
            and isinstance(times_raw, Sequence)
            and not isinstance(times_raw, (str, bytes))
            and isinstance(labels_raw, Sequence)
            and not isinstance(labels_raw, (str, bytes))
        ):
            self._draw_waveform_message(
                canvas, "N/A", "The waveform payload is incomplete."
            )
            return
        try:
            matrix = [
                [float(value) for value in row]
                for row in matrix_raw
            ]
            times = [float(value) for value in times_raw]
            labels = [str(value).split(" · ", 1)[0] for value in labels_raw]
        except (TypeError, ValueError):
            self._draw_waveform_message(
                canvas, "N/A", "The waveform payload contains invalid values."
            )
            return
        if (
            not matrix
            or len(labels) != len(matrix)
            or len(times) < 2
            or any(len(row) != len(times) for row in matrix)
            or not all(math.isfinite(value) for value in times)
            or any(right <= left for left, right in zip(times, times[1:]))
        ):
            self._draw_waveform_message(
                canvas, "N/A", "The waveform payload has inconsistent dimensions."
            )
            return

        amplitude_limit = max(
            (
                abs(value)
                for row in matrix
                for value in row
                if math.isfinite(value)
            ),
            default=0.0,
        )
        configured_limit = payload.get("amplitude_limit_uv")
        if isinstance(configured_limit, (int, float)) and math.isfinite(
            float(configured_limit)
        ):
            amplitude_limit = max(amplitude_limit, abs(float(configured_limit)))
        amplitude_limit = max(amplitude_limit, 1e-9)
        best_row_raw = payload.get(
            "best_channel_row", payload.get("best_row_index", -1)
        )
        best_row = int(best_row_raw) if isinstance(best_row_raw, int) else -1

        width = max(canvas.winfo_width(), 280)
        height = max(canvas.winfo_height(), 165)
        margin_l, margin_r, margin_t, margin_b = 58, 68, 16, 42
        grid_w = max(80.0, width - margin_l - margin_r)
        grid_h = max(80.0, height - margin_t - margin_b)
        x0, y0 = float(margin_l), float(margin_t)
        cell_w = grid_w / len(times)
        cell_h = grid_h / len(matrix)
        for row_index, row in enumerate(matrix):
            top = y0 + row_index * cell_h
            for sample_index, value in enumerate(row):
                left = x0 + sample_index * cell_w
                canvas.create_rectangle(
                    left,
                    top,
                    left + cell_w + 0.5,
                    top + cell_h + 0.5,
                    fill=waveform_color(value, amplitude_limit),
                    outline="",
                )
            label_color = "#b42318" if row_index == best_row else "#475467"
            canvas.create_text(
                x0 - 12,
                top + cell_h / 2,
                anchor="e",
                text=("★ " if row_index == best_row else "") + labels[row_index],
                fill=label_color,
                font=("TkFixedFont", 8, "bold" if row_index == best_row else "normal"),
            )
            if row_index == best_row:
                canvas.create_rectangle(
                    x0,
                    top,
                    x0 + grid_w,
                    top + cell_h,
                    outline="#b42318",
                    width=2,
                )
        canvas.create_rectangle(
            x0, y0, x0 + grid_w, y0 + grid_h, outline="#344054", width=1
        )

        time_span = times[-1] - times[0]
        if time_span > 0.0 and times[0] <= 0.0 <= times[-1]:
            zero_x = x0 + (
                (0.0 - times[0]) / time_span
            ) * max(0.0, grid_w - cell_w) + cell_w / 2.0
            canvas.create_line(
                zero_x,
                y0,
                zero_x,
                y0 + grid_h,
                fill="#111827",
                dash=(5, 4),
                width=2,
            )
        tick_values = [times[0], 0.0, times[-1]]
        for value in tick_values:
            if not times[0] <= value <= times[-1]:
                continue
            x = x0 + ((value - times[0]) / time_span) * max(
                0.0, grid_w - cell_w
            ) + cell_w / 2.0
            canvas.create_line(
                x, y0 + grid_h, x, y0 + grid_h + 5, fill="#475467"
            )
            canvas.create_text(
                x,
                y0 + grid_h + 19,
                text=f"{value:g}",
                fill="#475467",
                font=("TkDefaultFont", 8),
            )
        canvas.create_text(
            x0 + grid_w / 2,
            y0 + grid_h + 34,
            text="Time from spike (ms)",
            fill="#475467",
            font=("TkDefaultFont", 8),
        )

        colorbar_x = x0 + grid_w + 28
        colorbar_h = min(120.0, grid_h)
        steps = 80
        for index in range(steps):
            value = amplitude_limit * (1.0 - 2.0 * index / max(1, steps - 1))
            top = y0 + colorbar_h * index / steps
            bottom = y0 + colorbar_h * (index + 1) / steps
            canvas.create_rectangle(
                colorbar_x,
                top,
                colorbar_x + 16,
                bottom,
                fill=waveform_color(value, amplitude_limit),
                outline="",
            )
        canvas.create_rectangle(
            colorbar_x,
            y0,
            colorbar_x + 16,
            y0 + colorbar_h,
            outline="#475467",
        )
        for value, top in (
            (amplitude_limit, y0),
            (0.0, y0 + colorbar_h / 2),
            (-amplitude_limit, y0 + colorbar_h),
        ):
            canvas.create_text(
                colorbar_x + 23,
                top,
                anchor="w",
                text=f"{value:.3g}",
                fill="#475467",
                font=("TkDefaultFont", 7),
            )
        canvas.create_text(
            colorbar_x,
            y0 - 16,
            anchor="w",
            text="µV",
            fill="#475467",
            font=("TkDefaultFont", 8),
        )
        mode_label = WAVEFORM_CHANNEL_MODE_LABELS.get(
            key[1], key[1]
        )
        max_ptp = payload.get("max_ptp_uv")
        ptp_text = (
            f" · max PTP {float(max_ptp):.3g} µV"
            if isinstance(max_ptp, (int, float))
            and math.isfinite(float(max_ptp))
            else ""
        )
        self.waveform_subtitle_label.configure(
            text=(
                f"Cluster {key[0]} · Best + nearest {len(matrix) - 1} · "
                f"{mode_label}{ptp_text} · baseline ≤ -0.25 ms"
            )
        )

    def _draw_unavailable_unit(self, key: str) -> None:
        canvas = self.canvases[key]
        canvas.delete("all")
        self._canvas_layouts.pop(key, None)
        if key == "timeline":
            self._timeline_cells = []
            self._timeline_cells_by_bin = {}
            self._timeline_preview_cache_key = None
            self._timeline_preview_images = {}
        width = max(canvas.winfo_width(), 300)
        height = max(canvas.winfo_height(), 220)
        canvas.configure(scrollregion=(0, 0, width, height))
        unit_id = self._selected_unit_id_value()
        no_spatial_matches = (
            self.spatial_region is not None and not self._unit_navigation_ids()
        )
        filter_status = self._quality_filter_status(unit_id)
        canvas.create_text(
            width / 2,
            height / 2 - 14,
            text="N/A",
            fill="#667085",
            font=("TkDefaultFont", 28, "bold"),
        )
        canvas.create_text(
            width / 2,
            height / 2 + 26,
            text=(
                "No units are inside the selected probe region."
                if no_spatial_matches
                else (
                    filter_status
                    or f"Cluster {unit_id} is not available in this session."
                )
            ),
            fill="#667085",
            font=("TkDefaultFont", 12),
        )

    def _update_all(self) -> None:
        if self._redraw_after is not None:
            self.after_cancel(self._redraw_after)
            self._redraw_after = None
        self._normalize_control_values()
        self._reconcile_unit_filter_selection()
        self.hover_cell = None
        self._hover_signature = None
        self._hover_tooltip_text = ""
        unit_idx = self._selected_local_unit_index()
        cluster_id = self._selected_unit_id_value()
        if unit_idx is None:
            self.selected_cell = None
            self.header_label.configure(text=f"Unit N/A / cluster {cluster_id}")
            no_spatial_matches = (
                self.spatial_region is not None and not self._unit_navigation_ids()
            )
            filter_status = self._quality_filter_status(cluster_id)
            self.status_label.configure(
                text=(
                    "No units match the probe region."
                    if no_spatial_matches
                    else (
                        filter_status
                        or f"N/A: cluster {cluster_id} is not available in this session."
                    )
                )
            )
            self.unit_stats_label.configure(
                text=(
                    "N/A\nHidden by the zero-spike RF-bin filter."
                    if filter_status
                    else "N/A\nThis unit is available only in another paired window."
                )
            )
            self.cell_label.configure(text="N/A for this session")
            self._sync_context_controls()
            self._draw_probe_canvas()
            self._draw_active_tab()
            return

        self.header_label.configure(text=f"Unit {unit_idx:03d} / cluster {cluster_id}")
        self.status_label.configure(text="")
        self.unit_stats_label.configure(text="")
        self._update_cell_label()
        self._sync_context_controls()
        self._draw_probe_canvas()
        self._draw_active_tab()

    def _current_matrix(self) -> list[list[float | None]]:
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return [[None for _x in range(self.data.n_x)] for _y in range(self.data.n_y)]
        start, end = self._source_bins_for_display_range()
        return self.data.response_matrix(
            unit_idx,
            start,
            end,
            self.value_mode_var.get(),
        )

    def _delay_matrix_for_time_groups(self, floor: float = 0.0) -> list[list[float | None]]:
        delay, _entropy, _x_groups, _y_groups = self._grouped_temporal_metric_matrices(
            floor,
            smooth=False,
        )
        return delay

    def _base_bin_ms(self) -> float:
        if len(self.data.time_bin_edges) < 2:
            return 1.0
        diffs = [
            (self.data.time_bin_edges[i + 1] - self.data.time_bin_edges[i]) * 1000.0
            for i in range(len(self.data.time_bin_edges) - 1)
        ]
        positive = [diff for diff in diffs if diff > 1e-9]
        return min(positive) if positive else 1.0

    def _time_axis_start_ms(self) -> float:
        if not self.data.time_bin_edges:
            return 0.0
        return self.data.time_bin_edges[0] * 1000.0

    def _time_axis_end_ms(self) -> float:
        if not self.data.time_bin_edges:
            return self._base_bin_ms() * self.data.n_bins
        return self.data.time_bin_edges[-1] * 1000.0

    def _time_axis_range_ms(self) -> tuple[float, float]:
        start = self._time_axis_start_ms()
        end = self._time_axis_end_ms()
        if end <= start:
            end = start + self._base_bin_ms()
        return start, end

    def _total_time_ms(self) -> float:
        start, end = self._time_axis_range_ms()
        return max(end - start, self._base_bin_ms())

    def _time_group_size(self) -> int:
        base = self._base_bin_ms()
        total = self._total_time_ms()
        try:
            requested = float(self.time_res_ms_var.get())
        except (tk.TclError, ValueError):
            requested = base
        requested = max(base, min(total, requested))
        group_size = max(1, min(self.data.n_bins, int(round(requested / base))))
        self.time_res_ms_var.set(format_ms(group_size * base))
        return group_size

    def _time_groups(self) -> list[AxisGroup]:
        group_size = self._time_group_size()
        target_duration_ms = group_size * self._base_bin_ms()
        return physical_time_groups(
            [edge * 1000.0 for edge in self.data.time_bin_edges],
            target_duration_ms,
        )

    def _time_group_count(self) -> int:
        return max(1, len(self._time_groups()))

    def _display_range_indices(self) -> AxisGroup:
        count = self._time_group_count()
        start = max(0, min(count - 1, min(self.range_start_var.get(), self.range_end_var.get())))
        end = max(0, min(count - 1, max(self.range_start_var.get(), self.range_end_var.get())))
        return start, end

    def _is_full_display_range(self) -> bool:
        start, end = self._display_range_indices()
        return start == 0 and end == self._time_group_count() - 1

    def _display_range_label(self) -> str:
        start_ms, end_ms = self._timeline_selected_time_bounds_ms()
        return f"{format_ms(start_ms)} to {format_ms(end_ms)} ms"

    def _selected_time_bounds_ms(self) -> tuple[float, float]:
        """Return the independent spatial RF summation window."""
        start, end = self._source_bins_for_time_controls()
        return (
            self.data.time_bin_edges[start] * 1000.0,
            self.data.time_bin_edges[end + 1] * 1000.0,
        )

    def _timeline_selected_source_bins(self) -> AxisGroup:
        groups = self._time_groups()
        start, end = self._display_range_indices()
        return groups[start][0], groups[end][1]

    def _timeline_selected_time_bounds_ms(self) -> tuple[float, float]:
        start, end = self._timeline_selected_source_bins()
        return (
            self.data.time_bin_edges[start] * 1000.0,
            self.data.time_bin_edges[end + 1] * 1000.0,
        )

    def _time_group_bounds_ms(self, display_bin: int) -> tuple[float, float]:
        groups = self._time_groups()
        idx = max(0, min(len(groups) - 1, int(display_bin)))
        start, end = groups[idx]
        return self.data.time_bin_edges[start] * 1000.0, self.data.time_bin_edges[end + 1] * 1000.0

    def _time_group_label(self, display_bin: int) -> str:
        start_ms, end_ms = self._time_group_bounds_ms(display_bin)
        return f"{format_ms(start_ms)}–{format_ms(end_ms)} ms"

    def _time_group_start_label(self, display_bin: int) -> str:
        start_ms, _end_ms = self._time_group_bounds_ms(display_bin)
        return f"{format_ms(start_ms)} ms"

    def _time_group_center_ms(self, display_bin: int) -> float:
        start_ms, end_ms = self._time_group_bounds_ms(display_bin)
        return (start_ms + end_ms) / 2.0

    def _source_bins_for_display_bin(self, display_bin: int) -> AxisGroup:
        groups = self._time_groups()
        idx = max(0, min(len(groups) - 1, int(display_bin)))
        return groups[idx]

    def _source_bins_for_display_range(self) -> AxisGroup:
        return self._source_bins_for_time_controls()

    def _plot_range_group_indices(self) -> AxisGroup:
        source_start, source_end = self._source_bins_for_time_controls()
        groups = self._time_groups()
        start_group = next(
            (index for index, (start, end) in enumerate(groups) if start <= source_start <= end),
            0,
        )
        end_group = next(
            (index for index, (start, end) in enumerate(groups) if start <= source_end <= end),
            len(groups) - 1,
        )
        return start_group, end_group

    def _time_grouped_hist(self, hist: list[float]) -> list[float]:
        return [float(sum(hist[start : end + 1])) for start, end in self._time_groups()]

    def _has_time_selection(self) -> bool:
        return not self._is_full_display_range()

    def _visible_timeline_bins(self, display_bins: int) -> list[int]:
        # Timeline is an overview: its own selection highlights bins but never
        # removes temporal context. A dedicated timeline filter can be added
        # later if filtering is needed independently of the RF sum controls.
        return list(range(display_bins))

    def _sync_time_control_ranges(self) -> None:
        axis_start, axis_end = self._time_axis_range_ms()
        source_step = self._base_bin_ms()
        if hasattr(self, "range_start_spin"):
            self.range_start_spin.configure(from_=axis_start, to=axis_end, increment=source_step)
        if hasattr(self, "range_end_spin"):
            self.range_end_spin.configure(from_=axis_start, to=axis_end, increment=source_step)
        if hasattr(self, "time_res_spin"):
            base = self._base_bin_ms()
            self.time_res_spin.configure(from_=base, to=self._total_time_ms(), increment=base)

    def _x_target_bins(self) -> int:
        try:
            value = int(self.x_bins_var.get())
        except (tk.TclError, ValueError):
            value = self.data.n_x
        value = max(1, min(self.data.n_x, value))
        self.x_bins_var.set(value)
        return value

    def _y_target_bins(self) -> int:
        try:
            value = int(self.y_bins_var.get())
        except (tk.TclError, ValueError):
            value = self.data.n_y
        value = max(1, min(self.data.n_y, value))
        self.y_bins_var.set(value)
        return value

    def _smooth_radius(self) -> int:
        try:
            value = int(self.smooth_radius_var.get())
        except (tk.TclError, ValueError):
            value = 0
        value = max(0, min(3, value))
        self.smooth_radius_var.set(value)
        return value

    def _x_groups(self) -> list[AxisGroup]:
        return axis_groups_for_target(self.data.n_x, self._x_target_bins())

    def _display_y_groups(self) -> list[AxisGroup]:
        groups = axis_groups_for_target(self.data.n_y, self._y_target_bins())
        if self.flip_y_var.get():
            groups = list(reversed(groups))
        return groups

    def _prepare_plot_matrix(
        self,
        matrix: list[list[float | None]],
        *,
        smooth: bool = True,
    ) -> tuple[list[list[float | None]], list[AxisGroup], list[AxisGroup]]:
        if isinstance(matrix, PreparedSpatialMatrix):
            return [row[:] for row in matrix], matrix.x_groups, matrix.y_groups
        x_groups = self._x_groups()
        y_groups = self._display_y_groups()
        prepared = reduce_matrix_xy(matrix, y_groups, x_groups)
        if smooth:
            prepared = smooth_matrix(prepared, self._smooth_radius())
        return prepared, x_groups, y_groups

    def _prepare_response_plot_matrix(
        self,
        source_start: int,
        source_end: int,
        *,
        smooth: bool = True,
    ) -> tuple[list[list[float | None]], list[AxisGroup], list[AxisGroup]]:
        """Pool display-cell observations before deriving normalized values."""

        x_groups = self._x_groups()
        y_groups = self._display_y_groups()
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return [], x_groups, y_groups
        observations = [
            [
                self.data.spatial_group_observations(
                    unit_idx,
                    y_group,
                    x_group,
                    source_start,
                    source_end,
                )
                for x_group in x_groups
            ]
            for y_group in y_groups
        ]
        valid = [
            [value.source_pixel_count > 0 for value in row]
            for row in observations
        ]
        value_mode = self.value_mode_var.get()
        if value_mode == VALUE_MODE_COUNT:
            matrix: list[list[float | None]] = [
                [
                    None
                    if value.source_pixel_count <= 0
                    else value.count / value.source_pixel_count
                    for value in row
                ]
                for row in observations
            ]
            if smooth:
                matrix = smooth_matrix(matrix, self._smooth_radius())
                matrix = [
                    [value if valid[y_idx][x_idx] else None for x_idx, value in enumerate(row)]
                    for y_idx, row in enumerate(matrix)
                ]
            return matrix, x_groups, y_groups

        counts: list[list[float | None]] = [
            [value.count if value.source_pixel_count > 0 else None for value in row]
            for row in observations
        ]
        occupancies: list[list[float | None]] = [
            [
                value.occupancy_time_s
                if value.source_pixel_count > 0
                else None
                for value in row
            ]
            for row in observations
        ]
        if smooth:
            counts = smooth_matrix(counts, self._smooth_radius())
            occupancies = smooth_matrix(occupancies, self._smooth_radius())
        matrix = [
            [
                None
                if (
                    not valid[y_idx][x_idx]
                    or count is None
                    or exposure is None
                    or exposure <= 0
                )
                else count / exposure
                for x_idx, (count, exposure) in enumerate(zip(count_row, exposure_row))
            ]
            for y_idx, (count_row, exposure_row) in enumerate(zip(counts, occupancies))
        ]
        return matrix, x_groups, y_groups

    def _grouped_temporal_metric_matrices(
        self,
        floor: float = 0.0,
        *,
        smooth: bool = True,
    ) -> tuple[
        list[list[float | None]],
        list[list[float | None]],
        list[AxisGroup],
        list[AxisGroup],
    ]:
        x_groups = self._x_groups()
        y_groups = self._display_y_groups()
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return [], [], x_groups, y_groups
        safe_floor = max(0.0, float(floor))
        histograms = [
            [
                [
                    value
                    / max(
                        1,
                        self.data.spatial_group_source_pixel_count(
                            y_group,
                            x_group,
                        ),
                    )
                    for value in self.data.spatial_group_count_histogram(
                        unit_idx,
                        y_group,
                        x_group,
                    )
                ]
                for x_group in x_groups
            ]
            for y_group in y_groups
        ]
        if smooth and self._smooth_radius() > 0 and histograms and histograms[0]:
            output = [
                [
                    [0.0 for _bin_idx in range(self.data.n_bins)]
                    for _x_group in x_groups
                ]
                for _y_group in y_groups
            ]
            for bin_idx in range(self.data.n_bins):
                temporal_slice = [
                    [histogram[bin_idx] for histogram in row]
                    for row in histograms
                ]
                smoothed_slice = smooth_matrix(
                    temporal_slice,
                    self._smooth_radius(),
                )
                for y_idx, row in enumerate(smoothed_slice):
                    for x_idx, value in enumerate(row):
                        output[y_idx][x_idx][bin_idx] = float(value or 0.0)
            histograms = output
        delay: list[list[float | None]] = []
        entropy: list[list[float | None]] = []
        time_groups = self._time_groups()
        for y_idx, _y_group in enumerate(y_groups):
            delay_row: list[float | None] = []
            entropy_row: list[float | None] = []
            for x_idx, _x_group in enumerate(x_groups):
                metrics = self.data.temporal_metrics_from_histogram(
                    histograms[y_idx][x_idx],
                    time_groups,
                )
                delay_row.append(
                    metrics.delay_ms if metrics.mean_total_count > safe_floor else None
                )
                entropy_row.append(metrics.entropy)
            delay.append(delay_row)
            entropy.append(entropy_row)
        return delay, entropy, x_groups, y_groups

    def _group_hist(self, y_start: int, y_end: int, x_start: int, x_end: int) -> list[float]:
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return [0.0 for _ in range(self.data.n_bins)]
        n = max(
            1,
            self.data.spatial_group_source_pixel_count(
                (y_start, y_end),
                (x_start, x_end),
            ),
        )
        return [
            value / n
            for value in self.data.spatial_group_count_histogram(
                unit_idx,
                (y_start, y_end),
                (x_start, x_end),
            )
        ]

    def _group_response_value(
        self,
        y_start: int,
        y_end: int,
        x_start: int,
        x_end: int,
        source_start: int,
        source_end: int,
    ) -> float | None:
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return None
        return self.data.spatial_group_response_value(
            unit_idx,
            (y_start, y_end),
            (x_start, x_end),
            source_start,
            source_end,
            self.value_mode_var.get(),
        )

    def _group_response_values(
        self,
        y_start: int,
        y_end: int,
        x_start: int,
        x_end: int,
    ) -> list[float | None]:
        return [
            self._group_response_value(y_start, y_end, x_start, x_end, start, end)
            for start, end in self._time_groups()
        ]

    def _y_group_text(self, y_start: int, y_end: int) -> str:
        if y_start == y_end:
            return f"yIdx {y_start + 1}; y {format_pos(self.data.y_positions[y_start])}"
        return (
            f"yIdx {y_start + 1}-{y_end + 1}; "
            f"y {format_pos(self.data.y_positions[y_start])}..{format_pos(self.data.y_positions[y_end])}"
        )

    def _x_group_text(self, x_start: int, x_end: int) -> str:
        if x_start == x_end:
            return f"xIdx {x_start + 1}; x {format_pos(self.data.x_positions[x_start])}"
        return (
            f"xIdx {x_start + 1}-{x_end + 1}; "
            f"x {format_pos(self.data.x_positions[x_start])}..{format_pos(self.data.x_positions[x_end])}"
        )

    def _current_matrix_label(self) -> str:
        start_ms, end_ms = self._selected_time_bounds_ms()
        return f"{self.value_mode_var.get()}: {format_ms(start_ms)} to {format_ms(end_ms)} ms"

    def _rf_sum_range_value_text(self, value: float | None) -> str:
        value_mode = self.value_mode_var.get()
        start_ms, end_ms = self._selected_time_bounds_ms()
        return (
            f"RF sum range {format_ms(start_ms)}–{format_ms(end_ms)} ms: "
            f"{format_response_value(value, value_mode)} {value_mode_unit(value_mode)}"
        )

    def _cell_metrics_text(
        self,
        y_start: int,
        y_end: int,
        x_idx: int,
        x_end: int,
        display_bin: int | None = None,
    ) -> str:
        unit_idx = self.unit_idx.get()
        value_mode = self.value_mode_var.get()
        unit = value_mode_unit(value_mode)
        display_values = self._group_response_values(y_start, y_end, x_idx, x_end)
        bin_idx = self.bin_var.get() if display_bin is None else int(display_bin)
        bin_idx = max(0, min(len(display_values) - 1, bin_idx))
        range_start, range_end = self._source_bins_for_time_controls()
        range_value = self._group_response_value(
            y_start, y_end, x_idx, x_end, range_start, range_end
        )
        total_value = self._group_response_value(
            y_start, y_end, x_idx, x_end, 0, self.data.n_bins - 1
        )
        temporal = self.data.spatial_group_temporal_metrics(
            unit_idx,
            (y_start, y_end),
            (x_idx, x_end),
            self._time_groups(),
        )
        peak_bin = temporal.peak_group_index
        peak_value = display_values[peak_bin] if peak_bin is not None else None
        delay = temporal.delay_ms
        ent = temporal.entropy
        delay_text = f"{delay:.1f} ms" if delay is not None else "n/a"
        peak_text = f"{peak_bin + 1} ({self._time_group_label(peak_bin)})" if peak_bin is not None else "n/a"
        group_note = (
            (("mean" if value_mode == VALUE_MODE_COUNT else "occupancy-pooled")
             + " over source pixels\n")
            if (x_end != x_idx or y_end != y_start)
            else ""
        )
        return (
            f"cluster {self.data.cluster_id(unit_idx)}\n"
            f"{self._y_group_text(y_start, y_end)}, {self._x_group_text(x_idx, x_end)}\n"
            f"{group_note}"
            f"bin {format_response_value(display_values[bin_idx], value_mode)} {unit} "
            f"({self._time_group_label(bin_idx)})\n"
            f"{self._rf_sum_range_value_text(range_value)}\n"
            f"full window {format_response_value(total_value, value_mode)} {unit}\n"
            f"peak {format_response_value(peak_value, value_mode)} {unit}\n"
            f"peak bin {peak_text}\n"
            f"count-rate peak delay {delay_text}, count entropy {ent:.3f}"
        )

    def _update_cell_label(
        self,
        cell: CellRef | None = None,
        prefix: str = "",
        display_bin: int | None = None,
    ) -> None:
        if self._selected_local_unit_index() is None:
            self.cell_label.configure(text="N/A for this session")
            return
        if cell is None and self.hover_cell is not None:
            cell = self.hover_cell
            prefix = "Hover\n"
        if cell is None and self.selected_cell is None:
            best_y, best_x = self.data.best_cell(self.unit_idx.get())
            self.selected_cell = (best_y, best_y, best_x, best_x)
        if cell is None:
            cell = self.selected_cell
        if cell is None:
            return
        y_start, y_end, x_idx, x_end = cell
        self.cell_label.configure(
            text=prefix + self._cell_metrics_text(y_start, y_end, x_idx, x_end, display_bin=display_bin)
        )

    def _cell_tooltip_text(self, cell: CellRef, display_bin: int | None = None) -> str:
        y_start, y_end, x_start, x_end = cell
        value_mode = self.value_mode_var.get()
        unit = value_mode_unit(value_mode)
        display_values = self._group_response_values(y_start, y_end, x_start, x_end)
        bin_idx = self.bin_var.get() if display_bin is None else int(display_bin)
        bin_idx = max(0, min(len(display_values) - 1, bin_idx))
        temporal = self.data.spatial_group_temporal_metrics(
            self.unit_idx.get(),
            (y_start, y_end),
            (x_start, x_end),
            self._time_groups(),
        )
        delay = temporal.delay_ms
        total = self._group_response_value(
            y_start,
            y_end,
            x_start,
            x_end,
            0,
            self.data.n_bins - 1,
        )
        plot_start, plot_end = self._source_bins_for_time_controls()
        plot_value = self._group_response_value(
            y_start,
            y_end,
            x_start,
            x_end,
            plot_start,
            plot_end,
        )
        return "\n".join(
            [
                self._y_group_text(y_start, y_end),
                self._x_group_text(x_start, x_end),
                f"bin {bin_idx + 1}: {format_response_value(display_values[bin_idx], value_mode)} {unit}",
                self._rf_sum_range_value_text(plot_value),
                f"full window: {format_response_value(total, value_mode)} {unit}",
                f"delay {delay:.1f} ms" if delay is not None else "delay n/a",
            ]
        )

    def _draw_rf(self) -> None:
        source_start, source_end = self._source_bins_for_display_range()
        prepared = self._prepare_response_plot_matrix(source_start, source_end)
        matrix = PreparedSpatialMatrix(*prepared)
        title = f"RF map - {self._current_matrix_label()}"
        if self.polar_layout_var.get():
            self._draw_polar_matrix(
                "rf",
                matrix,
                title,
                self.palette_var.get(),
                value_suffix=value_mode_suffix(self.value_mode_var.get()),
                fixed_range=None,
            )
        else:
            self._draw_heatmap(
                "rf",
                matrix,
                title,
                self.palette_var.get(),
                value_suffix=value_mode_suffix(self.value_mode_var.get()),
                fixed_range=None,
            )

    def _draw_delay(self) -> None:
        delay, _entropy, x_groups, y_groups = self._grouped_temporal_metric_matrices(0.0)
        delay_matrix = PreparedSpatialMatrix(delay, x_groups, y_groups)
        if self.polar_layout_var.get():
            self._draw_polar_matrix(
                "delay",
                delay_matrix,
                "Delay map - peak count-rate interval center",
                "Delay",
                value_suffix=" ms",
                fixed_range=self._time_axis_range_ms(),
            )
        else:
            self._draw_heatmap(
                "delay",
                delay_matrix,
                "Delay map - peak count-rate interval center",
                "Delay",
                value_suffix=" ms",
                fixed_range=self._time_axis_range_ms(),
            )

    def _draw_heatmap(
        self,
        key: str,
        matrix: list[list[float | None]],
        title: str,
        palette: str,
        value_suffix: str,
        fixed_range: tuple[float, float] | None,
    ) -> None:
        canvas = self.canvases[key]
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 160)
        margin_l, margin_r, margin_t, margin_b = 78, 128, (22 if key == "rf" else 56), 72
        plot_w = max(10, w - margin_l - margin_r)
        plot_h = max(10, h - margin_t - margin_b)
        disp, x_groups, y_groups = self._prepare_plot_matrix(matrix)
        n_cols = len(x_groups)
        n_rows = len(y_groups)
        cell_x, cell_y, grid_w, grid_h = spatial_grid_dimensions(
            plot_w,
            plot_h,
            n_cols,
            n_rows,
            minimum_cell_width=4.0,
        )
        x0 = margin_l + (plot_w - grid_w) / 2
        y0 = margin_t + (plot_h - grid_h) / 2
        if fixed_range is None:
            low, high = palette_response_range(disp, palette)
        else:
            low, high = fixed_range

        unit_text = (
            f"Unit {self.unit_idx.get():03d} · "
            f"cluster {self.data.cluster_id(self.unit_idx.get())}"
        )
        if key == "rf" and hasattr(self, "rf_map_subtitle_label"):
            summary = title.removeprefix("RF map - ").removeprefix("RF map – ")
            self.rf_map_subtitle_label.configure(text=f"{summary} · {unit_text}")
        else:
            canvas.create_text(
                20,
                22,
                anchor="w",
                text=title,
                font=("TkDefaultFont", 13, "bold"),
                fill="#1d1d1f",
            )
            canvas.create_text(20, 44, anchor="w", text=unit_text, fill="#6e6e73")

        for display_y, row in enumerate(disp):
            y = y0 + display_y * cell_y
            for x_idx, value in enumerate(row):
                x = x0 + x_idx * cell_x
                if palette == "Delay":
                    fill = delay_color(value, low, high)
                else:
                    fill = palette_color(value, low, high, palette)
                canvas.create_rectangle(
                    x,
                    y,
                    x + cell_x,
                    y + cell_y,
                    fill=fill,
                    outline="#ffffff",
                    width=0,
                )
                if value is None or not math.isfinite(float(value)):
                    self._draw_missing_hatch(canvas, x, y, x + cell_x, y + cell_y)

        self._draw_selection_outline(
            canvas,
            x0,
            y0,
            cell_x,
            cell_y,
            x_groups,
            y_groups,
        )
        self._draw_axes(
            canvas,
            x0,
            y0,
            cell_x,
            cell_y,
            grid_w,
            grid_h,
            x_groups,
            y_groups,
        )
        self._draw_colorbar(canvas, x0 + grid_w + 36, y0, min(220, grid_h), low, high, palette, value_suffix)
        self._canvas_layouts[key] = {
            "geometry": "rectangle",
            "x0": x0,
            "y0": y0,
            "cell": cell_x,
            "cell_y": cell_y,
            "grid_w": grid_w,
            "grid_h": grid_h,
            "x_groups": x_groups,
            "y_groups": y_groups,
        }

    @staticmethod
    def _draw_missing_hatch(
        canvas: tk.Canvas,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ) -> None:
        """Overlay clipped diagonal hatching so missing is not read as zero."""

        diagonal = x0 + y0
        diagonal_end = x1 + y1
        while diagonal <= diagonal_end + 1e-9:
            candidates: list[tuple[float, float]] = []
            for x, y in (
                (x0, diagonal - x0),
                (x1, diagonal - x1),
                (diagonal - y0, y0),
                (diagonal - y1, y1),
            ):
                if x0 - 1e-9 <= x <= x1 + 1e-9 and y0 - 1e-9 <= y <= y1 + 1e-9:
                    point = (x, y)
                    if point not in candidates:
                        candidates.append(point)
            if len(candidates) >= 2:
                canvas.create_line(
                    *candidates[0],
                    *candidates[-1],
                    fill="#a9abb1",
                    width=1,
                )
            diagonal += 7.0

    def _draw_axes(
        self,
        canvas: tk.Canvas,
        x0: float,
        y0: float,
        cell_x: float,
        cell_y: float,
        grid_w: float,
        grid_h: float,
        x_groups: list[AxisGroup],
        y_groups: list[AxisGroup],
    ) -> None:
        axis_color = "#475467"
        canvas.create_rectangle(x0, y0, x0 + grid_w, y0 + grid_h, outline="#1f2937", width=1)
        tick_step = max(1, len(x_groups) // 6)
        for group_idx in range(0, len(x_groups), tick_step):
            start, end = x_groups[group_idx]
            x = x0 + (group_idx + 0.5) * cell_x
            canvas.create_line(x, y0 + grid_h, x, y0 + grid_h + 5, fill=axis_color)
            pos = (self.data.x_positions[start] + self.data.x_positions[end]) / 2.0
            canvas.create_text(x, y0 + grid_h + 18, text=format_pos(pos), fill=axis_color, font=("TkDefaultFont", 10))
        if (len(x_groups) - 1) not in range(0, len(x_groups), tick_step):
            start, end = x_groups[-1]
            x = x0 + (len(x_groups) - 0.5) * cell_x
            pos = (self.data.x_positions[start] + self.data.x_positions[end]) / 2.0
            canvas.create_text(x, y0 + grid_h + 18, text=format_pos(pos), fill=axis_color, font=("TkDefaultFont", 10))

        for display_y, (y_start, y_end) in enumerate(y_groups):
            y = y0 + (display_y + 0.5) * cell_y
            canvas.create_line(x0 - 5, y, x0, y, fill=axis_color)
            pos = (self.data.y_positions[y_start] + self.data.y_positions[y_end]) / 2.0
            label = f"{y_start + 1} / {format_pos(pos)}" if y_start == y_end else f"{y_start + 1}-{y_end + 1} / {format_pos(pos)}"
            canvas.create_text(x0 - 10, y, anchor="e", text=label, fill=axis_color, font=("TkDefaultFont", 10))

        canvas.create_text(x0 + grid_w / 2, y0 + grid_h + 44, text="x position", fill=axis_color)
        canvas.create_text(x0 - 58, y0 + grid_h / 2, text="yIdx / y", angle=90, fill=axis_color)

    def _draw_colorbar(
        self,
        canvas: tk.Canvas,
        x: float,
        y: float,
        height: float,
        low: float,
        high: float,
        palette: str,
        suffix: str,
    ) -> None:
        steps = 90
        width = 16
        unit_title = suffix.strip() or "Value"
        canvas.create_text(
            x,
            y - 17,
            anchor="w",
            text=unit_title,
            fill="#6e6e73",
            font=("TkDefaultFont", 10),
        )
        for i in range(steps):
            t0 = i / steps
            value = high - (high - low) * t0
            fill = delay_color(value, low, high) if palette == "Delay" else palette_color(value, low, high, palette)
            y1 = y + height * i / steps
            y2 = y + height * (i + 1) / steps
            canvas.create_rectangle(x, y1, x + width, y2, outline="", fill=fill)
        canvas.create_rectangle(x, y, x + width, y + height, outline="#475467")
        canvas.create_text(
            x + width + 8,
            y,
            anchor="w",
            text=f"{high:.3g}",
            fill="#475467",
            font=("TkDefaultFont", 10),
        )
        canvas.create_text(
            x + width + 8,
            y + height,
            anchor="w",
            text=f"{low:.3g}",
            fill="#475467",
            font=("TkDefaultFont", 10),
        )

        legend_y = y + height + 17
        canvas.create_rectangle(
            x,
            legend_y,
            x + 13,
            legend_y + 13,
            fill="#e6e8eb",
            outline="#c4c6ca",
        )
        self._draw_missing_hatch(canvas, x, legend_y, x + 13, legend_y + 13)
        if palette == "Delay":
            missing_label = "No detected peak"
        else:
            missing_label = "No occupancy"
        canvas.create_text(
            x + 20,
            legend_y + 6.5,
            anchor="w",
            text=missing_label,
            fill="#6e6e73",
            font=("TkDefaultFont", 10),
        )

    def _draw_selection_outline(
        self,
        canvas: tk.Canvas,
        x0: float,
        y0: float,
        cell_x: float,
        cell_y: float,
        x_groups: list[AxisGroup] | None = None,
        y_groups: list[AxisGroup] | None = None,
    ) -> None:
        if self.selected_cell is None:
            return
        y_start, _y_end, x_idx, _x_end = self.selected_cell
        x_groups = x_groups or self._x_groups()
        y_groups = y_groups or self._display_y_groups()
        group_idx = 0
        for idx, (start, end) in enumerate(x_groups):
            if start <= x_idx <= end:
                group_idx = idx
                break
        display_y = None
        for idx, (start, end) in enumerate(y_groups):
            if start <= y_start <= end:
                display_y = idx
                break
        if display_y is None:
            return
        x = x0 + group_idx * cell_x
        y = y0 + display_y * cell_y
        canvas.create_rectangle(
            x + 1,
            y + 1,
            x + cell_x - 1,
            y + cell_y - 1,
            outline="#111827",
            width=2,
        )
        canvas.create_rectangle(
            x + 3,
            y + 3,
            x + cell_x - 3,
            y + cell_y - 3,
            outline="#ffffff",
            width=1,
        )

    def _draw_polar_matrix(
        self,
        key: str,
        matrix: list[list[float | None]],
        title: str,
        palette: str,
        value_suffix: str,
        fixed_range: tuple[float, float] | None,
    ) -> None:
        canvas = self.canvases[key]
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 160)
        disp, x_groups, y_groups = self._prepare_plot_matrix(matrix)
        low, high = (
            fixed_range
            if fixed_range is not None
            else palette_response_range(disp, palette)
        )
        total_deg = self.data.infer_total_deg()
        n_rows = len(y_groups)
        ring_span = polar_ring_span(n_rows)
        radius_units = INNER_BLANK_ROWS + n_rows * ring_span + POLAR_PAD_ROWS
        reserved_height = 84 if key == "rf" else 130
        scale = min((w - 180) / (2 * radius_units), (h - reserved_height) / (2 * radius_units))
        scale = max(4.0, scale)
        cx = w / 2
        cy = h / 2 + (0 if key == "rf" else 22)

        polar_summary = (
            f"{title.removeprefix('RF map - ')} · polar {total_deg:.0f}° · "
            f"radius {self.polar_radius_var.get()}"
        )
        if key == "rf" and hasattr(self, "rf_map_subtitle_label"):
            self.rf_map_subtitle_label.configure(text=polar_summary)
        else:
            canvas.create_text(
                20,
                22,
                anchor="w",
                text=title,
                font=("TkDefaultFont", 13, "bold"),
                fill="#1d1d1f",
            )
            canvas.create_text(
                20,
                44,
                anchor="w",
                text=(
                    f"Polar layout · total angle {total_deg:.0f}° · "
                    f"radius {self.polar_radius_var.get()}"
                ),
                fill="#6e6e73",
            )
        canvas.create_oval(
            cx - INNER_BLANK_ROWS * scale,
            cy - INNER_BLANK_ROWS * scale,
            cx + INNER_BLANK_ROWS * scale,
            cy + INNER_BLANK_ROWS * scale,
            fill="#f8fafc",
            outline="#e5e7eb",
        )

        theta_edges = [
            math.radians(90.0 + total_deg / 2.0 - total_deg * i / len(x_groups))
            for i in range(len(x_groups) + 1)
        ]
        if self.polar_radius_var.get() == POLAR_RADIUS_MODES[0]:
            ring_rows = sorted(range(n_rows), key=lambda idx: y_groups[idx][0])
        else:
            ring_rows = list(range(n_rows - 1, -1, -1))

        for ring_idx, display_row in enumerate(ring_rows):
            r_inner = INNER_BLANK_ROWS + ring_idx * ring_span
            r_outer = r_inner + ring_span
            for col in range(len(x_groups)):
                value = disp[display_row][col]
                fill = delay_color(value, low, high) if palette == "Delay" else palette_color(value, low, high, palette)
                points = self._polar_cell_points(cx, cy, scale, r_inner, r_outer, theta_edges[col], theta_edges[col + 1])
                missing = value is None or not math.isfinite(float(value))
                canvas.create_polygon(
                    points,
                    fill=fill,
                    outline="#c4c6ca" if missing else "",
                    stipple="gray25" if missing else "",
                )

        self._draw_polar_selection_outline(
            canvas,
            cx,
            cy,
            scale,
            theta_edges,
            x_groups,
            y_groups,
            ring_rows,
            ring_span,
        )

        outer_r = (INNER_BLANK_ROWS + n_rows * ring_span) * scale
        canvas.create_oval(cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r, outline="#475467")
        canvas.create_text(cx, cy - outer_r - 18, text="x columns span visual angle", fill="#475467")
        canvas.create_text(
            cx,
            cy + outer_r + 22,
            text=f"Values: {self.value_mode_var.get() if palette != 'Delay' else 'delay (ms)'}",
            fill="#475467",
        )
        self._draw_colorbar(
            canvas,
            w - 124,
            cy - min(220, 2 * outer_r) / 2,
            min(220, 2 * outer_r),
            low,
            high,
            palette,
            value_suffix,
        )
        self._canvas_layouts[key] = {
            "geometry": "polar",
            "cx": cx,
            "cy": cy,
            "scale": scale,
            "total_deg": total_deg,
            "x_groups": x_groups,
            "y_groups": y_groups,
            "ring_rows": ring_rows,
            "ring_span": ring_span,
        }

    def _draw_polar_selection_outline(
        self,
        canvas: tk.Canvas,
        cx: float,
        cy: float,
        scale: float,
        theta_edges: list[float],
        x_groups: list[AxisGroup],
        y_groups: list[AxisGroup],
        ring_rows: list[int],
        ring_span: float,
    ) -> None:
        if self.selected_cell is None:
            return
        y_start, _y_end, x_start, _x_end = self.selected_cell
        display_row = next(
            (index for index, (start, end) in enumerate(y_groups) if start <= y_start <= end),
            None,
        )
        column = next(
            (index for index, (start, end) in enumerate(x_groups) if start <= x_start <= end),
            None,
        )
        if display_row is None or column is None or display_row not in ring_rows:
            return
        ring_idx = ring_rows.index(display_row)
        points = self._polar_cell_points(
            cx,
            cy,
            scale,
            INNER_BLANK_ROWS + ring_idx * ring_span,
            INNER_BLANK_ROWS + (ring_idx + 1) * ring_span,
            theta_edges[column],
            theta_edges[column + 1],
        )
        canvas.create_polygon(points, fill="", outline="#ffffff", width=4)
        canvas.create_polygon(points, fill="", outline="#111827", width=2)

    def _polar_cell_points(
        self,
        cx: float,
        cy: float,
        scale: float,
        r_inner: float,
        r_outer: float,
        theta_start: float,
        theta_end: float,
    ) -> list[float]:
        n_arc = 16
        points: list[tuple[float, float]] = []
        for i in range(n_arc):
            t = theta_start + (theta_end - theta_start) * i / (n_arc - 1)
            points.append((cx + r_outer * scale * math.cos(t), cy - r_outer * scale * math.sin(t)))
        for i in range(n_arc - 1, -1, -1):
            t = theta_start + (theta_end - theta_start) * i / (n_arc - 1)
            points.append((cx + r_inner * scale * math.cos(t), cy - r_inner * scale * math.sin(t)))
        flat: list[float] = []
        for x, y in points:
            flat.extend((x, y))
        return flat

    def _draw_rgb(self) -> None:
        canvas = self.canvases["delay"]
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 160)
        margin_l, margin_r, margin_t, margin_b = 78, 188, 56, 68
        plot_w = max(10, w - margin_l - margin_r)
        plot_h = max(10, h - margin_t - margin_b)
        total_disp, x_groups, y_groups = self._prepare_response_plot_matrix(
            0,
            self.data.n_bins - 1,
        )
        delay_disp, entropy_disp, _x_groups_temporal, _y_groups_temporal = (
            self._grouped_temporal_metric_matrices(0.0)
        )
        n_rows = len(y_groups)
        cell_x, cell_y, grid_w, grid_h = spatial_grid_dimensions(
            plot_w,
            plot_h,
            len(x_groups),
            n_rows,
            minimum_cell_width=4.0,
        )
        x0 = margin_l + (plot_w - grid_w) / 2
        y0 = margin_t + (plot_h - grid_h) / 2
        _response_low, response_high = nonnegative_response_range(total_disp)
        max_total = max(response_high, 1.0)
        min_delay, max_delay = self._time_axis_range_ms()
        delay_span = max(max_delay - min_delay, 1.0)

        if self.polar_layout_var.get():
            self._draw_rgb_polar(
                total_disp,
                delay_disp,
                entropy_disp,
                x_groups,
                y_groups,
                max_total,
                min_delay,
                delay_span,
            )
            return

        canvas.create_text(20, 22, anchor="w", text="RGB composite", font=("TkDefaultFont", 13, "bold"), fill="#1d1d1f")
        canvas.create_text(
            20,
            44,
            anchor="w",
            text=f"R {self.value_mode_var.get()}; G count-rate-peak delay; B temporal entropy",
            fill="#667085",
        )

        for display_y in range(n_rows):
            y = y0 + display_y * cell_y
            for group_idx, (x_start, x_end) in enumerate(x_groups):
                raw_total = total_disp[display_y][group_idx]
                missing = raw_total is None or not math.isfinite(float(raw_total))
                total_value = 0.0 if missing else float(raw_total)
                total_norm = clamp(total_value / max_total)
                delay = delay_disp[display_y][group_idx]
                delay_norm = 0.0 if delay is None else clamp((delay - min_delay) / delay_span)
                entropy_norm = clamp(entropy_disp[display_y][group_idx] or 0.0)
                if missing:
                    fill = "#e6e8eb"
                elif total_value <= 0:
                    fill = "#000000"
                else:
                    fill = hex_color(
                        (
                            int(round(total_norm * 255)),
                            int(round(delay_norm * 255)),
                            int(round(entropy_norm * 255)),
                        )
                    )
                x = x0 + group_idx * cell_x
                canvas.create_rectangle(
                    x,
                    y,
                    x + cell_x,
                    y + cell_y,
                    fill=fill,
                    outline="#ffffff",
                    width=0,
                )
                if missing:
                    self._draw_missing_hatch(canvas, x, y, x + cell_x, y + cell_y)
        self._draw_selection_outline(
            canvas,
            x0,
            y0,
            cell_x,
            cell_y,
            x_groups,
            y_groups,
        )
        self._draw_axes(
            canvas,
            x0,
            y0,
            cell_x,
            cell_y,
            grid_w,
            grid_h,
            x_groups,
            y_groups,
        )
        legend_x = min(x0 + grid_w + 34, w - 154)
        legend_y = y0
        for i, (label, color) in enumerate(
            ((f"R {value_mode_unit(self.value_mode_var.get())}", "#dc2626"), ("G delay", "#16a34a"), ("B entropy", "#2563eb"))
        ):
            y = legend_y + i * 26
            canvas.create_rectangle(legend_x, y, legend_x + 16, y + 16, fill=color, outline="")
            canvas.create_text(legend_x + 24, y + 8, anchor="w", text=label, fill="#475467")
        missing_y = legend_y + 82
        canvas.create_rectangle(
            legend_x,
            missing_y,
            legend_x + 16,
            missing_y + 16,
            fill="#e6e8eb",
            outline="#c4c6ca",
        )
        self._draw_missing_hatch(
            canvas,
            legend_x,
            missing_y,
            legend_x + 16,
            missing_y + 16,
        )
        canvas.create_text(
            legend_x + 24,
            missing_y + 8,
            anchor="w",
            text="No occupancy",
            fill="#6e6e73",
        )
        self._canvas_layouts["delay"] = {
            "geometry": "rectangle",
            "x0": x0,
            "y0": y0,
            "cell": cell_x,
            "cell_y": cell_y,
            "grid_w": grid_w,
            "grid_h": grid_h,
            "x_groups": x_groups,
            "y_groups": y_groups,
        }

    def _draw_rgb_polar(
        self,
        total_disp: list[list[float | None]],
        delay_disp: list[list[float | None]],
        entropy_disp: list[list[float | None]],
        x_groups: list[AxisGroup],
        y_groups: list[AxisGroup],
        max_total: float,
        min_delay: float,
        delay_span: float,
    ) -> None:
        canvas = self.canvases["delay"]
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 160)
        total_deg = self.data.infer_total_deg()
        n_rows = len(y_groups)
        ring_span = polar_ring_span(n_rows)
        radius_units = INNER_BLANK_ROWS + n_rows * ring_span + POLAR_PAD_ROWS
        scale = max(4.0, min((w - 220) / (2 * radius_units), (h - 130) / (2 * radius_units)))
        cx = w / 2
        cy = h / 2 + 22
        canvas.create_text(20, 22, anchor="w", text="RGB composite", font=("TkDefaultFont", 15, "bold"), fill="#111827")
        canvas.create_text(
            20,
            44,
            anchor="w",
            text=(
                f"Polar layout; R {self.value_mode_var.get()}; G count-rate-peak delay; "
                "B temporal entropy"
            ),
            fill="#667085",
        )
        canvas.create_oval(
            cx - INNER_BLANK_ROWS * scale,
            cy - INNER_BLANK_ROWS * scale,
            cx + INNER_BLANK_ROWS * scale,
            cy + INNER_BLANK_ROWS * scale,
            fill="#f8fafc",
            outline="#e5e7eb",
        )
        theta_edges = [
            math.radians(90.0 + total_deg / 2.0 - total_deg * index / len(x_groups))
            for index in range(len(x_groups) + 1)
        ]
        if self.polar_radius_var.get() == POLAR_RADIUS_MODES[0]:
            ring_rows = sorted(range(n_rows), key=lambda index: y_groups[index][0])
        else:
            ring_rows = list(range(n_rows - 1, -1, -1))

        for ring_idx, display_row in enumerate(ring_rows):
            for column in range(len(x_groups)):
                raw_total = total_disp[display_row][column]
                missing = raw_total is None or not math.isfinite(float(raw_total))
                total_value = 0.0 if missing else float(raw_total)
                delay = delay_disp[display_row][column]
                if missing:
                    fill = "#e6e8eb"
                elif total_value <= 0:
                    fill = "#000000"
                else:
                    fill = hex_color(
                        (
                            int(round(clamp(total_value / max_total) * 255)),
                            int(round((0.0 if delay is None else clamp((delay - min_delay) / delay_span)) * 255)),
                            int(round(clamp(entropy_disp[display_row][column] or 0.0) * 255)),
                        )
                    )
                points = self._polar_cell_points(
                    cx,
                    cy,
                    scale,
                    INNER_BLANK_ROWS + ring_idx * ring_span,
                    INNER_BLANK_ROWS + (ring_idx + 1) * ring_span,
                    theta_edges[column],
                    theta_edges[column + 1],
                )
                canvas.create_polygon(
                    points,
                    fill=fill,
                    outline="#c4c6ca" if missing else "",
                    stipple="gray25" if missing else "",
                )

        self._draw_polar_selection_outline(
            canvas,
            cx,
            cy,
            scale,
            theta_edges,
            x_groups,
            y_groups,
            ring_rows,
            ring_span,
        )
        outer_r = (INNER_BLANK_ROWS + n_rows * ring_span) * scale
        canvas.create_oval(cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r, outline="#475467")
        legend_x = min(cx + outer_r + 26, w - 154)
        legend_y = max(64.0, cy - 40.0)
        for index, (label, color) in enumerate(
            (
                (f"R {value_mode_unit(self.value_mode_var.get())}", "#dc2626"),
                ("G delay", "#16a34a"),
                ("B entropy", "#2563eb"),
            )
        ):
            y = legend_y + index * 26
            canvas.create_rectangle(legend_x, y, legend_x + 16, y + 16, fill=color, outline="")
            canvas.create_text(legend_x + 24, y + 8, anchor="w", text=label, fill="#475467")
        missing_y = legend_y + 82
        canvas.create_rectangle(
            legend_x,
            missing_y,
            legend_x + 16,
            missing_y + 16,
            fill="#e6e8eb",
            outline="#c4c6ca",
        )
        self._draw_missing_hatch(
            canvas,
            legend_x,
            missing_y,
            legend_x + 16,
            missing_y + 16,
        )
        canvas.create_text(
            legend_x + 24,
            missing_y + 8,
            anchor="w",
            text="No occupancy",
            fill="#6e6e73",
        )
        self._canvas_layouts["delay"] = {
            "geometry": "polar",
            "cx": cx,
            "cy": cy,
            "scale": scale,
            "total_deg": total_deg,
            "x_groups": x_groups,
            "y_groups": y_groups,
            "ring_rows": ring_rows,
            "ring_span": ring_span,
        }

    def _all_positions_timeline_values(
        self,
        unit_idx: int,
        time_groups: list[AxisGroup],
    ) -> list[float]:
        value_mode = self.value_mode_var.get()
        if value_mode == VALUE_MODE_COUNT:
            metrics = self.data.metrics(unit_idx)
            return [float(sum(metrics.bin_totals[start : end + 1])) for start, end in time_groups]

        occupancy_total = sum(
            duration
            for row in self.data.occupancy_time_s
            for duration in row
            if duration > 0
        )
        if occupancy_total <= 0:
            return [0.0 for _group in time_groups]
        unit = self.data.counts[unit_idx]
        values: list[float] = []
        for start, end in time_groups:
            count = sum(
                float(sum(unit[y_idx][x_idx][start : end + 1]))
                for y_idx in range(self.data.n_y)
                for x_idx in range(self.data.n_x)
            )
            values.append(count / occupancy_total)
        return values

    def _ensure_timeline_preview_images(
        self,
        canvas: tk.Canvas,
        unit_idx: int,
        visible_bins: list[int],
        time_groups: list[AxisGroup],
        x_groups: list[AxisGroup],
        y_groups: list[AxisGroup],
        smooth_radius: int,
        cell_width: float,
        cell_height: float,
        tile_positions: dict[int, tuple[float, float]],
        atlas_width: int,
        atlas_height: int,
    ) -> float:
        cache_key = (
            id(self.data),
            unit_idx,
            self.value_mode_var.get(),
            tuple(time_groups),
            tuple(visible_bins),
            tuple(x_groups),
            tuple(y_groups),
            smooth_radius,
            self.palette_var.get(),
            self.polar_layout_var.get(),
            self.polar_radius_var.get(),
            round(cell_width, 6),
            round(cell_height, 6),
            tuple((bin_idx, *tile_positions[bin_idx]) for bin_idx in visible_bins),
            atlas_width,
            atlas_height,
        )
        if self._timeline_preview_cache_key == cache_key:
            return self._timeline_preview_high

        prepared_by_bin: dict[int, list[list[float | None]]] = {}
        high = 0.0
        for bin_idx in visible_bins:
            source_start, source_end = time_groups[bin_idx]
            prepared, _prepared_x_groups, _prepared_y_groups = (
                self._prepare_response_plot_matrix(
                source_start,
                source_end,
                    smooth=True,
                )
            )
            prepared_by_bin[bin_idx] = prepared
            high = max(
                high,
                max(
                    (
                        float(value)
                        for row in prepared
                        for value in row
                        if value is not None and math.isfinite(float(value))
                    ),
                    default=0.0,
                ),
            )

        high = max(high, 1.0)
        palette = self.palette_var.get()
        color_for_value = lambda value, high=high, palette=palette: palette_color(value, 0.0, high, palette)
        if self.polar_layout_var.get():
            total_deg = self.data.infer_total_deg()
            if self.polar_radius_var.get() == POLAR_RADIUS_MODES[0]:
                ring_rows = sorted(range(len(y_groups)), key=lambda index: y_groups[index][0])
            else:
                ring_rows = list(range(len(y_groups) - 1, -1, -1))
            ring_span = polar_ring_span(len(y_groups))
            polar_tiles = [
                (
                    prepared_by_bin[bin_idx],
                    *tile_positions[bin_idx],
                    cell_width,
                    total_deg,
                    ring_rows,
                    ring_span,
                )
                for bin_idx in visible_bins
            ]
            ppm = polar_matrix_atlas_ppm_data(
                polar_tiles,
                atlas_width,
                atlas_height,
                color_for_value,
            )
        else:
            tiles = [
                (
                    prepared_by_bin[bin_idx],
                    *tile_positions[bin_idx],
                    cell_width,
                    cell_height,
                )
                for bin_idx in visible_bins
            ]
            ppm = matrix_atlas_ppm_data(
                tiles,
                atlas_width,
                atlas_height,
                color_for_value,
            )
        atlas = tk.PhotoImage(master=canvas, data=ppm, format="PPM")

        self._timeline_preview_cache_key = cache_key
        self._timeline_preview_images = {-1: atlas}
        self._timeline_preview_high = high
        return high

    def _timeline_mini_layout(
        self,
        canvas: tk.Canvas,
        width: float,
        height: float,
        mini_top: float,
        visible_count: int,
        x_count: int,
        y_count: int,
    ) -> dict[str, float | int]:
        try:
            screen_w = float(canvas.winfo_screenwidth())
            screen_h = float(canvas.winfo_screenheight())
        except tk.TclError:
            screen_w, screen_h = width, height
        try:
            window = canvas.winfo_toplevel()
            window_w = float(window.winfo_width())
            window_h = float(window.winfo_height())
        except tk.TclError:
            window_w, window_h = width, height

        count = max(1, int(visible_count))
        x_count = max(1, int(x_count))
        y_count = max(1, int(y_count))
        gap_x = max(1.0, min(3.0, width * 0.002))
        label_gap = 4.0
        label_height = 12.0
        row_gap = max(10.0, min(16.0, height * 0.014))
        left = 44.0
        right_pad = 44.0
        available_w = max(120.0, width - left - right_pad)
        base_grid_h = min(78.0, max(44.0, min(screen_h * 0.085, window_h * 0.12)))
        density_scale = min(1.0, max(0.35, math.sqrt(50.0 / count)))
        target_grid_h = max(18.0, base_grid_h * density_scale)
        target_aspect = (
            SINGLETON_Y_REFERENCE_COLUMNS / SINGLETON_Y_REFERENCE_ROWS
            if y_count == 1
            else x_count / y_count
        )
        target_grid_w = target_grid_h * target_aspect
        target_grid_w = max(target_grid_w, 2.0 * x_count)
        target_grid_h = target_grid_w / target_aspect
        max_cols_by_width = max(1, int((available_w + gap_x) // max(1.0, target_grid_w + gap_x)))
        max_cols_by_screen = max(1, int((min(screen_w, window_w, width) - left - right_pad + gap_x) // max(1.0, target_grid_w + gap_x)))
        cols = min(count, max(1, min(max_cols_by_width, max_cols_by_screen)))
        slot_w = max(1.0, (available_w - (cols - 1) * gap_x) / cols)
        grid_w = min(target_grid_w, slot_w)
        cell_x = max(2.0, grid_w / x_count)
        grid_w = cell_x * x_count
        grid_h = grid_w / target_aspect
        cell_y = grid_h / y_count
        row_step = grid_h + label_gap + label_height + row_gap
        rows = int(math.ceil(count / cols))
        return {
            "left": left,
            "cols": cols,
            "rows": rows,
            "gap_x": gap_x,
            "label_gap": label_gap,
            "label_height": label_height,
            "row_gap": row_gap,
            "slot_w": slot_w,
            "cell": cell_x,
            "cell_y": cell_y,
            "grid_w": grid_w,
            "grid_h": grid_h,
            "row_step": row_step,
        }

    def _draw_timeline(self) -> None:
        canvas = self.canvases["timeline"]
        canvas.delete("all")
        canvas_width = canvas.winfo_width()
        canvas_height = canvas.winfo_height()
        if canvas_width <= 1 and hasattr(self, "notebook"):
            canvas_width = max(canvas_width, self.notebook.winfo_width() - 20)
            canvas_height = max(canvas_height, self.notebook.winfo_height() - 34)
        w, h = max(canvas_width, 300), max(canvas_height, 280)
        unit_idx = self.unit_idx.get()
        time_groups = self._time_groups()
        display_bins = len(time_groups)
        visible_bins = self._visible_timeline_bins(display_bins)
        time_totals = self._all_positions_timeline_values(unit_idx, time_groups)
        axis_start_ms, axis_end_ms = self._time_axis_range_ms()
        time_group_centers_ms = [
            self._time_group_center_ms(bin_idx) for bin_idx in range(display_bins)
        ]
        time_group_end_bounds_ms = [
            self._time_group_bounds_ms(bin_idx)[1] for bin_idx in range(display_bins)
        ]
        timing_warning = " Negative bins may include previous-stimulus responses." if axis_start_ms < 0.0 else ""
        canvas.create_text(20, 22, anchor="w", text=f"Timeline and {display_bins} bin maps", font=("TkDefaultFont", 15, "bold"), fill="#111827")
        canvas.create_text(
            20,
            44,
            anchor="w",
            text=(
                f"Timeline selection {self._display_range_label()}; "
                f"target width {format_ms(self._time_group_size() * self._base_bin_ms())} ms; "
                "maps show actual time intervals; "
                f"{self.value_mode_var.get()}."
                f"{timing_warning}"
            ),
            fill="#667085",
        )

        chart_x, chart_y = 64, 78
        chart_w = max(320, w - 140)
        chart_h = 62
        selected_values: list[float] | None = None
        if self.selected_cell is not None:
            y_start, y_end, x_start, x_end = self.selected_cell
            selected_values_optional = self._group_response_values(
                y_start,
                y_end,
                x_start,
                x_end,
            )
            selected_values = [
                float(value) if value is not None else 0.0
                for value in selected_values_optional
            ]
        blue_high = timeline_response_high(time_totals)
        red_high = (
            timeline_response_high(selected_values)
            if selected_values is not None
            else None
        )
        zero_x: float | None = None
        if axis_start_ms <= 0.0 <= axis_end_ms and axis_end_ms > axis_start_ms:
            zero_x = chart_x + chart_w * (0.0 - axis_start_ms) / (axis_end_ms - axis_start_ms)
            if axis_start_ms < 0.0:
                canvas.create_rectangle(chart_x, chart_y, zero_x, chart_y + chart_h, fill="#f8fafc", outline="")
        canvas.create_rectangle(chart_x, chart_y, chart_x + chart_w, chart_y + chart_h, outline="#cbd5e1")
        if zero_x is not None:
            canvas.create_line(zero_x, chart_y, zero_x, chart_y + chart_h, fill="#7c3aed", width=1, dash=(4, 3))
            canvas.create_text(zero_x + 4, chart_y + 5, anchor="nw", text="VS 0 ms", fill="#6d28d9", font=("TkDefaultFont", 10, "bold"))

        legend_y = chart_y - 11
        canvas.create_line(chart_x, legend_y, chart_x + 16, legend_y, fill="#2563eb", width=2)
        all_positions_label = (
            "All positions (sum)"
            if self.value_mode_var.get() == VALUE_MODE_COUNT
            else "All positions (weighted mean)"
        )
        canvas.create_text(
            chart_x + 21,
            legend_y,
            anchor="w",
            text=all_positions_label,
            fill="#2563eb",
            font=("TkDefaultFont", 10),
        )
        if self.selected_cell is not None:
            canvas.create_line(chart_x + 196, legend_y, chart_x + 212, legend_y, fill="#dc2626", width=2)
            canvas.create_text(chart_x + 217, legend_y, anchor="w", text="Selected cell", fill="#dc2626", font=("TkDefaultFont", 10))
        points = timeline_chart_points(
            time_totals,
            time_group_centers_ms,
            (axis_start_ms, axis_end_ms),
            blue_high,
            (chart_x, chart_y, chart_w, chart_h),
        )
        if len(points) >= 4:
            canvas.create_line(*points, fill="#2563eb", width=2, smooth=False)
        elif len(points) == 2:
            canvas.create_oval(
                points[0] - 2,
                points[1] - 2,
                points[0] + 2,
                points[1] + 2,
                fill="#2563eb",
                outline="",
            )
        if selected_values is not None:
            assert red_high is not None
            selected_points = timeline_chart_points(
                selected_values,
                time_group_centers_ms,
                (axis_start_ms, axis_end_ms),
                red_high,
                (chart_x, chart_y, chart_w, chart_h),
            )
            if len(selected_points) >= 4:
                canvas.create_line(
                    *selected_points,
                    fill="#dc2626",
                    width=1.8,
                    smooth=False,
                )
            elif len(selected_points) == 2:
                canvas.create_oval(
                    selected_points[0] - 2,
                    selected_points[1] - 2,
                    selected_points[0] + 2,
                    selected_points[1] + 2,
                    fill="#dc2626",
                    outline="",
                )
        red_axis_x = chart_x - 20
        blue_axis_x = chart_x + chart_w + 20
        axis_font = ("TkDefaultFont", 10)
        if red_high is not None:
            canvas.create_line(red_axis_x, chart_y, red_axis_x, chart_y + chart_h, fill="#dc2626", width=1)
            canvas.create_line(red_axis_x - 4, chart_y, red_axis_x, chart_y, fill="#dc2626")
            canvas.create_line(red_axis_x - 4, chart_y + chart_h, red_axis_x, chart_y + chart_h, fill="#dc2626")
            canvas.create_text(
                red_axis_x - 7,
                chart_y,
                anchor="e",
                text=format_response_value(red_high, self.value_mode_var.get()),
                fill="#dc2626",
                font=axis_font,
            )
            canvas.create_text(
                red_axis_x - 7,
                chart_y + chart_h,
                anchor="e",
                text="0",
                fill="#dc2626",
                font=axis_font,
            )
        canvas.create_line(blue_axis_x, chart_y, blue_axis_x, chart_y + chart_h, fill="#2563eb", width=1)
        canvas.create_line(blue_axis_x, chart_y, blue_axis_x + 4, chart_y, fill="#2563eb")
        canvas.create_line(blue_axis_x, chart_y + chart_h, blue_axis_x + 4, chart_y + chart_h, fill="#2563eb")
        canvas.create_text(
            blue_axis_x + 7,
            chart_y,
            anchor="w",
            text=format_response_value(blue_high, self.value_mode_var.get()),
            fill="#2563eb",
            font=axis_font,
        )
        canvas.create_text(blue_axis_x + 7, chart_y + chart_h, anchor="w", text="0", fill="#2563eb", font=axis_font)
        if self._has_time_selection():
            selected_start_ms, selected_end_ms = self._timeline_selected_time_bounds_ms()
            time_span_ms = max(axis_end_ms - axis_start_ms, self._base_bin_ms())
            range_x0 = chart_x + chart_w * (selected_start_ms - axis_start_ms) / time_span_ms
            range_x1 = chart_x + chart_w * (selected_end_ms - axis_start_ms) / time_span_ms
            canvas.create_rectangle(range_x0, chart_y, range_x1, chart_y + chart_h, outline="#16a34a", width=1)
        max_tick_intervals = 5
        tick_step = max(1, int(math.ceil(display_bins / max_tick_intervals)))
        tick_boundaries = list(range(0, display_bins + 1, tick_step))
        if tick_boundaries[-1] != display_bins:
            tick_boundaries.append(display_bins)
        for boundary in tick_boundaries:
            time_ms = axis_start_ms if boundary == 0 else self._time_group_bounds_ms(boundary - 1)[1]
            x = chart_x + chart_w * timeline_position_fraction(
                time_ms,
                axis_start_ms,
                axis_end_ms,
            )
            anchor = "w" if boundary == 0 else ("e" if boundary == display_bins else "center")
            canvas.create_line(x, chart_y + chart_h, x, chart_y + chart_h + 4, fill="#64748b")
            canvas.create_text(
                x,
                chart_y + chart_h + 17,
                anchor=anchor,
                text=format_ms(time_ms),
                fill="#475467",
                font=("TkDefaultFont", 10),
            )
        canvas.create_text(
            chart_x + chart_w / 2,
            chart_y + chart_h + 36,
            anchor="center",
            text="Time from VS onset (ms)",
            fill="#475467",
            font=("TkDefaultFont", 10),
        )

        mini_top = chart_y + chart_h + 54
        preview_x_groups = self._x_groups()
        preview_y_groups = self._display_y_groups()
        smooth_radius = self._smooth_radius()
        preview_ring_span = polar_ring_span(len(preview_y_groups))
        if self.polar_layout_var.get():
            polar_diameter_units = 2 * (
                INNER_BLANK_ROWS + len(preview_y_groups) * preview_ring_span
            )
            layout_x_count = polar_diameter_units
            layout_y_count = polar_diameter_units
        else:
            layout_x_count = len(preview_x_groups)
            layout_y_count = len(preview_y_groups)
        mini_layout = self._timeline_mini_layout(
            canvas,
            w,
            h,
            mini_top,
            len(visible_bins),
            layout_x_count,
            layout_y_count,
        )
        cols = int(mini_layout["cols"])
        rows = int(mini_layout["rows"])
        gap_x = float(mini_layout["gap_x"])
        label_gap = float(mini_layout["label_gap"])
        label_height = float(mini_layout["label_height"])
        row_gap = float(mini_layout["row_gap"])
        slot_w = float(mini_layout["slot_w"])
        preview_cell_x = float(mini_layout["cell"])
        preview_cell_y = float(mini_layout.get("cell_y", preview_cell_x))
        preview_grid_w = float(mini_layout["grid_w"])
        preview_grid_h = float(mini_layout["grid_h"])
        row_step = float(mini_layout["row_step"])
        mini_left = float(mini_layout["left"])
        tile_positions: dict[int, tuple[float, float]] = {}
        for visible_idx, bin_idx in enumerate(visible_bins):
            row = visible_idx // cols
            col = visible_idx % cols
            slot_x = mini_left + col * (slot_w + gap_x)
            x0 = slot_x + max(0.0, (slot_w - preview_grid_w) / 2.0)
            tile_positions[bin_idx] = (x0 - mini_left, row * row_step)
        atlas_width = max(
            1,
            int(math.ceil(max((x + preview_grid_w for x, _y in tile_positions.values()), default=1.0))),
        )
        atlas_height = max(
            1,
            int(math.ceil(max((y + preview_grid_h for _x, y in tile_positions.values()), default=1.0))),
        )
        self._ensure_timeline_preview_images(
            canvas,
            unit_idx,
            visible_bins,
            time_groups,
            preview_x_groups,
            preview_y_groups,
            smooth_radius,
            preview_cell_x,
            preview_cell_y,
            tile_positions,
            atlas_width,
            atlas_height,
        )
        canvas.create_image(
            mini_left,
            mini_top,
            anchor="nw",
            image=self._timeline_preview_images[-1],
        )
        self._canvas_layouts["timeline"] = {
            "chart_x": chart_x,
            "chart_y": chart_y,
            "chart_w": chart_w,
            "chart_h": chart_h,
            "mini_top": mini_top,
            "mini_w": slot_w,
            "mini_h": preview_grid_h,
            "mini_left": mini_left,
            "gap_x": gap_x,
            "label_gap": label_gap,
            "label_height": label_height,
            "row_gap": row_gap,
            "row_step": row_step,
            "cols": cols,
            "display_bins": display_bins,
            "visible_bins": visible_bins,
            "axis_start_ms": axis_start_ms,
            "axis_end_ms": axis_end_ms,
            "time_group_end_bounds_ms": time_group_end_bounds_ms,
        }
        self._timeline_cells = []
        self._timeline_cells_by_bin = {}
        selected_start, selected_end = self._timeline_selected_source_bins()
        has_time_selection = self._has_time_selection()

        for visible_idx, bin_idx in enumerate(visible_bins):
            source_start, source_end = time_groups[bin_idx]
            row = visible_idx // cols
            col = visible_idx % cols
            slot_x = mini_left + col * (slot_w + gap_x)
            x0 = slot_x + max(0.0, (slot_w - preview_grid_w) / 2.0)
            y0 = mini_top + row * row_step
            cell_x = preview_cell_x
            cell_y = preview_cell_y
            grid_w = preview_grid_w
            grid_h = preview_grid_h
            timeline_layout: dict[str, object] = {
                "geometry": "polar" if self.polar_layout_var.get() else "rectangle",
                "bin_idx": bin_idx,
                "source_start": source_start,
                "source_end": source_end,
                "x0": x0,
                "y0": y0,
                "cell": cell_x,
                "cell_y": cell_y,
                "grid_w": grid_w,
                "grid_h": grid_h,
                "label_gap": label_gap,
                "label_height": label_height,
                "x_groups": preview_x_groups,
                "y_groups": preview_y_groups,
            }
            self._timeline_cells.append(timeline_layout)
            self._timeline_cells_by_bin[bin_idx] = timeline_layout
            if self.polar_layout_var.get():
                timeline_layout.update(
                    {
                        "cx": x0 + grid_w / 2.0,
                        "cy": y0 + grid_h / 2.0,
                        "scale": cell_x,
                        "total_deg": self.data.infer_total_deg(),
                        "ring_span": preview_ring_span,
                        "ring_rows": (
                            sorted(
                                range(len(preview_y_groups)),
                                key=lambda index: preview_y_groups[index][0],
                            )
                            if self.polar_radius_var.get() == POLAR_RADIUS_MODES[0]
                            else list(range(len(preview_y_groups) - 1, -1, -1))
                        ),
                    }
                )
            in_selected_range = source_start <= selected_end and source_end >= selected_start
            if has_time_selection and in_selected_range:
                outline = "#16a34a"
                width_line = 2
            else:
                outline = "#cbd5e1"
                width_line = 1
            if self.polar_layout_var.get():
                canvas.create_oval(x0, y0, x0 + grid_w, y0 + grid_h, outline=outline, width=width_line)
            else:
                canvas.create_rectangle(x0, y0, x0 + grid_w, y0 + grid_h, outline=outline, width=width_line)
            label_color = "#15803d" if has_time_selection and in_selected_range else "#475467"
            label_font = ("TkDefaultFont", 10, "bold") if has_time_selection and in_selected_range else ("TkDefaultFont", 10)
            canvas.create_text(
                x0,
                y0 + grid_h + label_gap,
                anchor="nw",
                text=self._time_group_label(bin_idx),
                fill=label_color,
                font=label_font,
            )
        content_bottom = (
            mini_top
            + max(0, rows - 1) * row_step
            + preview_grid_h
            + label_gap
            + label_height
            + 12
        )
        last_col_count = min(cols, len(visible_bins))
        content_right = max(
            w,
            blue_axis_x + 54,
            mini_left
            + last_col_count * slot_w
            + max(0, last_col_count - 1) * gap_x
            + 44,
        )
        canvas.configure(scrollregion=(0, 0, content_right, max(h, content_bottom)))
        self._restore_timeline_scroll()

    def _canvas_to_cell(self, key: str, event: tk.Event) -> CellRef | None:
        layout = self._canvas_layouts.get(key)
        if not layout or "cell" not in layout:
            return None
        x0 = layout["x0"]
        y0 = layout["y0"]
        cell_x = layout["cell"]
        cell_y = layout.get("cell_y", cell_x)
        grid_w = layout["grid_w"]
        grid_h = layout["grid_h"]
        if not (x0 <= event.x < x0 + grid_w and y0 <= event.y < y0 + grid_h):
            return None
        group_idx = int((event.x - x0) // cell_x)
        display_y = int((event.y - y0) // cell_y)
        x_groups = layout.get("x_groups") or self._x_groups()
        y_groups = layout.get("y_groups") or self._display_y_groups()
        if not (0 <= group_idx < len(x_groups) and 0 <= display_y < len(y_groups)):
            return None
        y_start, y_end = y_groups[display_y]
        x_start, x_end = x_groups[group_idx]
        return y_start, y_end, x_start, x_end

    def _timeline_layout_at_point(
        self,
        event_x: float,
        event_y: float,
        *,
        include_label: bool,
    ) -> dict[str, object] | None:
        """Find the one timeline mini-map candidate at a canvas coordinate."""
        timeline_layout = self._canvas_layouts.get("timeline")
        if not timeline_layout or not self._timeline_cells:
            return None
        mini_left = float(timeline_layout["mini_left"])
        mini_top = float(timeline_layout["mini_top"])
        slot_w = float(timeline_layout["mini_w"])
        gap_x = float(timeline_layout["gap_x"])
        row_step = float(timeline_layout["row_step"])
        cols = max(1, int(timeline_layout["cols"]))
        relative_x = event_x - mini_left
        relative_y = event_y - mini_top
        slot_stride = slot_w + gap_x
        if relative_x < 0.0 or relative_y < 0.0 or slot_stride <= 0.0 or row_step <= 0.0:
            return None
        column = int(relative_x // slot_stride)
        row = int(relative_y // row_step)
        if not (0 <= column < cols and row >= 0):
            return None
        candidate_index = row * cols + column
        if not (0 <= candidate_index < len(self._timeline_cells)):
            return None
        candidate = self._timeline_cells[candidate_index]
        x0 = float(candidate["x0"])
        y0 = float(candidate["y0"])
        grid_w = float(candidate["grid_w"])
        grid_h = float(candidate["grid_h"])
        if include_label:
            bottom = y0 + grid_h + float(candidate.get("label_gap", 4.0)) + float(
                candidate.get("label_height", 12.0)
            )
            inside = x0 <= event_x <= x0 + grid_w and y0 <= event_y <= bottom
        else:
            inside = x0 <= event_x < x0 + grid_w and y0 <= event_y < y0 + grid_h
        return candidate if inside else None

    def _timeline_bin_at(self, event: tk.Event) -> int | None:
        layout = self._canvas_layouts.get("timeline")
        if not layout:
            return None
        canvas = self.canvases["timeline"]
        event_x = canvas.canvasx(event.x)
        event_y = canvas.canvasy(event.y)
        chart_x = layout.get("chart_x")
        chart_y = layout.get("chart_y")
        chart_w = layout.get("chart_w")
        chart_h = layout.get("chart_h")
        if (
            chart_x is not None
            and chart_y is not None
            and chart_w is not None
            and chart_h is not None
            and float(chart_x) <= event_x <= float(chart_x) + float(chart_w)
            and float(chart_y) <= event_y <= float(chart_y) + float(chart_h)
        ):
            display_bins = int(layout.get("display_bins", self._time_group_count()))
            axis_start_ms = layout.get("axis_start_ms")
            axis_end_ms = layout.get("axis_end_ms")
            end_bounds_ms = layout.get("time_group_end_bounds_ms")
            if (
                axis_start_ms is not None
                and axis_end_ms is not None
                and isinstance(end_bounds_ms, (list, tuple))
            ):
                fraction = max(
                    0.0,
                    min(1.0, (event_x - float(chart_x)) / float(chart_w)),
                )
                time_ms = float(axis_start_ms) + fraction * (
                    float(axis_end_ms) - float(axis_start_ms)
                )
                physical_bin = timeline_bin_index(time_ms, end_bounds_ms)
                if physical_bin is not None:
                    return max(0, min(display_bins - 1, physical_bin))
            bin_idx = int((event_x - float(chart_x)) / (float(chart_w) / display_bins))
            return max(0, min(display_bins - 1, bin_idx))
        cell_layout = self._timeline_layout_at_point(event_x, event_y, include_label=True)
        return int(cell_layout["bin_idx"]) if cell_layout is not None else None

    def _timeline_cell_at(self, event: tk.Event) -> tuple[int, CellRef] | None:
        canvas = self.canvases["timeline"]
        event_x = canvas.canvasx(event.x)
        event_y = canvas.canvasy(event.y)
        layout = self._timeline_layout_at_point(event_x, event_y, include_label=False)
        if layout is None:
            return None
        if layout.get("geometry") == "polar":
            polar_cell = self._polar_cell_from_layout(layout, event_x, event_y)
            if polar_cell is None:
                return None
            _ring_idx, cell_ref = polar_cell
            return int(layout["bin_idx"]), cell_ref
        x0 = float(layout["x0"])
        y0 = float(layout["y0"])
        cell_x = float(layout["cell"])
        cell_y = float(layout.get("cell_y", cell_x))
        group_idx = int((event_x - x0) // cell_x)
        display_y = int((event_y - y0) // cell_y)
        x_groups = layout.get("x_groups") or self._x_groups()
        y_groups = layout.get("y_groups") or self._display_y_groups()
        if 0 <= group_idx < len(x_groups) and 0 <= display_y < len(y_groups):
            y_start, y_end = y_groups[display_y]
            x_start, x_end = x_groups[group_idx]
            return int(layout["bin_idx"]), (y_start, y_end, x_start, x_end)
        return None

    def _polar_cell_at(self, key: str, event: tk.Event) -> tuple[int, CellRef] | None:
        layout = self._canvas_layouts.get(key)
        if not layout:
            return None
        canvas = self.canvases[key]
        return self._polar_cell_from_layout(layout, canvas.canvasx(event.x), canvas.canvasy(event.y))

    def _polar_cell_from_layout(
        self,
        layout: dict[str, object],
        event_x: float,
        event_y: float,
    ) -> tuple[int, CellRef] | None:
        cx = layout["cx"]
        cy = layout["cy"]
        scale = layout["scale"]
        total_deg = layout["total_deg"]
        x_groups = layout.get("x_groups") or self._x_groups()
        y_groups = layout.get("y_groups") or self._display_y_groups()
        ring_rows = layout.get("ring_rows")
        if not isinstance(ring_rows, list):
            ring_rows = list(range(len(y_groups) - 1, -1, -1))
        dx = (event_x - cx) / scale
        dy = (cy - event_y) / scale
        radius = math.hypot(dx, dy)
        ring_span = max(float(layout.get("ring_span", 1.0)), 1e-9)
        if not (
            INNER_BLANK_ROWS
            <= radius
            < INNER_BLANK_ROWS + len(y_groups) * ring_span
        ):
            return None
        ring_idx = int(math.floor((radius - INNER_BLANK_ROWS) / ring_span))
        if not (0 <= ring_idx < len(ring_rows)):
            return None
        display_row = int(ring_rows[ring_idx])
        theta_deg = math.degrees(math.atan2(dy, dx))
        start = 90.0 + total_deg / 2.0
        if total_deg >= 359.999:
            rel = (start - theta_deg) % 360.0
            col = int(rel / (total_deg / len(x_groups)))
        else:
            end = 90.0 - total_deg / 2.0
            while theta_deg > start:
                theta_deg -= 360.0
            while theta_deg < end:
                theta_deg += 360.0
            if not (end <= theta_deg <= start):
                return None
            col = int((start - theta_deg) / (total_deg / len(x_groups)))
        col = max(0, min(len(x_groups) - 1, col))
        y_start, y_end = y_groups[display_row]
        x_start, x_end = x_groups[col]
        return ring_idx, (y_start, y_end, x_start, x_end)

    def _on_canvas_motion(self, key: str, event: tk.Event) -> None:
        if self._selected_local_unit_index() is None:
            return
        if key in {"rf", "delay"}:
            if self._canvas_layouts.get(key, {}).get("geometry") == "polar":
                polar_cell = self._polar_cell_at(key, event)
                if polar_cell is not None:
                    ring_idx, cell = polar_cell
                    self._set_hover_cell(key, cell, event, extra=f"polar ring {ring_idx + 1}")
                else:
                    self._clear_canvas_hover(key)
            else:
                cell = self._canvas_to_cell(key, event)
                if cell is not None:
                    self._set_hover_cell(key, cell, event)
                else:
                    self._clear_canvas_hover(key)
        elif key == "timeline":
            cell = self._timeline_cell_at(event)
            if cell is not None:
                bin_idx, cell_ref = cell
                self._set_hover_cell(
                    key,
                    cell_ref,
                    event,
                    extra=f"timeline bin {self._time_group_label(bin_idx)}",
                    display_bin=bin_idx,
                )
            else:
                bin_idx = self._timeline_bin_at(event)
                if bin_idx is not None:
                    self.status_label.configure(text=f"Hover bin {self._time_group_label(bin_idx)}")
                self._clear_canvas_hover(key, keep_status=bin_idx is not None)

    def _on_canvas_click(self, key: str, event: tk.Event) -> None:
        self.canvases[key].focus_set()
        if self._selected_local_unit_index() is None:
            return
        if key in {"rf", "delay"}:
            if self._canvas_layouts.get(key, {}).get("geometry") == "polar":
                polar_cell = self._polar_cell_at(key, event)
                cell = polar_cell[1] if polar_cell is not None else None
            else:
                cell = self._canvas_to_cell(key, event)
            if cell is not None:
                self.selected_cell = cell
                self._update_all()
                self._publish_pairing_state_if_changed()
        elif key == "timeline":
            timeline_cell = self._timeline_cell_at(event)
            if timeline_cell is not None:
                bin_idx, cell = timeline_cell
                self.selected_cell = cell
            else:
                bin_idx = self._timeline_bin_at(event)
            if bin_idx is not None:
                self._select_timeline_bin(bin_idx, event)
                self._update_all()
                self._publish_pairing_state_if_changed()

    def _select_timeline_bin(self, bin_idx: int, event: tk.Event) -> None:
        if self._event_has_range_modifier(event):
            if self._timeline_range_anchor is None:
                self._timeline_range_anchor = self.range_start_var.get()
            start = min(self._timeline_range_anchor, bin_idx)
            end = max(self._timeline_range_anchor, bin_idx)
            self.range_start_var.set(start)
            self.range_end_var.set(end)
            self.bin_var.set(bin_idx)
            self._timeline_range_anchor = bin_idx
        else:
            self._timeline_range_anchor = bin_idx
            self.bin_var.set(bin_idx)
            self.range_start_var.set(bin_idx)
            self.range_end_var.set(bin_idx)
        self._sync_time_range_controls()

    def _event_has_range_modifier(self, event: tk.Event) -> bool:
        state = int(getattr(event, "state", 0) or 0)
        # Tk uses platform-dependent modifier bits. Include Shift, Control,
        # Option/Alt, Command/Meta candidates so the behavior works on macOS.
        modifier_mask = 0x100000 | 0x0001 | 0x0004 | 0x0008 | 0x0010 | 0x0020 | 0x0040 | 0x0080
        return bool(state & modifier_mask)

    def _clear_hover(self) -> None:
        had_hover = self._hover_signature is not None or self.hover_cell is not None
        for canvas in self.canvases.values():
            canvas.delete("hover")
        self.hover_cell = None
        self._hover_signature = None
        self._hover_tooltip_text = ""
        if had_hover and self._selected_local_unit_index() is not None:
            self._update_cell_label(cell=self.selected_cell)
        if self._selected_local_unit_index() is None:
            unit_id = self._selected_unit_id_value()
            self.status_label.configure(
                text=(
                    self._quality_filter_status(unit_id)
                    or (
                        f"N/A: cluster {unit_id} is not available in this session. "
                        "Use ←/→ to continue through the paired unit list."
                    )
                )
            )
            return
        self.status_label.configure(
            text=(
                f"x: {format_pos(self.data.x_positions[0])}..{format_pos(self.data.x_positions[-1])}  "
                f"y: {format_pos(self.data.y_positions[0])}..{format_pos(self.data.y_positions[-1])}  "
                f"time: {format_ms(self._time_axis_start_ms())}..{format_ms(self._time_axis_end_ms())} ms  "
                f"value: {self.value_mode_var.get()}"
            )
        )

    def _set_hover_cell(
        self,
        key: str,
        cell: CellRef,
        event: tk.Event,
        polygon: tuple[tuple[float, float], ...] | None = None,
        extra: str = "",
        display_bin: int | None = None,
    ) -> None:
        effective_bin = self.bin_var.get() if display_bin is None else int(display_bin)
        signature = (
            key,
            id(self.data),
            self.unit_idx.get(),
            cell,
            effective_bin,
            self.value_mode_var.get(),
            self.time_res_ms_var.get(),
            self.range_start_ms_var.get(),
            self.range_end_ms_var.get(),
            extra,
        )
        if signature != self._hover_signature:
            self._hover_signature = signature
            self.hover_cell = cell
            y_start, y_end, x_idx, x_end = cell
            self.status_label.configure(
                text=(
                    f"Hover {extra + '; ' if extra else ''}"
                    f"{self._y_group_text(y_start, y_end)}, {self._x_group_text(x_idx, x_end)}"
                )
            )
            self._update_cell_label(cell=cell, prefix="Hover\n", display_bin=display_bin)
            self._hover_tooltip_text = self._cell_tooltip_text(cell, display_bin=display_bin)
        self._draw_hover_overlay(
            key,
            cell,
            event,
            polygon=polygon,
            display_bin=display_bin,
            tooltip_text=self._hover_tooltip_text,
        )

    def _clear_canvas_hover(self, key: str, keep_status: bool = False) -> None:
        canvas = self.canvases.get(key)
        if canvas is not None:
            canvas.delete("hover")
        if self._hover_signature is None and self.hover_cell is None:
            return
        self.hover_cell = None
        self._hover_signature = None
        self._hover_tooltip_text = ""
        if self._selected_local_unit_index() is not None:
            self._update_cell_label(cell=self.selected_cell)

    def _draw_hover_overlay(
        self,
        key: str,
        cell: CellRef,
        event: tk.Event,
        polygon: tuple[tuple[float, float], ...] | None = None,
        display_bin: int | None = None,
        tooltip_text: str = "",
    ) -> None:
        canvas = self.canvases[key]
        canvas.delete("hover")
        y_start, _y_end, x_idx, _x_end = cell
        if polygon is not None:
            coords: list[float] = []
            for x, y in polygon:
                coords.extend((x, y))
            canvas.create_polygon(*coords, fill="", outline="#f97316", width=3, tags="hover")
        elif key in {"rf", "delay"} and self._canvas_layouts.get(key, {}).get("geometry") != "polar":
            layout = self._canvas_layouts.get(key)
            if layout:
                y_groups = layout.get("y_groups") or self._display_y_groups()
                display_y = next((idx for idx, (start, end) in enumerate(y_groups) if start <= y_start <= end), None)
                if display_y is not None:
                    x_groups = layout.get("x_groups") or self._x_groups()
                    group_idx = next((idx for idx, (start, end) in enumerate(x_groups) if start <= x_idx <= end), 0)
                    x0 = layout["x0"]
                    y0 = layout["y0"]
                    cell_x = layout["cell"]
                    cell_y = layout.get("cell_y", cell_x)
                    x = x0 + group_idx * cell_x
                    y = y0 + display_y * cell_y
                    canvas.create_rectangle(
                        x + 1,
                        y + 1,
                        x + cell_x - 1,
                        y + cell_y - 1,
                        outline="#f97316",
                        width=3,
                        tags="hover",
                    )
        elif key in {"rf", "delay"}:
            polar = self._polar_cell_at(key, event)
            layout = self._canvas_layouts.get(key)
            if polar is not None and layout:
                ring_idx, polar_cell = polar
                _y_start, _y_end, x_start, _x_end = polar_cell
                x_groups = layout.get("x_groups") or self._x_groups()
                col = next((idx for idx, (start, end) in enumerate(x_groups) if start <= x_start <= end), 0)
                total_deg = layout["total_deg"]
                theta_edges = [
                    math.radians(90.0 + total_deg / 2.0 - total_deg * i / len(x_groups))
                    for i in range(len(x_groups) + 1)
                ]
                points = self._polar_cell_points(
                    layout["cx"],
                    layout["cy"],
                    layout["scale"],
                    INNER_BLANK_ROWS + ring_idx * float(layout.get("ring_span", 1.0)),
                    INNER_BLANK_ROWS + (ring_idx + 1) * float(layout.get("ring_span", 1.0)),
                    theta_edges[col],
                    theta_edges[col + 1],
                )
                canvas.create_polygon(points, fill="", outline="#f97316", width=3, tags="hover")
        elif key == "timeline":
            if display_bin is not None:
                bin_idx = int(display_bin)
                y_start_t, _y_end_t, x_idx_t, _x_end_t = cell
                layout = self._timeline_cells_by_bin.get(bin_idx)
                if layout is not None:
                    y_groups = layout.get("y_groups") or self._display_y_groups()
                    display_y = next((idx for idx, (start, end) in enumerate(y_groups) if start <= y_start_t <= end), 0)
                    x_groups = layout.get("x_groups") or self._x_groups()
                    group_idx = next((idx for idx, (start, end) in enumerate(x_groups) if start <= x_idx_t <= end), 0)
                    x0 = float(layout["x0"])
                    y0 = float(layout["y0"])
                    cell_x = float(layout["cell"])
                    cell_y = float(layout.get("cell_y", cell_x))
                    if layout.get("geometry") == "polar":
                        polar = self._polar_cell_from_layout(
                            layout,
                            canvas.canvasx(event.x),
                            canvas.canvasy(event.y),
                        )
                        if polar is None:
                            self._draw_canvas_tooltip(canvas, event, tooltip_text)
                            return
                        ring_idx, polar_cell = polar
                        _polar_y_start, _polar_y_end, polar_x_start, _polar_x_end = polar_cell
                        x_groups = layout.get("x_groups") or self._x_groups()
                        column = next(
                            (
                                index
                                for index, (start, end) in enumerate(x_groups)
                                if start <= polar_x_start <= end
                            ),
                            0,
                        )
                        total_deg = float(layout["total_deg"])
                        theta_edges = [
                            math.radians(
                                90.0 + total_deg / 2.0 - total_deg * index / len(x_groups)
                            )
                            for index in range(len(x_groups) + 1)
                        ]
                        points = self._polar_cell_points(
                            float(layout["cx"]),
                            float(layout["cy"]),
                            float(layout["scale"]),
                            INNER_BLANK_ROWS
                            + ring_idx * float(layout.get("ring_span", 1.0)),
                            INNER_BLANK_ROWS
                            + (ring_idx + 1) * float(layout.get("ring_span", 1.0)),
                            theta_edges[column],
                            theta_edges[column + 1],
                        )
                        canvas.create_polygon(
                            points,
                            fill="",
                            outline="#f97316",
                            width=2,
                            tags="hover",
                        )
                    else:
                        x = x0 + group_idx * cell_x
                        y = y0 + display_y * cell_y
                        canvas.create_rectangle(
                            x,
                            y,
                            x + cell_x,
                            y + cell_y,
                            outline="#f97316",
                            width=2,
                            tags="hover",
                        )
        self._draw_canvas_tooltip(canvas, event, tooltip_text)

    def _draw_canvas_tooltip(
        self,
        canvas: tk.Canvas,
        event: tk.Event,
        text: str,
    ) -> None:
        line_count = max(1, len(text.splitlines()))
        pad = 8
        event_x = canvas.canvasx(event.x)
        event_y = canvas.canvasy(event.y)
        x = event_x + 14
        y = event_y + 14
        width = 190
        height = 22 + 15 * (line_count - 1) + pad
        canvas_w = canvas.winfo_width()
        canvas_h = canvas.winfo_height()
        view_left = canvas.canvasx(0)
        view_right = canvas.canvasx(canvas_w)
        view_top = canvas.canvasy(0)
        view_bottom = canvas.canvasy(canvas_h)
        if x + width > view_right - 8:
            x = event_x - width - 14
        if x < view_left + 8:
            x = view_left + 8
        if y + height > view_bottom - 8:
            y = event_y - height - 14
        if y < view_top + 8:
            y = view_top + 8
        canvas.create_rectangle(x, y, x + width, y + height, fill="#111827", outline="#111827", tags="hover")
        canvas.create_text(x + pad, y + pad, anchor="nw", text=text, fill="#f8fafc", font=("TkDefaultFont", 10), tags="hover")

    def _load_json_path(self, path: Path) -> None:
        try:
            self.data = RFMappingData(path)
        except Exception as exc:
            messagebox.showerror("Could not load RF map", str(exc))
            return
        self.settings = self._app_root._rfm_settings
        self.title(f"{self.data.path.name} — RF Map Viewer {APP_DISPLAY_VERSION}")
        self.unit_idx.set(0)
        self._selected_unit_id = self.data.unit_pool[0]
        self._last_supported_unit_id = self.data.unit_pool[0]
        self.bin_var.set(0)
        self.range_start_var.set(0)
        self.time_res_ms_var.set(
            format_ms(max(self._base_bin_ms(), self.settings.rf_time_resolution_ms))
        )
        self._last_time_group_count = self.data.n_bins
        self._last_time_groups = [(index, index) for index in range(self.data.n_bins)]
        self.range_end_var.set(self._time_group_count() - 1)
        plot_start_ms, plot_end_ms = self._default_plot_time_bounds_ms()
        self.range_start_ms_var.set(format_ms(plot_start_ms))
        self.range_end_ms_var.set(format_ms(plot_end_ms))
        value_mode = self.settings.rf_value_mode
        self.value_mode_var.set(
            value_mode if self.data.supports_value_mode(value_mode) else VALUE_MODE_RATE
        )
        self.flip_y_var.set(self.settings.rf_flip_y)
        self.palette_var.set(self.settings.rf_palette)
        self.polar_radius_var.set(self.settings.rf_polar_radius)
        self.polar_layout_var.set(self.settings.rf_polar_layout)
        self.rgb_mode_var.set(self.settings.rf_rgb_mode)
        self.smooth_radius_var.set(self.settings.rf_smooth_radius)
        self.show_probe_layout_var.set(self.settings.show_probe_layout)
        self.show_tuning_curve_var.set(self.settings.show_tuning_curve)
        self.show_waveform_var.set(self.settings.show_waveform)
        self.tuning_plot_mode_var.set(self.settings.tuning_plot_mode)
        self.tuning_layout_var.set(self.settings.tuning_layout)
        self.tuning_display_bins_var.set(self.settings.tuning_display_bins)
        self.tuning_smoothing_var.set(self.settings.tuning_smoothing)
        self.tuning_smooth_sigma_var.set(self.settings.tuning_smooth_sigma)
        self.tuning_compare_scale_var.set(self.settings.tuning_compare_scale)
        self.waveform_channel_mode_var.set(self.settings.waveform_channel_mode)
        self.selected_cell = None
        self.hover_cell = None
        self._hover_signature = None
        self._hover_tooltip_text = ""
        self._timeline_preview_cache_key = None
        self._timeline_preview_images = {}
        self._timeline_preview_high = 1.0
        self._timeline_cells = []
        self._timeline_cells_by_bin = {}
        self._timeline_range_anchor = None
        self._timeline_scroll_fraction = 0.0
        self._pair_last_local_state = None
        self.probe_geometry = None
        self.tuning_curve_data = None
        self._tuning_curve_error = None
        self._tuning_curve_candidate = None
        self._tuning_processed_cache = None
        self._tuning_scale_cache = None
        self.spatial_region = None
        self._probe_drag_start = None
        self._probe_canvas_transform = None
        self._probe_static_signature = None
        self._waveform_generation += 1
        self.waveform_payload = None
        self._waveform_payload_key = None
        self._waveform_loading_key = None
        self._waveform_error = None
        self._waveform_error_key = None
        self._sync_time_control_ranges()
        self.time_res_spin.configure(from_=self._base_bin_ms(), to=self._total_time_ms(), increment=self._base_bin_ms())
        self.x_bins_var.set(min(self.data.n_x, self.settings.rf_x_bins or self.data.n_x))
        self.y_bins_var.set(min(self.data.n_y, self.settings.rf_y_bins or self.data.n_y))
        self.x_bins_spin.configure(to=self.data.n_x)
        self.y_bins_spin.configure(to=self.data.n_y)
        self._sync_optional_view_visibility(redraw=False)
        self._select_tab_key(self.settings.default_viewer_tab)
        self._sync_json_menu()
        self._sync_unit_combo()
        self._update_all()
        self._pair_ready_viewer_set_changed(adopt_viewer=self)
        self._schedule_optional_autoload()

    def _open_figure_exporter(self) -> None:
        existing = self.__dict__.get("_figure_export_window")
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
        if not self._local_quality_visible_unit_ids():
            messagebox.showinfo(
                "No visible units",
                "No units pass the zero-spike RF-bin filter for the current RF "
                "window. Change the RF window or unit-filter Settings before exporting.",
                parent=self,
            )
            return
        self._figure_export_window = FigureExportWindow(self)

    def _export_current_matrix(self) -> None:
        if self._selected_local_unit_index() is None:
            messagebox.showinfo(
                "Unit unavailable",
                f"Cluster {self._selected_unit_id_value()} is not available in this session.",
                parent=self,
            )
            return
        source_start, source_end = self._source_bins_for_display_range()
        matrix, x_groups, y_groups = self._prepare_response_plot_matrix(
            source_start,
            source_end,
            smooth=True,
        )
        export_space = "displayed"

        range_start, range_end = self._plot_range_group_indices()
        range_start_ms, range_end_ms = self._selected_time_bounds_ms()
        value_mode = self.value_mode_var.get()
        path = filedialog.asksaveasfilename(
            title=f"Export {export_space} RF matrix",
            defaultextension=".csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
            initialfile=(
                f"unit_{self.unit_idx.get():03d}_cluster_{self.data.cluster_id(self.unit_idx.get())}_"
                f"{value_mode_slug(value_mode)}_displayed.csv"
            ),
        )
        if not path:
            return
        try:
            def write_export(writer: csv.writer) -> None:
                writer.writerow(
                    [
                        "unit_index",
                        "cluster_id",
                        "y_index_0based",
                        "y_index_matlab",
                        "y_position",
                        "x_index_0based",
                        "x_index_matlab",
                        "x_position",
                        "value",
                        "value_mode",
                        "value_unit",
                        "occupancy_time_sec_min",
                        "occupancy_time_sec_max",
                        "mode",
                        "display_y_index_0based",
                        "source_y_start_0based",
                        "source_y_end_0based",
                        "source_y_start_matlab",
                        "source_y_end_matlab",
                        "y_position_start",
                        "y_position_end",
                        "display_x_index_0based",
                        "source_x_start_0based",
                        "source_x_end_0based",
                        "source_x_start_matlab",
                        "source_x_end_matlab",
                        "x_position_start",
                        "x_position_end",
                        "export_space",
                        "time_resolution_ms",
                        "rf_range_start_group_0based",
                        "rf_range_end_group_0based",
                        "rf_range_start_ms",
                        "rf_range_end_ms",
                        "display_x_bins",
                        "display_y_bins",
                        "smooth_radius",
                        "flip_y",
                        "palette",
                        "source_json",
                    ]
                )
                for display_y, (y_start, y_end) in enumerate(y_groups):
                    for display_x, (x_start, x_end) in enumerate(x_groups):
                        occupancy_times = [
                            self.data.occupancy_time_s[y_idx][x_idx]
                            for y_idx in range(y_start, y_end + 1)
                            for x_idx in range(x_start, x_end + 1)
                        ]
                        writer.writerow(
                            [
                                self.unit_idx.get(),
                                self.data.cluster_id(self.unit_idx.get()),
                                y_start,
                                y_start + 1,
                                (self.data.y_positions[y_start] + self.data.y_positions[y_end]) / 2.0,
                                x_start,
                                x_start + 1,
                                (self.data.x_positions[x_start] + self.data.x_positions[x_end]) / 2.0,
                                matrix[display_y][display_x],
                                value_mode,
                                value_mode_unit(value_mode),
                                min(occupancy_times) if occupancy_times else "",
                                max(occupancy_times) if occupancy_times else "",
                                self._current_matrix_label(),
                                display_y,
                                y_start,
                                y_end,
                                y_start + 1,
                                y_end + 1,
                                self.data.y_positions[y_start],
                                self.data.y_positions[y_end],
                                display_x,
                                x_start,
                                x_end,
                                x_start + 1,
                                x_end + 1,
                                self.data.x_positions[x_start],
                                self.data.x_positions[x_end],
                                export_space,
                                format_ms(self._time_group_size() * self._base_bin_ms()),
                                range_start,
                                range_end,
                                range_start_ms,
                                range_end_ms,
                                self._x_target_bins(),
                                self._y_target_bins(),
                                self._smooth_radius(),
                                self.flip_y_var.get(),
                                self.palette_var.get(),
                                self.data.path,
                            ]
                        )

            _atomic_write_csv(path, write_export)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        messagebox.showinfo("Export complete", f"Wrote {export_space} matrix to {path}")


@dataclass(frozen=True)
class FigureViewerSnapshot:
    """Immutable viewer settings used by preview and final figure rendering."""

    value_mode: str
    rf_source_start: int
    rf_source_end: int
    time_groups: tuple[AxisGroup, ...]
    x_groups: tuple[AxisGroup, ...]
    y_groups: tuple[AxisGroup, ...]
    smooth_radius: int
    palette: str
    polar_radius: str
    timeline_polar: bool
    selected_cell: CellRef | None
    total_degrees: float
    timeline_range_start: int = 0
    timeline_range_end: int = -1
    timeline_active_bin: int = 0
    hd_display_bins: int = DEFAULT_HD_DISPLAY_BINS
    hd_smoothing: bool = True
    hd_smooth_sigma: float = DEFAULT_HD_SMOOTH_SIGMA
    unit_filter_enabled: bool = False
    zero_bin_threshold: int = 1
    visible_unit_ids: tuple[int, ...] | None = None
    waveform_channel_mode: str = "same_x_column"

    @classmethod
    def capture(cls, viewer: RFMViewer) -> FigureViewerSnapshot:
        source_start, source_end = viewer._source_bins_for_time_controls()
        timeline_range_start, timeline_range_end = viewer._display_range_indices()
        return cls(
            value_mode=viewer.value_mode_var.get(),
            rf_source_start=source_start,
            rf_source_end=source_end,
            time_groups=tuple(viewer._time_groups()),
            x_groups=tuple(viewer._x_groups()),
            y_groups=tuple(viewer._display_y_groups()),
            smooth_radius=viewer._smooth_radius(),
            palette=viewer.palette_var.get(),
            polar_radius=viewer.polar_radius_var.get(),
            timeline_polar=bool(viewer.polar_layout_var.get()),
            selected_cell=viewer.selected_cell,
            total_degrees=viewer.data.infer_total_deg(),
            timeline_range_start=timeline_range_start,
            timeline_range_end=timeline_range_end,
            timeline_active_bin=max(
                0,
                min(len(viewer._time_groups()) - 1, int(viewer.bin_var.get())),
            ),
            hd_display_bins=normalize_hd_bin_count(
                viewer.tuning_display_bins_var.get()
            ),
            hd_smoothing=bool(viewer.tuning_smoothing_var.get()),
            hd_smooth_sigma=float(viewer.tuning_smooth_sigma_var.get()),
            unit_filter_enabled=bool(
                viewer.settings.rf_filter_units_with_zero_bins
            ),
            zero_bin_threshold=int(viewer.settings.rf_zero_bin_threshold),
            visible_unit_ids=tuple(viewer._local_quality_visible_unit_ids()),
            waveform_channel_mode=viewer.waveform_channel_mode_var.get(),
        )


class GUIFigureDataProvider:
    """Prepare every registered figure without mutating the live viewer."""

    def __init__(
        self,
        data: RFMappingData,
        snapshot: FigureViewerSnapshot,
        *,
        shared_rf_scale: tuple[float, float] | None = None,
        shared_waveform_limit: float | None = None,
    ):
        self.data = data
        self.snapshot = snapshot
        self.shared_rf_scale = shared_rf_scale
        self.shared_waveform_limit = shared_waveform_limit
        # Capture companion geometry with the same source-session object used
        # for every other plot.  A non-modal composer must not start reading a
        # different CSV after the parent viewer switches JSON documents.
        self.probe_geometry = data.probe_geometry()
        self.probe_geometry_error = data.probe_geometry_error
        self.hd_tuning = data.hd_tuning()
        self.hd_tuning_error = data.hd_tuning_error
        self.waveform_store = data.waveform_store()
        self.waveform_error = data.waveform_error

    def __call__(self, unit_id: int, template: PlotSpec) -> PlotSpec:
        try:
            unit_idx = self.data.rf_map_by_unit_id(unit_id).unit_index
        except KeyError:
            return replace(
                template,
                data={"unavailable": f"Unit {unit_id} is unavailable in this RF dataset."},
            )

        kind = template.kind
        options = dict(template.options)
        options.setdefault("palette", self.snapshot.palette)
        options.setdefault("total_degrees", self.snapshot.total_degrees)
        if kind in {
            PlotKind.RF_POLAR,
            PlotKind.DELAY_POLAR,
            PlotKind.RGB_POLAR,
            PlotKind.TIMELINE_CURRENT,
        }:
            options.setdefault("inner_blank_rows", INNER_BLANK_ROWS)
        if kind in {PlotKind.RF_CARTESIAN, PlotKind.RF_POLAR}:
            payload = self._rf_matrix(unit_idx, polar=kind is PlotKind.RF_POLAR)
            if self.shared_rf_scale is not None:
                options.setdefault("vmin", self.shared_rf_scale[0])
                options.setdefault("vmax", self.shared_rf_scale[1])
            options.setdefault("value_unit", value_mode_unit(self.snapshot.value_mode))
            options.setdefault("show_colorbar", True)
        elif kind in {PlotKind.DELAY_CARTESIAN, PlotKind.DELAY_POLAR}:
            options["palette"] = "delay"
            options["vmin"] = self.data.time_bin_edges[0] * 1000.0
            options["vmax"] = self.data.time_bin_edges[-1] * 1000.0
            payload = self._delay_matrix(unit_idx, polar=kind is PlotKind.DELAY_POLAR)
        elif kind in {PlotKind.RGB_CARTESIAN, PlotKind.RGB_POLAR}:
            payload = self._rgb_matrix(unit_idx, polar=kind is PlotKind.RGB_POLAR)
        elif kind is PlotKind.TIMELINE_CURRENT:
            options["polar"] = self.snapshot.timeline_polar
            payload = self._timeline_payload(unit_idx)
        elif kind in {PlotKind.HD_LINE, PlotKind.HD_POLAR}:
            payload = self._hd_payload(unit_id)
        elif kind is PlotKind.PROBE_LAYOUT:
            options.setdefault("coordinate_unit", "µm")
            payload = self._probe_payload(unit_id)
            if self.probe_geometry is not None:
                template = replace(
                    template,
                    title=f"{self.probe_geometry.probe_name} layout",
                )
        elif kind is PlotKind.WAVEFORM_LOCAL_AVERAGE:
            payload = self._waveform_payload(unit_id)
            options["palette"] = "rdbu_r"
            options["value_unit"] = "µV"
            options["show_colorbar"] = True
            if "unavailable" not in payload:
                local_limit = float(payload["amplitude_limit_uv"])
                limit = (
                    self.shared_waveform_limit
                    if self.shared_waveform_limit is not None
                    else local_limit
                )
                options["vmin"] = -abs(float(limit))
                options["vmax"] = abs(float(limit))
        else:
            payload = {"unavailable": f"Unsupported figure kind: {kind.value}"}
        if kind in {PlotKind.RF_CARTESIAN, PlotKind.DELAY_CARTESIAN, PlotKind.RGB_CARTESIAN}:
            options.setdefault(
                "x_values",
                [
                    (self.data.x_positions[start] + self.data.x_positions[end]) / 2.0
                    for start, end in self.snapshot.x_groups
                ],
            )
            options.setdefault(
                "y_values",
                [
                    (self.data.y_positions[start] + self.data.y_positions[end]) / 2.0
                    for start, end in self.snapshot.y_groups
                ],
            )
            options.setdefault("x_unit", "°")
            options.setdefault("y_unit", "°")
            options.setdefault("show_axes", True)
        if kind in {PlotKind.DELAY_CARTESIAN, PlotKind.DELAY_POLAR}:
            options.setdefault("value_unit", "ms")
            options.setdefault("show_colorbar", True)
        return replace(template, data=payload, options=options)

    def shared_rf_bounds(
        self,
        unit_ids: Iterable[int],
        cancelled: Callable[[], bool] | None = None,
    ) -> tuple[float, float]:
        matrices = []
        for unit_id in unit_ids:
            if cancelled is not None and cancelled():
                raise RuntimeError("Preview superseded by a newer recipe")
            unit_idx = self.data.rf_map_by_unit_id(int(unit_id)).unit_index
            matrices.append(self._rf_matrix(unit_idx, polar=False))
        bounds = shared_scalar_scale(matrices)
        return float(bounds["vmin"]), float(bounds["vmax"])

    def shared_waveform_amplitude_limit(
        self,
        unit_ids: Iterable[int],
        cancelled: Callable[[], bool] | None = None,
    ) -> float | None:
        limit = 0.0
        found = False
        for unit_id in unit_ids:
            if cancelled is not None and cancelled():
                raise RuntimeError("Preview superseded by a newer recipe")
            try:
                payload = self.data.waveform_payload(
                    int(unit_id), self.snapshot.waveform_channel_mode
                )
            except (OSError, ValueError):
                continue
            limit = max(limit, float(payload.amplitude_limit_uv))
            found = True
        return limit if found else None

    def _prepare(
        self,
        matrix: list[list[float | None]],
        *,
        polar: bool,
    ) -> list[list[float | None]]:
        prepared = reduce_matrix_xy(
            matrix,
            list(self.snapshot.y_groups),
            list(self.snapshot.x_groups),
        )
        prepared = smooth_matrix(prepared, self.snapshot.smooth_radius)
        if not polar:
            return prepared
        if self.snapshot.polar_radius == POLAR_RADIUS_MODES[0]:
            ring_rows = sorted(
                range(len(self.snapshot.y_groups)),
                key=lambda index: self.snapshot.y_groups[index][0],
            )
        else:
            ring_rows = list(range(len(prepared) - 1, -1, -1))
        return [prepared[index] for index in ring_rows]

    def _rf_matrix(self, unit_idx: int, *, polar: bool) -> list[list[float | None]]:
        return self._grouped_response_matrix(
            unit_idx,
            self.snapshot.rf_source_start,
            self.snapshot.rf_source_end,
            polar=polar,
        )

    def _polarize_grouped(
        self,
        matrix: list[list[float | None]],
        *,
        polar: bool,
    ) -> list[list[float | None]]:
        if not polar:
            return matrix
        if self.snapshot.polar_radius == POLAR_RADIUS_MODES[0]:
            ring_rows = sorted(
                range(len(self.snapshot.y_groups)),
                key=lambda index: self.snapshot.y_groups[index][0],
            )
        else:
            ring_rows = list(range(len(matrix) - 1, -1, -1))
        return [matrix[index] for index in ring_rows]

    def _grouped_response_matrix(
        self,
        unit_idx: int,
        source_start: int,
        source_end: int,
        *,
        polar: bool,
    ) -> list[list[float | None]]:
        """Pool count/exposure observations before spatial smoothing."""

        observations = [
            [
                self.data.spatial_group_observations(
                    unit_idx,
                    y_group,
                    x_group,
                    source_start,
                    source_end,
                )
                for x_group in self.snapshot.x_groups
            ]
            for y_group in self.snapshot.y_groups
        ]
        valid = [
            [value.source_pixel_count > 0 for value in row]
            for row in observations
        ]
        if self.snapshot.value_mode == VALUE_MODE_COUNT:
            matrix: list[list[float | None]] = [
                [
                    None
                    if value.source_pixel_count <= 0
                    else value.count / value.source_pixel_count
                    for value in row
                ]
                for row in observations
            ]
            matrix = smooth_matrix(matrix, self.snapshot.smooth_radius)
            matrix = [
                [
                    value if valid[y_idx][x_idx] else None
                    for x_idx, value in enumerate(row)
                ]
                for y_idx, row in enumerate(matrix)
            ]
            return self._polarize_grouped(matrix, polar=polar)

        counts: list[list[float | None]] = [
            [value.count if value.source_pixel_count > 0 else None for value in row]
            for row in observations
        ]
        occupancies: list[list[float | None]] = [
            [
                value.occupancy_time_s
                if value.source_pixel_count > 0
                else None
                for value in row
            ]
            for row in observations
        ]
        counts = smooth_matrix(counts, self.snapshot.smooth_radius)
        occupancies = smooth_matrix(occupancies, self.snapshot.smooth_radius)
        matrix = [
            [
                None
                if (
                    not valid[y_idx][x_idx]
                    or count is None
                    or exposure is None
                    or exposure <= 0
                )
                else count / exposure
                for x_idx, (count, exposure) in enumerate(
                    zip(count_row, exposure_row)
                )
            ]
            for y_idx, (count_row, exposure_row) in enumerate(
                zip(counts, occupancies)
            )
        ]
        return self._polarize_grouped(matrix, polar=polar)

    def _delay_raw(self, unit_idx: int) -> list[list[float | None]]:
        unit = self.data.rf_map(unit_idx).spike_counts
        metrics = self.data.metrics(unit_idx)
        result: list[list[float | None]] = []
        for y_idx in range(self.data.n_y):
            row: list[float | None] = []
            for x_idx in range(self.data.n_x):
                if metrics.total[y_idx][x_idx] <= 0:
                    row.append(None)
                    continue
                hist = unit[y_idx, x_idx]
                grouped = [
                    float(hist[start : end + 1].sum())
                    for start, end in self.snapshot.time_groups
                ]
                if not grouped or max(grouped) <= 0:
                    row.append(None)
                    continue
                peak = max(range(len(grouped)), key=grouped.__getitem__)
                start, end = self.snapshot.time_groups[peak]
                row.append(
                    (
                        self.data.time_bin_edges[start]
                        + self.data.time_bin_edges[end + 1]
                    )
                    * 500.0
                )
            result.append(row)
        return result

    def _delay_matrix(self, unit_idx: int, *, polar: bool) -> list[list[float | None]]:
        delay, _entropy = self._grouped_temporal_matrices(unit_idx, polar=polar)
        return delay

    def _grouped_temporal_matrices(
        self,
        unit_idx: int,
        *,
        polar: bool,
    ) -> tuple[list[list[float | None]], list[list[float | None]]]:
        histograms = [
            [
                [
                    value
                    / max(
                        1,
                        self.data.spatial_group_source_pixel_count(
                            y_group, x_group
                        ),
                    )
                    for value in self.data.spatial_group_count_histogram(
                        unit_idx, y_group, x_group
                    )
                ]
                for x_group in self.snapshot.x_groups
            ]
            for y_group in self.snapshot.y_groups
        ]
        if (
            self.snapshot.smooth_radius > 0
            and histograms
            and histograms[0]
        ):
            output = [
                [
                    [0.0 for _bin in range(self.data.n_bins)]
                    for _x_group in self.snapshot.x_groups
                ]
                for _y_group in self.snapshot.y_groups
            ]
            for bin_idx in range(self.data.n_bins):
                temporal_slice = [
                    [histogram[bin_idx] for histogram in row]
                    for row in histograms
                ]
                smoothed = smooth_matrix(
                    temporal_slice, self.snapshot.smooth_radius
                )
                for y_idx, row in enumerate(smoothed):
                    for x_idx, value in enumerate(row):
                        output[y_idx][x_idx][bin_idx] = float(value or 0.0)
            histograms = output

        delay: list[list[float | None]] = []
        entropy: list[list[float | None]] = []
        for row in histograms:
            delay_row: list[float | None] = []
            entropy_row: list[float | None] = []
            for histogram in row:
                metrics = self.data.temporal_metrics_from_histogram(
                    histogram,
                    list(self.snapshot.time_groups),
                )
                delay_row.append(
                    metrics.delay_ms if metrics.mean_total_count > 0.0 else None
                )
                entropy_row.append(
                    metrics.entropy if metrics.mean_total_count > 0.0 else None
                )
            delay.append(delay_row)
            entropy.append(entropy_row)
        return (
            self._polarize_grouped(delay, polar=polar),
            self._polarize_grouped(entropy, polar=polar),
        )

    def _rgb_matrix(self, unit_idx: int, *, polar: bool) -> list[list[tuple[int, int, int]]]:
        response = self._grouped_response_matrix(
            unit_idx,
            0,
            self.data.n_bins - 1,
            polar=polar,
        )
        delay, entropy = self._grouped_temporal_matrices(unit_idx, polar=polar)
        response_values = [
            float(value)
            for row in response
            for value in row
            if value is not None and math.isfinite(float(value))
        ]
        response_high = max(response_values, default=0.0)
        max_response = max(response_high, 1.0)
        delay_start = self.data.time_bin_edges[0] * 1000.0
        delay_end = self.data.time_bin_edges[-1] * 1000.0
        delay_span = max(delay_end - delay_start, 1.0)
        rgb: list[list[tuple[int, int, int]]] = []
        for y_idx, row in enumerate(response):
            output_row: list[tuple[int, int, int]] = []
            for x_idx, value in enumerate(row):
                response_value = float(value) if value is not None else 0.0
                delay_value = delay[y_idx][x_idx]
                entropy_value = entropy[y_idx][x_idx]
                if response_value <= 0:
                    output_row.append((237, 240, 243))
                else:
                    output_row.append(
                        (
                            int(round(clamp(response_value / max_response) * 255)),
                            int(
                                round(
                                    clamp(
                                        (
                                            (float(delay_value) if delay_value is not None else delay_start)
                                            - delay_start
                                        )
                                        / delay_span
                                    )
                                    * 255
                                )
                            ),
                            int(round(clamp(float(entropy_value or 0.0)) * 255)),
                        )
                    )
            rgb.append(output_row)
        return rgb

    def _all_positions_timeline(self, unit_idx: int) -> list[float]:
        if self.snapshot.value_mode == VALUE_MODE_COUNT:
            totals = self.data.metrics(unit_idx).bin_totals
            return [
                float(sum(totals[start : end + 1]))
                for start, end in self.snapshot.time_groups
            ]
        occupancy_total = sum(
            float(duration)
            for row in self.data.occupancy_time_s
            for duration in row
            if duration > 0
        )
        if occupancy_total <= 0:
            return [0.0 for _group in self.snapshot.time_groups]
        unit = self.data.rf_map(unit_idx).spike_counts
        values: list[float] = []
        for start, end in self.snapshot.time_groups:
            values.append(float(unit[..., start : end + 1].sum()) / occupancy_total)
        return values

    def _selected_timeline(self, unit_idx: int) -> list[float] | None:
        if self.snapshot.selected_cell is None:
            return None
        y_start, y_end, x_start, x_end = self.snapshot.selected_cell
        result: list[float] = []
        for start, end in self.snapshot.time_groups:
            value = self.data.spatial_group_response_value(
                unit_idx,
                (y_start, y_end),
                (x_start, x_end),
                start,
                end,
                self.snapshot.value_mode,
            )
            result.append(float(value) if value is not None else 0.0)
        return result

    def _timeline_payload(self, unit_idx: int) -> dict[str, object]:
        frames = [
            self._grouped_response_matrix(
                unit_idx,
                start,
                end,
                polar=self.snapshot.timeline_polar,
            )
            for start, end in self.snapshot.time_groups
        ]
        times = [
            (
                self.data.time_bin_edges[start]
                + self.data.time_bin_edges[end + 1]
            )
            * 500.0
            for start, end in self.snapshot.time_groups
        ]
        group_count = len(self.snapshot.time_groups)
        selection_start = max(
            0,
            min(group_count - 1, int(self.snapshot.timeline_range_start)),
        )
        requested_end = self.snapshot.timeline_range_end
        selection_end = (
            group_count - 1
            if requested_end < 0
            else max(selection_start, min(group_count - 1, int(requested_end)))
        )
        time_edges = [
            self.data.time_bin_edges[start] * 1000.0
            for start, _end in self.snapshot.time_groups
        ]
        if self.snapshot.time_groups:
            time_edges.append(
                self.data.time_bin_edges[self.snapshot.time_groups[-1][1] + 1] * 1000.0
            )
        return {
            "times": times,
            "time_edges": time_edges,
            "time_unit": "ms",
            "value_unit": value_mode_unit(self.snapshot.value_mode),
            "totals": self._all_positions_timeline(unit_idx),
            "selected": self._selected_timeline(unit_idx),
            "frames": frames,
            "selection_start_index": selection_start,
            "selection_end_index": selection_end,
            "active_index": max(
                0,
                min(group_count - 1, int(self.snapshot.timeline_active_bin)),
            ),
        }

    def _hd_payload(self, unit_id: int) -> dict[str, object]:
        tuning = self.hd_tuning
        if tuning is None:
            detail = self.hd_tuning_error
            return {
                "unavailable": (
                    f"HD tuning data could not be loaded: {detail}"
                    if detail
                    else "No companion HD tuning JSON was found for this RF dataset."
                )
            }
        if isinstance(tuning, TuningCurveData):
            processed = tuning.processed_for(
                unit_id,
                self.snapshot.hd_display_bins,
                smoothing=self.snapshot.hd_smoothing,
                sigma=self.snapshot.hd_smooth_sigma,
            )
            if processed is None:
                return {"unavailable": f"HD tuning is unavailable for unit {unit_id}."}
            angles, rates = processed
            return {"angles_deg": list(angles), "rates": list(rates)}
        try:
            curve = tuning.processed_curve(
                unit_id,
                display_bins=self.snapshot.hd_display_bins,
                smoothing=self.snapshot.hd_smoothing,
                sigma=self.snapshot.hd_smooth_sigma,
            )
        except KeyError:
            return {"unavailable": f"HD tuning is unavailable for unit {unit_id}."}
        return {
            "angles_deg": curve.angles_deg.tolist(),
            "rates": curve.rates_hz.tolist(),
        }

    def _probe_payload(self, unit_id: int) -> dict[str, object]:
        geometry = self.probe_geometry
        if geometry is None:
            detail = self.probe_geometry_error
            return {
                "unavailable": (
                    f"Probe geometry could not be loaded: {detail}"
                    if detail
                    else "No companion positions.csv was found for this RF dataset."
                )
            }
        selected_unit = next(
            (unit for unit in geometry.units if unit.unit_id == unit_id),
            None,
        )
        if selected_unit is None:
            return {
                "unavailable": (
                    f"Probe position is unavailable for RF unit {unit_id}; "
                    "the selected unit is absent from positions.csv."
                )
            }
        missing_position = (
            selected_unit.x_um is None and selected_unit.y_um is None
        )
        points: list[dict[str, object]] = [
            {
                "x": channel.x_um,
                "y": channel.y_um,
                "label": "",
                "color": "#94a3b8",
            }
            for channel in geometry.channels
        ]
        # A Probe plot belongs to one output page and therefore one unit. Keep
        # physical channels as spatial context, but never leak markers for the
        # other selected/exported units onto this page.
        if not missing_position:
            if selected_unit.x_um is None or selected_unit.y_um is None:
                raise ValueError(
                    f"Probe position for unit {unit_id} is incomplete"
                )
            points.append(
                {
                    "x": selected_unit.x_um,
                    "y": selected_unit.y_um,
                    "label": str(selected_unit.unit_id),
                    "color": "#dc2626",
                }
            )
        if not points and not missing_position:
            return {"unavailable": "Probe geometry contains no channels or units."}
        return {
            "points": points,
            **({"missingPosition": True} if missing_position else {}),
        }

    def _waveform_payload(self, unit_id: int) -> dict[str, object]:
        if self.waveform_store is None:
            detail = self.waveform_error
            return {
                "unavailable": (
                    f"Waveform artifact could not be loaded: {detail}"
                    if detail
                    else "No companion waveform artifact was found for this RF dataset."
                )
            }
        try:
            return self.data.waveform_plot_payload(
                int(unit_id), self.snapshot.waveform_channel_mode
            )
        except (OSError, ValueError) as exc:
            return {"unavailable": str(exc)}


def _figure_snapshot_metadata(data: RFMappingData, snapshot: FigureViewerSnapshot) -> dict[str, object]:
    visible_unit_ids = (
        tuple(int(unit_id) for unit_id in data.unit_pool)
        if snapshot.visible_unit_ids is None
        else snapshot.visible_unit_ids
    )
    return {
        "valueMode": snapshot.value_mode,
        "valueUnit": value_mode_unit(snapshot.value_mode),
        "rfSourceBins": [snapshot.rf_source_start, snapshot.rf_source_end],
        "rfTimeRangeMs": [
            data.time_bin_edges[snapshot.rf_source_start] * 1000.0,
            data.time_bin_edges[snapshot.rf_source_end + 1] * 1000.0,
        ],
        "timeBinEdgesMs": [edge * 1000.0 for edge in data.time_bin_edges],
        "timeGroups": [list(group) for group in snapshot.time_groups],
        "xPositions": list(data.x_positions),
        "yPositions": list(data.y_positions),
        "xGroups": [list(group) for group in snapshot.x_groups],
        "yGroups": [list(group) for group in snapshot.y_groups],
        "smoothRadius": snapshot.smooth_radius,
        "palette": snapshot.palette,
        "polarRadius": snapshot.polar_radius,
        "timelinePolar": snapshot.timeline_polar,
        "timelineRange": [snapshot.timeline_range_start, snapshot.timeline_range_end],
        "timelineActiveBin": snapshot.timeline_active_bin,
        "waveformChannelMode": snapshot.waveform_channel_mode,
        "totalDegrees": snapshot.total_degrees,
        "selectedCell": list(snapshot.selected_cell) if snapshot.selected_cell is not None else None,
        "occupancyTimeSecAvailable": True,
        "occupancyTimeSecSize": [data.n_y, data.n_x],
        "unitFilter": {
            "enabled": snapshot.unit_filter_enabled,
            "zeroSpikeSpatialBinThreshold": snapshot.zero_bin_threshold,
            "spatialBinCount": data.spatial_bin_count,
            "comparison": "hide when zero-bin count is greater than or equal to threshold",
            "visibleUnitIds": list(visible_unit_ids),
            "excludedUnitIds": [
                int(unit_id)
                for unit_id in data.unit_pool
                if int(unit_id) not in visible_unit_ids
            ],
        },
    }


def _figure_provenance_metadata(
    data: RFMappingData,
    snapshot: FigureViewerSnapshot,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, object]:
    source_hash = _hash_frozen_file(data.source_identity, cancelled)
    companions: list[dict[str, object]] = []
    for kind, identities in (
        ("headDirection", (data._hd_tuning_identity,) if data._hd_tuning_identity else ()),
        ("probeGeometry", data._probe_file_identities),
        ("waveform", data._waveform_file_identities),
    ):
        for identity in identities:
            companions.append({"kind": kind, **identity.metadata(_hash_frozen_file(identity, cancelled))})
    return {
        "provenanceVersion": 1,
        "application": {
            "name": "RF Map Viewer",
            "version": APP_VERSION,
            "edition": APP_EDITION,
        },
        "source": data.source_identity.metadata(source_hash),
        "snapshot": _figure_snapshot_metadata(data, snapshot),
        "companions": companions,
        "companionStatus": {
            "headDirection": "available" if data._hd_tuning is not None else (data._hd_tuning_error or "unavailable"),
            "probeGeometry": "available" if data._probe_geometry is not None else (data._probe_geometry_error or "unavailable"),
            "waveform": "available" if data._waveform_store is not None else (data._waveform_error or "unavailable"),
        },
        "renderingContract": {
            "preview": "same-page-renderer",
            "svg": "lossless PNG embedded in SVG; plot primitives are not vector paths",
        },
    }


class FigureExportWindow(tk.Toplevel):
    """Page-based, multi-unit figure composer with exact live preview."""

    def __init__(self, viewer: RFMViewer):
        super().__init__(viewer)
        self.viewer = viewer
        self._app_root = viewer._app_root
        # The composer is a recipe for one immutable source session.  Never
        # combine its captured provider with indices from a JSON subsequently
        # selected in the still-interactive parent viewer.
        self.data = viewer.data
        self.snapshot = FigureViewerSnapshot.capture(viewer)
        self.unit_ids = self.snapshot.visible_unit_ids or ()
        if not self.unit_ids:
            raise ValueError(
                "No units pass the zero-spike RF-bin filter for the current RF window."
            )
        self._selected_unit_indices: set[int] = set()
        self._unit_selection_anchor: int | None = None
        self._unit_selection_focus: int | None = None
        selected_unit_id = int(viewer._selected_unit_id_value())
        self.current_unit_id = (
            selected_unit_id if selected_unit_id in self.unit_ids else self.unit_ids[0]
        )
        self._provider_lock = threading.Lock()
        self._base_data_provider: GUIFigureDataProvider | None = None
        self._provenance_metadata: dict[str, object] | None = None
        self._context_cache: dict[tuple[object, ...], tuple[tuple[ExportPage, ...], dict[str, object], GUIFigureDataProvider]] = {}
        self.pages: list[dict[str, object]] = [
            {"name": "Page 1", "plots": [self._current_plot_kind()]}
        ]
        self._preview_photo = None
        self._preview_after: str | None = None
        self._preview_poll_after: str | None = None
        self._preview_generation = 0
        self._preview_future: Future | None = None
        self._preview_futures: set[Future] = set()
        self._preview_futures_lock = threading.Lock()
        self._preview_queue: queue.SimpleQueue[tuple[int, object]] = queue.SimpleQueue()
        self._preview_shutdown = threading.Event()
        self._preview_cancel_events: dict[int, threading.Event] = {}
        self._export_busy = False
        self._export_future: Future | None = None
        self._export_poll_after: str | None = None
        self.title(f"Export Figures — RF Map Viewer {APP_DISPLAY_VERSION}")
        self.geometry("1380x840")
        self.minsize(1050, 680)
        self.transient(viewer)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._populate_units()
        self._refresh_pages(select=0)
        self._refresh_current_plots()
        self._schedule_preview()

    def _current_plot_kind(self) -> PlotKind:
        tab = self.viewer._active_tab_key()
        polar = bool(self.viewer.polar_layout_var.get())
        if tab == "rf":
            return PlotKind.RF_POLAR if polar else PlotKind.RF_CARTESIAN
        if tab == "delay":
            if self.viewer.rgb_mode_var.get():
                return PlotKind.RGB_POLAR if polar else PlotKind.RGB_CARTESIAN
            return PlotKind.DELAY_POLAR if polar else PlotKind.DELAY_CARTESIAN
        return PlotKind.TIMELINE_CURRENT

    def _build(self) -> None:
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=0)
        self.rowconfigure(1, weight=1)
        ttk.Label(
            self,
            text="Export Figures",
            font=("TkDefaultFont", 17, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(14, 4))
        ttk.Label(
            self,
            text=(
                "Each selected unit receives every page below. Preview and final "
                "files use the same renderer; SVG embeds a lossless raster."
            ),
            foreground="#667085",
        ).grid(row=0, column=1, columnspan=2, sticky="e", padx=16, pady=(14, 4))

        left = ttk.Frame(self, padding=14)
        left.grid(row=1, column=0, sticky="nsew")
        center = ttk.Frame(self, padding=(6, 14))
        center.grid(row=1, column=1, sticky="nsew")
        right = ttk.Frame(self, padding=14)
        right.grid(row=1, column=2, sticky="nsew")
        center.columnconfigure(0, weight=1)
        center.rowconfigure(1, weight=1)

        ttk.Label(left, text="Figure type", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.format_var = tk.StringVar(value="PDF")
        format_combo = ttk.Combobox(
            left,
            state="readonly",
            values=("PDF", "PNG", "SVG (embedded raster)"),
            textvariable=self.format_var,
            width=24,
        )
        format_combo.pack(fill="x", pady=(5, 12))
        format_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_format_changed())

        ttk.Label(
            left,
            text="Units  (click · Shift-click · ⌘-click)",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w")
        self.unit_list = tk.Listbox(left, selectmode="extended", exportselection=False, width=30, height=13)
        self.unit_list.pack(fill="both", expand=True, pady=(5, 5))
        try:
            checkbox_width = int(
                self.tk.call(
                    "font",
                    "measure",
                    self.unit_list.cget("font"),
                    "☑  ",
                )
            )
        except (tk.TclError, TypeError, ValueError):
            checkbox_width = 24
        self._unit_checkbox_hit_width = max(24, checkbox_width)
        self.unit_list.bind(
            "<Button-1>",
            lambda event: self._on_unit_list_click(event),
        )
        self.unit_list.bind(
            "<Shift-Button-1>",
            lambda event: self._on_unit_list_click(event, shift=True),
        )
        if self.tk.call("tk", "windowingsystem") == "aqua":
            self.unit_list.bind(
                "<Command-Button-1>",
                lambda event: self._on_unit_list_click(event, command=True),
            )
            self.unit_list.bind(
                "<Command-Shift-Button-1>",
                lambda event: self._on_unit_list_click(
                    event,
                    command=True,
                    shift=True,
                ),
            )
        else:
            self.unit_list.bind(
                "<Control-Button-1>",
                lambda event: self._on_unit_list_click(event, command=True),
            )
            self.unit_list.bind(
                "<Control-Shift-Button-1>",
                lambda event: self._on_unit_list_click(
                    event,
                    command=True,
                    shift=True,
                ),
            )
        unit_buttons = ttk.Frame(left)
        unit_buttons.pack(fill="x", pady=(0, 12))
        ttk.Button(unit_buttons, text="Current", command=self._select_current_unit).pack(side="left")
        ttk.Button(unit_buttons, text="All", command=self._select_all_units).pack(side="left", padx=5)
        ttk.Button(unit_buttons, text="Clear", command=self._clear_units).pack(side="left")

        ttk.Label(
            left,
            text="Pages per selected unit  (shared template)",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w")
        self.page_list = tk.Listbox(left, exportselection=False, width=30, height=8)
        self.page_list.pack(fill="both", expand=True, pady=(5, 5))
        self.page_list.bind("<<ListboxSelect>>", lambda _event: self._on_page_selected())
        page_buttons = ttk.Frame(left)
        page_buttons.pack(fill="x")
        ttk.Button(page_buttons, text="+ Page", command=self._add_page).pack(side="left")
        ttk.Button(page_buttons, text="− Page", command=self._remove_page).pack(side="left", padx=5)
        ttk.Button(
            page_buttons,
            text="↑",
            width=3,
            command=lambda: self._move_page(-1),
        ).pack(side="left", padx=(0, 2))
        ttk.Button(
            page_buttons,
            text="↓",
            width=3,
            command=lambda: self._move_page(1),
        ).pack(side="left")

        ttk.Label(center, text="Live preview", font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.preview_label = ttk.Label(center, text="Preparing preview…", anchor="center", relief="solid")
        self.preview_label.grid(row=1, column=0, sticky="nsew", pady=(6, 6))
        self.preview_status = ttk.Label(center, text="", foreground="#667085")
        self.preview_status.grid(row=2, column=0, sticky="w")

        ttk.Label(right, text="Page name", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.page_name_var = tk.StringVar(value="Page 1")
        page_name_entry = ttk.Entry(right, textvariable=self.page_name_var, width=34)
        page_name_entry.pack(fill="x", pady=(5, 12))
        page_name_entry.bind("<Return>", self._rename_page)
        page_name_entry.bind("<FocusOut>", self._rename_page)

        ttk.Label(right, text="Available views", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.available_kinds = [definition.kind for definition in PLOT_KIND_REGISTRY.values()]
        self.available_list = tk.Listbox(right, exportselection=False, width=36, height=11)
        for kind in self.available_kinds:
            self.available_list.insert("end", PLOT_KIND_REGISTRY[kind.value].label)
        self.available_list.selection_set(0)
        self.available_list.pack(fill="both", expand=True, pady=(5, 5))
        ttk.Button(right, text="Add view to page →", command=self._add_plot).pack(fill="x", pady=(0, 12))

        ttk.Label(right, text="Views on current page", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.current_plot_list = tk.Listbox(right, exportselection=False, width=36, height=10)
        self.current_plot_list.pack(fill="both", expand=True, pady=(5, 5))
        plot_buttons = ttk.Frame(right)
        plot_buttons.pack(fill="x")
        ttk.Button(plot_buttons, text="Remove", command=self._remove_plot).pack(side="left")
        ttk.Button(plot_buttons, text="↑", width=3, command=lambda: self._move_plot(-1)).pack(side="left", padx=(5, 2))
        ttk.Button(plot_buttons, text="↓", width=3, command=lambda: self._move_plot(1)).pack(side="left")

        footer = ttk.Frame(self, padding=(16, 8, 16, 14))
        footer.grid(row=2, column=0, columnspan=3, sticky="ew")
        footer.columnconfigure(1, weight=1)
        ttk.Label(footer, text="Destination").grid(row=0, column=0, sticky="w")
        self.destination_var = tk.StringVar(value="")
        ttk.Entry(footer, textvariable=self.destination_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(footer, text="Choose…", command=self._choose_destination).grid(row=0, column=2)
        self.export_button = ttk.Button(footer, text="Export", command=self._start_export)
        self.export_button.grid(row=0, column=3, padx=(12, 0))
        ttk.Button(footer, text="Close", command=self._close).grid(row=0, column=4, padx=(6, 0))
        self.export_status = ttk.Label(footer, text="", foreground="#475467")
        self.export_status.grid(row=1, column=0, columnspan=5, sticky="w", pady=(7, 0))

    def _populate_units(self) -> None:
        self._select_current_unit()

    def _refresh_unit_rows(self, *, see_focus: bool = False) -> None:
        yview = self.unit_list.yview()
        self.unit_list.delete(0, "end")
        for index, unit_id in enumerate(self.unit_ids):
            rf_map = self.data.rf_map_by_unit_id(unit_id)
            checkbox = "☑" if index in self._selected_unit_indices else "☐"
            self.unit_list.insert(
                "end",
                f"{checkbox}  index {rf_map.unit_index:03d}  ·  unit {rf_map.unit_id}",
            )
        self.unit_list.selection_clear(0, "end")
        for index in sorted(self._selected_unit_indices):
            self.unit_list.selection_set(index)
        if self._unit_selection_focus is not None:
            self.unit_list.activate(self._unit_selection_focus)
            if see_focus:
                self.unit_list.see(self._unit_selection_focus)
            elif yview:
                self.unit_list.yview_moveto(yview[0])

    def _unit_index_at_event(self, event) -> int | None:
        if not self.unit_ids:
            return None
        index = int(self.unit_list.nearest(event.y))
        bounds = self.unit_list.bbox(index)
        if bounds is None:
            return None
        _x, y, _width, height = bounds
        if not y <= int(event.y) < y + height:
            return None
        return index

    def _on_unit_list_click(
        self,
        event,
        *,
        command: bool = False,
        shift: bool = False,
    ) -> str:
        index = self._unit_index_at_event(event)
        if index is None:
            return "break"
        bounds = self.unit_list.bbox(index)
        checkbox_click = bool(
            bounds is not None
            and composer_unit_checkbox_hit(
                int(event.x),
                int(bounds[0]),
                self._unit_checkbox_hit_width,
            )
        )
        selected, anchor = composer_unit_selection_after_click(
            self._selected_unit_indices,
            index,
            self._unit_selection_anchor,
            len(self.unit_ids),
            # Clicking the checkbox itself is an additive toggle even without
            # a modifier. Shift retains its range meaning; Command retains its
            # explicit toggle/add-range meaning anywhere on the row.
            command=command or (checkbox_click and not shift),
            shift=shift,
        )
        self._selected_unit_indices = set(selected)
        self._unit_selection_anchor = anchor
        self._unit_selection_focus = index
        self._refresh_unit_rows()
        self._schedule_preview()
        return "break"

    def _select_current_unit(self) -> None:
        try:
            index = self.unit_ids.index(self.current_unit_id)
        except ValueError:
            index = None
        self._selected_unit_indices = set() if index is None else {index}
        self._unit_selection_anchor = index
        self._unit_selection_focus = index
        self._refresh_unit_rows(see_focus=True)
        self._schedule_preview()

    def _select_all_units(self) -> None:
        self._selected_unit_indices = set(range(len(self.unit_ids)))
        try:
            focus = self.unit_ids.index(self.current_unit_id)
        except ValueError:
            focus = 0 if self.unit_ids else None
        self._unit_selection_anchor = focus
        self._unit_selection_focus = focus
        self._refresh_unit_rows(see_focus=True)
        self._schedule_preview()

    def _clear_units(self) -> None:
        self._selected_unit_indices.clear()
        self._unit_selection_anchor = None
        self._unit_selection_focus = None
        self._refresh_unit_rows()
        self._schedule_preview()

    def _selected_unit_ids(self) -> tuple[int, ...]:
        return tuple(
            unit_id
            for index, unit_id in enumerate(self.unit_ids)
            if index in self._selected_unit_indices
        )

    def _selected_page_index(self) -> int:
        selection = self.page_list.curselection()
        return int(selection[0]) if selection else 0

    def _refresh_pages(self, *, select: int | None = None) -> None:
        current = self._selected_page_index() if select is None else select
        self.page_list.delete(0, "end")
        for index, page in enumerate(self.pages):
            plots = page["plots"]
            self.page_list.insert("end", f"{index + 1}. {page['name']}  ({len(plots)} views)")
        current = max(0, min(len(self.pages) - 1, current))
        self.page_list.selection_set(current)
        self.page_list.see(current)
        self.page_name_var.set(str(self.pages[current]["name"]))

    def _on_page_selected(self) -> None:
        index = self._selected_page_index()
        self.page_name_var.set(str(self.pages[index]["name"]))
        self._refresh_current_plots()
        self._schedule_preview()

    def _rename_page(self, _event=None) -> None:
        index = self._selected_page_index()
        name = self.page_name_var.get().strip()
        if not name:
            self.page_name_var.set(str(self.pages[index]["name"]))
            return
        self.pages[index]["name"] = name
        self._refresh_pages(select=index)
        self._schedule_preview()

    def _add_page(self) -> None:
        self.pages.append({"name": f"Page {len(self.pages) + 1}", "plots": []})
        self._refresh_pages(select=len(self.pages) - 1)
        self._refresh_current_plots()
        self._schedule_preview()

    def _remove_page(self) -> None:
        if len(self.pages) <= 1:
            messagebox.showinfo("Keep one page", "Each unit must have at least one page.", parent=self)
            return
        index = self._selected_page_index()
        self.pages.pop(index)
        self._refresh_pages(select=max(0, index - 1))
        self._refresh_current_plots()
        self._schedule_preview()

    def _move_page(self, delta: int) -> None:
        index = self._selected_page_index()
        target = index + delta
        if not 0 <= target < len(self.pages):
            return
        self.pages[index], self.pages[target] = self.pages[target], self.pages[index]
        self._refresh_pages(select=target)
        self._refresh_current_plots()
        self._schedule_preview()

    def _current_plot_kinds(self) -> list[PlotKind]:
        return self.pages[self._selected_page_index()]["plots"]  # type: ignore[return-value]

    def _refresh_current_plots(self, *, select: int | None = None) -> None:
        plots = self._current_plot_kinds()
        self.current_plot_list.delete(0, "end")
        for kind in plots:
            self.current_plot_list.insert("end", PLOT_KIND_REGISTRY[kind.value].label)
        if plots and select is not None:
            index = max(0, min(len(plots) - 1, select))
            self.current_plot_list.selection_set(index)
        self._refresh_pages(select=self._selected_page_index())

    def _add_plot(self) -> None:
        selection = self.available_list.curselection()
        if not selection:
            return
        plots = self._current_plot_kinds()
        plots.append(self.available_kinds[int(selection[0])])
        self._refresh_current_plots(select=len(plots) - 1)
        self._schedule_preview()

    def _remove_plot(self) -> None:
        selection = self.current_plot_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        plots = self._current_plot_kinds()
        plots.pop(index)
        self._refresh_current_plots(select=max(0, index - 1))
        self._schedule_preview()

    def _move_plot(self, delta: int) -> None:
        selection = self.current_plot_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        target = index + delta
        plots = self._current_plot_kinds()
        if not 0 <= target < len(plots):
            return
        plots[index], plots[target] = plots[target], plots[index]
        self._refresh_current_plots(select=target)
        self._schedule_preview()

    def _export_pages(self) -> tuple[ExportPage, ...]:
        pages: list[ExportPage] = []
        for index, page in enumerate(self.pages):
            kinds: list[PlotKind] = page["plots"]  # type: ignore[assignment]
            if not kinds:
                raise ValueError(f"Page {index + 1} ({page['name']}) has no views.")
            pages.append(
                ExportPage(
                    str(page["name"]),
                    tuple(PlotSpec(kind) for kind in kinds),
                )
            )
        return tuple(pages)

    def _resolved_export_pages(
        self,
        raw_pages: tuple[ExportPage, ...],
        shared_rf_scale: tuple[float, float] | None,
        shared_waveform_limit: float | None = None,
    ) -> tuple[ExportPage, ...]:
        pages: list[ExportPage] = []
        for page in raw_pages:
            plots: list[PlotSpec] = []
            for plot in page.plots:
                options = dict(plot.options)
                x_values = [
                    (self.data.x_positions[start] + self.data.x_positions[end]) / 2.0
                    for start, end in self.snapshot.x_groups
                ]
                y_values = [
                    (self.data.y_positions[start] + self.data.y_positions[end]) / 2.0
                    for start, end in self.snapshot.y_groups
                ]
                if self.snapshot.polar_radius == POLAR_RADIUS_MODES[0]:
                    polar_row_indices = sorted(
                        range(len(self.snapshot.y_groups)),
                        key=lambda index: self.snapshot.y_groups[index][0],
                    )
                else:
                    polar_row_indices = list(range(len(y_values) - 1, -1, -1))
                polar_y_values = [y_values[index] for index in polar_row_indices]
                spatial_kinds = {
                    PlotKind.RF_CARTESIAN,
                    PlotKind.RF_POLAR,
                    PlotKind.DELAY_CARTESIAN,
                    PlotKind.DELAY_POLAR,
                    PlotKind.RGB_CARTESIAN,
                    PlotKind.RGB_POLAR,
                }
                if plot.kind in spatial_kinds:
                    options.update(
                        x_values=x_values,
                        y_values=y_values,
                        x_unit="°",
                        y_unit="°",
                        show_axes=True,
                        palette=self.snapshot.palette,
                        total_degrees=self.snapshot.total_degrees,
                    )
                if plot.kind in {
                    PlotKind.RF_POLAR,
                    PlotKind.DELAY_POLAR,
                    PlotKind.RGB_POLAR,
                }:
                    # The provider has already reordered its payload into
                    # inner-to-outer rows. Freeze matching radial labels and
                    # prohibit a second renderer-side reversal.
                    options.update(
                        y_values=polar_y_values,
                        inner_blank_rows=INNER_BLANK_ROWS,
                        ring_order="inner_to_outer",
                        reverse_rings=False,
                        clockwise=True,
                    )
                if plot.kind in {PlotKind.DELAY_CARTESIAN, PlotKind.DELAY_POLAR}:
                    options.update(
                        palette="delay",
                        vmin=self.data.time_bin_edges[0] * 1000.0,
                        vmax=self.data.time_bin_edges[-1] * 1000.0,
                        value_unit="ms",
                        show_colorbar=True,
                    )
                if plot.kind is PlotKind.TIMELINE_CURRENT:
                    options.update(
                        polar=self.snapshot.timeline_polar,
                        inner_blank_rows=INNER_BLANK_ROWS,
                        palette=self.snapshot.palette,
                        total_degrees=self.snapshot.total_degrees,
                        value_unit=value_mode_unit(self.snapshot.value_mode),
                        time_unit="ms",
                    )
                if plot.kind in {PlotKind.HD_LINE, PlotKind.HD_POLAR}:
                    options.update(x_unit="°", y_unit="Hz", show_axes=True)
                if plot.kind is PlotKind.PROBE_LAYOUT:
                    options.update(
                        coordinate_unit="µm",
                        show_axes=True,
                        show_scale_bar=True,
                    )
                if plot.kind is PlotKind.WAVEFORM_LOCAL_AVERAGE:
                    options.update(
                        palette="rdbu_r",
                        value_unit="µV",
                        show_axes=True,
                        show_colorbar=True,
                    )
                    if shared_waveform_limit is not None:
                        options.update(
                            vmin=-abs(float(shared_waveform_limit)),
                            vmax=abs(float(shared_waveform_limit)),
                        )
                if plot.kind in {PlotKind.RGB_CARTESIAN, PlotKind.RGB_POLAR}:
                    options["show_colorbar"] = False
                if shared_rf_scale is not None and plot.kind in {
                    PlotKind.RF_CARTESIAN,
                    PlotKind.RF_POLAR,
                }:
                    options.update(
                        vmin=shared_rf_scale[0],
                        vmax=shared_rf_scale[1],
                        value_unit=value_mode_unit(self.snapshot.value_mode),
                        show_colorbar=True,
                    )
                start_ms = self.data.time_bin_edges[self.snapshot.rf_source_start] * 1000.0
                end_ms = self.data.time_bin_edges[self.snapshot.rf_source_end + 1] * 1000.0
                full_start_ms = self.data.time_bin_edges[0] * 1000.0
                full_end_ms = self.data.time_bin_edges[-1] * 1000.0
                grouping = (
                    f"{self.data.n_x}x{self.data.n_y} to "
                    f"{len(self.snapshot.x_groups)}x{len(self.snapshot.y_groups)}; "
                    f"smooth r={self.snapshot.smooth_radius}"
                )
                if plot.kind in {PlotKind.RF_CARTESIAN, PlotKind.RF_POLAR}:
                    context = (
                        f"{format_ms(start_ms)} to {format_ms(end_ms)} ms; "
                        f"{self.snapshot.value_mode} ({value_mode_unit(self.snapshot.value_mode)}); "
                        f"{grouping}"
                    )
                elif plot.kind in {
                    PlotKind.DELAY_CARTESIAN,
                    PlotKind.DELAY_POLAR,
                    PlotKind.RGB_CARTESIAN,
                    PlotKind.RGB_POLAR,
                }:
                    context = (
                        f"full timeline {format_ms(full_start_ms)} to "
                        f"{format_ms(full_end_ms)} ms; {grouping}"
                    )
                elif plot.kind is PlotKind.WAVEFORM_LOCAL_AVERAGE:
                    context = (
                        "best + nearest 4; "
                        f"{WAVEFORM_CHANNEL_MODE_LABELS.get(self.snapshot.waveform_channel_mode, self.snapshot.waveform_channel_mode)}; "
                        "baseline ≤ -0.25 ms"
                    )
                else:
                    context = None
                if context is not None:
                    options["subtitle"] = context
                title = plot.title or PLOT_KIND_REGISTRY[plot.kind.value].label
                if (
                    plot.kind is PlotKind.PROBE_LAYOUT
                    and self._base_data_provider is not None
                    and self._base_data_provider.probe_geometry is not None
                ):
                    title = f"{self._base_data_provider.probe_geometry.probe_name} layout"
                plots.append(replace(plot, title=title, options=options))
            pages.append(ExportPage(page.name, tuple(plots)))
        return tuple(pages)

    def _verify_export_inputs(self) -> None:
        self.data.source_identity.verify_path()
        identities = tuple(
            identity
            for identity in (
                self.data._hd_tuning_identity,
                *self.data._probe_file_identities,
                *self.data._waveform_file_identities,
            )
            if identity is not None
        )
        for identity in identities:
            identity.verify_path()

    def _recipe_key(
        self,
        unit_ids: tuple[int, ...],
        raw_pages: tuple[ExportPage, ...],
    ) -> tuple[object, ...]:
        return (
            unit_ids,
            tuple(
                (page.name, tuple(plot.kind.value for plot in page.plots))
                for page in raw_pages
            ),
        )

    def _freeze_context(
        self,
        unit_ids: tuple[int, ...],
        raw_pages: tuple[ExportPage, ...],
        cancelled: Callable[[], bool] | None = None,
    ) -> tuple[tuple[ExportPage, ...], dict[str, object], GUIFigureDataProvider]:
        key = self._recipe_key(unit_ids, raw_pages)
        with self._provider_lock:
            if cancelled is not None and cancelled():
                raise RuntimeError("Preview superseded by a newer recipe")
            self._verify_export_inputs()
            has_waveform = any(
                plot.kind is PlotKind.WAVEFORM_LOCAL_AVERAGE
                for page in raw_pages
                for plot in page.plots
            )
            if has_waveform:
                previous_waveform_inputs = self.data._waveform_file_identities
                captured_waveform_inputs = self.data.capture_waveform_inputs(
                    unit_ids
                )
                if captured_waveform_inputs != previous_waveform_inputs:
                    self._provenance_metadata = None
                self._verify_export_inputs()
            cached = self._context_cache.get(key)
            if cached is not None:
                return cached
            if self._base_data_provider is None:
                self._base_data_provider = GUIFigureDataProvider(self.data, self.snapshot)
            if self._provenance_metadata is None:
                self._provenance_metadata = _figure_provenance_metadata(
                    self.data,
                    self.snapshot,
                    cancelled,
                )
            has_rf = any(
                plot.kind in {PlotKind.RF_CARTESIAN, PlotKind.RF_POLAR}
                for page in raw_pages
                for plot in page.plots
            )
            scale = (
                self._base_data_provider.shared_rf_bounds(unit_ids, cancelled)
                if has_rf
                else None
            )
            waveform_limit = (
                self._base_data_provider.shared_waveform_amplitude_limit(
                    unit_ids, cancelled
                )
                if has_waveform
                else None
            )
            pages = (
                self._resolved_export_pages(raw_pages, scale, waveform_limit)
                if has_waveform
                else self._resolved_export_pages(raw_pages, scale)
            )
            provider = GUIFigureDataProvider(
                self.data,
                self.snapshot,
                shared_rf_scale=scale,
                shared_waveform_limit=waveform_limit,
            )
            metadata = dict(self._provenance_metadata)
            if scale is not None:
                metadata["sharedRFScale"] = {
                    "vmin": scale[0],
                    "vmax": scale[1],
                    "unit": value_mode_unit(self.snapshot.value_mode),
                    "unitIds": list(unit_ids),
                }
            if waveform_limit is not None:
                metadata["sharedWaveformScale"] = {
                    "vmin": -waveform_limit,
                    "vmax": waveform_limit,
                    "unit": "µV",
                    "unitIds": list(unit_ids),
                    "baselineEndMs": -0.25,
                    "channelMode": self.snapshot.waveform_channel_mode,
                }
            result = (pages, metadata, provider)
            if cancelled is not None and cancelled():
                raise RuntimeError("Preview superseded by a newer recipe")
            self._verify_export_inputs()
            self._context_cache[key] = result
            return result

    def _preview_request(self) -> tuple[tuple[int, ...], tuple[ExportPage, ...], int, int, int]:
        unit_ids = self._selected_unit_ids() or (self.current_unit_id,)
        pages = self._export_pages()
        page_index = self._selected_page_index()
        available_width = max(480, self.preview_label.winfo_width() - 20)
        available_height = max(360, self.preview_label.winfo_height() - 20)
        return unit_ids, pages, page_index, available_width, available_height

    def _preview_plan(
        self,
        unit_ids: tuple[int, ...],
        pages: tuple[ExportPage, ...],
        metadata: dict[str, object],
    ) -> ExportPlan:
        return ExportPlan(
            FigureFormat.PDF,
            unit_ids,
            pages,
            Path("/tmp/rfmap-live-preview.pdf"),
            metadata=metadata,
        )

    def _schedule_preview(self) -> None:
        self._preview_generation += 1
        with self._preview_futures_lock:
            old_futures = tuple(self._preview_futures)
            old_cancel_events = tuple(self._preview_cancel_events.values())
        for event in old_cancel_events:
            event.set()
        for future in old_futures:
            future.cancel()
        if self._preview_after is not None:
            try:
                self.after_cancel(self._preview_after)
            except tk.TclError:
                pass
        generation = self._preview_generation
        self._preview_after = self.after(
            80,
            lambda generation=generation: self._start_preview(generation),
        )

    def _start_preview(self, generation: int) -> None:
        self._preview_after = None
        try:
            unit_ids, raw_pages, page_index, width, height = self._preview_request()
        except Exception as exc:
            self._show_preview_error(exc)
            return
        self.preview_status.configure(text="Preparing preview and provenance…")
        cancel_event = threading.Event()
        with self._preview_futures_lock:
            self._preview_cancel_events[generation] = cancel_event

        def cancelled() -> bool:
            return self._preview_shutdown.is_set() or cancel_event.is_set()

        def worker() -> tuple[int, int, object]:
            pages, metadata, provider = self._freeze_context(
                unit_ids,
                raw_pages,
                cancelled,
            )
            if cancelled():
                raise RuntimeError("Preview superseded by a newer recipe")
            plan = self._preview_plan(unit_ids, pages, metadata)
            image = render_live_preview(
                plan,
                unit_ids[0],
                page_index,
                data_provider=provider,
            )
            if cancelled():
                image.close()
                raise RuntimeError("Preview superseded by a newer recipe")
            image.thumbnail((width, height))
            return unit_ids[0], page_index, image

        future = _submit_daemon_future(worker, name="rfmap-preview")
        self._preview_future = future
        with self._preview_futures_lock:
            self._preview_futures.add(future)

        def finished(done: Future) -> None:
            try:
                payload: object = done.result()
            except Exception as exc:
                payload = exc
            with self._preview_futures_lock:
                self._preview_futures.discard(done)
                self._preview_cancel_events.pop(generation, None)
            if self._preview_shutdown.is_set():
                if isinstance(payload, tuple) and len(payload) == 3:
                    image = payload[2]
                    if hasattr(image, "close"):
                        image.close()
                return
            self._preview_queue.put((generation, payload))

        future.add_done_callback(finished)
        self._schedule_preview_poll()

    def _schedule_preview_poll(self) -> None:
        if self._preview_poll_after is None:
            self._preview_poll_after = self.after(40, self._poll_preview)

    def _poll_preview(self) -> None:
        self._preview_poll_after = None
        while True:
            try:
                generation, payload = self._preview_queue.get_nowait()
            except queue.Empty:
                break
            if generation != self._preview_generation:
                if isinstance(payload, tuple) and len(payload) == 3:
                    stale_image = payload[2]
                    if hasattr(stale_image, "close"):
                        stale_image.close()
                continue
            self._preview_future = None
            if isinstance(payload, Exception):
                self._show_preview_error(payload)
            else:
                unit_id, page_index, image = payload
                from PIL import ImageTk

                try:
                    self._preview_photo = ImageTk.PhotoImage(image)
                finally:
                    image.close()
                self.preview_label.configure(image=self._preview_photo, text="")
                self.preview_status.configure(
                    text=(
                        f"Preview: unit {unit_id}, page {page_index + 1} "
                        "· same renderer · provenance verified"
                    )
                )
        # A stale result may arrive before the latest worker. Keep polling until
        # the current generation has either rendered or produced an error.
        with self._preview_futures_lock:
            preview_inflight = bool(self._preview_futures)
        if preview_inflight or not self._preview_queue.empty():
            self._schedule_preview_poll()

    def _show_preview_error(self, exc: Exception) -> None:
        self._preview_photo = None
        self.preview_label.configure(image="", text=f"Preview unavailable\n{exc}")
        self.preview_status.configure(
            text=(
                "Export will re-verify this source and fail safely until it is "
                "reopened or the page recipe is fixed."
            )
        )

    def _on_format_changed(self) -> None:
        self.destination_var.set("")

    def _default_base_name(self) -> str:
        stem = self.data.path.stem
        return f"{stem}_figures"

    def _choose_destination(self) -> None:
        figure_format = FigureFormat.coerce(self.format_var.get().split()[0])
        initial_dir = self.data.path.parent
        if figure_format is FigureFormat.PDF:
            path = filedialog.asksaveasfilename(
                parent=self,
                title="Export multi-page PDF",
                initialdir=initial_dir,
                initialfile=f"{self._default_base_name()}.pdf",
                defaultextension=".pdf",
                filetypes=(("PDF document", "*.pdf"),),
            )
            if path:
                self.destination_var.set(path)
            return
        parent = filedialog.askdirectory(
            parent=self,
            title=f"Choose parent folder for {figure_format.value.upper()} pages",
            initialdir=initial_dir,
            mustexist=True,
        )
        if parent:
            self.destination_var.set(str(Path(parent) / self._default_base_name()))

    def _start_export(self) -> None:
        if self._export_busy:
            return
        unit_ids = self._selected_unit_ids()
        if not unit_ids:
            messagebox.showerror("No units", "Select at least one unit to export.", parent=self)
            return
        destination_text = self.destination_var.get().strip()
        if not destination_text:
            self._choose_destination()
            destination_text = self.destination_var.get().strip()
            if not destination_text:
                return
        try:
            figure_format = FigureFormat.coerce(self.format_var.get().split()[0])
            destination = Path(destination_text).expanduser()
            raw_pages = self._export_pages()
        except Exception as exc:
            messagebox.showerror("Invalid export", str(exc), parent=self)
            return

        overwrite = False
        if destination.exists():
            if figure_format is not FigureFormat.PDF:
                messagebox.showerror(
                    "Choose a new folder",
                    "PNG/SVG export never replaces an existing directory. Choose a new output folder name.",
                    parent=self,
                )
                return
            overwrite = messagebox.askyesno(
                "Replace PDF?",
                f"{destination} already exists. Replace this file?",
                parent=self,
            )
            if not overwrite:
                return

        self._export_busy = True
        self.export_button.state(["disabled"])
        self.export_status.configure(text="Verifying provenance and freezing export plan…")

        def worker():
            pages, metadata, provider = self._freeze_context(unit_ids, raw_pages)
            plan = ExportPlan(
                figure_format,
                unit_ids,
                pages,
                destination,
                metadata=metadata,
            )
            return export_figures(
                plan,
                data_provider=provider,
                overwrite=overwrite,
                before_publish=self._verify_export_inputs,
            )

        future = _export_executor(self._app_root).submit(worker)
        self._export_future = future
        _register_export_job(self._app_root, self.viewer, future)
        page_count = len(unit_ids) * len(raw_pages)
        self.export_status.configure(text=f"Exporting {page_count} pages…")
        self._export_poll_after = self.after(50, self._poll_export)

    def _poll_export(self) -> None:
        self._export_poll_after = None
        future = self._export_future
        if future is None:
            return
        if not future.done():
            self._export_poll_after = self.after(50, self._poll_export)
            return
        try:
            result = future.result()
        except Exception as exc:
            self._finish_export(error=str(exc))
        else:
            self._finish_export(result=result)

    def _finish_export(self, *, result=None, error: str | None = None) -> None:
        future = self._export_future
        self._export_future = None
        _unregister_export_job(self._app_root, future)
        self._export_busy = False
        self.export_button.state(["!disabled"])
        if error is not None:
            self.export_status.configure(text="Export failed.")
            messagebox.showerror("Export failed", error, parent=self)
            return
        self.export_status.configure(
            text=f"Exported {result.page_count} pages to {result.destination}"
        )
        messagebox.showinfo(
            "Export complete",
            f"Exported {result.page_count} pages to\n{result.destination}",
            parent=self,
        )

    def _close(self) -> None:
        if self._export_busy:
            messagebox.showinfo(
                "Export is running",
                "Wait for the export to finish before closing the composer.",
                parent=self,
            )
            return
        self.viewer.__dict__.pop("_figure_export_window", None)
        self.destroy()

    def destroy(self) -> None:
        if (
            not getattr(self._app_root, "_rfm_quitting", False)
            and self._export_busy
        ):
            messagebox.showinfo(
                "Export is running",
                "Wait for the export to finish before closing the composer.",
                parent=self,
            )
            return
        self._preview_generation += 1
        self._preview_shutdown.set()
        with self._preview_futures_lock:
            cancel_events = tuple(self._preview_cancel_events.values())
        for event in cancel_events:
            event.set()
        if self._preview_future is not None:
            self._preview_future.cancel()
        while True:
            try:
                _generation, payload = self._preview_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(payload, tuple) and len(payload) == 3:
                image = payload[2]
                if hasattr(image, "close"):
                    image.close()
        for name in ("_preview_after", "_preview_poll_after", "_export_poll_after"):
            callback = getattr(self, name, None)
            if callback is not None:
                try:
                    self.after_cancel(callback)
                except tk.TclError:
                    pass
                setattr(self, name, None)
        super().destroy()


def run_self_test(path: Path) -> None:
    data = RFMappingData(path)
    assert data.size == (data.n_units, data.n_y, data.n_x, data.n_bins)
    assert len(data.unit_pool) == data.n_units
    assert len(data.time_bin_edges) == data.n_bins + 1
    assert data.display_y_indices(True)[0] == data.n_y - 1
    assert data.display_y_indices(True)[-1] == 0

    unit_idx = 0
    y_idx = 0
    x_idx = 0
    hist = [float(v) for v in data.counts[unit_idx][y_idx][x_idx]]
    metrics = data.metrics(unit_idx)
    assert abs(metrics.total[y_idx][x_idx] - sum(hist)) < 1e-9
    assert abs(metrics.peak[y_idx][x_idx] - (max(hist) if hist else 0.0)) < 1e-9
    if sum(hist) > 0:
        expected_bin = max(range(data.n_bins), key=lambda i: hist[i])
        assert metrics.peak_bin[y_idx][x_idx] == expected_bin
        assert metrics.delay_ms[y_idx][x_idx] == data.bin_center_ms(expected_bin)
    else:
        assert metrics.peak_bin[y_idx][x_idx] is None
        assert metrics.delay_ms[y_idx][x_idx] is None

    total = data.aggregate_matrix(unit_idx, "Total", 0, 0, data.n_bins - 1)
    peak = data.aggregate_matrix(unit_idx, "Peak", 0, 0, data.n_bins - 1)
    one_bin = data.aggregate_matrix(unit_idx, "Bin", 0, 0, data.n_bins - 1)
    test_range_end = min(4, data.n_bins - 1)
    range_sum = data.aggregate_matrix(unit_idx, "Range sum", 0, 0, test_range_end)
    assert total[y_idx][x_idx] == sum(hist)
    assert peak[y_idx][x_idx] == (max(hist) if hist else 0.0)
    assert one_bin[y_idx][x_idx] == hist[0]
    assert range_sum[y_idx][x_idx] == sum(hist[: test_range_end + 1])
    count_response = data.response_matrix(unit_idx, 0, test_range_end, VALUE_MODE_COUNT)
    if data.occupancy_time_s[y_idx][x_idx] <= 0:
        assert count_response[y_idx][x_idx] is None
    else:
        assert count_response[y_idx][x_idx] == range_sum[y_idx][x_idx]
    occupancy_time_s = data.occupancy_time_s[y_idx][x_idx]
    if occupancy_time_s > 0:
        expected_rate = sum(hist[: test_range_end + 1]) / occupancy_time_s
        firing_rate = data.response_value(
            unit_idx, y_idx, x_idx, 0, test_range_end, VALUE_MODE_RATE
        )
        assert firing_rate is not None
        assert abs(firing_rate - expected_rate) < 1e-9
    assert 0.0 <= metrics.entropy[y_idx][x_idx] <= 1.0
    inferred_total_deg = data.infer_total_deg()
    assert math.isfinite(inferred_total_deg) and inferred_total_deg > 0
    hd_angles, hd_rates = processed_tuning_curve(
        tuple(float(index % 17) for index in range(HD_RAW_BIN_COUNT)),
        DEFAULT_HD_DISPLAY_BINS,
        smoothing=True,
        sigma=DEFAULT_HD_SMOOTH_SIGMA,
    )
    assert len(hd_angles) == DEFAULT_HD_DISPLAY_BINS
    assert len(hd_rates) == DEFAULT_HD_DISPLAY_BINS
    assert all(math.isfinite(value) and value >= 0.0 for value in hd_rates)
    _cli_print(
        "self-test passed:",
        f"{data.n_units} units, {data.n_y} y, {data.n_x} x, {data.n_bins} bins",
        "occupancy metadata: yes",
    )


def run_tkdnd_self_test() -> None:
    """Verify that the optional-file drop runtime is usable in a frozen app."""

    if not TK_AVAILABLE:
        raise RuntimeError("tkinter is not available")
    try:
        from tkinterdnd2 import TkinterDnD
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError("tkinterdnd2 is not available") from exc

    root = TkinterDnD.Tk()
    try:
        root.withdraw()
        version = TkinterDnD.require(root)
        root.update_idletasks()
    finally:
        root.destroy()
    _cli_print(f"TkDND self-test passed: {version}")


def run_figure_export_self_test(output_root: Path) -> None:
    """Exercise packaged PDF, directory-figure, and CSV publication paths."""

    if output_root.exists():
        if not output_root.is_dir() or any(output_root.iterdir()):
            raise RuntimeError("figure export self-test directory must be empty")
    else:
        output_root.mkdir(parents=True)
    page = ExportPage(
        "Self test",
        [
            PlotSpec(
                PlotKind.RF_CARTESIAN,
                [[0.0, 1.0], [2.0, 3.0]],
                options={"subtitle": "packaged publication smoke"},
            ),
            PlotSpec(
                PlotKind.WAVEFORM_LOCAL_AVERAGE,
                {
                    "matrix": [
                        [-1.0, -2.0, 0.0, 1.0],
                        [-2.0, -5.0, 2.0, 1.0],
                    ],
                    "times_ms": [-0.5, 0.0, 0.5, 1.0],
                    "time_edges_ms": [-0.75, -0.25, 0.25, 0.75, 1.25],
                    "channel_labels": ["ch 7", "ch 3"],
                    "best_channel_row": 1,
                },
                options={"subtitle": "same x column"},
            ),
        ],
    )
    png_destination = output_root / "figure-export-smoke"
    pdf_destination = output_root / "figure-export-smoke.pdf"
    png_result = export_figures(
        ExportPlan(FigureFormat.PNG, [1], [page], png_destination)
    )
    pdf_result = export_figures(
        ExportPlan(FigureFormat.PDF, [1], [page], pdf_destination)
    )
    csv_destination = output_root / "displayed-data-smoke.csv"

    def write_smoke_csv(writer: csv.writer) -> None:
        writer.writerow(["unit_id", "value"])
        writer.writerow([1, 3])

    _atomic_write_csv(csv_destination, write_smoke_csv)
    if (
        png_result.page_count != 1
        or not (png_destination / "manifest.json").is_file()
        or not png_result.files[0].is_file()
        or pdf_result.page_count != 1
        or not pdf_destination.read_bytes().startswith(b"%PDF-")
        or csv_destination.read_text(encoding="utf-8") != "unit_id,value\n1,3\n"
    ):
        raise RuntimeError("figure export self-test output verification failed")
    _cli_print(f"Figure export self-test passed: {output_root}")


def _cli_print(*values: object, error: bool = False) -> None:
    """Emit CLI diagnostics when the frozen executable has a console.

    PyInstaller's Windows ``--windowed`` bootloader intentionally exposes
    ``sys.stdout`` and ``sys.stderr`` as ``None``.  Release smoke tests still
    need their exit status to be meaningful, so a missing stream must not turn
    a successful (or deliberately failed) self-test into an unrelated
    ``AttributeError``.
    """

    stream = sys.stderr if error else sys.stdout
    if stream is not None:
        print(*values, file=stream)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Native GUI viewer for RF mapping data.")
    parser.add_argument(
        "json_path",
        nargs="?",
        default=None,
        help="Path to an RF .rfmap or JSON file. Omit it to open the file chooser.",
    )
    parser.add_argument("--self-test", action="store_true", help="Run data/model tests and exit.")
    parser.add_argument(
        "--self-test-dnd",
        action="store_true",
        help="Load the bundled TkDND runtime and exit.",
    )
    parser.add_argument(
        "--self-test-export",
        metavar="DIRECTORY",
        default=None,
        help="Write packaged PDF, PNG, and CSV smoke outputs, then exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.self_test_dnd:
        try:
            run_tkdnd_self_test()
        except Exception as exc:
            _cli_print(f"TkDND self-test failed: {exc}", error=True)
            return 1
        return 0
    if args.self_test_export is not None:
        try:
            run_figure_export_self_test(Path(args.self_test_export).expanduser())
        except Exception as exc:
            _cli_print(f"Figure export self-test failed: {exc}", error=True)
            return 1
        return 0
    path: Path | None
    if args.json_path is not None:
        path = Path(args.json_path).expanduser()
        if not path.exists():
            _cli_print(f"RF mapping file not found: {path}", error=True)
            return 2
        if document_kind(path) != "rf":
            _cli_print(
                "A .tc or .probe companion needs an RF map; "
                "open a .rfmap or .json file first.",
                error=True,
            )
            return 2
    else:
        path = None
    if args.self_test and path is None:
        _cli_print("--self-test requires an explicit RF mapping file", error=True)
        return 2
    if args.self_test and not path.exists():
        _cli_print(f"RF mapping file not found: {path}", error=True)
        return 2
    if args.self_test:
        assert path is not None
        run_self_test(path)
        return 0

    if not TK_AVAILABLE:
        _cli_print(
            "tkinter is not available in this Python; use a local Python with Tk to launch the GUI.",
            error=True,
        )
        return 1

    app = RFMViewer(startup_path=path) if path is not None else RFMViewer()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

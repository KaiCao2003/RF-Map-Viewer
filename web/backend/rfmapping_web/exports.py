from __future__ import annotations

import csv
import math
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, TextIO

import numpy as np

from .datasets import DatasetRecord
from .paths import is_within


VALUE_MODE_COUNT = "Spike count"
VALUE_MODE_PER_PRESENTATION = "Spikes / presentation"
VALUE_MODE_RATE = "Mean firing rate (Hz)"
VALUE_MODES = {
    VALUE_MODE_COUNT,
    VALUE_MODE_PER_PRESENTATION,
    VALUE_MODE_RATE,
}
PALETTES = {"Gray", "Viridis", "Inferno"}

CSV_HEADERS = (
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
    "presentation_count_min",
    "presentation_count_max",
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
)


class ExportValidationError(ValueError):
    """The requested displayed export cannot be produced from this dataset."""


class OutputPathError(ValueError):
    """The requested server-side output path is unsafe or unsupported."""


@dataclass(frozen=True)
class DisplayedCsvOptions:
    cluster_id: int
    value_mode: str
    rf_start_ms: float
    rf_end_ms: float
    time_resolution_ms: float
    x_bins: int
    y_bins: int
    smooth_radius: int
    flip_y: bool
    palette: str
    output_path: str | None = None
    overwrite: bool = False


def _format_ms(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _value_mode_unit(value_mode: str) -> str:
    if value_mode == VALUE_MODE_COUNT:
        return "spikes"
    if value_mode == VALUE_MODE_PER_PRESENTATION:
        return "spikes/presentation"
    if value_mode == VALUE_MODE_RATE:
        return "Hz"
    raise ExportValidationError(f"Unknown value mode: {value_mode}")


def _value_mode_slug(value_mode: str) -> str:
    if value_mode == VALUE_MODE_COUNT:
        return "spike_count"
    if value_mode == VALUE_MODE_PER_PRESENTATION:
        return "spikes_per_presentation"
    if value_mode == VALUE_MODE_RATE:
        return "mean_firing_rate_hz"
    raise ExportValidationError(f"Unknown value mode: {value_mode}")


def _axis_groups(source_count: int, target_count: int) -> list[tuple[int, int]]:
    target = max(1, min(source_count, int(target_count)))
    groups: list[tuple[int, int]] = []
    for group_index in range(target):
        start = group_index * source_count // target
        end = ((group_index + 1) * source_count // target) - 1
        groups.append((start, max(start, end)))
    return groups


def _snap_time_range(
    edges_ms: list[float], requested_start: float, requested_end: float
) -> tuple[int, int]:
    n_bins = len(edges_ms) - 1
    axis_start, axis_end = edges_ms[0], edges_ms[-1]
    requested_start = max(axis_start, min(axis_end, requested_start))
    requested_end = max(axis_start, min(axis_end, requested_end))
    if requested_start > requested_end:
        requested_start, requested_end = requested_end, requested_start

    start_edge = min(
        range(n_bins), key=lambda index: abs(edges_ms[index] - requested_start)
    )
    end_edge = min(
        range(1, n_bins + 1),
        key=lambda index: abs(edges_ms[index] - requested_end),
    )
    if end_edge <= start_edge:
        if requested_start >= axis_end:
            start_edge, end_edge = n_bins - 1, n_bins
        elif requested_end <= axis_start:
            start_edge, end_edge = 0, 1
        else:
            end_edge = min(n_bins, start_edge + 1)
    return start_edge, end_edge - 1


def _base_bin_ms(time_edges: list[float]) -> float:
    differences = [
        (right - left) * 1000.0
        for left, right in zip(time_edges, time_edges[1:])
    ]
    positive = [difference for difference in differences if difference > 1e-9]
    return min(positive) if positive else 1.0


def _time_groups(
    time_edges: list[float], n_bins: int, requested_resolution_ms: float
) -> tuple[list[tuple[int, int]], int, float]:
    base = _base_bin_ms(time_edges)
    total = max((time_edges[-1] - time_edges[0]) * 1000.0, base)
    requested = max(base, min(total, requested_resolution_ms))
    group_size = max(1, min(n_bins, int(round(requested / base))))
    groups = [
        (start, min(start + group_size - 1, n_bins - 1))
        for start in range(0, n_bins, group_size)
    ]
    return groups, group_size, base


def _reduce_matrix(
    matrix: list[list[float | None]],
    y_groups: list[tuple[int, int]],
    x_groups: list[tuple[int, int]],
) -> list[list[float | None]]:
    reduced: list[list[float | None]] = []
    for y_start, y_end in y_groups:
        output_row: list[float | None] = []
        for x_start, x_end in x_groups:
            values: list[float] = []
            for y_index in range(y_start, y_end + 1):
                for x_index in range(x_start, x_end + 1):
                    value = matrix[y_index][x_index]
                    if value is not None and math.isfinite(float(value)):
                        values.append(float(value))
            output_row.append(sum(values) / len(values) if values else None)
        reduced.append(output_row)
    return reduced


def _smooth_matrix(
    matrix: list[list[float | None]], radius: int
) -> list[list[float | None]]:
    radius = max(0, int(radius))
    current = [row[:] for row in matrix]
    rows = len(current)
    columns = len(current[0]) if rows else 0
    for _pass in range(radius):
        output: list[list[float | None]] = []
        for y_index in range(rows):
            output_row: list[float | None] = []
            for x_index in range(columns):
                total = 0.0
                weight_total = 0.0
                for dy in (-1, 0, 1):
                    y_neighbor = y_index + dy
                    if y_neighbor < 0 or y_neighbor >= rows:
                        continue
                    for dx in (-1, 0, 1):
                        x_neighbor = x_index + dx
                        if x_neighbor < 0 or x_neighbor >= columns:
                            continue
                        value = current[y_neighbor][x_neighbor]
                        if value is None or not math.isfinite(float(value)):
                            continue
                        weight = (
                            4.0
                            if dx == 0 and dy == 0
                            else (2.0 if dx == 0 or dy == 0 else 1.0)
                        )
                        total += float(value) * weight
                        weight_total += weight
                output_row.append(total / weight_total if weight_total else None)
            output.append(output_row)
        current = output
    return current


def _response_matrix(
    counts: np.ndarray,
    metadata: dict[str, Any],
    start: int,
    end: int,
    value_mode: str,
) -> list[list[float | None]]:
    if value_mode not in VALUE_MODES:
        raise ExportValidationError(f"Unknown value mode: {value_mode}")
    _n_units, n_y, n_x, _n_bins = metadata["shape"]
    presentations = metadata["presentationCounts"]
    if value_mode != VALUE_MODE_COUNT and presentations is None:
        raise ExportValidationError(
            f"{value_mode} requires stimulusPresentationCounts metadata in the JSON file."
        )
    duration = metadata["timeBinEdges"][end + 1] - metadata["timeBinEdges"][start]
    result: list[list[float | None]] = []
    for y_index in range(n_y):
        row: list[float | None] = []
        for x_index in range(n_x):
            count = float(
                sum(float(value) for value in counts[y_index, x_index, start : end + 1])
            )
            if value_mode == VALUE_MODE_COUNT:
                row.append(count)
                continue
            presentation_count = presentations[y_index][x_index]
            if presentation_count <= 0:
                row.append(None)
            elif value_mode == VALUE_MODE_PER_PRESENTATION:
                row.append(count / presentation_count)
            else:
                row.append(count / (presentation_count * duration))
        result.append(row)
    return result


def _displayed_csv_rows(
    record: DatasetRecord,
    unit_index: int,
    counts: np.ndarray,
    options: DisplayedCsvOptions,
) -> tuple[list[list[Any]], str]:
    metadata = record.cache.metadata
    _n_units, n_y, n_x, n_bins = metadata["shape"]
    for label, value in (
        ("rfStartMs", options.rf_start_ms),
        ("rfEndMs", options.rf_end_ms),
        ("timeResolutionMs", options.time_resolution_ms),
    ):
        if not math.isfinite(value):
            raise ExportValidationError(f"{label} must be finite")
    if options.palette not in PALETTES:
        raise ExportValidationError(f"Unknown palette: {options.palette}")

    edges_ms = [float(edge) * 1000.0 for edge in metadata["timeBinEdges"]]
    source_start, source_end = _snap_time_range(
        edges_ms, options.rf_start_ms, options.rf_end_ms
    )
    range_start_ms = edges_ms[source_start]
    range_end_ms = edges_ms[source_end + 1]
    raw_matrix = _response_matrix(
        counts, metadata, source_start, source_end, options.value_mode
    )

    x_bins = max(1, min(n_x, int(options.x_bins)))
    y_bins = max(1, min(n_y, int(options.y_bins)))
    smooth_radius = max(0, min(3, int(options.smooth_radius)))
    x_groups = _axis_groups(n_x, x_bins)
    y_groups = _axis_groups(n_y, y_bins)
    if options.flip_y:
        y_groups.reverse()
    matrix = _smooth_matrix(
        _reduce_matrix(raw_matrix, y_groups, x_groups), smooth_radius
    )

    display_time_groups, time_group_size, base_bin_ms = _time_groups(
        metadata["timeBinEdges"], n_bins, options.time_resolution_ms
    )
    range_start_group = next(
        (
            index
            for index, (start, end) in enumerate(display_time_groups)
            if start <= source_start <= end
        ),
        0,
    )
    range_end_group = next(
        (
            index
            for index, (start, end) in enumerate(display_time_groups)
            if start <= source_end <= end
        ),
        len(display_time_groups) - 1,
    )
    mode = (
        f"{options.value_mode}: {_format_ms(range_start_ms)} "
        f"to {_format_ms(range_end_ms)} ms"
    )
    presentation_counts = metadata["presentationCounts"]
    rows: list[list[Any]] = []
    for display_y, (y_start, y_end) in enumerate(y_groups):
        for display_x, (x_start, x_end) in enumerate(x_groups):
            grouped_presentations = (
                [
                    presentation_counts[y_index][x_index]
                    for y_index in range(y_start, y_end + 1)
                    for x_index in range(x_start, x_end + 1)
                ]
                if presentation_counts is not None
                else []
            )
            rows.append(
                [
                    unit_index,
                    options.cluster_id,
                    y_start,
                    y_start + 1,
                    (metadata["yPositions"][y_start] + metadata["yPositions"][y_end])
                    / 2.0,
                    x_start,
                    x_start + 1,
                    (metadata["xPositions"][x_start] + metadata["xPositions"][x_end])
                    / 2.0,
                    matrix[display_y][display_x],
                    options.value_mode,
                    _value_mode_unit(options.value_mode),
                    min(grouped_presentations) if grouped_presentations else "",
                    max(grouped_presentations) if grouped_presentations else "",
                    mode,
                    display_y,
                    y_start,
                    y_end,
                    y_start + 1,
                    y_end + 1,
                    metadata["yPositions"][y_start],
                    metadata["yPositions"][y_end],
                    display_x,
                    x_start,
                    x_end,
                    x_start + 1,
                    x_end + 1,
                    metadata["xPositions"][x_start],
                    metadata["xPositions"][x_end],
                    "displayed",
                    _format_ms(time_group_size * base_bin_ms),
                    range_start_group,
                    range_end_group,
                    range_start_ms,
                    range_end_ms,
                    x_bins,
                    y_bins,
                    smooth_radius,
                    options.flip_y,
                    options.palette,
                    record.source,
                ]
            )

    if any(len(row) != len(CSV_HEADERS) for row in rows):
        raise RuntimeError("Displayed CSV schema and row length are inconsistent")

    default_name = (
        f"unit_{unit_index:03d}_cluster_{options.cluster_id}_"
        f"{_value_mode_slug(options.value_mode)}_displayed.csv"
    )
    return rows, default_name


class LinuxExportService:
    """Atomically materialize GUI exports beneath one Linux-only output root."""

    def __init__(self, root: Path):
        self.root = root

    def _target(
        self, requested_path: str | None, default_name: str, extension: str
    ) -> Path:
        self.root.mkdir(mode=0o750, parents=True, exist_ok=True)
        try:
            root = self.root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise OutputPathError(f"Output root is unavailable: {self.root}") from exc
        if not root.is_dir():
            raise OutputPathError(f"Output root is not a directory: {self.root}")

        value = default_name if requested_path is None else requested_path
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise OutputPathError("Output path must be a non-empty relative path")
        relative = PurePosixPath(value.replace("\\", "/"))
        if relative.is_absolute() or any(
            part in {"", ".", ".."} or part.startswith(".")
            for part in relative.parts
        ):
            raise OutputPathError("Output path must stay inside the configured output root")
        if relative.suffix == "":
            relative = relative.with_name(relative.name + extension)
        elif relative.suffix.casefold() != extension:
            raise OutputPathError(f"Output filename must end with {extension}")

        parent = root.joinpath(*relative.parts[:-1])
        try:
            parent.mkdir(mode=0o750, parents=True, exist_ok=True)
            resolved_parent = parent.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise OutputPathError("Unable to create the requested output directory") from exc
        if not resolved_parent.is_dir() or not is_within(resolved_parent, root):
            raise OutputPathError("Output path must stay inside the configured output root")
        return resolved_parent / relative.name

    @staticmethod
    def _atomic_write(
        target: Path,
        overwrite: bool,
        writer: Callable[[Path], None],
    ) -> tuple[bool, int]:
        existed = target.exists() or target.is_symlink()
        if existed and not overwrite:
            raise FileExistsError(f"Output already exists: {target}")
        temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        try:
            writer(temporary)
            if overwrite:
                os.replace(temporary, target)
            else:
                try:
                    os.link(temporary, target)
                except FileExistsError as exc:
                    raise FileExistsError(f"Output already exists: {target}") from exc
                temporary.unlink()
            try:
                descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            except (AttributeError, OSError):
                descriptor = None
            if descriptor is not None:
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return existed, target.stat().st_size
        finally:
            temporary.unlink(missing_ok=True)

    def write_displayed_csv(
        self,
        record: DatasetRecord,
        unit_index: int,
        counts: np.ndarray,
        options: DisplayedCsvOptions,
    ) -> dict[str, Any]:
        rows, default_name = _displayed_csv_rows(
            record, unit_index, counts, options
        )
        target = self._target(options.output_path, default_name, ".csv")

        def write(temporary: Path) -> None:
            with temporary.open("x", encoding="utf-8", newline="") as handle:
                typed_handle: TextIO = handle
                writer = csv.writer(typed_handle)
                writer.writerow(CSV_HEADERS)
                writer.writerows(rows)
                handle.flush()
                os.fsync(handle.fileno())

        overwritten, byte_count = self._atomic_write(
            target, options.overwrite, write
        )
        return {
            "path": str(target),
            "name": target.name,
            "rows": len(rows),
            "bytes": byte_count,
            "overwritten": overwritten,
        }

    def save_png(
        self,
        source: Path,
        *,
        output_path: str | None,
        overwrite: bool,
    ) -> dict[str, Any]:
        target = self._target(output_path, source.name, ".png")

        def write(temporary: Path) -> None:
            with source.open("rb") as input_handle, temporary.open("xb") as output_handle:
                shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
                output_handle.flush()
                os.fsync(output_handle.fileno())

        overwritten, byte_count = self._atomic_write(target, overwrite, write)
        return {
            "path": str(target),
            "name": target.name,
            "bytes": byte_count,
            "overwritten": overwritten,
        }

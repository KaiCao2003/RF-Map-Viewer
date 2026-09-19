"""Read-only RF data and cached display calculations."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable, Sequence
import numpy as np
from rfmapping_viewer.hd_tuning import (
    HDTuningData,
    discover_hd_tuning_path,
    load_hd_tuning,
)
from rfmapping_viewer.rf_archive import IndexedRFMapList, RFCountSequence
from rfmapping_viewer.rf_dataset import (
    RFMap, _count_sum_dtype, is_indexed_rfmap, load_rf_maps,
)
from rfmapping_viewer.rf_loading import load_rf_maps_isolated
from rfmapping_viewer.waveform import (
    WaveformArtifactStore,
    WaveformPayload,
    discover_waveform_artifact,
)

from rfmapping_viewer.companions import (
    ProbeGeometry,
    TuningCurveData,
    discover_probe_geometry_paths,
    discover_tuning_curve_path,
    load_probe_geometry,
    probe_name_for_json,
)
from rfmapping_viewer.constants import (
    AxisGroup,
    RF_COUNT_CACHE_UNIT_LIMIT,
    VALUE_MODES,
    VALUE_MODE_COUNT,
    VALUE_MODE_RATE,
)
from rfmapping_viewer.display import (
    _nullable_array_list,
    _rectangular_group_sums,
    _smooth_matrix_array,
    clone_matrix,
)
from rfmapping_viewer.export_inputs import FrozenFileIdentity


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


class RFMappingData:
    """GUI adapter for JSON and per-unit indexed RF documents."""

    def __init__(
        self,
        path: Path,
        *,
        isolated: bool = False,
        cancelled: Callable[[], bool] | None = None,
    ):
        source_identity = FrozenFileIdentity.capture(path)
        self.path = source_identity.path
        self.unit_archive: IndexedRFMapList | None = None
        if is_indexed_rfmap(self.path):
            self.unit_archive = IndexedRFMapList(self.path)
            self.rf_maps = self.unit_archive
        else:
            self.rf_maps = (
                load_rf_maps_isolated(self.path, cancelled=cancelled)
                if isolated else load_rf_maps(self.path)
            )
        try:
            source_identity.verify_path()
        except Exception:
            self.close()
            raise
        self.source_identity = source_identity
        first = self.rf_maps[0]
        self.n_units = len(self.rf_maps)
        self.n_y = first.n_y
        self.n_x = first.n_x
        self.n_bins = first.n_time_bins
        self.size = (self.n_units, self.n_y, self.n_x, self.n_bins)
        self.counts = (
            RFCountSequence(self.unit_archive) if self.unit_archive is not None
            else [rf_map.spike_counts for rf_map in self.rf_maps]
        )
        self.unit_pool = list(self.rf_maps.unit_ids)
        self.x_positions = first.x_positions.tolist()
        self.y_positions = first.y_positions.tolist()
        self.time_bin_edges = first.time_bin_edges_s.tolist()
        self.occupancy_time_s = first.occupancy_time_s.tolist()
        self._occupancy_array = first.occupancy_time_s
        self._count_prefix_cache: dict[int, np.ndarray] = {}
        self._count_window_cache: dict[
            tuple[int, tuple[AxisGroup, ...]], np.ndarray
        ] = {}
        self._spatial_exposure_cache: dict[
            tuple[tuple[AxisGroup, ...], tuple[AxisGroup, ...]],
            tuple[np.ndarray, np.ndarray],
        ] = {}
        self._count_cache_lock = threading.Lock()
        self._temporal_array_cache: dict[
            tuple[object, ...], tuple[np.ndarray, np.ndarray]
        ] = {}
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

    def close(self) -> None:
        if self.unit_archive is not None:
            self.unit_archive.close()

    def rf_map(self, unit_idx: int) -> RFMap:
        """Return one unit by its original unitPool index."""

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

    def hd_tuning(
        self,
        session_index: int | None = None,
    ) -> HDTuningData | TuningCurveData | None:
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
            tuning_path = (
                discover_hd_tuning_path(self.path)
                if session_index is None
                else discover_tuning_curve_path(self.path, session_index)
            )
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
        self._publish_hd_tuning(identity, tuning)
        return tuning

    def _publish_hd_tuning(
        self,
        identity: FrozenFileIdentity,
        tuning: TuningCurveData,
    ) -> None:
        """Publish a previously parsed, still-identical tuning document."""

        identity.verify_path()
        with self._hd_tuning_lock:
            self._hd_tuning = tuning
            self._hd_tuning_identity = identity
            self._hd_tuning_error = None
            self._hd_tuning_checked = True

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
        source = self.counts[unit_idx]
        totals = source.sum(
            axis=-1, dtype=_count_sum_dtype(source, self.n_bins)
        ).astype(np.float64)
        rates = np.divide(
            totals,
            self._occupancy_array,
            out=np.full(totals.shape, -1.0),
            where=self._occupancy_array > 0.0,
        )
        best_y, best_x = np.unravel_index(np.argmax(rates), rates.shape)
        result = (int(best_y), int(best_x))
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

    def _normalized_time_groups(
        self,
        time_groups: Sequence[AxisGroup],
    ) -> tuple[AxisGroup, ...]:
        return tuple(
            (
                max(0, min(self.n_bins - 1, min(start, end))),
                max(0, min(self.n_bins - 1, max(start, end))),
            )
            for start, end in time_groups
        )

    def _unit_count_prefix(self, unit_idx: int) -> np.ndarray:
        """Return a small per-unit lossless time prefix with LRU retention."""

        unit_idx = int(self.rf_map(unit_idx).unit_index)
        with self._count_cache_lock:
            cached = self._count_prefix_cache.pop(unit_idx, None)
            if cached is not None:
                self._count_prefix_cache[unit_idx] = cached
                return cached

        source = np.asarray(self.counts[unit_idx])
        dtype = _count_sum_dtype(source, self.n_bins)
        prefix = np.zeros(
            (self.n_y, self.n_x, self.n_bins + 1),
            dtype=dtype,
        )
        np.cumsum(source, axis=-1, dtype=dtype, out=prefix[..., 1:])
        prefix.setflags(write=False)
        with self._count_cache_lock:
            existing = self._count_prefix_cache.pop(unit_idx, None)
            if existing is not None:
                prefix = existing
            self._count_prefix_cache[unit_idx] = prefix
            while len(self._count_prefix_cache) > RF_COUNT_CACHE_UNIT_LIMIT:
                oldest = next(iter(self._count_prefix_cache))
                self._count_prefix_cache.pop(oldest)
        return prefix

    def _spatial_group_exposure_arrays(
        self,
        y_groups: Sequence[AxisGroup],
        x_groups: Sequence[AxisGroup],
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return pooled occupancy and valid-pixel counts for one layout.

        There are few spatial groups compared with timeline frames, so this
        one-time calculation retains the original row-major Python summation
        order. That keeps exported firing-rate values reproducible down to the
        last floating-point bit while the large count workload stays batched.
        """

        normalized_y = tuple(
            (
                max(0, min(self.n_y - 1, min(group))),
                max(0, min(self.n_y - 1, max(group))),
            )
            for group in y_groups
        )
        normalized_x = tuple(
            (
                max(0, min(self.n_x - 1, min(group))),
                max(0, min(self.n_x - 1, max(group))),
            )
            for group in x_groups
        )
        key = (normalized_y, normalized_x)
        with self._count_cache_lock:
            cached = self._spatial_exposure_cache.pop(key, None)
            if cached is not None:
                self._spatial_exposure_cache[key] = cached
                return cached

        occupancy = np.zeros(
            (len(normalized_y), len(normalized_x)),
            dtype=np.float64,
        )
        source_pixel_counts = np.zeros_like(occupancy)
        for y_group_index, (y_start, y_end) in enumerate(normalized_y):
            for x_group_index, (x_start, x_end) in enumerate(normalized_x):
                positive = [
                    float(self.occupancy_time_s[y_index][x_index])
                    for y_index in range(y_start, y_end + 1)
                    for x_index in range(x_start, x_end + 1)
                    if self.occupancy_time_s[y_index][x_index] > 0.0
                ]
                occupancy[y_group_index, x_group_index] = sum(positive)
                source_pixel_counts[y_group_index, x_group_index] = len(positive)
        occupancy.setflags(write=False)
        source_pixel_counts.setflags(write=False)
        result = (occupancy, source_pixel_counts)
        with self._count_cache_lock:
            existing = self._spatial_exposure_cache.pop(key, None)
            if existing is not None:
                result = existing
            self._spatial_exposure_cache[key] = result
            while len(self._spatial_exposure_cache) > 8:
                oldest = next(iter(self._spatial_exposure_cache))
                self._spatial_exposure_cache.pop(oldest)
        return result

    def count_windows_array(
        self,
        unit_idx: int,
        time_groups: Sequence[AxisGroup],
    ) -> np.ndarray:
        """Return inclusive time-window counts as ``(window, y, x)``."""

        unit_idx = int(self.rf_map(unit_idx).unit_index)
        groups = self._normalized_time_groups(time_groups)
        if not groups:
            empty = np.empty((0, self.n_y, self.n_x), dtype=np.uint64)
            empty.setflags(write=False)
            return empty
        key = (unit_idx, groups)
        with self._count_cache_lock:
            cached = self._count_window_cache.pop(key, None)
            if cached is not None:
                self._count_window_cache[key] = cached
                return cached

        if len(groups) == 1:
            start, end = groups[0]
            source = self.counts[unit_idx][..., start : end + 1]
            windows = np.sum(
                source,
                axis=-1,
                dtype=_count_sum_dtype(source, end - start + 1),
            )[None, ...]
        else:
            prefix = self._unit_count_prefix(unit_idx)
            starts = np.fromiter(
                (start for start, _end in groups),
                dtype=np.intp,
                count=len(groups),
            )
            stops = np.fromiter(
                (end + 1 for _start, end in groups),
                dtype=np.intp,
                count=len(groups),
            )
            windows = np.moveaxis(
                prefix[..., stops] - prefix[..., starts],
                -1,
                0,
            )
        windows.setflags(write=False)
        with self._count_cache_lock:
            existing = self._count_window_cache.pop(key, None)
            if existing is not None:
                windows = existing
            self._count_window_cache[key] = windows
            while len(self._count_window_cache) > RF_COUNT_CACHE_UNIT_LIMIT:
                oldest = next(iter(self._count_window_cache))
                self._count_window_cache.pop(oldest)
        return windows

    def spatial_group_response_frames(
        self,
        unit_idx: int,
        time_groups: Sequence[AxisGroup],
        value_mode: str,
        y_groups: Sequence[AxisGroup],
        x_groups: Sequence[AxisGroup],
        *,
        smooth_radius: int = 0,
    ) -> np.ndarray:
        """Pool and normalize all requested timeline frames in one array pass."""

        if value_mode not in VALUE_MODES:
            raise ValueError(f"Unknown value mode: {value_mode}")
        window_counts = self.count_windows_array(unit_idx, time_groups)
        grouped_counts = _rectangular_group_sums(
            window_counts,
            y_groups,
            x_groups,
        )
        grouped_occupancy, source_pixel_counts = (
            self._spatial_group_exposure_arrays(y_groups, x_groups)
        )
        valid = source_pixel_counts > 0.0

        if value_mode == VALUE_MODE_COUNT:
            response = np.divide(
                grouped_counts,
                source_pixel_counts,
                out=np.full_like(grouped_counts, np.nan),
                where=valid,
            )
            return _smooth_matrix_array(response, smooth_radius)

        counts_for_smoothing = np.where(valid, grouped_counts, np.nan)
        occupancy_for_smoothing = np.where(valid, grouped_occupancy, np.nan)
        counts_for_smoothing = _smooth_matrix_array(
            counts_for_smoothing,
            smooth_radius,
        )
        occupancy_for_smoothing = _smooth_matrix_array(
            occupancy_for_smoothing,
            smooth_radius,
        )
        return np.divide(
            counts_for_smoothing,
            occupancy_for_smoothing,
            out=np.full_like(counts_for_smoothing, np.nan),
            where=valid & (occupancy_for_smoothing > 0.0),
        )

    def spatial_group_histograms_array(
        self,
        unit_idx: int,
        y_groups: Sequence[AxisGroup],
        x_groups: Sequence[AxisGroup],
        *,
        smooth_radius: int = 0,
    ) -> np.ndarray:
        """Return mean count histograms as ``(y_group, x_group, time)``."""

        counts_by_time = np.moveaxis(
            np.asarray(self.counts[int(self.rf_map(unit_idx).unit_index)]),
            -1,
            0,
        )
        grouped = np.moveaxis(
            _rectangular_group_sums(counts_by_time, y_groups, x_groups),
            0,
            -1,
        )
        _grouped_occupancy, source_pixel_counts = (
            self._spatial_group_exposure_arrays(y_groups, x_groups)
        )
        grouped = np.divide(
            grouped,
            source_pixel_counts[..., None],
            out=np.full_like(grouped, np.nan),
            where=source_pixel_counts[..., None] > 0.0,
        )
        if smooth_radius > 0:
            grouped = np.moveaxis(
                _smooth_matrix_array(
                    np.moveaxis(grouped, -1, 0),
                    smooth_radius,
                ),
                0,
                -1,
            )
        return grouped

    def spatial_group_temporal_arrays(
        self,
        unit_idx: int,
        y_groups: Sequence[AxisGroup],
        x_groups: Sequence[AxisGroup],
        time_groups: Sequence[AxisGroup],
        *,
        smooth_radius: int = 0,
        count_floor: float = 0.0,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Derive delay and entropy for every grouped spatial cell in bulk."""

        groups = self._normalized_time_groups(time_groups)
        key = (
            int(unit_idx), tuple(y_groups), tuple(x_groups), groups,
            int(smooth_radius), float(count_floor),
        )
        with self._count_cache_lock:
            cached = self._temporal_array_cache.pop(key, None)
            if cached is not None:
                self._temporal_array_cache[key] = cached
                return cached
        histograms = self.spatial_group_histograms_array(
            unit_idx,
            y_groups,
            x_groups,
            smooth_radius=smooth_radius,
        )
        shape = histograms.shape[:-1]
        delay = np.full(shape, np.nan, dtype=np.float64)
        entropy = np.full(shape, np.nan, dtype=np.float64)
        # Occupancy marks every time bin at a source position unavailable.
        available = np.isfinite(histograms[..., 0])
        # Preserve direct window summation: subtracting floating prefixes can
        # turn equal peaks into different values and select a later interval.
        for index in np.ndindex(shape):
            if not available[index]:
                continue
            metrics = self.temporal_metrics_from_histogram(
                histograms[index].tolist(), groups,
            )
            if metrics.delay_ms is not None and metrics.mean_total_count > count_floor:
                delay[index] = metrics.delay_ms
            entropy[index] = metrics.entropy
        delay.setflags(write=False)
        entropy.setflags(write=False)
        with self._count_cache_lock:
            result = self._temporal_array_cache.setdefault(key, (delay, entropy))
            while len(self._temporal_array_cache) > RF_COUNT_CACHE_UNIT_LIMIT:
                self._temporal_array_cache.pop(next(iter(self._temporal_array_cache)))
        return result

    def all_positions_timeline_values(
        self,
        unit_idx: int,
        time_groups: Sequence[AxisGroup],
        value_mode: str,
    ) -> list[float]:
        if value_mode not in VALUE_MODES:
            raise ValueError(f"Unknown value mode: {value_mode}")
        windows = self.count_windows_array(unit_idx, time_groups)
        totals = windows.sum(
            axis=(1, 2),
            dtype=_count_sum_dtype(windows, self.n_y * self.n_x),
        ).astype(np.float64)
        if value_mode == VALUE_MODE_RATE:
            occupancy_total = sum(
                float(duration)
                for row in self.occupancy_time_s
                for duration in row
                if duration > 0.0
            )
            if occupancy_total <= 0.0:
                return [0.0 for _group in time_groups]
            totals /= occupancy_total
        return totals.tolist()

    def spatial_group_response_values(
        self,
        unit_idx: int,
        y_group: AxisGroup,
        x_group: AxisGroup,
        time_groups: Sequence[AxisGroup],
        value_mode: str,
    ) -> list[float | None]:
        if value_mode not in VALUE_MODES:
            raise ValueError(f"Unknown value mode: {value_mode}")
        groups = self._normalized_time_groups(time_groups)
        if not groups:
            return []
        occupancy, pixels = self._spatial_group_exposure_arrays([y_group], [x_group])
        denominator = float(
            pixels[0, 0] if value_mode == VALUE_MODE_COUNT else occupancy[0, 0]
        )
        if denominator <= 0:
            return [None] * len(groups)
        # Inspector and hover queries need only the selected source rectangle.
        # Building an integral image for every spatial cell here scales poorly
        # even when the unit's full-frame count cache is already warm.
        source = self._spatial_group_slice(unit_idx, y_group, x_group)
        starts = np.asarray([start for start, _end in groups], dtype=np.intp)
        stops = np.asarray([end + 1 for _start, end in groups], dtype=np.intp)
        if np.all(stops == starts + 1):
            windows = source[..., starts]
        else:
            dtype = _count_sum_dtype(source, self.n_bins)
            prefix = np.empty(source.shape[:-1] + (self.n_bins + 1,), dtype=dtype)
            prefix[..., 0] = 0
            np.cumsum(source, axis=-1, dtype=dtype, out=prefix[..., 1:])
            windows = prefix[..., stops] - prefix[..., starts]
        values = windows.sum(axis=(0, 1), dtype=np.float64) / denominator
        return values.tolist()

    def _spatial_group_slice(
        self, unit_idx: int, y_group: AxisGroup, x_group: AxisGroup
    ) -> np.ndarray:
        y_start = max(0, min(self.n_y - 1, min(y_group)))
        y_end = max(0, min(self.n_y - 1, max(y_group)))
        x_start = max(0, min(self.n_x - 1, min(x_group)))
        x_end = max(0, min(self.n_x - 1, max(x_group)))
        return self.rf_map(unit_idx).spike_counts[
            y_start : y_end + 1, x_start : x_end + 1
        ]

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
        source = self.rf_map(unit_idx).spike_counts[y_idx, x_idx, start : end + 1]
        count = float(source.sum(dtype=_count_sum_dtype(source, end - start + 1)))
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
        if value_mode not in VALUE_MODES:
            raise ValueError(f"Unknown value mode: {value_mode}")
        counts = self.count_windows_array(unit_idx, [(start, end)])[0].astype(
            np.float64,
            copy=False,
        )
        valid = self._occupancy_array > 0.0
        if value_mode == VALUE_MODE_COUNT:
            values = np.where(valid, counts, np.nan)
        else:
            values = np.divide(
                counts,
                self._occupancy_array,
                out=np.full_like(counts, np.nan),
                where=valid,
            )
        return _nullable_array_list(values)

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

        start, end = self._normalized_time_groups([(start, end)])[0]
        source = self._spatial_group_slice(unit_idx, y_group, x_group)[..., start : end + 1]
        grouped_count = source.sum(
            axis=-1, dtype=_count_sum_dtype(source, end - start + 1)
        ).sum(dtype=np.float64)
        grouped_occupancy, source_pixel_counts = (
            self._spatial_group_exposure_arrays([y_group], [x_group])
        )
        return SpatialGroupObservations(
            count=float(grouped_count),
            occupancy_time_s=float(grouped_occupancy[0, 0]),
            source_pixel_count=int(source_pixel_counts[0, 0]),
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
        values = self.spatial_group_response_frames(
            unit_idx,
            [(start, end)],
            value_mode,
            y_groups,
            x_groups,
        )[0]
        return _nullable_array_list(values)

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
        source = self.counts[unit_idx][y_start : y_end + 1, x_start : x_end + 1]
        histogram = np.sum(
            source,
            axis=(0, 1),
            dtype=_count_sum_dtype(source, source.shape[0] * source.shape[1]),
        )
        return histogram.astype(np.float64).tolist()

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
        return int(
            np.count_nonzero(
                self._occupancy_array[
                    y_start : y_end + 1,
                    x_start : x_end + 1,
                ]
                > 0.0
            )
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

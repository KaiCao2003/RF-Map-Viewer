import builtins
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any, Literal

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.colorbar import Colorbar
from matplotlib.collections import PolyCollection, QuadMesh
from matplotlib.figure import Figure
from matplotlib.legend import Legend
from numpy.typing import NDArray
from probeinterface import Probe
from probeinterface.plotting import plot_probe

from Utils.waveform_artifacts import WaveformArtifactStore, WaveformUnitArtifact


type FloatArray = NDArray[np.floating[Any]]
type IntArray = NDArray[np.integer[Any]]
type AxesArray = NDArray[np.object_]


@dataclass(frozen=True)
class PlotResult:
    figure: Figure | None
    plotted_unit_ids: list[int]
    message: str


class _WaveformUnitPlotSelection:
    """Bind collection plotting methods to one unit ID."""

    def __init__(self, collection: 'WaveformUnitPlotCollection', unit_id: int):
        self._collection = collection
        self._unit_id = unit_id

    def __getattr__(self, name: str) -> Any:
        if not name.startswith('plot_') or name == 'plot_unit_locations':
            raise AttributeError(name)
        plot_method = getattr(self._collection, name, None)
        if plot_method is None:
            raise AttributeError(name)

        def plot_selected(*args: Any, **kwargs: Any) -> PlotResult:
            return plot_method(self._unit_id, *args, **kwargs)

        return plot_selected


class _WaveformUnitPlotList:
    """Apply a single-unit plotting method to every requested unit."""

    def __init__(self, collection: 'WaveformUnitPlotCollection'):
        self._collection = collection

    def __getattr__(self, name: str) -> Any:
        if not name.startswith('plot_') or name == 'plot_unit_locations':
            raise AttributeError(name)
        plot_method = getattr(WaveformUnitPlotCollection, name, None)
        if plot_method is None:
            raise AttributeError(name)

        def plot_each(*args: Any, **kwargs: Any) -> list[PlotResult]:
            return [
                getattr(self._collection, name)(unit_id, *args, **kwargs)
                for unit_id in self._collection.requested_unit_ids
            ]

        return plot_each


class WaveformUnitPlot:
    def __init__(
            self,
            artifact: WaveformUnitArtifact,
            store: WaveformArtifactStore,
            color: str,
            heatmap_baseline_end_ms: float,
    ):
        self.artifact = artifact
        self.store = store
        self.summary = artifact.summary
        self.unit_id = artifact.summary.unit_id
        self.color = color

        heatmap_baseline_mask = store.time_ms <= heatmap_baseline_end_ms
        if not np.any(heatmap_baseline_mask):
            raise ValueError(f'Unit {self.unit_id} has no samples in the heatmap baseline window.')
        heatmap_baseline_uv = np.mean(
            artifact.template_uv[heatmap_baseline_mask],
            axis=0,
            keepdims=True,
        )
        self.heatmap_template_uv: FloatArray = artifact.template_uv - heatmap_baseline_uv
        self.best_channel_waveform_uv: FloatArray = artifact.template_uv[
            :,
            artifact.summary.best_channel_index,
        ]

    def spike_minutes(self, *, selected: bool) -> FloatArray:
        samples = (
            self.artifact.selected_spike_samples
            if selected
            else self.artifact.all_spike_samples
        )
        sampling_frequency = self.store.manifest['recording']['sampling_frequency_hz']
        return samples / sampling_frequency / 60.0

    def select_local_channel_indices(
            self,
            local_channel_mode: Literal['same_shank', 'same_x_column'],
            local_channel_count: int,
    ) -> IntArray:
        channel_locations = self.store.channel_locations
        channel_shank_ids = self.store.channel_shank_ids
        best_channel_index = self.summary.best_channel_index
        distances = np.linalg.norm(
            channel_locations - channel_locations[best_channel_index],
            axis=1,
        )
        if local_channel_mode == 'same_shank':
            best_shank_id = channel_shank_ids[best_channel_index]
            candidate_indices = np.flatnonzero(channel_shank_ids == best_shank_id)
        else:
            best_x_um = channel_locations[best_channel_index, 0]
            candidate_indices = np.flatnonzero(np.isclose(
                channel_locations[:, 0],
                best_x_um,
                rtol=0.0,
                atol=1e-6,
            ))

        if len(candidate_indices) < local_channel_count:
            raise ValueError(
                f'Unit {self.unit_id} has only {len(candidate_indices)} channels available '
                f'for {local_channel_mode}; requested {local_channel_count}.'
            )
        neighbor_count = local_channel_count - 1
        neighbor_candidates = candidate_indices[candidate_indices != best_channel_index]
        candidate_order = np.argsort(distances[neighbor_candidates], kind='stable')
        nearest_indices = np.r_[
            best_channel_index,
            neighbor_candidates[candidate_order[:neighbor_count]],
        ].astype(int)
        local_order = np.lexsort((
            channel_locations[nearest_indices, 0],
            -channel_locations[nearest_indices, 1],
        ))
        return nearest_indices[local_order]

    def draw_local_average_heatmap(
            self,
            ax: Axes,
            *,
            local_channel_indices: IntArray,
            time_edges_ms: FloatArray,
            template_limit_uv: float,
    ) -> QuadMesh:
        local_indices = local_channel_indices
        local_template = self.heatmap_template_uv[:, local_indices].T
        row_edges = np.arange(len(local_indices) + 1, dtype=float) - 0.5
        mesh = ax.pcolormesh(
            time_edges_ms,
            row_edges,
            local_template,
            shading='flat',
            cmap='RdBu_r',
            vmin=-template_limit_uv,
            vmax=template_limit_uv,
            antialiased=False,
            edgecolors='none',
            rasterized=True,
        )

        best_channel_index = self.summary.best_channel_index
        best_row = int(np.flatnonzero(local_indices == best_channel_index)[0])
        for local_number, channel_index_value in enumerate(local_indices):
            channel_index = int(channel_index_value)
            is_best_channel = channel_index == best_channel_index
            ax.scatter(
                -0.028,
                local_number,
                transform=ax.get_yaxis_transform(),
                marker='o',
                s=42 if is_best_channel else 25,
                facecolors=self.color if is_best_channel else 'white',
                edgecolors=self.color if is_best_channel else '#667085',
                linewidths=1.15 if is_best_channel else 0.85,
                clip_on=False,
                zorder=4,
            )
        ax.axvline(0, color='#172033', linestyle='--', linewidth=0.8, alpha=0.65)
        channel_labels = [
            f'ch {int(self.store.channel_ids[int(channel_index)])}'
            for channel_index in local_indices
        ]
        ax.set_yticks(np.arange(len(local_indices)), channel_labels, fontsize=8)
        ax.tick_params(axis='y', pad=15)
        ax.get_yticklabels()[best_row].set_color(self.color)
        ax.get_yticklabels()[best_row].set_fontweight('bold')
        ax.set_xlim(time_edges_ms[0], time_edges_ms[-1])
        ax.set_ylim(len(local_indices) - 0.5, -0.5)
        best_shank_id = int(self.store.channel_shank_ids[best_channel_index])
        ax.set_title(
            f'Shank{best_shank_id}, Unit{self.unit_id}',
            color=self.color,
            fontsize=10,
            fontweight='bold',
        )
        ax.set_xlabel('Time from spike alignment (ms)')
        return mesh

    def draw_best_channel_average(self, ax: Axes, time_ms: FloatArray) -> None:
        waveform_limit_uv = max(
            float(np.max(np.abs(self.best_channel_waveform_uv))),
            np.finfo(float).eps,
        )
        ax.plot(time_ms, self.best_channel_waveform_uv, color=self.color, linewidth=1.6)
        ax.axvline(0, color='#667085', linestyle='--', linewidth=0.8)
        ax.axhline(0, color='#CBD5E1', linewidth=0.8)
        ax.set_ylim(-1.08 * waveform_limit_uv, 1.08 * waveform_limit_uv)
        ax.set_title(
            (
                f'Unit {self.unit_id}\nch {self.summary.best_channel_id} · '
                f'({self.summary.best_channel_x_um:.0f}, {self.summary.best_channel_y_um:.0f}) µm '
                f'· PTP {self.summary.max_ptp_uv:.1f} µV'
            ),
            loc='left',
            color=self.color,
            fontweight='bold',
        )
        ax.set_xlabel('Time from spike alignment (ms)')
        ax.set_ylabel('Mean raw amplitude (µV)')

    def draw_ptp_gradient(
            self,
            ax: Axes,
            probe: Probe,
            *,
            probe_ptp_scale: Literal['per_unit', 'global_uv'],
            global_ptp_max_uv: float,
    ) -> PolyCollection:
        if probe_ptp_scale == 'per_unit':
            unit_ptp_max_uv = max(self.summary.max_ptp_uv, np.finfo(float).eps)
            contact_values = self.artifact.ptp_by_channel_uv / unit_ptp_max_uv
            color_limit = 1.0
        else:
            contact_values = self.artifact.ptp_by_channel_uv
            color_limit = global_ptp_max_uv
        contact_poly, _ = plot_probe(
            probe,
            ax=ax,
            title=False,
            contacts_values=contact_values,
            cmap='Blues',
            contact_kwargs={'alpha': 1.0, 'edgecolor': '#CBD5E1', 'lw': 0.25},
            probe_shape_kwargs={
                'facecolor': '#F8FAFC',
                'edgecolor': '#CBD5E1',
                'lw': 0.8,
            },
        )
        contact_poly.set_clim(0, color_limit)
        ax.set_title(
            (
                f'Unit {self.unit_id} · best ch {self.summary.best_channel_id}'
                f'\nmax {self.summary.max_ptp_uv:.1f} µV'
            ),
            loc='left',
            color=self.color,
            fontweight='bold',
        )
        return contact_poly

    def draw_max_ptp_contact(self, ax: Axes) -> None:
        ax.scatter(
            self.summary.best_channel_x_um,
            self.summary.best_channel_y_um,
            marker='o',
            s=92,
            color=self.color,
            edgecolors='white',
            linewidths=1.1,
            label=f'unit {self.unit_id} · ch {self.summary.best_channel_id}',
            zorder=4,
        )

    def draw_unit_location(self, ax: Axes) -> None:
        ax.scatter(
            self.summary.unit_x_um,
            self.summary.unit_y_um,
            s=84,
            color=self.color,
            edgecolors='white',
            linewidths=1.0,
            label=f'unit {self.unit_id}',
            zorder=4,
        )
        ax.annotate(
            str(self.unit_id),
            (self.summary.unit_x_um, self.summary.unit_y_um),
            xytext=(6, 4),
            textcoords='offset points',
            color=self.color,
            fontsize=8,
            fontweight='bold',
            zorder=5,
        )


class WaveformUnitPlotCollection:
    plot_colors: dict[str, str] = {
        'ink': '#172033',
        'muted': '#667085',
        'border': '#CBD5E1',
        'grid': '#E2E8F0',
        'contact': '#D8DEE8',
        'probe': '#F8FAFC',
    }
    unit_colors: tuple[str, ...] = (
        '#2563EB',
        '#D97706',
        '#0F766E',
        '#7C3AED',
        '#BE123C',
        '#4F46E5',
    )

    plt.style.use('default')
    plt.rcParams.update({
        'figure.facecolor': 'white',
        'axes.facecolor': 'white',
        'savefig.facecolor': 'white',
        'savefig.transparent': False,
        'text.color': plot_colors['ink'],
        'axes.labelcolor': plot_colors['ink'],
        'axes.edgecolor': plot_colors['border'],
        'axes.titlecolor': plot_colors['ink'],
        'xtick.color': plot_colors['muted'],
        'ytick.color': plot_colors['muted'],
        'grid.color': plot_colors['grid'],
        'grid.linewidth': 0.7,
        'grid.alpha': 0.8,
        'axes.spines.top': False,
        'axes.spines.right': False,
        'image.interpolation': 'none',
        'path.simplify': False,
        'font.size': 10,
        'axes.titlesize': 12,
        'axes.labelsize': 10,
        'figure.dpi': 110,
        'savefig.dpi': 200,
    })

    def __init__(
            self,
            analysis_dir: str | Path,
            unit_ids: builtins.list[int],
            *,
            heatmap_baseline_end_ms: float = -0.25,
            probe_ptp_scale: Literal['per_unit', 'global_uv'] = 'per_unit',
            probe_contact_radius_um: float = 6.0,
            save_figures: bool = False,
            output_dir: PathLike | None = None,
    ):
        if not isinstance(unit_ids, list) or not all(
                type(unit_id) is int
                for unit_id in unit_ids
        ):
            raise TypeError('unit_ids must be a list[int].')
        if not unit_ids:
            raise ValueError('unit_ids must contain at least one unit ID.')
        if len(set(unit_ids)) != len(unit_ids):
            raise ValueError('unit_ids must not contain duplicate unit IDs.')

        self.store = WaveformArtifactStore(analysis_dir)
        self.requested_unit_ids = unit_ids.copy()
        self.probe_ptp_scale = probe_ptp_scale
        self.save_figures = save_figures
        self.output_dir = Path(output_dir) if output_dir is not None else Path(analysis_dir) / 'figures'

        self.probe = Probe(ndim=2, si_units='um')
        self.probe.set_contacts(
            self.store.channel_locations,
            shapes='circle',
            shape_params={'radius': probe_contact_radius_um},
        )
        self.probe.set_device_channel_indices(np.arange(len(self.store.channel_ids)))

        self.units: list[WaveformUnitPlot | None] = []
        self.load_messages: list[str] = []
        for unit_number, unit_id in enumerate(self.requested_unit_ids):
            try:
                artifact = self.store.load_unit(unit_id)
                unit = WaveformUnitPlot(
                    artifact,
                    self.store,
                    self.unit_colors[unit_number % len(self.unit_colors)],
                    heatmap_baseline_end_ms,
                )
            except (KeyError, FileNotFoundError, OSError, ValueError) as error:
                self.units.append(None)
                message = error.args[0] if error.args else str(error)
                self.load_messages.append(str(message))
            else:
                self.units.append(unit)
                self.load_messages.append(f'Loaded unit {unit_id}.')

        self.load_success: NDArray[np.bool_] = np.asarray([
            unit is not None
            for unit in self.units
        ])
        self.loaded_units: list[WaveformUnitPlot] = [
            unit
            for unit in self.units
            if unit is not None
        ]

    @property
    def list(self) -> _WaveformUnitPlotList:
        """Plot every requested unit separately instead of combining panels."""
        return _WaveformUnitPlotList(self)

    def get(self, unit_id: int) -> _WaveformUnitPlotSelection:
        """Select one requested unit for chained plotting calls."""
        if type(unit_id) is not int:
            raise TypeError('unit_id must be an int.')
        if unit_id not in self.requested_unit_ids:
            raise KeyError(
                f'Unit {unit_id} was not included when this plot collection was created.'
            )
        return _WaveformUnitPlotSelection(self, unit_id)

    def _single_unit_view(self, unit_id: int) -> 'WaveformUnitPlotCollection':
        if type(unit_id) is not int:
            raise TypeError('unit_id must be an int.')
        try:
            unit_index = self.requested_unit_ids.index(unit_id)
        except ValueError as error:
            raise KeyError(
                f'Unit {unit_id} was not included when this plot collection was created.'
            ) from error

        view = object.__new__(type(self))
        view.__dict__ = self.__dict__.copy()
        view.requested_unit_ids = [unit_id]
        view.units = [self.units[unit_index]]
        view.load_messages = [self.load_messages[unit_index]]
        view.load_success = self.load_success[[unit_index]].copy()
        view.loaded_units = [
            unit
            for unit in view.units
            if unit is not None
        ]
        view._single_unit_filename_id = unit_id
        return view

    def plot_waveform_spike_selection_times(self, unit_id: int) -> PlotResult:
        return self._single_unit_view(unit_id)._plot_waveform_spike_selection_times()

    def select_local_channel_indices(
            self,
            unit_id: int,
            *,
            local_channel_mode: Literal['same_shank', 'same_x_column'],
            local_channel_count: int = 5,
    ) -> IntArray:
        """Select nearby channels and return their indices from probe top to bottom."""
        if local_channel_mode not in ('same_shank', 'same_x_column'):
            raise ValueError(
                "local_channel_mode must be 'same_shank' or 'same_x_column'."
            )
        if type(local_channel_count) is not int:
            raise TypeError('local_channel_count must be an int.')
        if local_channel_count < 1:
            raise ValueError('local_channel_count must be at least 1.')

        view = self._single_unit_view(unit_id)
        if not view.loaded_units:
            raise ValueError(view.load_messages[0])
        return view.loaded_units[0].select_local_channel_indices(
            local_channel_mode,
            local_channel_count,
        )

    def plot_local_average_heatmaps(
            self,
            unit_id: int,
            *,
            local_channel_mode: Literal['same_shank', 'same_x_column'],
            local_channel_count: int = 5,
    ) -> PlotResult:
        return self._single_unit_view(unit_id)._plot_local_average_heatmaps(
            local_channel_mode=local_channel_mode,
            local_channel_count=local_channel_count,
        )

    def plot_best_channel_averages(self, unit_id: int) -> PlotResult:
        return self._single_unit_view(unit_id)._plot_best_channel_averages()

    def plot_ptp_gradients(self, unit_id: int) -> PlotResult:
        return self._single_unit_view(unit_id)._plot_ptp_gradients()

    def plot_max_ptp_contacts(
            self,
            unit_id: int,
            *,
            layout: Literal['panels', 'combined'] = 'panels',
            show_other_units: bool = False,
    ) -> PlotResult:
        return self._single_unit_view(unit_id)._plot_max_ptp_contacts(
            layout=layout,
            show_other_units=show_other_units,
        )

    def print_load_summary(self) -> None:
        for unit_id, success, message in zip(
                self.requested_unit_ids,
                self.load_success,
                self.load_messages,
        ):
            status = 'ok' if success else 'skip'
            print(f'Unit {unit_id}: {status} · {message}')

    @staticmethod
    def _make_panel_grid(
            panel_count: int,
            panel_width: float,
            panel_height: float,
            max_columns: int = 3,
            sharex: bool = False,
            sharey: bool = False,
    ) -> tuple[Figure, AxesArray]:
        column_count = min(max_columns, panel_count)
        row_count = (panel_count + column_count - 1) // column_count
        figure, axes = plt.subplots(
            row_count,
            column_count,
            figsize=(panel_width * column_count, panel_height * row_count),
            squeeze=False,
            sharex=sharex,
            sharey=sharey,
            constrained_layout=True,
        )
        return figure, np.asarray(axes, dtype=object)

    @staticmethod
    def _hide_unused_axes(axes: AxesArray, used_count: int) -> None:
        for unused_ax in axes.ravel()[used_count:]:
            unused_ax.set_visible(False)

    def _style_axes(self, ax: Axes, grid: bool = False) -> None:
        ax.set_facecolor('white')
        ax.tick_params(colors=self.plot_colors['muted'], labelsize=9)
        for spine in ax.spines.values():
            spine.set_color(self.plot_colors['border'])
        ax.grid(grid)
        ax.set_axisbelow(True)

    def _plot_probe_background(self, ax: Axes, *, contact_alpha: float) -> None:
        plot_probe(
            self.probe,
            ax=ax,
            title=False,
            contacts_colors=self.plot_colors['contact'],
            contact_kwargs={
                'alpha': contact_alpha,
                'edgecolor': self.plot_colors['border'],
                'lw': 0.25,
            },
            probe_shape_kwargs={
                'facecolor': self.plot_colors['probe'],
                'edgecolor': self.plot_colors['border'],
                'lw': 0.8,
            },
        )

    def _style_probe_axes(self, ax: Axes, *, show_y_label: bool = True) -> None:
        self._style_axes(ax)
        ax.set_xlabel('Probe x (µm)')
        ax.set_ylabel('Probe y (µm)' if show_y_label else '')

    def _result_message(self) -> str:
        skipped = [
            f'{unit_id}: {message}'
            for unit_id, success, message in zip(
                self.requested_unit_ids,
                self.load_success,
                self.load_messages,
            )
            if not success
        ]
        message = f'Plotted {len(self.loaded_units)} unit(s).'
        if skipped:
            message += ' Skipped ' + '; '.join(skipped)
        return message

    def _finish(self, figure: Figure, filename: str) -> PlotResult:
        if self.save_figures:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            unit_id = getattr(self, '_single_unit_filename_id', None)
            if unit_id is not None:
                path = Path(filename)
                filename = f'{path.stem}_unit_{unit_id}{path.suffix}'
            figure.savefig(self.output_dir / filename, dpi=200, bbox_inches='tight')
        plt.show()
        return PlotResult(
            figure,
            [unit.unit_id for unit in self.loaded_units],
            self._result_message(),
        )

    def _no_units(self) -> PlotResult | None:
        if self.loaded_units:
            return None
        message = self._result_message()
        print(message)
        return PlotResult(None, [], message)

    def _plot_waveform_spike_selection_times(self) -> PlotResult:
        if (empty_result := self._no_units()) is not None:
            return empty_result

        print(f"{'unit':>6} {'selected':>10} {'total':>10} {'time coverage':>15}")
        print('-' * 47)
        figure, ax = plt.subplots(
            figsize=(12.0, 2.2 + 1.15 * len(self.loaded_units)),
            constrained_layout=True,
        )
        selection_labels: list[str] = []
        for unit_number, unit in enumerate(self.loaded_units):
            all_spike_minutes = unit.spike_minutes(selected=False)
            selected_spike_minutes = unit.spike_minutes(selected=True)
            summary = unit.summary
            print(
                f'{unit.unit_id:>6} {summary.selected_spike_count:>10,} '
                f'{summary.total_spike_count:>10,} {summary.time_coverage_percent:14.1f}%'
            )
            ax.scatter(
                all_spike_minutes,
                np.full(len(all_spike_minutes), unit_number, dtype=float),
                marker='|',
                s=14,
                color=self.plot_colors['border'],
                alpha=0.10,
                linewidths=0.45,
                rasterized=True,
            )
            ax.scatter(
                selected_spike_minutes,
                np.full(len(selected_spike_minutes), unit_number, dtype=float),
                marker='|',
                s=24,
                color=unit.color,
                alpha=0.72,
                linewidths=0.75,
                rasterized=True,
            )
            selection_labels.append(
                f'unit {unit.unit_id} · {summary.selected_spike_count:,} / {summary.total_spike_count:,}'
            )

        duration_minutes = self.store.manifest['recording']['duration_minutes']
        ax.set_xlim(0, duration_minutes)
        ax.set_ylim(len(self.loaded_units) - 0.35, -0.65)
        ax.set_yticks(np.arange(len(self.loaded_units)), selection_labels)
        ax.set_xlabel('Recording time (min)')
        ax.set_ylabel('Unit · selected / total spikes')
        self._style_axes(ax, grid=True)
        return self._finish(figure, 'waveform_spike_selection_times.png')

    def _plot_local_average_heatmaps(
            self,
            *,
            local_channel_mode: Literal['same_shank', 'same_x_column'],
            local_channel_count: int,
    ) -> PlotResult:
        if (empty_result := self._no_units()) is not None:
            return empty_result

        figure, axes = self._make_panel_grid(
            len(self.loaded_units),
            panel_width=4.6,
            panel_height=3.7,
            sharex=True,
        )
        template_limit_uv = max(
            max(float(np.max(np.abs(unit.heatmap_template_uv))) for unit in self.loaded_units),
            np.finfo(float).eps,
        )
        mesh: QuadMesh
        for unit_number, unit in enumerate(self.loaded_units):
            ax = axes.ravel()[unit_number]
            local_channel_indices = self.select_local_channel_indices(
                unit.unit_id,
                local_channel_mode=local_channel_mode,
                local_channel_count=local_channel_count,
            )
            mesh = unit.draw_local_average_heatmap(
                ax,
                local_channel_indices=local_channel_indices,
                time_edges_ms=self.store.time_edges_ms,
                template_limit_uv=template_limit_uv,
            )
            axis_label = (
                'Same-shank channels'
                if local_channel_mode == 'same_shank'
                else 'Same-x-column channels'
            )
            ax.set_ylabel(axis_label)
            self._style_axes(ax)

        self._hide_unused_axes(axes, len(self.loaded_units))
        colorbar: Colorbar = figure.colorbar(
            mesh,
            ax=axes.ravel()[:len(self.loaded_units)].tolist(),
            shrink=0.82,
            pad=0.02,
        )
        colorbar.set_label('Mean amplitude relative to early baseline (µV)')
        colorbar.ax.tick_params(colors=self.plot_colors['muted'], labelsize=9)
        return self._finish(figure, 'spikeinterface_local_average_heatmaps.png')

    def _plot_best_channel_averages(self) -> PlotResult:
        if (empty_result := self._no_units()) is not None:
            return empty_result

        figure, axes = self._make_panel_grid(
            len(self.loaded_units),
            panel_width=4.4,
            panel_height=3.8,
            sharex=True,
        )
        for unit_number, unit in enumerate(self.loaded_units):
            ax = axes.ravel()[unit_number]
            unit.draw_best_channel_average(ax, self.store.time_ms)
            self._style_axes(ax, grid=True)
        self._hide_unused_axes(axes, len(self.loaded_units))
        return self._finish(figure, 'spikeinterface_best_channel_averages.png')

    def _plot_ptp_gradients(self) -> PlotResult:
        if (empty_result := self._no_units()) is not None:
            return empty_result

        figure, axes = self._make_panel_grid(
            len(self.loaded_units),
            panel_width=3.9,
            panel_height=6.8,
            sharex=True,
            sharey=True,
        )
        global_ptp_max_uv = (
            max(
                max(unit.summary.max_ptp_uv for unit in self.loaded_units),
                np.finfo(float).eps,
            )
            if self.probe_ptp_scale == 'global_uv'
            else 1.0
        )
        contact_poly: PolyCollection
        for unit_number, unit in enumerate(self.loaded_units):
            ax = axes.ravel()[unit_number]
            contact_poly = unit.draw_ptp_gradient(
                ax,
                self.probe,
                probe_ptp_scale=self.probe_ptp_scale,
                global_ptp_max_uv=global_ptp_max_uv,
            )
            self._style_probe_axes(
                ax,
                show_y_label=unit_number % axes.shape[1] == 0,
            )

        self._hide_unused_axes(axes, len(self.loaded_units))
        colorbar: Colorbar = figure.colorbar(
            contact_poly,
            ax=axes.ravel()[:len(self.loaded_units)].tolist(),
            shrink=0.82,
            pad=0.02,
        )
        colorbar.set_label(
            'Relative PTP (unit maximum = 1)'
            if self.probe_ptp_scale == 'per_unit'
            else 'Mean-waveform PTP (µV)'
        )
        colorbar.ax.tick_params(colors=self.plot_colors['muted'], labelsize=9)
        return self._finish(figure, 'spikeinterface_ptp_probe_maps.png')

    def _plot_max_ptp_contacts(
            self,
            *,
            layout: Literal['panels', 'combined'] = 'panels',
            show_other_units: bool = False,
    ) -> PlotResult:
        if (empty_result := self._no_units()) is not None:
            return empty_result

        panel_count = 1 if layout == 'combined' else len(self.loaded_units)
        figure, axes = self._make_panel_grid(
            panel_count,
            panel_width=4.5 if layout == 'combined' else 3.9,
            panel_height=7.4 if layout == 'combined' else 6.8,
            sharex=True,
            sharey=True,
        )
        eligible_summaries = self.store.unit_summaries
        for panel_number in range(panel_count):
            ax = axes.ravel()[panel_number]
            self._plot_probe_background(ax, contact_alpha=0.78)
            if show_other_units:
                foreground_ids = (
                    {unit.unit_id for unit in self.loaded_units}
                    if layout == 'combined'
                    else {self.loaded_units[panel_number].unit_id}
                )
                other_units = [
                    summary
                    for unit_id, summary in eligible_summaries.items()
                    if unit_id not in foreground_ids
                ]
                ax.scatter(
                    [summary.best_channel_x_um for summary in other_units],
                    [summary.best_channel_y_um for summary in other_units],
                    marker='o',
                    s=18,
                    color=self.plot_colors['border'],
                    alpha=0.34,
                    edgecolors='white',
                    linewidths=0.25,
                    zorder=2,
                )
            self._style_probe_axes(
                ax,
                show_y_label=panel_number % axes.shape[1] == 0,
            )

        for unit_number, unit in enumerate(self.loaded_units):
            panel_index = 0 if layout == 'combined' else unit_number
            ax = axes.ravel()[panel_index]
            unit.draw_max_ptp_contact(ax)
            if layout == 'panels':
                ax.set_title(
                    (
                        f'Unit {unit.unit_id} · maximum-PTP contact\n'
                        f'ch {unit.summary.best_channel_id} · max {unit.summary.max_ptp_uv:.1f} µV'
                    ),
                    loc='left',
                    color=unit.color,
                    fontweight='bold',
                )

        if layout == 'combined':
            ax = axes.ravel()[0]
            ax.set_title('Maximum-PTP contact · one circle per unit', loc='left', fontweight='bold')
            legend: Legend = ax.legend(
                loc='upper right',
                frameon=True,
                facecolor='white',
                edgecolor=self.plot_colors['border'],
            )
            legend.get_frame().set_alpha(0.95)
        self._hide_unused_axes(axes, panel_count)
        return self._finish(figure, 'spikeinterface_max_ptp_contacts.png')

    def plot_unit_locations(self, *, show_other_units: bool = False) -> PlotResult:
        if (empty_result := self._no_units()) is not None:
            return empty_result

        figure, ax = plt.subplots(figsize=(7.2, 9.2), constrained_layout=True)
        self._plot_probe_background(ax, contact_alpha=0.85)
        if show_other_units:
            requested_ids = {unit.unit_id for unit in self.loaded_units}
            other_units = [
                summary
                for unit_id, summary in self.store.unit_summaries.items()
                if unit_id not in requested_ids
            ]
            ax.scatter(
                [summary.unit_x_um for summary in other_units],
                [summary.unit_y_um for summary in other_units],
                s=24,
                color='#4C78A8',
                alpha=0.25,
                edgecolors='white',
                linewidths=0.35,
                label=f'{len(other_units)} other units',
                zorder=3,
            )
        for unit in self.loaded_units:
            unit.draw_unit_location(ax)

        self._style_probe_axes(ax)
        legend: Legend = ax.legend(
            loc='upper right',
            frameon=True,
            facecolor='white',
            edgecolor=self.plot_colors['border'],
        )
        legend.get_frame().set_alpha(0.95)
        return self._finish(figure, 'spikeinterface_unit_locations.png')

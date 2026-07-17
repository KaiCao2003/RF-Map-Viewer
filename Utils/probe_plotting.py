from os import PathLike
from pathlib import Path
from typing import Any, Literal

import matplotlib.pyplot as plt
import numpy as np
import spikeinterface as si
from matplotlib.axes import Axes
from matplotlib.colorbar import Colorbar
from matplotlib.collections import PolyCollection, QuadMesh
from matplotlib.figure import Figure
from matplotlib.legend import Legend
from numpy.typing import NDArray
from probeinterface import Probe
from probeinterface.plotting import plot_probe

from Utils.si_utils import compute_template_ptp_summary

type FloatArray = NDArray[np.floating[Any]]
type IntArray = NDArray[np.integer[Any]]
type StructuredArray = NDArray[np.void]
type AxesArray = NDArray[np.object_]



class ProbePlot:
    plot_colors: dict[str, str] = {
        'ink': '#172033',
        'muted': '#667085',
        'border': '#CBD5E1',
        'grid': '#E2E8F0',
        'contact': '#D8DEE8',
        'probe': '#F8FAFC',
    }
    unit_colors: tuple[str, ...] = ('#2563EB', '#D97706', '#0F766E', '#7C3AED', '#BE123C', '#4F46E5')

    # Force a light plotting theme even when Jupyter itself uses dark mode.
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
            probe: Probe,
            session_dir: Path,
            *,
            unit_ids: list[int],
            waveform_analyzer: si.SortingAnalyzer,
            template_extension: si.ComputeTemplates,
            probe_ptp_scale: Literal['per_unit', 'global_uv'],
            is_debug: bool,
            probe_name: str,
            save_figures: bool,
            output_dir: PathLike | None = None,
    ):
        self.probe = probe
        self.session_dir = session_dir
        self.unit_ids = unit_ids
        self.waveform_analyzer = waveform_analyzer
        self.template_extension = template_extension
        self.probe_ptp_scale = probe_ptp_scale
        self.is_debug = is_debug
        self.probe_name = probe_name
        self.save_figures = save_figures
        self.output_dir = Path(output_dir) if output_dir is not None else None


    def _style_axes(self, ax: Axes, grid: bool = False) -> None:
        ax.set_facecolor('white')
        ax.tick_params(colors= self.plot_colors['muted'], labelsize=9)
        for spine in ax.spines.values():
            spine.set_color(self.plot_colors['border'])
        ax.grid(grid)
        ax.set_axisbelow(True)

    @staticmethod
    def _make_panel_grid(
            panel_count: int,
            panel_width: float,
            panel_height: float,
            max_columns: int = 3,
            sharex: bool = False,
            sharey: bool = False,
    ) -> tuple[Figure, AxesArray]:
        if panel_count < 1:
            raise ValueError('panel_count must be positive.')
        column_count: int = min(max_columns, panel_count)
        row_count: int = (panel_count + column_count - 1) // column_count
        fig: Figure
        axes: AxesArray
        fig, axes = plt.subplots(
            row_count,
            column_count,
            figsize=(panel_width * column_count, panel_height * row_count),
            squeeze=False,
            sharex=sharex,
            sharey=sharey,
            constrained_layout=True,
        )
        return fig, np.asarray(axes, dtype=object)

    @staticmethod
    def _hide_unused_axes(axes: AxesArray, used_count: int) -> None:
        for unused_ax in axes.ravel()[used_count:]:
            unused_ax.set_visible(False)

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

    @staticmethod
    def _select_local_channel_indices(
            best_channel_index: int,
            channel_locations: FloatArray,
            channel_shank_ids: IntArray,
            local_channel_mode: Literal['same_shank', 'same_x_column'],
            local_channel_count: int,
    ) -> IntArray:
        distances: FloatArray = np.linalg.norm(
            channel_locations - channel_locations[best_channel_index],
            axis=1,
        )
        best_shank_id: int = int(channel_shank_ids[best_channel_index])
        best_x_um: float = float(channel_locations[best_channel_index, 0])
        candidate_indices: IntArray
        if local_channel_mode == 'same_shank':
            candidate_indices = np.flatnonzero(channel_shank_ids == best_shank_id)
        else:
            candidate_indices = np.flatnonzero(
                np.isclose(
                    channel_locations[:, 0],
                    best_x_um,
                    rtol=0.0,
                    atol=1e-6,
                )
            )
        neighbor_count: int = local_channel_count - 1
        neighbor_candidates: IntArray = candidate_indices[candidate_indices != best_channel_index]
        candidate_order: IntArray = np.argsort(distances[neighbor_candidates], kind='stable')
        nearest_neighbor_indices: IntArray = neighbor_candidates[
            candidate_order[:min(neighbor_count, len(neighbor_candidates))]
        ]
        if len(nearest_neighbor_indices) != neighbor_count:
            raise ValueError(f'{local_channel_mode} does not contain enough neighboring channels.')
        nearest_indices: IntArray = np.r_[
            best_channel_index,
            nearest_neighbor_indices,
        ].astype(int)
        if best_channel_index not in nearest_indices:
            raise RuntimeError('The maximum-PTP channel was not retained in the local heatmap.')
        if len(np.unique(nearest_indices)) != local_channel_count:
            raise RuntimeError('The local heatmap channel selection contains duplicates.')
        if local_channel_mode == 'same_shank':
            if np.any(channel_shank_ids[nearest_indices] != best_shank_id):
                raise RuntimeError('A local heatmap channel came from a different shank.')
        elif not np.allclose(
                channel_locations[nearest_indices, 0],
                channel_locations[best_channel_index, 0],
                rtol=0.0,
                atol=1e-6,
        ):
            raise RuntimeError('A local heatmap channel came from a different x column.')
        local_order: IntArray = np.lexsort(
            (channel_locations[nearest_indices, 0], -channel_locations[nearest_indices, 1])
        )
        return nearest_indices[local_order]

    def finalize_figure(self, figure: Figure, filename: str) -> None:
        if self.save_figures:
            figure.savefig(self.output_dir / filename, dpi=200, bbox_inches='tight')
        plt.show()


    def plot_probe_geometry(self):
        fig, ax = plt.subplots(figsize=(6.4, 8.8), constrained_layout=True)
        self._plot_probe_background(ax, contact_alpha=1.0)
        self._style_probe_axes(ax)
        ax.set_title(f'{self.session_dir.name} · Probe{self.probe_name} geometry', loc='left', fontweight='bold')
        self.finalize_figure(fig, 'probe_geometry.png')

    def plot_waveform_spike_selection_times(
            self,
            sorting: si.BaseSorting,
            selected_spikes: StructuredArray,
            raw_sampling_frequency: float,
            recording_duration_minutes: float,
            selected_spike_counts: dict[int, int],
            spike_counts: dict[int, int],
    ):
        fig, ax = plt.subplots(
            figsize=(12.0, 2.2 + 1.15 * len(self.unit_ids)),
            constrained_layout=True,
        )
        selection_labels: list[str] = []
        for unit_number, unit_id in enumerate(self.unit_ids):
            unit_index: int = int(sorting.id_to_index(unit_id))
            all_spike_samples: IntArray = sorting.get_unit_spike_train(unit_id=unit_id)
            all_spike_minutes: FloatArray = all_spike_samples / raw_sampling_frequency / 60.0
            selected_unit_mask: NDArray[np.bool_] = selected_spikes['unit_index'] == unit_index
            selected_spike_minutes: FloatArray = selected_spikes['sample_index'][selected_unit_mask] / raw_sampling_frequency / 60.0

            y_all: FloatArray = np.full(len(all_spike_minutes), unit_number, dtype=float)
            y_selected: FloatArray = np.full(len(selected_spike_minutes), unit_number, dtype=float)
            color: str = self.unit_colors[unit_number % len(self.unit_colors)]
            ax.scatter(
                all_spike_minutes,
                y_all,
                marker='|',
                s=14,
                color=self.plot_colors['border'],
                alpha=0.10,
                linewidths=0.45,
                rasterized=True,
            )
            ax.scatter(
                selected_spike_minutes,
                y_selected,
                marker='|',
                s=24,
                color=color,
                alpha=0.72,
                linewidths=0.75,
                rasterized=True,
            )
            selection_labels.append(
                f'unit {unit_id} · {selected_spike_counts[unit_id]:,} / {spike_counts[unit_id]:,}'
            )

        ax.set_xlim(0, recording_duration_minutes)
        ax.set_ylim(len(self.unit_ids) - 0.35, -0.65)
        ax.set_yticks(np.arange(len(self.unit_ids)), selection_labels)
        ax.set_xlabel('Recording time (min)')
        ax.set_ylabel('Unit · selected  / total spikes')
        ax.set_title('Spikes used for waveform averages · exact recording times', loc='left', fontweight='bold')
        self._style_axes(ax, grid=True)
        self.finalize_figure(fig, 'waveform_spike_selection_times.png')

    def plot_local_average_heatmaps(
            self,
            heatmap_template_array: FloatArray,
            best_channel_indices: IntArray,
            channel_locations: FloatArray,
            channel_shank_ids: IntArray,
            local_channel_mode: Literal['same_shank', 'same_x_column'],
            local_channel_count: int,
            time_edges_ms: FloatArray,
            template_limit_uv: float,
            channel_ids_array: IntArray,
            local_channel_axis_label: str,
            local_channel_mode_description: str,
    ):
        fig: Figure
        axes: AxesArray
        fig, axes = self._make_panel_grid(
            len(self.unit_ids),
            panel_width=4.6,
            panel_height=3.7,
            sharex=True,
        )
        mesh: QuadMesh | None = None
        for unit_number, unit_id in enumerate(self.unit_ids):
            ax: Axes = axes.ravel()[unit_number]
            unit_template: FloatArray = heatmap_template_array[unit_number]
            best_channel_index: int = int(best_channel_indices[unit_number])
            best_shank_id: int = int(channel_shank_ids[best_channel_index])
            local_indices: IntArray = self._select_local_channel_indices(
                best_channel_index,
                channel_locations,
                channel_shank_ids,
                local_channel_mode,
                local_channel_count,
            )
            local_template: FloatArray = unit_template[:, local_indices].T
            row_edges: FloatArray = np.arange(len(local_indices) + 1, dtype=float) - 0.5
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
            best_row: int = int(np.flatnonzero(local_indices == best_channel_index)[0])
            color: str = self.unit_colors[unit_number % len(self.unit_colors)]
            for local_number, channel_index_value in enumerate(local_indices):
                channel_index: int = int(channel_index_value)
                is_best_channel: bool = channel_index == best_channel_index
                ax.scatter(
                    -0.028,
                    local_number,
                    transform=ax.get_yaxis_transform(),
                    marker='o',
                    s=42 if is_best_channel else 25,
                    facecolors=color if is_best_channel else 'white',
                    edgecolors=color if is_best_channel else self.plot_colors['muted'],
                    linewidths=1.15 if is_best_channel else 0.85,
                    clip_on=False,
                    zorder=4,
                )
            ax.axvline(0, color=self.plot_colors['ink'], linestyle='--', linewidth=0.8, alpha=0.65)
            channel_labels: list[str] = [
                f'ch {int(channel_ids_array[int(channel_index)])}'
                for channel_index in local_indices
            ]
            ax.set_yticks(np.arange(len(local_indices)), channel_labels, fontsize=8)
            ax.tick_params(axis='y', pad=15)
            for local_number, tick_label in enumerate(ax.get_yticklabels()):
                if local_number == best_row:
                    tick_label.set_color(color)
                    tick_label.set_fontweight('bold')
            ax.set_xlim(time_edges_ms[0], time_edges_ms[-1])
            ax.set_ylim(len(local_indices) - 0.5, -0.5)
            ax.set_title(
                f'Shank{best_shank_id}, Unit{unit_id}',
                loc='center',
                color=color,
                fontsize=10,
                fontweight='bold',
            )
            ax.set_xlabel('Time from spike alignment (ms)')
            ax.set_ylabel(local_channel_axis_label)
            self._style_axes(ax)

        self._hide_unused_axes(axes, len(self.unit_ids))
        if mesh is None:
            raise RuntimeError('No local heatmap rows were created.')
        fig.suptitle(
            (
                f'SpikeInterface raw average · {local_channel_count} channels including maximum '
                f'· {local_channel_mode_description}'
            ),
            fontsize=15,
            fontweight='bold',
        )
        colorbar: Colorbar = fig.colorbar(
            mesh,
            ax=axes.ravel()[:len(self.unit_ids)].tolist(),
            shrink=0.82,
            pad=0.02,
        )
        colorbar.set_label('Mean amplitude relative to early baseline (µV)')
        colorbar.ax.tick_params(colors=self.plot_colors['muted'], labelsize=9)
        self.finalize_figure(fig, 'spikeinterface_local_average_heatmaps.png')

    def plot_best_channel_averages(
            self,
            best_channel_waveforms: FloatArray,
            best_channel_indices: IntArray,
            best_channel_ptp_uv: FloatArray,
            channel_locations: FloatArray,
            time_ms: FloatArray,
    ):
        fig: Figure
        axes: AxesArray
        fig, axes = self._make_panel_grid(
            len(self.unit_ids),
            panel_width=4.4,
            panel_height=3.8,
            sharex=True,
            sharey=False,
        )
        for unit_number, unit_id in enumerate(self.unit_ids):
            ax: Axes = axes.ravel()[unit_number]
            channel_index: int = int(best_channel_indices[unit_number])
            waveform_uv: FloatArray = best_channel_waveforms[unit_number]
            channel_id: int = int(self.waveform_analyzer.channel_ids[channel_index])
            x_um: float = float(channel_locations[channel_index, 0])
            y_um: float = float(channel_locations[channel_index, 1])
            ptp_uv: float = float(best_channel_ptp_uv[unit_number])
            waveform_limit_uv: float = max(float(np.max(np.abs(waveform_uv))), np.finfo(float).eps)
            color: str = self.unit_colors[unit_number % len(self.unit_colors)]
            ax.plot(
                time_ms,
                waveform_uv,
                color=color,
                linewidth=1.6,
                # marker='o',
                markersize=2.2,
                markerfacecolor='white',
                markeredgewidth=0.7,
            )
            ax.axvline(0, color=self.plot_colors['muted'], linestyle='--', linewidth=0.8)
            ax.axhline(0, color=self.plot_colors['border'], linewidth=0.8)
            ax.set_ylim(-1.08 * waveform_limit_uv, 1.08 * waveform_limit_uv)
            ax.set_title(
                f'Unit {unit_id}\nch {channel_id} · ({x_um:.0f}, {y_um:.0f}) µm · PTP {ptp_uv:.1f} µV',
                loc='left',
                color=color,
                fontweight='bold',
            )
            ax.set_xlabel('Time from spike alignment (ms)')
            ax.set_ylabel('Mean raw amplitude (µV)')
            self._style_axes(ax, grid=True)

        self._hide_unused_axes(axes, len(self.unit_ids))
        fig.suptitle('SpikeInterface best-channel averages · exact samples', fontsize=15, fontweight='bold')
        self.finalize_figure(fig, 'spikeinterface_best_channel_averages.png')

    def plot_probe_ptp(
            self,
            unit_ids_to_show: list[int],
            plot_the_center: bool = False,
            show_other_units: bool = False,
            is_in_one_plot: bool = False,
    ) -> tuple[Figure, AxesArray]:
        channel_locations = self.waveform_analyzer.get_channel_locations()

        if not isinstance(unit_ids_to_show, list) or not all(type(unit_id) is int for unit_id in unit_ids_to_show):
            raise TypeError('unit_ids_to_show must be a list[int].')
        if any(type(value) is not bool for value in (plot_the_center, show_other_units, is_in_one_plot)):
            raise TypeError('plot_the_center, show_other_units, and is_in_one_plot must be bool values.')
        if not unit_ids_to_show:
            raise ValueError('unit_ids_to_show must contain at least one unit ID.')
        if len(set(unit_ids_to_show)) != len(unit_ids_to_show):
            raise ValueError('unit_ids_to_show must not contain duplicate unit IDs.')
        missing_unit_ids: list[int] = sorted(set(unit_ids_to_show) - set(self.unit_ids))
        if missing_unit_ids:
            raise ValueError(f'Unit IDs are not present in the selected sorting: {missing_unit_ids}')
        if self.probe_ptp_scale not in ('per_unit', 'global_uv'):
            raise ValueError("probe_ptp_scale must be 'per_unit' or 'global_uv'.")
        if not plot_the_center and show_other_units:
            raise ValueError('show_other_units is only meaningful when plot_the_center is True.')
        if not plot_the_center and is_in_one_plot and len(unit_ids_to_show) > 1:
            raise ValueError('Multiple PTP gradients require one panel per unit.')

        shown_template_array: FloatArray = self.template_extension.get_templates(
            unit_ids=unit_ids_to_show,
            operator='average',
        )
        shown_ptp_summary = compute_template_ptp_summary(shown_template_array)
        shown_ptp_uv: FloatArray = shown_ptp_summary.ptp_by_channel
        shown_best_channel_indices: IntArray = shown_ptp_summary.best_channel_indices
        shown_ptp_max_uv: FloatArray = shown_ptp_summary.max_ptp_by_unit
        global_ptp_max_uv: float = max(float(np.max(shown_ptp_uv)), np.finfo(float).eps)

        panel_count: int = 1 if is_in_one_plot else len(unit_ids_to_show)
        figure: Figure
        panel_axes: AxesArray
        figure, panel_axes = self._make_panel_grid(
            panel_count,
            panel_width=4.5 if is_in_one_plot else 3.9,
            panel_height=7.4 if is_in_one_plot else 6.8,
            sharex=True,
            sharey=True,
        )

        if plot_the_center:
            all_best_contact_locations: FloatArray | None = None
            if show_other_units:
                all_ptp_summary = compute_template_ptp_summary(
                    self.template_extension.get_templates(operator='average')
                )
                all_best_contact_locations = channel_locations[all_ptp_summary.best_channel_indices]

            for panel_number in range(panel_count):
                panel_ax: Axes = panel_axes.ravel()[panel_number]
                self._plot_probe_background(panel_ax, contact_alpha=0.78)
                if show_other_units:
                    if all_best_contact_locations is None:
                        raise RuntimeError('Other-unit locations were not calculated.')
                    foreground_ids: set[int] = (
                        set(unit_ids_to_show)
                        if is_in_one_plot
                        else {unit_ids_to_show[panel_number]}
                    )
                    other_unit_indices: IntArray = np.asarray(
                        [
                            unit_index
                            for unit_index, unit_id in enumerate(self.unit_ids)
                            if unit_id not in foreground_ids
                        ],
                        dtype=np.intp,
                    )
                    if len(other_unit_indices) > 0:
                        panel_ax.scatter(
                            all_best_contact_locations[other_unit_indices, 0],
                            all_best_contact_locations[other_unit_indices, 1],
                            marker='o',
                            s=18,
                            color=self.plot_colors['border'],
                            alpha=0.34,
                            edgecolors='white',
                            linewidths=0.25,
                            zorder=2,
                        )
                self._style_probe_axes(
                    panel_ax,
                    show_y_label=panel_number % panel_axes.shape[1] == 0,
                )

            for unit_number, unit_id in enumerate(unit_ids_to_show):
                panel_index: int = 0 if is_in_one_plot else unit_number
                panel_ax = panel_axes.ravel()[panel_index]
                best_channel_index: int = int(shown_best_channel_indices[unit_number])
                channel_id: int = int(self.waveform_analyzer.channel_ids[best_channel_index])
                x_um: float = float(channel_locations[best_channel_index, 0])
                y_um: float = float(channel_locations[best_channel_index, 1])
                unit_ptp_max_uv: float = float(shown_ptp_max_uv[unit_number])
                color: str = self.unit_colors[unit_number % len(self.unit_colors)]
                panel_ax.scatter(
                    x_um,
                    y_um,
                    marker='o',
                    s=92,
                    color=color,
                    edgecolors='white',
                    linewidths=1.1,
                    label=f'unit {unit_id} · ch {channel_id}',
                    zorder=4,
                )
                if not is_in_one_plot:
                    panel_ax.set_title(
                        f'Unit {unit_id} · maximum-PTP contact\nch {channel_id} · max {unit_ptp_max_uv:.1f} µV',
                        loc='left',
                        color=color,
                        fontweight='bold',
                    )

            if is_in_one_plot:
                combined_ax: Axes = panel_axes.ravel()[0]
                combined_ax.set_title('Maximum-PTP contact · one circle per unit', loc='left', fontweight='bold')
                legend: Legend = combined_ax.legend(
                    loc='upper right',
                    frameon=True,
                    facecolor='white',
                    edgecolor=self.plot_colors['border'],
                )
                legend.get_frame().set_alpha(0.95)
            figure.suptitle('Highest-weight contact · raw-average PTP', fontsize=15, fontweight='bold')
        else:
            contact_poly: PolyCollection | None = None
            for unit_number, unit_id in enumerate(unit_ids_to_show):
                panel_ax = panel_axes.ravel()[unit_number]
                unit_ptp_uv: FloatArray = shown_ptp_uv[unit_number]
                unit_ptp_max_uv = max(float(shown_ptp_max_uv[unit_number]), np.finfo(float).eps)
                contact_values: FloatArray
                color_limit: float
                if self.probe_ptp_scale == 'per_unit':
                    contact_values = unit_ptp_uv / unit_ptp_max_uv
                    color_limit = 1.0
                else:
                    contact_values = unit_ptp_uv
                    color_limit = global_ptp_max_uv
                contact_poly, _ = plot_probe(
                    self.probe,
                    ax=panel_ax,
                    title=False,
                    contacts_values=contact_values,
                    cmap='Blues',
                    contact_kwargs={'alpha': 1.0, 'edgecolor': self.plot_colors['border'], 'lw': 0.25},
                    probe_shape_kwargs={
                        'facecolor': self.plot_colors['probe'],
                        'edgecolor': self.plot_colors['border'],
                        'lw': 0.8,
                    },
                )
                contact_poly.set_clim(0, color_limit)
                best_channel_index = int(shown_best_channel_indices[unit_number])
                color = self.unit_colors[unit_number % len(self.unit_colors)]
                self._style_probe_axes(
                    panel_ax,
                    show_y_label=unit_number % panel_axes.shape[1] == 0,
                )
                panel_ax.set_title(
                    (
                        f'Unit {unit_id} · best ch {int(self.waveform_analyzer.channel_ids[best_channel_index])}'
                        f'\nmax {unit_ptp_max_uv:.1f} µV'
                    ),
                    loc='left',
                    color=color,
                    fontweight='bold',
                )

            if contact_poly is None:
                raise RuntimeError('The PTP gradient was not created.')
            ptp_scale_title: str = (
                'per-unit normalized display'
                if self.probe_ptp_scale == 'per_unit'
                else 'shared absolute µV scale'
            )
            figure.suptitle(f'SpikeInterface raw-average PTP · {ptp_scale_title}', fontsize=15, fontweight='bold')
            colorbar: Colorbar = figure.colorbar(
                contact_poly,
                ax=panel_axes.ravel()[:len(unit_ids_to_show)].tolist(),
                shrink=0.82,
                pad=0.02,
            )
            colorbar_label: str = (
                'Relative PTP (unit maximum = 1)'
                if self.probe_ptp_scale == 'per_unit'
                else 'Mean-waveform PTP (µV)'
            )
            colorbar.set_label(colorbar_label)
            colorbar.ax.tick_params(colors=self.plot_colors['muted'], labelsize=9)

        self._hide_unused_axes(panel_axes, panel_count)
        return figure, panel_axes

    def plot_unit_locations(
            self,
            sorting: si.BaseSorting,
            unit_locations: FloatArray,
    ):
        fig: Figure
        ax: Axes
        fig, ax = plt.subplots(figsize=(7.2, 9.2), constrained_layout=True)
        self._plot_probe_background(ax, contact_alpha=0.85)
        ax.scatter(
            unit_locations[:, 0],
            unit_locations[:, 1],
            s=24,
            color='#4C78A8',
            alpha=0.62,
            edgecolors='white',
            linewidths=0.35,
            label=f'{len(self.unit_ids)} selected units',
            zorder=3,
        )
        for unit_number, unit_id in enumerate(self.unit_ids):
            unit_index: int = int(sorting.id_to_index(unit_id))
            x_um: float = float(unit_locations[unit_index, 0])
            y_um: float = float(unit_locations[unit_index, 1])
            color: str = self.unit_colors[unit_number % len(self.unit_colors)]
            ax.scatter(
                x_um,
                y_um,
                s=84,
                color=color,
                edgecolors='white',
                linewidths=1.0,
                label=f'unit {unit_id}',
                zorder=4,
            )
            ax.annotate(
                str(unit_id),
                (x_um, y_um),
                xytext=(6, 4),
                textcoords='offset points',
                color=color,
                fontsize=8,
                fontweight='bold',
                zorder=5,
            )

        self._style_probe_axes(ax)
        ax.set_title('Unit position · SpikeInterface raw-average center of mass', loc='left', fontweight='bold')
        legend: Legend = ax.legend(
            loc='upper right',
            frameon=True,
            facecolor='white',
            edgecolor=self.plot_colors['border'],
        )
        legend.get_frame().set_alpha(0.95)
        self.finalize_figure(fig, 'spikeinterface_unit_locations.png')

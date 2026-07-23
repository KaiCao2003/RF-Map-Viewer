import csv
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

import numpy as np
from numpy.typing import NDArray

from Utils.si_utils import TemplatePtpSummary


type FloatArray = NDArray[np.floating[Any]]
type IntArray = NDArray[np.integer[Any]]
type StructuredArray = NDArray[np.void]

def _probe_directory_name(probe_name: str) -> str:
    return probe_name if probe_name.startswith('Probe') else f'Probe{probe_name}'


def waveform_root_dir(output_dir: str | Path) -> Path:
    """Return the waveform root under the shared output directory."""
    return Path(output_dir).expanduser() / 'waveform'


def spike_position_root_dir(output_dir: str | Path) -> Path:
    """Return the spike-position root next to the waveform root."""
    return Path(output_dir).expanduser() / 'spike_position'


def waveform_analysis_dir(
        output_dir: str | Path,
        probe_name: str,
) -> Path:
    """Return the stable waveform-analysis directory for one probe."""
    return waveform_root_dir(output_dir) / _probe_directory_name(probe_name)


def spike_position_analysis_dir(
        output_dir: str | Path,
        probe_name: str,
) -> Path:
    """Return the stable spike-position directory for one probe."""
    return spike_position_root_dir(output_dir) / _probe_directory_name(probe_name)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        f'{json.dumps(payload, indent=2, default=str)}\n',
        encoding='utf-8',
    )


def _update_root_config(
        root_dir: Path,
        *,
        schema_name: str,
        probe_name: str,
        generated_at_utc: str,
        probe_config: Mapping[str, Any],
) -> None:
    root_dir.mkdir(parents=True, exist_ok=True)
    config_path = root_dir / 'config.json'
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            config = {}
    else:
        config = {}
    if not isinstance(config, dict) or config.get('schema_name') != schema_name:
        config = {
            'schema_name': schema_name,
            'schema_version': 1,
            'probes': {},
        }

    config['updated_at_utc'] = generated_at_utc
    config.setdefault('probes', {})[_probe_directory_name(probe_name)] = dict(probe_config)
    _write_json(config_path, config)


def _append_run_log(
        root_dir: Path,
        *,
        generated_at_utc: str,
        source_session: str,
        source_probe: str,
        unit_scope: str,
        unit_count: int,
        output_dir: Path,
) -> None:
    log_line = (
        f'{generated_at_utc} status=complete'
        f' session={source_session}'
        f' probe={_probe_directory_name(source_probe)}'
        f' scope={unit_scope}'
        f' units={unit_count}'
        f' output={output_dir}\n'
    )
    with (root_dir / 'run.log').open('a', encoding='utf-8') as log_file:
        log_file.write(log_line)


@dataclass(frozen=True)
class UnitSummary:
    unit_index: int
    unit_id: int
    quality: str
    total_spike_count: int
    selected_spike_count: int
    time_coverage_percent: float
    best_channel_index: int
    best_channel_id: int
    best_channel_x_um: float
    best_channel_y_um: float
    max_ptp_uv: float
    unit_x_um: float
    unit_y_um: float
    unit_data_dir: str


@dataclass(frozen=True)
class WaveformUnitArtifact:
    summary: UnitSummary
    all_spike_samples: IntArray
    selected_spike_samples: IntArray
    template_uv: FloatArray
    ptp_by_channel_uv: FloatArray


@dataclass(frozen=True)
class WaveformExportPaths:
    waveform_dir: Path
    spike_position_dir: Path


def _read_unit_positions(path: Path) -> dict[int, tuple[float, float]]:
    positions: dict[int, tuple[float, float]] = {}
    with path.open(newline='') as csv_file:
        for row in csv.DictReader(csv_file):
            positions[int(row['unit_id'])] = (
                float(row['x_um']),
                float(row['y_um']),
            )
    return positions


def _read_unit_summaries(
        path: Path,
        *,
        positions: Mapping[int, tuple[float, float]] | None = None,
) -> dict[int, UnitSummary]:
    summaries: dict[int, UnitSummary] = {}
    with path.open(newline='') as csv_file:
        for row in csv.DictReader(csv_file):
            unit_id = int(row['unit_id'])
            if 'unit_x_um' in row and 'unit_y_um' in row:
                unit_x_um = float(row['unit_x_um'])
                unit_y_um = float(row['unit_y_um'])
            elif positions is not None and unit_id in positions:
                unit_x_um, unit_y_um = positions[unit_id]
            else:
                raise ValueError(f'Missing spike position for unit {unit_id}.')
            summary = UnitSummary(
                unit_index=int(row['unit_index']),
                unit_id=unit_id,
                quality=row['quality'],
                total_spike_count=int(row['total_spike_count']),
                selected_spike_count=int(row['selected_spike_count']),
                time_coverage_percent=float(row['time_coverage_percent']),
                best_channel_index=int(row['best_channel_index']),
                best_channel_id=int(row['best_channel_id']),
                best_channel_x_um=float(row['best_channel_x_um']),
                best_channel_y_um=float(row['best_channel_y_um']),
                max_ptp_uv=float(row['max_ptp_uv']),
                unit_x_um=unit_x_um,
                unit_y_um=unit_y_um,
                unit_data_dir=row['unit_data_dir'],
            )
            summaries[summary.unit_id] = summary
    return summaries


def _load_int_column(path: Path) -> IntArray:
    return np.loadtxt(
        path,
        delimiter=',',
        skiprows=1,
        dtype=np.int64,
        ndmin=1,
    )


class WaveformArtifactStore:
    def __init__(self, analysis_dir: str | Path):
        self.analysis_dir = Path(analysis_dir)
        self.manifest: dict[str, Any] = json.loads(
            (self.analysis_dir / 'manifest.json').read_text()
        )

        with (self.analysis_dir / 'channels.csv').open(newline='') as csv_file:
            channel_rows = list(csv.DictReader(csv_file))
        self.channel_ids: IntArray = np.asarray([int(row['channel_id']) for row in channel_rows])
        self.channel_locations: FloatArray = np.asarray([
            [float(row['x_um']), float(row['y_um'])]
            for row in channel_rows
        ])
        self.channel_shank_ids: IntArray = np.asarray([
            int(row['shank_id'])
            for row in channel_rows
        ])

        waveform_time = np.loadtxt(
            self.analysis_dir / 'waveform_time.csv',
            delimiter=',',
            skiprows=1,
            ndmin=2,
        )
        self.time_ms: FloatArray = waveform_time[:, 2]
        time_step_ms = float(np.median(np.diff(self.time_ms)))
        self.time_edges_ms: FloatArray = np.r_[
            self.time_ms[0] - time_step_ms / 2.0,
            (self.time_ms[:-1] + self.time_ms[1:]) / 2.0,
            self.time_ms[-1] + time_step_ms / 2.0,
        ]

        self.unit_scope: str = self.manifest['units']['scope']
        spike_positions_file = self.manifest['files'].get('spike_positions')
        positions = (
            _read_unit_positions(self.analysis_dir / spike_positions_file)
            if spike_positions_file is not None
            else None
        )
        self.unit_summaries = _read_unit_summaries(
            self.analysis_dir / self.manifest['files']['units'],
            positions=positions,
        )

    def load_unit(self, unit_id: int) -> WaveformUnitArtifact:
        unit_id = int(unit_id)
        if unit_id not in self.unit_summaries:
            raise KeyError(
                f'Unit {unit_id} is not available in this {self.unit_scope} analysis.'
            )

        summary = self.unit_summaries[unit_id]
        unit_data_dir = self.analysis_dir / summary.unit_data_dir
        template_table = np.loadtxt(
            unit_data_dir / 'template_uv.csv.gz',
            delimiter=',',
            skiprows=1,
            ndmin=2,
        )
        ptp_table = np.loadtxt(
            unit_data_dir / 'ptp_uv.csv.gz',
            delimiter=',',
            skiprows=1,
            ndmin=2,
        )
        return WaveformUnitArtifact(
            summary=summary,
            all_spike_samples=_load_int_column(unit_data_dir / 'spike_samples_all.csv.gz'),
            selected_spike_samples=_load_int_column(unit_data_dir / 'spike_samples_selected.csv.gz'),
            template_uv=template_table[:, 1:],
            ptp_by_channel_uv=ptp_table[:, 2],
        )


def _write_unit_index(path: Path, columns: list[str], rows: list[list[object]]) -> None:
    with path.open('w', newline='') as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(columns)
        writer.writerows(rows)


def export_waveform_analysis(
        output_dir: str | Path,
        *,
        source_session: str,
        source_probe: str,
        source_kilosort_dir: str | Path,
        source_raw_file: str | Path,
        unit_scope: Literal['good', 'all'],
        sorting: Any,
        unit_ids: IntArray,
        selected_spikes: StructuredArray,
        template_array: FloatArray,
        template_ptp_summary: TemplatePtpSummary,
        unit_locations: FloatArray,
        channel_ids: IntArray,
        channel_locations: FloatArray,
        channel_shank_ids: IntArray,
        sampling_frequency: float,
        recording_num_frames: int,
        recording_duration_minutes: float,
        time_ms: FloatArray,
        nbefore: int,
        pre_spike_ms: float,
        post_spike_ms: float,
        max_spikes_per_unit: int,
        waveform_seed: int,
        unit_location_feature: str,
        unit_location_radius_um: float,
        run_config: Mapping[str, Any] | None = None,
) -> WaveformExportPaths:
    output_dir = Path(output_dir).expanduser()
    analysis_dir = waveform_analysis_dir(output_dir, source_probe)
    spike_position_dir = spike_position_analysis_dir(output_dir, source_probe)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    spike_position_dir.mkdir(parents=True, exist_ok=True)
    unit_directory_pattern = re.compile(r'Unit-?\d+')

    channel_ids = np.asarray(channel_ids)
    with (analysis_dir / 'channels.csv').open('w', newline='') as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow([
            'channel_index',
            'channel_id',
            'raw_channel_index',
            'x_um',
            'y_um',
            'shank_id',
        ])
        for channel_index, channel_id in enumerate(channel_ids):
            writer.writerow([
                channel_index,
                int(channel_id),
                int(channel_id),
                float(channel_locations[channel_index, 0]),
                float(channel_locations[channel_index, 1]),
                int(channel_shank_ids[channel_index]),
            ])

    sample_indices = np.arange(len(time_ms))
    waveform_time = np.column_stack((sample_indices, sample_indices - nbefore, time_ms))
    np.savetxt(
        analysis_dir / 'waveform_time.csv',
        waveform_time,
        delimiter=',',
        header='sample_index,sample_offset,time_ms',
        comments='',
        fmt=['%d', '%d', '%.17g'],
    )

    selected_order = np.argsort(selected_spikes['unit_index'], kind='stable')
    selected_unit_indices = selected_spikes['unit_index'][selected_order]
    selected_samples = selected_spikes['sample_index'][selected_order]
    selected_boundaries = np.searchsorted(
        selected_unit_indices,
        np.arange(len(unit_ids) + 1),
    )
    quality_values = sorting.get_property('KSLabel')

    unit_columns = [
        'unit_index',
        'unit_id',
        'quality',
        'total_spike_count',
        'selected_spike_count',
        'time_coverage_percent',
        'best_channel_index',
        'best_channel_id',
        'best_channel_x_um',
        'best_channel_y_um',
        'max_ptp_uv',
        'unit_data_dir',
    ]
    unit_rows: list[list[object]] = []
    position_columns = ['unit_index', 'unit_id', 'x_um', 'y_um']
    position_rows: list[list[object]] = []
    exported_unit_directory_names: set[str] = set()

    template_header = ','.join([
        'sample_index',
        *[f'chidx_{channel_index:03d}_uv' for channel_index in range(len(channel_ids))],
    ])
    template_format = ['%d', *(['%.9g'] * len(channel_ids))]

    for unit_index, unit_id_value in enumerate(unit_ids):
        unit_id = int(unit_id_value)
        quality = str(quality_values[unit_index])
        all_spike_samples = sorting.get_unit_spike_train(unit_id=unit_id)
        selected_unit_samples = selected_samples[
            selected_boundaries[unit_index]:selected_boundaries[unit_index + 1]
        ]
        time_coverage_percent = (
            float(np.ptp(selected_unit_samples))
            / sampling_frequency
            / 60.0
            / recording_duration_minutes
            * 100.0
            if len(selected_unit_samples) > 1
            else 0.0
        )

        best_channel_index = int(template_ptp_summary.best_channel_indices[unit_index])
        unit_data_dir = Path(f'Unit{unit_id}')
        exported_unit_directory_names.add(unit_data_dir.name)
        unit_output_dir = analysis_dir / unit_data_dir
        unit_output_dir.mkdir(parents=True, exist_ok=True)

        template_table = np.column_stack((sample_indices, template_array[unit_index]))
        np.savetxt(
            unit_output_dir / 'template_uv.csv.gz',
            template_table,
            delimiter=',',
            header=template_header,
            comments='',
            fmt=template_format,
        )
        ptp_table = np.column_stack((
            np.arange(len(channel_ids)),
            channel_ids,
            template_ptp_summary.ptp_by_channel[unit_index],
        ))
        np.savetxt(
            unit_output_dir / 'ptp_uv.csv.gz',
            ptp_table,
            delimiter=',',
            header='channel_index,channel_id,ptp_uv',
            comments='',
            fmt=['%d', '%d', '%.9g'],
        )
        np.savetxt(
            unit_output_dir / 'spike_samples_all.csv.gz',
            np.asarray(all_spike_samples, dtype=np.int64),
            delimiter=',',
            header='sample_index',
            comments='',
            fmt='%d',
        )
        np.savetxt(
            unit_output_dir / 'spike_samples_selected.csv.gz',
            np.asarray(selected_unit_samples, dtype=np.int64),
            delimiter=',',
            header='sample_index',
            comments='',
            fmt='%d',
        )

        unit_row: list[object] = [
            unit_index,
            unit_id,
            quality,
            len(all_spike_samples),
            len(selected_unit_samples),
            time_coverage_percent,
            best_channel_index,
            int(channel_ids[best_channel_index]),
            float(channel_locations[best_channel_index, 0]),
            float(channel_locations[best_channel_index, 1]),
            float(template_ptp_summary.max_ptp_by_unit[unit_index]),
            str(unit_data_dir),
        ]
        unit_rows.append(unit_row)
        position_rows.append([
            unit_index,
            unit_id,
            float(unit_locations[unit_index, 0]),
            float(unit_locations[unit_index, 1]),
        ])

    unit_index_file = 'units.csv'
    _write_unit_index(analysis_dir / unit_index_file, unit_columns, unit_rows)
    position_file = 'positions.csv'
    _write_unit_index(
        spike_position_dir / position_file,
        position_columns,
        position_rows,
    )

    generated_at_utc = datetime.now(timezone.utc).isoformat()
    probe_directory = _probe_directory_name(source_probe)
    spike_positions_relative = (
        Path('..') / '..' / 'spike_position' / probe_directory / position_file
    ).as_posix()
    spike_position_manifest_relative = (
        Path('..') / '..' / 'spike_position' / probe_directory / 'manifest.json'
    ).as_posix()
    waveform_manifest = {
        'schema_name': 'rfmapping-spikeinterface-waveforms',
        'schema_version': 4,
        'complete': True,
        'generated_at_utc': generated_at_utc,
        'session': {
            'name': source_session,
            'probe': source_probe,
            'kilosort_dir': str(source_kilosort_dir),
            'raw_file': str(source_raw_file),
        },
        'recording': {
            'sampling_frequency_hz': sampling_frequency,
            'num_frames': recording_num_frames,
            'duration_minutes': recording_duration_minutes,
        },
        'units': {
            'scope': unit_scope,
            'count': len(unit_rows),
        },
        'waveform': {
            'selection_method': 'uniform',
            'max_spikes_per_unit': max_spikes_per_unit,
            'seed': waveform_seed,
            'pre_ms': pre_spike_ms,
            'post_ms': post_spike_ms,
            'nbefore': nbefore,
            'num_samples': len(time_ms),
        },
        'files': {
            'channels': 'channels.csv',
            'waveform_time': 'waveform_time.csv',
            'units': unit_index_file,
            'unit_data': 'Unit<unit_id>/',
            'spike_positions': spike_positions_relative,
            'spike_position_manifest': spike_position_manifest_relative,
        },
    }
    spike_position_manifest = {
        'schema_name': 'rfmapping-spike-positions',
        'schema_version': 1,
        'complete': True,
        'generated_at_utc': generated_at_utc,
        'session': {
            'name': source_session,
            'probe': source_probe,
            'kilosort_dir': str(source_kilosort_dir),
            'raw_file': str(source_raw_file),
        },
        'units': {
            'scope': unit_scope,
            'count': len(position_rows),
        },
        'spike_position': {
            'method': 'center_of_mass',
            'feature': unit_location_feature,
            'radius_um': unit_location_radius_um,
        },
        'files': {
            'positions': position_file,
        },
    }
    _write_json(analysis_dir / 'manifest.json', waveform_manifest)
    _write_json(spike_position_dir / 'manifest.json', spike_position_manifest)

    _update_root_config(
        waveform_root_dir(output_dir),
        schema_name='rfmapping-spikeinterface-waveform-config',
        probe_name=source_probe,
        generated_at_utc=generated_at_utc,
        probe_config={
            'generated_at_utc': generated_at_utc,
            'output': {
                'root_dir': str(waveform_root_dir(output_dir)),
                'probe_dir': str(analysis_dir),
                'unit_directory_pattern': 'Unit<unit_id>/',
            },
            'artifact': waveform_manifest,
            'run': dict(run_config or {}),
        },
    )
    _update_root_config(
        spike_position_root_dir(output_dir),
        schema_name='rfmapping-spike-position-config',
        probe_name=source_probe,
        generated_at_utc=generated_at_utc,
        probe_config={
            'generated_at_utc': generated_at_utc,
            'output': {
                'root_dir': str(spike_position_root_dir(output_dir)),
                'probe_dir': str(spike_position_dir),
            },
            'artifact': spike_position_manifest,
        },
    )

    for child in analysis_dir.iterdir():
        if (
            child.is_dir()
            and unit_directory_pattern.fullmatch(child.name)
            and child.name not in exported_unit_directory_names
        ):
            shutil.rmtree(child)

    _append_run_log(
        waveform_root_dir(output_dir),
        generated_at_utc=generated_at_utc,
        source_session=source_session,
        source_probe=source_probe,
        unit_scope=unit_scope,
        unit_count=len(unit_rows),
        output_dir=analysis_dir,
    )
    _append_run_log(
        spike_position_root_dir(output_dir),
        generated_at_utc=generated_at_utc,
        source_session=source_session,
        source_probe=source_probe,
        unit_scope=unit_scope,
        unit_count=len(position_rows),
        output_dir=spike_position_dir,
    )
    return WaveformExportPaths(
        waveform_dir=analysis_dir,
        spike_position_dir=spike_position_dir,
    )

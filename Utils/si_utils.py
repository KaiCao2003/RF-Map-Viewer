from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping
import spikeinterface as si
import spikeinterface.extractors as se
from probeinterface import Probe
from spikeinterface.postprocessing.localization_tools import compute_center_of_mass

import numpy as np
from numpy.typing import NDArray

type FloatArray = NDArray[np.floating[Any]]
type IntArray = NDArray[np.integer[Any]]
type StructuredArray = NDArray[np.void]

import csv
import json
from datetime import datetime, timezone





@dataclass(frozen=True)
class TemplatePtpSummary:
    ptp_by_channel: FloatArray
    best_channel_indices: IntArray
    max_ptp_by_unit: FloatArray


@dataclass(frozen=True)
class ValidatedData:
    channel_map: IntArray
    channel_positions: FloatArray
    channel_shank_ids: IntArray


def compute_template_ptp_summary(template_array: FloatArray) -> TemplatePtpSummary:
    ptp_by_channel: FloatArray = np.ptp(template_array, axis=1)
    best_channel_indices: IntArray = np.argmax(ptp_by_channel, axis=1)
    return TemplatePtpSummary(
        ptp_by_channel=ptp_by_channel,
        best_channel_indices=best_channel_indices,
        max_ptp_by_unit=ptp_by_channel[np.arange(len(ptp_by_channel)), best_channel_indices],
    )


def validate_data(
        *,
        kilosort_dir: str | Path,
        recording_file: str | Path,
) -> ValidatedData:
    raw_dtype = "int16"
    raw_num_channels = 384

    kilosort_dir = Path(kilosort_dir)
    recording_file = Path(recording_file)

    required_files: tuple[str, ...] = (
        "params.py",
        "spike_times.npy",
        "spike_clusters.npy",
        "channel_map.npy",
        "channel_positions.npy",
        "ops.npy",
    )
    missing = [
        name
        for name in required_files
        if not (kilosort_dir / name).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"Missing Kilosort files in {kilosort_dir}: {missing}"
        )
    if not recording_file.is_file():
        raise FileNotFoundError(f"Raw file not found: {recording_file}")

    bytes_per_frame = np.dtype(raw_dtype).itemsize * raw_num_channels
    raw_byte_count = recording_file.stat().st_size
    if raw_byte_count % bytes_per_frame != 0:
        raise ValueError(
            "Raw binary size is not divisible by "
            "dtype size × channel count."
        )

    channel_map = np.load(kilosort_dir / "channel_map.npy")
    channel_positions = np.load(kilosort_dir / "channel_positions.npy")
    if channel_map.shape != (raw_num_channels,) or channel_positions.shape != (raw_num_channels, 2):
        raise ValueError(
            "channel_map.npy and channel_positions.npy do not match "
            f"the expected {raw_num_channels} channels."
        )

    probe_ops = np.load(kilosort_dir / "ops.npy", allow_pickle=True).item()["probe"]
    ops_channel_map = np.asarray(probe_ops["chanMap"]).squeeze().astype(int)
    channel_shank_ids = np.asarray(probe_ops["kcoords"]).squeeze().astype(int)
    if not np.array_equal(ops_channel_map, channel_map):
        raise ValueError("Kilosort chanMap and channel_map.npy do not agree.")
    if channel_shank_ids.shape != (raw_num_channels,):
        raise ValueError("Kilosort kcoords do not match the channel count.")

    return ValidatedData(
        channel_map=channel_map,
        channel_positions=channel_positions,
        channel_shank_ids=channel_shank_ids,
    )


def _probe_directory_name(probe_name: str) -> str:
    return probe_name if probe_name.startswith('Probe') else f'Probe{probe_name}'


def waveform_root_dir(output_dir: str | Path) -> Path:
    """Return the waveform root under the shared output directory."""
    return Path(output_dir).expanduser() / 'waveform'


def spike_position_root_dir(output_dir: str | Path) -> Path:
    """Return the spike-position root next to the waveform root."""
    return Path(output_dir).expanduser() / 'spike_position'


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        f'{json.dumps(payload, indent=2)}\n',
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
        config = json.loads(config_path.read_text(encoding='utf-8'))
    else:
        config = {
            'schema_name': schema_name,
            'schema_version': 1,
            'probes': {},
        }

    config['updated_at_utc'] = generated_at_utc
    config['probes'][_probe_directory_name(probe_name)] = dict(probe_config)
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
    unit_x_um: float | None
    unit_y_um: float | None
    unit_data_dir: str


@dataclass(frozen=True)
class WaveformUnitArtifact:
    summary: UnitSummary
    all_spike_samples: IntArray
    selected_spike_samples: IntArray
    template_uv: FloatArray
    ptp_by_channel_uv: FloatArray


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
            if positions is not None:
                unit_x_um, unit_y_um = positions.get(unit_id, (None, None))
            elif 'unit_x_um' in row and 'unit_y_um' in row:
                unit_x_um = float(row['unit_x_um'])
                unit_y_um = float(row['unit_y_um'])
            else:
                unit_x_um, unit_y_um = None, None
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
        spike_positions_path = (
            self.analysis_dir / spike_positions_file
            if spike_positions_file is not None
            else None
        )
        positions = (
            _read_unit_positions(spike_positions_path)
            if spike_positions_path is not None and spike_positions_path.is_file()
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


def export_waveform_data(
        output_dir: str | Path,
        *,
        source_session: str,
        source_probe: str,
        source_kilosort_dir: str | Path,
        source_raw_file: str | Path,
        only_good_units: bool,
        sorting: Any,
        unit_ids: IntArray,
        selected_spikes: StructuredArray,
        template_array: FloatArray,
        template_ptp_summary: TemplatePtpSummary,
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
        run_config: Mapping[str, Any] | None = None,
) -> Path:
    output_dir = Path(output_dir).expanduser()
    waveform_root = waveform_root_dir(output_dir)
    probe_directory = _probe_directory_name(source_probe)
    analysis_dir = waveform_root / probe_directory
    analysis_dir.mkdir(parents=True, exist_ok=True)

    unit_scope = 'good' if only_good_units else 'all'
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

    unit_index_file = 'units.csv'
    _write_unit_index(analysis_dir / unit_index_file, unit_columns, unit_rows)
    position_file = 'positions.csv'

    generated_at_utc = datetime.now(timezone.utc).isoformat()
    spike_positions_relative = (
            Path('..') / '..' / 'spike_position' / probe_directory / position_file
    ).as_posix()
    waveform_manifest = {
        'schema_name': 'rfmapping-spikeinterface-waveforms',
        'schema_version': 4,
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
            'units': unit_index_file,
            'spike_positions': spike_positions_relative,
        },
    }
    _write_json(analysis_dir / 'manifest.json', waveform_manifest)

    _update_root_config(
        waveform_root,
        schema_name='rfmapping-spikeinterface-waveform-config',
        probe_name=source_probe,
        generated_at_utc=generated_at_utc,
        probe_config={
            'manifest': f'{probe_directory}/manifest.json',
            'run': dict(run_config or {}),
        },
    )

    _append_run_log(
        waveform_root,
        generated_at_utc=generated_at_utc,
        source_session=source_session,
        source_probe=source_probe,
        unit_scope=unit_scope,
        unit_count=len(unit_rows),
        output_dir=analysis_dir,
    )
    return analysis_dir


def export_spike_position_data(
        output_dir: str | Path,
        *,
        source_session: str,
        source_probe: str,
        source_kilosort_dir: str | Path,
        source_raw_file: str | Path,
        only_good_units: bool,
        unit_ids: IntArray,
        unit_locations: FloatArray,
        unit_location_feature: str,
        unit_location_radius_um: float,
        run_config: Mapping[str, Any] | None = None,
) -> Path:
    output_dir = Path(output_dir).expanduser()
    spike_position_root = spike_position_root_dir(output_dir)
    probe_directory = _probe_directory_name(source_probe)
    spike_position_dir = spike_position_root / probe_directory
    spike_position_dir.mkdir(parents=True, exist_ok=True)

    unit_scope = 'good' if only_good_units else 'all'
    position_rows = [
        [
            unit_index,
            int(unit_id),
            float(unit_locations[unit_index, 0]),
            float(unit_locations[unit_index, 1]),
        ]
        for unit_index, unit_id in enumerate(unit_ids)
    ]
    position_file = 'positions.csv'
    _write_unit_index(
        spike_position_dir / position_file,
        ['unit_index', 'unit_id', 'x_um', 'y_um'],
        position_rows,
    )

    generated_at_utc = datetime.now(timezone.utc).isoformat()
    spike_position_manifest = {
        'schema_name': 'rfmapping-spike-positions',
        'schema_version': 1,
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
    }
    _write_json(spike_position_dir / 'manifest.json', spike_position_manifest)
    _update_root_config(
        spike_position_root,
        schema_name='rfmapping-spike-position-config',
        probe_name=source_probe,
        generated_at_utc=generated_at_utc,
        probe_config={
            'manifest': f'{probe_directory}/manifest.json',
            'run': dict(run_config or {}),
        },
    )
    _append_run_log(
        spike_position_root,
        generated_at_utc=generated_at_utc,
        source_session=source_session,
        source_probe=source_probe,
        unit_scope=unit_scope,
        unit_count=len(position_rows),
        output_dir=spike_position_dir,
    )
    return spike_position_dir


def get_unit_info(base_dir: str | Path,
                  date: int | str,
                  session_id: str,
                  probe_name: str, *,
                  session_dir: Path | None = None,
                  output_dir: Path | None = None,
                  only_good_units: bool = True,
                  unit_waveform: bool = True,
                  unit_position: bool = True,
                  overwrite_waveform_analyzer: bool = False,
                  pre_spike_ms: float = 0.5,
                  post_spike_ms: float = 1.5,
                  max_spikes_per_unit: int = 2_000,
                  n_jobs: int = 32):

    print(f"----- Working on Probe{probe_name} -----")
    base_dir = Path(base_dir)
    date = str(date)
    session_dir = Path(session_dir) if session_dir is not None else base_dir / date / f'{date}_{session_id}'
    output_dir = Path(output_dir) if output_dir is not None else session_dir / 'data'


    waveform_seed: int = 0

    raw_num_channels: int = 384
    raw_dtype: Literal['int16'] = 'int16'
    raw_sampling_frequency: float = 30_000.0
    gain_to_uv: float = 0.195
    offset_to_uv: float = 0.0
    probe_contact_radius_um: float = 6.0
    unit_location_feature: Literal['ptp'] = 'ptp'
    unit_location_radius_um: float = 75.0

    kilosort_run: str = f'kilosort_{session_id}'
    kilosort_dir: Path = session_dir / 'kilosort' / f'Probe{probe_name}' / kilosort_run
    raw_file = next((session_dir / session_dir.parent.name)
                    .glob(f"*/experiment1/recording1/continuous/OneBox-*.Probe{probe_name}/continuous.dat"))

    waveform_analyzer_folder: Path = (
            session_dir / 'data' / 'spikeinterface_analyzer' / f'Probe{probe_name}'
    )

    validated_data = validate_data(
        recording_file=raw_file,
        kilosort_dir=kilosort_dir,
    )

    sorting: si.BaseSorting = se.read_kilosort(kilosort_dir, keep_good_only=only_good_units)
    unit_ids: IntArray = sorting.unit_ids
    channel_map: IntArray = validated_data.channel_map
    channel_positions: FloatArray = validated_data.channel_positions
    channel_shank_ids: IntArray = validated_data.channel_shank_ids

    recording: si.BaseRecording = si.read_binary(
        file_paths=raw_file,
        sampling_frequency=raw_sampling_frequency,
        dtype=raw_dtype,
        num_channels=raw_num_channels,
        gain_to_uV=gain_to_uv,
        offset_to_uV=offset_to_uv,
        is_filtered=False,
    )
    recording = recording.select_channels(channel_map)
    probe = Probe(ndim=2, si_units='um')
    probe.set_contacts(
        channel_positions,
        shapes='circle',
        shape_params={'radius': probe_contact_radius_um},
    )
    probe.set_device_channel_indices(np.arange(raw_num_channels))
    recording = recording.set_probe(probe)
    recording_duration_minutes: float = recording.get_total_duration() / 60.0

    selection_params = {
        'method': 'uniform',
        'max_spikes_per_unit': max_spikes_per_unit,
        'margin_size': None,
        'seed': waveform_seed,
    }

    waveform_analyzer: si.SortingAnalyzer
    if waveform_analyzer_folder.exists() and not overwrite_waveform_analyzer:
        waveform_analyzer = si.load_sorting_analyzer(waveform_analyzer_folder)
        if not np.array_equal(waveform_analyzer.unit_ids, sorting.unit_ids):
            raise ValueError('Cached analyzer unit IDs do not match the current sorting.')
        if not np.array_equal(waveform_analyzer.channel_ids, recording.channel_ids):
            raise ValueError('Cached analyzer channel IDs do not match the current recording.')
        if waveform_analyzer.sparsity is not None:
            raise ValueError('Cached analyzer is sparse; rebuild it with overwrite_waveform_analyzer = True.')
        print(f'Loaded waveform analyzer: {waveform_analyzer_folder}')
    else:
        waveform_analyzer_folder.parent.mkdir(parents=True, exist_ok=True)
        waveform_analyzer = si.create_sorting_analyzer(
            sorting,
            recording,
            format='binary_folder',
            folder=waveform_analyzer_folder,
            sparse=False,
            overwrite=overwrite_waveform_analyzer,
            n_jobs=n_jobs,
            chunk_duration='1s',
            progress_bar=True,
        )

    selection_extension = waveform_analyzer.get_extension('random_spikes')
    if not isinstance(selection_extension, si.ComputeRandomSpikes) or selection_extension.params != selection_params:
        waveform_analyzer.compute('random_spikes', **selection_params)
        selection_extension = waveform_analyzer.get_extension('random_spikes')

    template_extension = waveform_analyzer.get_extension('templates')
    template_params_match = (
            isinstance(template_extension, si.ComputeTemplates)
            and template_extension.params.get('operators') == ['average']
            and template_extension.params.get('ms_before') == pre_spike_ms
            and template_extension.params.get('ms_after') == post_spike_ms
    )
    if not template_params_match:
        waveform_analyzer.compute(
            'templates',
            ms_before=pre_spike_ms,
            ms_after=post_spike_ms,
            operators=['average'],
            n_jobs=n_jobs,
            chunk_duration='1s',
            progress_bar=True,
        )
        template_extension = waveform_analyzer.get_extension('templates')

    selected_spikes: StructuredArray = selection_extension.get_random_spikes()

    print("Analyzer Done")
    print("--------------------")

    template_array: FloatArray = template_extension.get_templates(operator='average')
    time_ms: FloatArray = (
                                  np.arange(template_array.shape[1]) - template_extension.nbefore
                          ) / waveform_analyzer.sampling_frequency * 1_000.0
    template_ptp_summary = compute_template_ptp_summary(template_array)
    channel_locations: FloatArray = waveform_analyzer.get_channel_locations()

    run_config = {
        'output_dir': str(output_dir),
        'waveform_analyzer_folder': str(waveform_analyzer_folder),
        'n_jobs': n_jobs,
        'overwrite_waveform_analyzer': overwrite_waveform_analyzer,
        'raw_dtype': raw_dtype,
        'raw_num_channels': raw_num_channels,
        'gain_to_uv': gain_to_uv,
        'offset_to_uv': offset_to_uv,
        'probe_contact_radius_um': probe_contact_radius_um,
    }

    if unit_waveform:
        print("Gen unit waveform")

        exported_waveform_dir: Path = export_waveform_data(
            output_dir,
            source_session=session_dir.name,
            source_probe=probe_name,
            source_kilosort_dir=kilosort_dir,
            source_raw_file=raw_file,
            only_good_units=only_good_units,
            sorting=sorting,
            unit_ids=unit_ids,
            selected_spikes=selected_spikes,
            template_array=template_array,
            template_ptp_summary=template_ptp_summary,
            channel_ids=np.asarray(waveform_analyzer.channel_ids),
            channel_locations=channel_locations,
            channel_shank_ids=channel_shank_ids,
            sampling_frequency=raw_sampling_frequency,
            recording_num_frames=recording.get_num_frames(),
            recording_duration_minutes=recording_duration_minutes,
            time_ms=time_ms,
            nbefore=template_extension.nbefore,
            pre_spike_ms=pre_spike_ms,
            post_spike_ms=post_spike_ms,
            max_spikes_per_unit=max_spikes_per_unit,
            waveform_seed=waveform_seed,
            run_config=run_config,
        )
        print(f'Exported waveform data: {exported_waveform_dir}')
        print("--------------------")

    if unit_position:
        print("Gen unit position")

        location_sparsity_mask = np.zeros(
            (len(unit_ids), waveform_analyzer.get_num_channels()),
            dtype=bool,
        )
        for unit_index in range(len(unit_ids)):
            best_channel_index = int(template_ptp_summary.best_channel_indices[unit_index])
            distances = np.linalg.norm(
                channel_locations - channel_locations[best_channel_index],
                axis=1,
            )
            location_sparsity_mask[unit_index] = distances <= unit_location_radius_um

        location_sparsity = si.ChannelSparsity(
            mask=location_sparsity_mask,
            unit_ids=unit_ids,
            channel_ids=waveform_analyzer.channel_ids,
        )
        dense_location_templates = si.Templates(
            templates_array=template_array,
            sampling_frequency=waveform_analyzer.sampling_frequency,
            nbefore=template_extension.nbefore,
            is_in_uV=True,
            channel_ids=waveform_analyzer.channel_ids,
            unit_ids=unit_ids,
            probe=waveform_analyzer.get_probe(),
        )
        unit_locations: FloatArray = compute_center_of_mass(
            dense_location_templates.to_sparse(location_sparsity),
            feature=unit_location_feature,
        )

        exported_spike_position_dir: Path = export_spike_position_data(
            output_dir,
            source_session=session_dir.name,
            source_probe=probe_name,
            source_kilosort_dir=kilosort_dir,
            source_raw_file=raw_file,
            only_good_units=only_good_units,
            unit_ids=unit_ids,
            unit_locations=unit_locations,
            unit_location_feature=unit_location_feature,
            unit_location_radius_um=unit_location_radius_um,
            run_config=run_config,
        )
        print(f'Exported unit positions: {exported_spike_position_dir}')

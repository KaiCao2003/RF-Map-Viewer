from pathlib import Path
import numpy as np


def validate_data(
        *,
        save_figures: bool,
        export_unit_locations: bool,
        kilosort_dir: str | Path,
        recording_file: str | Path,
        output_dir: str | Path | None = None,
) -> tuple[bool, dict]:
    raw_dtype = "int16"
    raw_num_channels = 384

    kilosort_dir = Path(kilosort_dir)
    recording_file = Path(recording_file)
    output_dir = Path(output_dir) if output_dir is not None else None

    result: dict = {
        "kilosort_directory": str(kilosort_dir),
        "raw_recording": str(recording_file),
        "files": {},
    }

    try:
        required_files: tuple[str, ...] = (
            "params.py",
            "spike_times.npy",
            "spike_clusters.npy",
            "channel_map.npy",
            "channel_positions.npy",
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
            raise FileNotFoundError(
                f"Raw file not found: {recording_file}"
            )

        log_file = kilosort_dir / "kilosort4.log"
        if log_file.is_file():
            lines = log_file.read_text(errors="replace").splitlines()
            if lines:
                result["kilosort_log_first_line"] = lines[0]

        bytes_per_frame = np.dtype(raw_dtype).itemsize * raw_num_channels
        raw_byte_count = recording_file.stat().st_size

        if raw_byte_count % bytes_per_frame != 0:
            raise ValueError(
                "Raw binary size is not divisible by "
                "dtype size × channel count."
            )

        raw_frame_count = raw_byte_count // bytes_per_frame

        result["raw_dtype"] = raw_dtype
        result["raw_num_channels"] = raw_num_channels
        result["raw_byte_count"] = raw_byte_count
        result["raw_frame_count"] = raw_frame_count

        for name in required_files:
            if name.endswith(".npy"):
                array = np.load(
                    kilosort_dir / name,
                    mmap_mode="r",
                    allow_pickle=False,
                )

                result["files"][name] = {
                    "shape": array.shape,
                    "dtype": str(array.dtype),
                }

        if save_figures or export_unit_locations:
            if output_dir is None:
                raise ValueError(
                    "output_dir is required when save_figures or "
                    "export_unit_locations is True."
                )

            output_dir.mkdir(parents=True, exist_ok=True)
            result["output_directory"] = str(output_dir)

        result["error"] = None
        return True, result

    except (FileNotFoundError, ValueError, OSError) as error:
        result["error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        return False, result

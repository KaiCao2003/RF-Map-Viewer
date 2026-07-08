import json
from pathlib import Path


class Session:
    def __init__(self, session_path: str | Path):
        """OpenEphys session descriptor loader.

        Accepts flexible inputs:
        - A full path to ``structure.oebin`` (or ``structure.json``)
        - A path to the session folder (containing one or more Record Node dirs)
        - A path to a specific Record Node (with or without ``record_nodes`` provided)
        - A path to an experiment/recording folder inside a Record Node
        """
        self.file_path = self._resolve_structure_file(Path(session_path))
        self._parse_session_file()

    @staticmethod
    def _resolve_structure_file(base: Path) -> Path:
        if not base.exists():
            raise FileNotFoundError(f"Session path does not exist: {base!s}")

        descriptor_suffixes = {".json", ".oebin"}

        # Direct descriptor provided
        if base.is_file():
            if base.suffix in descriptor_suffixes:
                print(f"Loading session from file: {base!s}")
                return base
            raise ValueError(
                "Unsupported session descriptor file. Expected 'structure.oebin' or "
                "'structure.json', got: "
                f"{base.name}"
            )

        if not base.is_dir():
            raise ValueError(f"Unsupported session path type: {base!s}")

        # Directory provided – look for the descriptor recursively.
        matches = sorted(
            {
                match
                for pattern in ("structure.oebin", "structure.json")
                for match in base.rglob(pattern)
            },
            key=lambda p: str(p)
        )

        if not matches:
            raise FileNotFoundError(
                "Could not locate 'structure.oebin' or 'structure.json' within "
                f"{base!s}. Ensure you passed a session, Record Node, or recording folder."
            )

        if len(matches) > 1:
            pretty = "\n - ".join(str(m) for m in matches)
            raise ValueError(
                "Multiple session descriptor files found. Please specify the "
                "exact Record Node/recording path."
                f"\n - {pretty}"
            )

        print(f"Loading session from file: {matches[0]!s}")
        return matches[0]

    def get_session_info(self) -> dict:
        info = {
            'base_path': self.base_path,
            "session_name": self.file_path,
            "experiment_id": self.experiment_id,
            "version": self.version,
            "recording_name": self.recording_name,
            "continuous_ADC_folder": self.continuous_ADC_folder,
            "record_nodes": self.record_nodes,
            "ADC_sample_rate": self.ADC_sample_rate,
            "ADC_input_channel": self.ADC_input_channel,
            "events_ADC_folder": self.events_ADC_folder,
            "probe_count": self.probe_count,
        }

        if getattr(self, "continuous_probe_A_folder", None):
            info.update({
                "continuous_probe_A_folder": self.continuous_probe_A_folder,
                "probe_A_sample_rate": getattr(self, "probe_A_sample_rate", None),
                "probe_A_input_channel": getattr(self, "probe_A_input_channel", None),
                "events_probe_A_folder": getattr(self, "events_probe_A_folder", None),
            })

        if getattr(self, "continuous_probe_B_folder", None):
            info.update({
                "continuous_probe_B_folder": self.continuous_probe_B_folder,
                "probe_B_sample_rate": getattr(self, "probe_B_sample_rate", None),
                "probe_B_input_channel": getattr(self, "probe_B_input_channel", None),
                "events_probe_B_folder": getattr(self, "events_probe_B_folder", None),
            })
        if getattr(self, "continuous_probe_C_folder", None):
            info.update({
                "continuous_probe_C_folder": self.continuous_probe_C_folder,
                "probe_C_sample_rate": getattr(self, "probe_C_sample_rate", None),
                "probe_C_input_channel": getattr(self, "probe_C_input_channel", None),
                "events_probe_C_folder": getattr(self, "events_probe_C_folder", None),
            })
        if getattr(self, "continuous_probe_D_folder", None):
            info.update({
                "continuous_probe_D_folder": self.continuous_probe_D_folder,
                "probe_D_sample_rate": getattr(self, "probe_D_sample_rate", None),
                "probe_D_input_channel": getattr(self, "probe_D_input_channel", None),
                "events_probe_D_folder": getattr(self, "events_probe_D_folder", None),
            })


        return info

    def _parse_session_file(self) -> None:
        with open(self.file_path, "r") as f:
            data = json.load(f)

        self.base_path = str(Path(*self.file_path.parts[:-4]))
        self.experiment_id = str(self.file_path.parts[-3])
        self.recording_name: str = self.file_path.parts[-2]

        self.version = data.get("GUI version")

        *probes, self.continuous_ADC_folder = [
            e.get("folder_name").rstrip("/\\") for e in data.get("continuous")
        ]
        self.probe_count: int = len(probes)

        self.continuous_probe_A_folder, self.continuous_probe_B_folder, self.continuous_probe_C_folder, self.continuous_probe_D_folder = (
                probes + [None, None, None, None])[:4]

        self.record_nodes: str = (
                "Record Node " + str(data.get("continuous")[0].get("recorded_processor_id"))
        )

        *probes, self.ADC_sample_rate = [
            e.get("sample_rate") for e in data.get("continuous")
        ]
        self.probe_A_sample_rate, self.probe_B_sample_rate, self.probe_C_sample_rate, self.probe_D_sample_rate = (probes + [None, None, None, None])[:4]

        *probes, self.ADC_input_channel = [
            e.get("num_channels") for e in data.get("continuous")
        ]
        self.probe_A_input_channel, self.probe_B_input_channel, self.probe_C_input_channel, self.probe_D_input_channel = (
                probes + [None, None, None, None])[:4]

        *probes, self.events_ADC_folder = [
            e.get("folder_name").rstrip("/\\") for e in data.get("events")
        ]
        self.events_probe_A_folder, self.events_probe_B_folder, self.events_probe_C_folder, self.events_probe_D_folder = (
                probes + [None, None, None, None])[:4]

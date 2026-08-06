import argparse
from pathlib import Path

from Utils.si_utils import get_unit_info

parser = argparse.ArgumentParser()
parser.add_argument('base_dir', type=Path)
parser.add_argument('date')
parser.add_argument('session_id')
parser.add_argument('probeList')
args = parser.parse_args()

base_dir: Path = args.base_dir
date: str = args.date
session_id: str = args.session_id
probe_list: str = args.probeList
data_dir: Path = base_dir / date / f'{date}_{session_id}' / 'data'  # Parent of waveform/ and spike_position/; set another root to export elsewhere.
only_good_units: bool = True

for probe in probe_list:
    get_unit_info(
        base_dir=base_dir,
        probe_name=probe,
        date=date,
        session_id=session_id,
        output_dir=data_dir,
        only_good_units=only_good_units,)

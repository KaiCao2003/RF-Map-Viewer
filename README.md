# RF Mapping Viewers

This repository contains the display and export applications for RF mapping
data. It is self-contained: none of its runtime paths import the sibling
`../rfmapping` analysis repository.

| Implementation | Directory | Primary entry point |
| --- | --- | --- |
| Python/Tk FM preview | `python/` | `python/rfmapping_fm_gui.py` |
| SwiftUI | `swift/` | `swift/Package.swift` |
| Web | `web/` | FastAPI under `web/backend/`; React under `web/frontend/` |

Scientific RF detection, trial reconstruction, notebooks, and Matlab-related
sources remain in `../rfmapping`. The two repositories communicate only
through versioned file contracts, principally the RF JSON described in
[`contracts/rf-json.md`](contracts/rf-json.md).

## Python free-moving preview

Python version **1.10.0.1** is the **freemoving rf viewer preview**. It accepts
only HDF5 `.rfmap` files with `format=rfmapping_fm_hdf5_v1`, displays the
head-centric elevation/azimuth firing-rate result, and exposes exposure and
calibration QA. Legacy JSON, tuning-curve, head-direction, and probe companions
are intentionally outside this preview app. Swift and Web remain unchanged.

## Legacy file compatibility

The unchanged Swift and Web implementations retain these aliases:

| Data | Preferred extension | Existing extension |
| --- | --- | --- |
| RF map (JSON) | `.rfmap` | `.json` |
| Tuning curve (JSON) | `.tc` | `.json` |
| Spike positions (CSV) | `.probe` | `.csv` |

When both companion names exist, `tuning_curves.tc` and `positions.probe` take
precedence over `tuning_curves.json` and `positions.csv`. Probe channel geometry
continues to use `channels.csv`. An RF map is the primary document; tuning and
probe files are opened as companions of a loaded RF map.

## Remote validation

Project code is run on `hhw9l84` with `~/.virtualenvs/rfmapping`:

```sh
ssh hhw9l84 'cd ~/Developer/rfmapping_gui/python && \
  PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q \
    --ignore=tests/test_rfmapping_gui_tk.py'

ssh hhw9l84 'cd ~/Developer/rfmapping_gui/web && \
  PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q'

ssh hhw9l84 'cd ~/Developer/rfmapping_gui/web/frontend && \
  npm ci --no-audit --no-fund && npm test && npm run build'
```

The remote host is Linux and has no display, so it validates the Python HDF5
model, aggregation, release scripts, and non-GUI smoke path. A real Tk launch
or signed Python bundle requires a Tk-enabled Apple-silicon Mac; the Python
bundle has a macOS 14.0 deployment minimum.

See the implementation READMEs for target-specific install, build, and release
commands.

# RF Mapping Viewers

This repository contains the display and export applications for RF mapping
data. It is self-contained: none of its runtime paths import the sibling
`../rfmapping` analysis repository.

| Implementation | Directory | Primary entry point |
| --- | --- | --- |
| Python/Tk stable | `python/` | `python/rfmapping_gui.py` |
| Python/Tk Free-Moving alpha | `python/` | `python/rfmapping_fm_gui.py` |
| SwiftUI | `swift/` | `swift/Package.swift` |
| Web | `web/` | FastAPI under `web/backend/`; React under `web/frontend/` |

Scientific RF detection, trial reconstruction, notebooks, and Matlab-related
sources remain in `../rfmapping`. The two repositories communicate only
through versioned file contracts, principally the RF JSON described in
[`contracts/rf-json.md`](contracts/rf-json.md).

## Component versions

Python, Swift, and Web implement the same stable feature generation and are
versioned `1.9.6`.
The Free-Moving Python viewer begins the next generation as
**`1.10.0-alpha.3`**. Component identity belongs in release tags and artifact
names, not in a fourth version component. See
[`release/README.md`](release/README.md) for the canonical mapping and tag
policy.

## Downloads

This repository was recreated from a reviewed source snapshot on 2026-09-07.
Previous release attachments were not imported. Source and build instructions
are available in the implementation directories; new downloads will appear on
the [Releases page](https://github.com/KaiCao2003/RF-Map-Viewer/releases) after a
separate privacy review. See [PRIVACY.md](PRIVACY.md) before migrating an old clone
or publishing a package.

## Python free-moving alpha

Python **1.10.0-alpha.3** is the **freemoving rf viewer alpha**. Before opening
a file, the user explicitly chooses **Square** or **Bar**. The viewer accepts
the matching HDF5 contract (`rfmapping_fm_hdf5_v1` for Square or
`rfmapping_fm_bar_hdf5_v1` for the latest full-height vertical Bar analysis)
and rejects a mismatched choice. Both formats display the head-centric
elevation/azimuth firing-rate result in a 2D equirectangular map and an
interactive 3D sphere, with exposure and calibration QA. Bar files also show
the recorded widths pooled by the analysis. A singleton-elevation 2D map uses
the legacy `30:7` visual footprint; the physical 3D sphere is unchanged. Drag
the sphere to rotate the viewing direction or double-click to reset it. Legacy
JSON, tuning-curve, head-direction, and probe companions are intentionally
outside this alpha app. The stable Python viewer remains available separately
at `1.9.6`; Swift and Web use the same stable version.

## Current RF format and filename aliases

Stable version 1.9.6 requires the current raw-count plus
`occupancyTimeSec` RF schema written by `Utils/RFmapping_core.m`. Earlier RF
payloads without occupancy metadata, including the previously normalized
vertical-bar format, are intentionally unsupported. Firing rate is the default
display value so unequal spatial occupancy does not bias the RF map.

The current payload and companion documents retain these filename aliases:

| Data | Preferred extension | Existing extension |
| --- | --- | --- |
| RF map (JSON) | `.rfmap` | `.json` |
| Tuning curve (JSON) | `.tc` | `.json` |
| Spike positions (CSV) | `.probe` | `.csv` |

When both companion names exist, `tuning_curves.tc` and `positions.probe` take
precedence over `tuning_curves.json` and `positions.csv`. Probe channel geometry
continues to use `channels.csv`. An RF map is the primary document; tuning and
probe files are opened as companions of a loaded RF map.

Python, Swift, and Web `1.9.6` accept MATLAB numeric scalars
for a declared singleton `xPositions` or `yPositions` axis. Singleton-y maps
use a `30:7` Cartesian footprint and a seven-unit Polar ring; multirow data and
all scientific indices remain unchanged.

Python, Swift, and Web `1.9.6` discover the companion schema-v4
SpikeInterface waveform artifact and expose its notebook-equivalent nearest
channels in the viewer and Figure Composer. All three use exact positive
tuning-session selection (default `1`), `P` for rectangle/polar layout, and
`Shift-P` for palette cycling.

The stable viewers also share the Python responsiveness improvements: document
loads discard superseded selections, repeated temporal calculations reuse
cached results, and titles identify the current file. Swift reuses grouped
count windows and pooled occupancy across its plots. Web stores count caches
as compact integers and coalesces canvas draws. Windows uses the same Python
source as macOS; both packaging smoke tests exercise the spawned loader.
Plot range continues to affect only the 2-D RF display, while timelines retain
the full time axis.

## Remote validation

Project code is run on `RFMAPPING_REMOTE_HOST` from your untracked `.env.local` with `~/.virtualenvs/rfmapping`:

```sh
ssh "$RFMAPPING_REMOTE_HOST" 'cd ~/Developer/rfmapping_gui/python && \
  PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q \
    --ignore=tests/test_rfmapping_gui_tk.py'

ssh "$RFMAPPING_REMOTE_HOST" 'cd ~/Developer/rfmapping_gui/web && \
  PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q'

ssh "$RFMAPPING_REMOTE_HOST" 'cd ~/Developer/rfmapping_gui/web/frontend && \
  npm ci --no-audit --no-fund && npm test && npm run build'
```

The remote host is Linux and has no display, so it validates the Python HDF5
model, aggregation, release scripts, and non-GUI smoke path. A real Tk launch
or signed Python bundle requires a Tk-enabled Apple-silicon Mac; the Python
bundle has a macOS 14.0 deployment minimum.

See the implementation READMEs for target-specific install, build, and release
commands.

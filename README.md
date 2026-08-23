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

The Python viewer is the stable feature reference at `1.9.4`. Swift and Web
implement the same `1.9` feature generation and are versioned `1.9.1`.
The Free-Moving Python viewer begins the next generation as
**`1.10.0-alpha.3`**. Component identity belongs in release tags and artifact
names, not in a fourth version component. See
[`release/README.md`](release/README.md) for the canonical mapping and tag
policy.

## Downloads

The current component releases are published independently so each viewer can
advance without inventing a platform-specific fourth version number.

| Viewer | Channel | Download | Release notes |
| --- | --- | --- | --- |
| Python RF Map Viewer `1.9.4` | Stable | [macOS Apple-silicon ZIP](https://github.com/KaiCao2003/RF-Map-Viewer/releases/download/python-v1.9.4/RF_Map_Viewer-python-1.9.4-full-macos-arm64.zip) | [`python-v1.9.4`](https://github.com/KaiCao2003/RF-Map-Viewer/releases/tag/python-v1.9.4) |
| Python Free-Moving `1.10.0-alpha.3` | Alpha preview | [macOS Apple-silicon ZIP](https://github.com/KaiCao2003/RF-Map-Viewer/releases/download/python-v1.10.0-alpha.3/Free_Moving_RF_Viewer-python-1.10.0-alpha.3-freemoving-macos-arm64.zip) | [`python-v1.10.0-alpha.3`](https://github.com/KaiCao2003/RF-Map-Viewer/releases/tag/python-v1.10.0-alpha.3) |
| Swift `1.9.1` | Stable | [macOS Apple-silicon ZIP](https://github.com/KaiCao2003/RF-Map-Viewer/releases/download/swift-v1.9.1/RF_Map_Viewer-1.9.1-swift-macos-arm64.zip) | [`swift-v1.9.1`](https://github.com/KaiCao2003/RF-Map-Viewer/releases/tag/swift-v1.9.1) |
| Web `1.9.1` | Stable | [deployment source archive](https://github.com/KaiCao2003/RF-Map-Viewer/releases/download/web-v1.9.1/RF_Map_Viewer-1.9.1-web.tar.gz) | [`web-v1.9.1`](https://github.com/KaiCao2003/RF-Map-Viewer/releases/tag/web-v1.9.1) |

Each Release also includes a SHA-256 checksum file. The Free-Moving build is a
preview and is deliberately marked as a GitHub prerelease. Browse every
published version on the [Releases page](https://github.com/KaiCao2003/RF-Map-Viewer/releases).

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
at `1.9.4`; Swift and Web remain on the `1.9` generation.

## Legacy file compatibility

All stable implementations retain these aliases:

| Data | Preferred extension | Existing extension |
| --- | --- | --- |
| RF map (JSON) | `.rfmap` | `.json` |
| Tuning curve (JSON) | `.tc` | `.json` |
| Spike positions (CSV) | `.probe` | `.csv` |

When both companion names exist, `tuning_curves.tc` and `positions.probe` take
precedence over `tuning_curves.json` and `positions.csv`. Probe channel geometry
continues to use `channels.csv`. An RF map is the primary document; tuning and
probe files are opened as companions of a loaded RF map.

Python `1.9.4`, Swift `1.9.1`, and Web `1.9.1` accept MATLAB numeric scalars
for a declared singleton `xPositions` or `yPositions` axis. Singleton-y maps
use a `30:7` Cartesian footprint and a seven-unit Polar ring; multirow data and
all scientific indices remain unchanged.

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

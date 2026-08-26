# Python RF Map Viewers

This directory contains two separately versioned applications:

- `rfmapping_gui.py`: the stable RF Map Viewer `1.9.5`;
- `rfmapping_fm_gui.py`: the Free-Moving RF Viewer `1.10.0-alpha.3`.

They have distinct app names, bundle identifiers, release artifacts, and tags,
so the alpha can be installed and released without replacing the stable app.

## Stable viewer 1.9.5

The stable viewer opens the current JSON-text RF `.rfmap` contract and its
tuning-curve, probe, and waveform companions. Version 1.9.5 requires raw non-negative
integer `unitsSpikeCounts` together with the matching spatial
`occupancyTimeSec` map and the current response-definition fields written by
`RFmapping_core.m`. Older RF documents without occupancy metadata are rejected
instead of being interpreted heuristically. At least one spatial cell must
have positive occupancy.

The default RF value is mean firing rate in Hz: counts in the selected response
window are divided by spatial occupancy seconds. Spatial rebinning and
smoothing pool counts and occupancy independently before division. Raw spike
count remains available as the other value mode. MATLAB `jsonencode` numeric
scalars are restored for singleton `unitPool`, `xPositions`, `yPositions`, and
`occupancyTimeSec` dimensions. A singleton-y RF map keeps the `30:7` Cartesian
footprint and seven-unit Polar ring across live plots, timeline thumbnails, hit
testing, and figure exports.

The RF tab can show a compact **Local Average Waveform** panel in the left
sidebar, directly below **Spike Time**. **Unit Info** stays at the bottom-right
below the HD tuning curve. The viewer auto-discovers the read-only schema-v4 SpikeInterface
artifact at `data/waveform/ProbeA` or `ProbeB` and shows the selected unit's
baseline-corrected average template on the best-PTP channel plus the four
nearest channels. **Settings → Waveform** controls whether the panel is shown
and switches between `Same x column` and `Same shank`; both modes intentionally
match the notebook's nearest-four selector rather than forcing two channels
above and two below. Figure Composer exports the same payload with a shared
symmetric µV scale across selected units and records the manifest, metadata
tables, and selected template files in provenance.

Run it from source with:

```sh
~/.virtualenvs/rfmapping/bin/python rfmapping_gui.py /path/to/result.rfmap
```

Opening the app without a path shows the native file chooser. Release packages
do not contain or auto-load sample RF data.

Its macOS identity is `RF Map Viewer.app`, bundle ID
`org.local.rfmapping.viewer`, and version/build `1.9.5` / `10906`. Build it with:

```sh
script/build_python_stable_macos_app.sh
```

The same Python release is packaged for Windows x64 as a portable ZIP and an
Inno Setup installer. On a Windows build host with Python 3.14, PyInstaller,
and Inno Setup 6 installed, build and smoke-test both artifacts with:

```powershell
script/build_python_stable_windows_app.ps1
```

The versioned outputs are written under `dist/windows/`; the builder verifies
the portable executable and a silent temporary installation with the RF
fixture, TkDND, and packaged PDF/PNG/CSV export smoke tests.

## Free-Moving alpha 1.10.0-alpha.3

> **freemoving rf viewer alpha**

This Python/Tk application is a read-only viewer for the HDF5 `.rfmap` files
written by `RFmapping_core_fm.m` and `rfmapping_core_fm_bar.m`. Version
**1.10.0-alpha.3** is intentionally a separate alpha application: it does not
open legacy RF JSON, tuning curves, probe files, or head-direction companions.

### What it shows

- one unit at a time from `/rf/rate_hz`;
- an explicit **Square / Bar** choice before any file is loaded;
- strict matching of Square `rfmapping_fm_hdf5_v1` and vertical-Bar
  `rfmapping_fm_bar_hdf5_v1` files;
- head-centric azimuth `[-180, 180)` and elevation `[-90, 90]`;
- switchable 2D equirectangular and interactive 3D spherical RF views;
- a legacy `30:7` visual footprint for singleton-elevation 2D maps, without
  changing the physical 3D sphere;
- drag-to-rotate 3D navigation with a deterministic front-view reset;
- a continuously adjustable half-open response window;
- time-weighted mean firing rate in Hz;
- exposure and effective-trial QA maps;
- a spatial-mean response timeline; and
- embedded cylinder, rigid-body, viewpoint, and input provenance.

The Bar loader additionally validates
`stimulus_geometry=vertical_bar_full_source_height`, pooled recorded bar
widths, and the latest Bar format contract. Both loaders validate
`logical_dimension_order=unit,elevation,azimuth,time`, the completion marker,
the embedded `rf-calib-1.0` document, and MATLAB's reversed on-disk HDF5
dimension order. Only the selected unit is read from the large rate dataset.

### Install and run from source

Project validation is performed on `hhw9l84`:

```sh
ssh hhw9l84
cd ~/Developer/rfmapping_gui/python
~/.virtualenvs/rfmapping/bin/pip install -e '.[test]'
~/.virtualenvs/rfmapping/bin/python rfmapping_fm_gui.py /path/to/result.rfmap
```

The app asks **Square or Bar** before opening a file selected from the picker,
Finder Open With, or drag-and-drop. For an explicit noninteractive launch, pass
`--stimulus square` or `--stimulus bar` with the path.

### Validate

```sh
cd ~/Developer/rfmapping_gui/python
PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q \
  --ignore=tests/test_rfmapping_gui_tk.py
PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python \
  rfmapping_fm_gui.py --stimulus square --self-test /path/to/result.rfmap
```

The remote Linux host validates the HDF5 model and non-GUI behavior. A complete
release additionally requires the Tk/TkDND smoke test on the Apple-silicon
build host.

### macOS alpha identity

- App: `Free-Moving RF Viewer.app`
- Bundle ID: `org.local.rfmapping.viewer.freemoving`
- Release: `1.10.0-alpha.3`
- Apple version/build: `1.10.0` / `110003`
- Python package version: `1.10.0a3`
- Edition: `FreeMovingAlpha`
- Minimum system: macOS 14.0, Apple silicon

The distinct name and bundle ID allow this alpha to be installed alongside
the stable full RF Map Viewer. Build and inspect it with:

```sh
script/build_python_macos_app.sh
script/install_python_macos_app.sh --preflight
```

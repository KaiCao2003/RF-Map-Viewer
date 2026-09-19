# Python RF Map Viewers

This directory contains two separately versioned applications:

- `rfmapping_gui.py`: the stable RF Map Viewer `1.10.1`;
- `rfmapping_fm_gui.py`: the Free-Moving RF Viewer `1.10.0-alpha.3`.

They have distinct app names, bundle identifiers, release artifacts, and tags,
so the alpha can be installed and released without replacing the stable app.

## Stable viewer 1.10.1

Version 1.10.1 restores macOS keyboard navigation, gives newly opened documents
focus on their visible plot, and returns keyboard focus to clicked viewer
controls after editing a field. Caps Lock preserves F/D/P actions; Shift+P
selects palette cycling. Native arrow-key keypad/Fn flags do not suppress
navigation. Colored RF exports retain the live view's zero-based color scale.
Missing-occupancy cells stay missing during temporal smoothing; sampled zero
responses still contribute normally. Raw-count schema markers are validated,
and large integral IDs and count sums retain their precision. Unit-picker
refreshes reuse visibility results; probe filtering, paired selections after
background loading, and Auto tuning orientation remain synchronized.

Version 1.10.0 adds indexed RF input and progressive caching.

Version 1.9.9 separates data, display calculations, settings, and figure
composition into focused modules. Stable regression tests share synthetic
fixtures and run once through the same command in remote validation and
macOS PR checks.

Version 1.9.8 shares Delay/RGB calculations between the viewer and Figure
Composer, preserving the first equal peak after smoothing and distinguishing
black zero-response cells from gray, marked cells with no occupancy. Temporal
results use a bounded cache, spatial display controls avoid redrawing companion
panels, and waveform navigation keeps one active read plus the latest pending
unit. The stable macOS package excludes the separate Free-Moving/HDF5 modules.

The stable viewer accepts legacy JSON `.rfmap`/`.json` files and version-2
indexed NPZ `.rfmap` files. It detects the file signature rather than requiring
a `.npz` extension. The indexed reader decodes shared metadata and the first
unit, displays the plot, then caches the rest off the Tk thread. A small
bottom-right progress bar reports cached units. Selecting an uncached unit
prioritizes it; repeated visits reuse immutable arrays. Multi-unit Figure
Composer is available after the cache completes. Errors preserve loaded units
and expose Retry; closing or replacing a document cancels pending reads.

Legacy JSON still loads the complete document. Indexed archives store UTF-8
JSON in a `uint8` `metadata` entry, plus `unitPool`, `xPositions`, `yPositions`,
`timeBinEdges`, `occupancyTimeSec`, and one `unit_<ID>` array per recorded ID.
Each unit has axes `(y, x, time)`, including singleton dimensions. The reader
requires `formatVersion=2`; no transpose, time-axis trimming, or rate conversion
is applied while reading. See the shared file contract for details.

Counts are raw non-negative integers. Occupancy dimensions come from the
y-by-x axes in `unitsSpikeCountsSize`. Each qualifying trial contributes once
per final spatial bin, and occupancy sums the qualifying trial durations.
At least one spatial cell must have positive occupancy.

The default RF value is mean firing rate in Hz: counts in the selected response
window are divided by spatial occupancy seconds. Spatial rebinning and
smoothing pool counts and occupancy independently before division. Raw spike
count remains available as the other value mode. MATLAB `jsonencode` numeric
scalars are restored for singleton `unitPool`, `xPositions`, `yPositions`, and
`occupancyTimeSec` dimensions. A singleton-y RF map keeps the `30:7` Cartesian
footprint and seven-unit Polar ring across live plots, timeline thumbnails, hit
testing, and figure exports.

The RF tab can show a compact **Local Average Waveform** panel in the left
sidebar. Double-clicking the waveform opens a large, in-window view; another
double-click, **Esc**, or **Done** restores the compact panel. **Unit Info** and
**Spike Time** share the bottom-right inspector below the HD tuning curve. The
viewer auto-discovers the read-only schema-v4 SpikeInterface
artifact at `data/waveform/ProbeA` or `ProbeB` and shows the selected unit's
baseline-corrected average template on the best-PTP channel plus the four
nearest channels. **Settings → Waveform** controls whether the panel is shown
and switches between `Same x column` and `Same shank`; both modes intentionally
match the notebook's nearest-four selector rather than forcing two channels
above and two below. Figure Composer exports the same payload with a shared
symmetric µV scale across selected units and records the manifest, metadata
tables, and selected template files in provenance.

**Settings → Tuning Curve → Tuning Curve Session** selects the positive
same-date session number used for automatic tuning-curve discovery. For an RF
file below `260730_3`, for example, a value of `2` reads only from
`260730_2/data/tuning_curves/ProbeA` or `ProbeB`; it does not fall back to a
different session. The default is session `1`.

Press **P** to switch the RF display between Rectangle and Polar layouts.
Press **Shift+P** to cycle the color palette.
These also work while the closed, read-only unit or value-mode picker has
focus. Editable fields and open pickers keep their normal key handling.

With a plot, plot tab, or toolbar button focused, **← / →** selects the
previous / next unit, **↑ / ↓** selects the timeline bin, and **1–3** switches
plot tabs. **Shift+,** makes the time resolution coarser and **Shift+.** makes
it finer, one source bin at a time, matching the Navigate menu. Input fields
keep their normal editing keys; Command, Control, and Option combinations do
not trigger the plain-key viewer actions.

Press **-** (or **View → Subtract RF Windows (A − B)**) to toggle between
the usual RF window sum and the difference of two independently adjustable
windows. In difference mode the RF controls read
**(start ms – end ms) − (start ms – end ms)**. For example, set A to
**80–160 ms** and B to **0–80 ms** (the initial difference defaults).
**Settings → RF Map → Timing** saves the default mode, the Sum window, and
both A − B windows. Switching modes restores that mode's last used range;
new windows start with the saved defaults, and **Reset** restores the active
mode's defaults. All windows snap to source-bin edges and exclude their end
edge. Each window uses the selected
metric and spatial pooling/smoothing before subtraction; negative differences
display as **NaN** in gray, while zero remains zero. The same result and both
windows are included in displayed-data CSV and Figure Composer exports.
Pair Windows also synchronizes the subtraction mode and both windows. The
zero-spike unit filter continues to use window A. RF windows do not restrict
the timeline or alter Delay / RGB.

Press **D** to show or hide **Display Options**; the expanded button reads
**Hide (D)**. These shortcuts leave text entry, including negative time
values, available while an input has focus.

Press **Command+Shift+.** (or use **View → Show Filtered Units / Hide Units
with Zero RF Bins**) to toggle the Settings zero-bin unit filter. It restores
or hides the affected units while preserving the current time windows,
palette, and other display controls. The filter preference is saved and
updated in open viewer and Settings windows. **Shift+.** alone still adjusts
the target time width.

Run it from source with:

```sh
~/.virtualenvs/rfmapping/bin/python rfmapping_gui.py /path/to/result.rfmap
```

Opening the app without a path shows the native file chooser. Release packages
do not contain or auto-load sample RF data.

Its macOS identity is `RF Map Viewer.app`, bundle ID
`org.local.rfmapping.viewer`, and version/build `1.10.1` / `110001`. Build it with:

```sh
script/build_python_stable_macos_app.sh
```

Stable 1.10.0 is also packaged for Windows x64 as a portable ZIP and an
Inno Setup installer (Windows build 11000). On a Windows build host with Python 3.14, PyInstaller,
and Inno Setup 6 installed, build and smoke-test both artifacts with:

```powershell
script/build_python_stable_windows_app.ps1
```

The versioned outputs are written under `dist/windows/`; the builder verifies
the portable executable and a silent temporary installation with the RF
fixture, TkDND, and packaged PDF/PNG/CSV export smoke tests. Both macOS and
Windows builders also run `--self-test-isolated` to exercise the spawned
document loader inside the packaged executable.

Windows and macOS use the same Python viewer source, including cancellable
large-file loading, compact count storage, cached time-window calculations,
and filename-based window titles.

### Stable development and validation

The Tk window and CLI entry point remain in `rfmapping_gui.py`. Supporting
code lives in `rfmapping_viewer/`:

| Module | Responsibility |
| --- | --- |
| `rf_model.py`, `companions.py` | Read-only data adapters and cached display values |
| `display.py` | Spatial grouping, colors, timelines, and raster calculations |
| `settings.py`, `settings_window.py` | Validated preferences and their Tk editor |
| `viewer_state.py` | Paired-window state and waveform worker results |
| `figure_composer.py`, `export_inputs.py` | Figure composition, input identity, and CSV publication |
| `constants.py`, `paths.py`, `tk_support.py` | Stable identity, discovery, and native integration |

Data and display modules can be imported without Tk or the alpha HDF5 stack.
Tests import each function from its owning module and share synthetic fixtures
in `tests/gui_test_support.py`.

Run the complete stable regression suite on the configured remote host:

```sh
ssh "$RFMAPPING_REMOTE_HOST" 'cd ~/Developer/rfmapping_gui/python && \
  RF_MAPPING_TEST_PYTHON="$HOME/.virtualenvs/rfmapping/bin/python" \
    xvfb-run -a script/test_python_stable.sh'
```

The remote virtual environment must include Tk; Xvfb supplies a display, not
the Tk runtime. A missing Tk runtime fails the suite. `pytest-stable.ini`
selects stable tests, including all window interactions. The macOS PR check
and stable release job use the same test script without Xvfb.

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

Project validation is performed on `RFMAPPING_REMOTE_HOST` from your untracked `.env.local`:

```sh
ssh "$RFMAPPING_REMOTE_HOST"
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

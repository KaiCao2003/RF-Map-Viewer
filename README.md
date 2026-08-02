# RF Map Viewer

RF Map Viewer is a desktop application for exploring receptive-field spike-count
data stored as JSON. It provides rectangular and polar RF maps, delay and RGB
views, time-bin timelines, response normalization, head-direction tuning
curves, interactive selection, and synchronized multi-window viewing. The
native Swift 2.1 and Python/Tk 1.8 releases share the same scientific display
semantics; Python 1.8 also supports 64-bit Windows.

Python/Tk pairing navigates the sorted union of unit IDs; a unit absent from one
file is shown as **N/A**, and differing lists are flagged as potentially
different sessions. Swift window synchronization is available only when loaded
documents have identical ordered `unitPool` arrays; differing lists disable
synchronization.

The repository contains two implementations of the same viewer:

- A native Swift/SwiftUI application for Apple silicon Macs running macOS 15
  Sequoia or later.
- A Python/Tk application packaged for Apple silicon Macs and 64-bit Windows.

## Download

Release builds publish these artifacts to the corresponding GitHub Release:

| Platform | Release asset | Notes |
| --- | --- | --- |
| macOS 15+, Apple silicon | `RF_Map_Viewer-macos-arm64.zip` | Native Swift 2.1 arm64 application |
| macOS 14+, Apple silicon | `RF_Map_Viewer-python-macos-arm64.zip` | Python/Tk 1.8 arm64 application |
| Windows 64-bit | `RF_Map_Viewer-python-windows-x64-portable.zip` | Portable build; extract before running |
| Windows 64-bit | `RF_Map_Viewer-python-windows-x64-setup.exe` | Windows installer |

On macOS, extract the archive and replace the existing **RF Map Viewer.app** in
Applications. Delete older extracted copies instead of keeping them elsewhere:
Finder can list every copy that claims JSON files in the **Open With** menu. On
Windows, either extract the complete portable archive or run the installer. Do
not move only the `.exe` out of the extracted portable folder, because its
supporting files are required.

## Try the synthetic example

Open [`data/demo_rf_map.json`](data/demo_rf_map.json) from the application. This
small dataset is fully synthetic and includes two artificial units, angular and
radial positions, ten time bins, and presentation counts. It is designed to make
the RF, polar, delay/RGB, normalized-response, and timeline controls easy to
explore; it does not contain experimental or participant data.

The viewer expects these JSON fields:

- `unitsSpikeCounts`: a non-negative numeric array arranged as
  `unit × y × x × time bin`.
- `unitsSpikeCountsSize`: the four corresponding dimensions.
- `unitPool`: one displayed identifier per unit.
- `xPositions` and `yPositions`: the spatial coordinates.
- `timeBinEdges`: strictly increasing edges in seconds, with one more edge than
  the number of time bins.
- `stimulusPresentationCounts` (optional): a non-negative integer `y × x`
  array used by normalized response modes.

## Head-direction tuning curves

Both macOS viewers can place a head-direction tuning curve beside or below the
RF map.
For an RF document under a dated session such as `YYMMDD_2`, automatic loading
searches that day's sessions in numeric order for the same probe at:

```text
YYMMDD_N/data/tuning_curves/ProbeA/tuning_curves.json
```

Use `ProbeB` for Probe B recordings. If no file is found, either macOS viewer
can use **File > Attach Tuning Curves…**; the Swift empty state also provides an
**Attach Tuning Curves…** button. Python/Tk additionally opens the picker when
its empty tuning canvas is clicked and accepts one dropped
`tuning_curves.json` file. Schema 2 stores the observation counts and occupancy
needed to aggregate firing rates correctly:

```json
{
  "schema_version": 2,
  "metadata": {
    "timestamp_reference": "motive_frame_time_from_device_trigger_interpulse_midpoints",
    "angle_convention_note": "0 degrees up; positive counter-clockwise"
  },
  "angle_bin_edges_deg": [0.0, 2.0, 4.0],
  "occupancy_time_s": [1.2, 1.0],
  "units": [
    {
      "unit_id": 123,
      "spike_counts": [4, 2],
      "firing_rate_hz": [3.3333333333, 2.0],
      "hd_class": 2
    }
  ]
}
```

The abbreviated arrays above illustrate the shape only. A real file has 181
edges spanning 0–360°, 180 occupancy values, and 180 counts/rates per unit.
The generator detects the short Motive device-trigger pulses in the raw ADC
stream. Following Motive's timing convention, it converts `N` triggers to
`N + 1` frame timestamps using inter-trigger midpoints plus a local half-period
extrapolation at each endpoint, then indexes those timestamps by Motive frame
ID. It does not synthesize time as `frame / 120`.

For every displayed direction bin, firing rate is `sum(counts) /
sum(occupancy seconds)`; rates are never averaged when occupancy is available.
A zero-occupancy group is missing data, not 0 Hz. Circular Gaussian smoothing
is applied separately to counts and occupancy before division, and its default
width remains 18° regardless of the displayed bin count.

By default each cell uses an explicit 0-to-own-peak Hz scale. Enable **Compare
cells** in Settings to use one shared 0-to-global-peak Hz scale. Polar curves
are outline-only with ordinary Hz rings; decorative statistic rings and area
fills are intentionally omitted. In polar plots, 0° is at 12 o'clock and
positive angles run counter-clockwise. Line plots unwrap the same convention
onto −180…180° and always start at 0 Hz. HD class 1 is shown as a yellow `1`
(exactly one of the Rayleigh or shuffle tests is significant); class 2 is a
green `2` (both are significant). Class 0 and unavailable labels are hidden.

Legacy `{clusterID: [180 rates]}` files remain readable. Because they do not
contain occupancy, grouped legacy rates are averaged and the UI marks their
timing/occupancy provenance as unavailable.

Tuning-curve files never add units to navigation. Unit navigation continues to
use the RF units permitted by the viewer's current navigation and pairing
rules; a missing curve is shown explicitly for the selected RF unit.

## Optional probe positions (Python/Tk)

The Python/Tk viewer on macOS and Windows can display four-shank probe geometry
in its unit sidebar and filter the unit picker spatially. For JSON names ending
in `_A.json` or `_B.json`, it looks beside the recording data for:

```text
<data-root>/spike_position/ProbeA/positions.csv
<data-root>/waveform/ProbeA/channels.csv
```

Use `ProbeB` for a `_B.json` document. Set `RF_MAPPING_PROBE_DATA_ROOT` to
`<data-root>` when the JSON lives elsewhere, or choose **File > Attach Probe
Geometry…**, click the empty probe area, or drop `positions.csv` onto it. Unit
positions are joined to JSON units by `unit_id`; CSV row order is not used.

Clicking a channel selects a centered 160 × 75 µm neighborhood. Dragging
selects an arbitrary region, and Escape clears the spatial filter. The unit
picker and previous/next navigation only include units inside the active
region. In Python/Tk, discovered JSON choices in the File menu open in a new
window, the main-area Display controls can be collapsed, the Probe canvas folds
toward the left edge, and the HD canvas folds toward the right edge or downward
when stacked. Folded optional views remain attached and are not redrawn. The
Swift viewer currently has no probe-geometry panel; its HD panel can still
collapse toward the right or downward.

## Settings

Open **Settings…** from the macOS application menu with `⌘,`; Python on Windows
exposes it under **View** with `Ctrl+,`.

The implementations currently differ. Python/Tk uses **General**, **RF Map**,
and **Tuning Curve** tabs. General controls visibility and automatic loading
for tuning curves and probe geometry; RF Map stores its display defaults;
Tuning Curve controls plot style, arrangement, displayed bins, fixed-angle
smoothing, and per-cell or shared scaling. **Save** applies and persists the
values, closes the dialog, and broadcasts them only when Python window pairing
is enabled.

Swift Settings contains the HD tuning controls only. Changes apply immediately
and persist in `UserDefaults`; RF-map controls remain in each document window,
and Swift has no probe-geometry setting. These HD preferences are shared by all
Swift windows.

Available navigation and view shortcuts appear beside their menu commands
instead of in the plotting workspace; for example, **Navigate > Increase Time
Resolution** uses `⇧.`. Python/Tk also provides **Help > Keyboard Shortcuts**.
**Help > Support Documentation** opens the online project guide in Swift and
the bundled local README in Python/Tk.

## Build from source

### Native macOS application

The Swift package requires macOS 15 or later and the Apple development command
line tools. Build the Apple silicon application and release archive with:

```sh
script/build_macos_app.sh
```

The application is created at `dist/RF Map Viewer.app`, and the distributable
archive is created at `dist/RF_Map_Viewer-macos-arm64.zip`.

For development, build and test the Swift package with:

```sh
swift build
swift test
```

### Python macOS application

The macOS packaging script runs natively on Apple silicon with Python 3.14 and
PyInstaller. It installs the pinned runtime dependencies into its build virtual
environment. Build the application and archive with:

```sh
script/build_python_macos_app.sh
```

The archive is created at
`dist/python/RF_Map_Viewer-python-macos-arm64.zip`. The temporary app at
`dist/python/RF Map Viewer.app` is unregistered and removed after packaging so
Finder does not discover the build copy as another JSON **Open With** entry.

### Windows application

The release workflow builds the Python/Tk viewer on a Windows x64 runner. It
creates both a complete portable archive and an installer; the installer recipe
is in `packaging/windows/RFMapViewer.iss`.

Both macOS build scripts code-sign and verify the app bundle. By default they
use ad-hoc signing (`-`). Set `RF_MAPPING_CODESIGN_IDENTITY` or
`CODE_SIGN_IDENTITY` to use a named identity with the hardened runtime and a
timestamp. The scripts do not notarize the app, and the Windows artifacts are
not Authenticode-signed.

### Python dependencies and tests

Install the pinned runtime dependencies before running
the Python tests:

```sh
python -m pip install -r requirements-python-runtime.txt
python -m unittest discover -s tests -p 'test_rfmapping_gui.py'
```

The Tk integration tests require a graphical desktop session:

```sh
python -m unittest discover -s tests -p 'test_rfmapping_gui_tk.py'
```

## Automated releases

The workflow in [`.github/workflows/release.yml`](.github/workflows/release.yml)
builds the Swift 2.1 and Python/Tk 1.8 releases on a GitHub-hosted Apple silicon
macOS runner, plus Python 1.8 on a 64-bit Windows runner. macOS executables are
arm64-only; no Intel or Universal macOS artifact is produced. Release archives
and installers belong on the GitHub Release, not in the source tree.

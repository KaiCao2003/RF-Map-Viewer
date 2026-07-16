# RF Map Viewer

RF Map Viewer is a desktop application for exploring receptive-field spike-count
data stored as JSON. It provides rectangular and polar RF maps, delay and RGB
views, time-bin timelines, response normalization, interactive selection, and
synchronized multi-window viewing.

The repository contains two implementations of the same viewer:

- A native Swift/SwiftUI application for Apple silicon Macs running macOS 15
  Sequoia or later.
- A Python/Tk application packaged for Intel and Apple silicon Macs and for
  64-bit Windows.

## Download

Choose an artifact from the latest GitHub Release:

| Platform | Release asset | Notes |
| --- | --- | --- |
| macOS 15+, Apple silicon | `RF_Map_Viewer-macos-arm64.zip` | Native Swift/SwiftUI application |
| macOS, Intel or Apple silicon | `RF_Map_Viewer-python-macos-universal2.zip` | Python/Tk universal2 application |
| Windows 64-bit | `RF_Map_Viewer-python-windows-x64-portable.zip` | Portable build; extract before running |
| Windows 64-bit | `RF_Map_Viewer-python-windows-x64-setup.exe` | Windows installer |

On macOS, extract the archive and move **RF Map Viewer.app** to Applications if
desired. On Windows, either extract the complete portable archive or run the
installer. Do not move only the `.exe` out of the extracted portable folder,
because its supporting files are required.

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

The macOS packaging script uses a universal2 Python 3.14 installation and
PyInstaller. Build the application and archive with:

```sh
script/build_python_macos_app.sh
```

The application is created at `dist/python/RF Map Viewer.app`, and the archive
is created at
`dist/python/RF_Map_Viewer-python-macos-universal2.zip`.

### Windows application

The release workflow builds the Python/Tk viewer on a Windows x64 runner. It
creates both a complete portable archive and an installer; the installer recipe
is in `packaging/windows/RFMapViewer.iss`.

### Python tests

The data/model tests use only Python's standard library:

```sh
python -m unittest discover -s tests -p 'test_rfmapping_gui.py'
```

The Tk integration tests require a graphical desktop session:

```sh
python -m unittest discover -s tests -p 'test_rfmapping_gui_tk.py'
```

## Automated releases

The workflow in [`.github/workflows/release.yml`](.github/workflows/release.yml)
builds platform-specific artifacts on their corresponding operating systems.
Release archives and installers belong on the GitHub Release, not in the source
tree.

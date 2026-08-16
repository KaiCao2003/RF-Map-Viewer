# Free-Moving RF Viewer 1.10.0.1 Preview

> **freemoving rf viewer preview**

This Python/Tk application is a read-only viewer for the HDF5 `.rfmap` files
written by `RFmapping_core_fm.m`. Version **1.10.0.1** is intentionally a
separate preview application: it does not open legacy RF JSON, tuning curves,
probe files, or head-direction companions.

## What it shows

- one unit at a time from `/rf/rate_hz`;
- head-centric azimuth `[-180, 180)` and elevation `[-90, 90]`;
- a continuously adjustable half-open response window;
- time-weighted mean firing rate in Hz;
- exposure and effective-trial QA maps;
- a spatial-mean response timeline; and
- embedded cylinder, rigid-body, viewpoint, and input provenance.

The loader validates `format=rfmapping_fm_hdf5_v1`,
`logical_dimension_order=unit,elevation,azimuth,time`, the completion marker,
the embedded `rf-calib-1.0` document, and MATLAB's reversed on-disk HDF5
dimension order. Only the selected unit is read from the large rate dataset.

## Install and run from source

Project validation is performed on `hhw9l84`:

```sh
ssh hhw9l84
cd ~/Developer/rfmapping_gui/python
~/.virtualenvs/rfmapping/bin/pip install -e '.[test]'
~/.virtualenvs/rfmapping/bin/python rfmapping_fm_gui.py /path/to/result.rfmap
```

The app also accepts one `.rfmap` by Finder Open With or drag-and-drop.

## Validate

```sh
cd ~/Developer/rfmapping_gui/python
PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q \
  --ignore=tests/test_rfmapping_gui_tk.py
PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python \
  rfmapping_fm_gui.py --self-test /path/to/result.rfmap
```

The remote Linux host validates the HDF5 model and non-GUI behavior. A complete
release additionally requires the Tk/TkDND smoke test on the Apple-silicon
build host.

## macOS preview identity

- App: `Free-Moving RF Viewer.app`
- Bundle ID: `org.local.rfmapping.viewer.freemoving`
- Version/build: `1.10.0.1` / `110001`
- Edition: `FreeMovingPreview`
- Minimum system: macOS 14.0, Apple silicon

The distinct name and bundle ID allow this preview to be installed alongside
the stable full RF Map Viewer. Build and inspect it with:

```sh
script/build_python_macos_app.sh
script/install_python_macos_app.sh --preflight
```

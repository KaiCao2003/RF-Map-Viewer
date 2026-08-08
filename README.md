# RF Map Viewer

RF Map Viewer 1.9 provides a Python/Tk desktop viewer, a private Web viewer,
and a SwiftUI parity implementation. The Python application is the installed
macOS product; Swift source is retained for parity work but its build product is
not installed alongside Python.

## RFMap object model

`Utils/rfmap.py` loads one JSON file into an ordered `RFMapList` containing one
`RFMap` per unit. Index lookup and cluster-ID lookup are deliberately separate,
because real unit IDs overlap valid source indices:

```python
from Utils.rfmap import load_rf_maps

rf_maps = load_rf_maps("regular_unitsSpikeCounts_260630_3.json")
unit_by_source_index = rf_maps.by_index(5)
the_same_unit = rf_maps.by_unit_id(unit_by_source_index.unit_id)

summed = the_same_unit.sum_between_s(0.0, 0.2)
spatial_bumps = the_same_unit.detect_spatial_bumps(
    threshold_ratio=1.2,
    spatial_size=(3, 3),
    baseline_start_s=-0.1,
    baseline_end_s=0.0,
)
```

`sum_between_s(earlier, later)` uses the JSON's seconds-based edges and sums the
half-open interval `[earlier, later)`. Both arguments must resolve to actual
edges (absolute tolerance `1e-12` seconds). A reversed interval is invalid; an
equal pair is valid and returns a new zero-valued RFMap with one singleton time
axis. `detect_spatial_bumps()` applies SciPy's 2-D maximum filter independently
to every time bin and intersects local maxima with the baseline-ratio mask.

`locate_rf.ipynb` uses this model directly and derives its 0–200 ms 2-D and 1-D
maps from the returned per-unit objects.

## Python desktop app

- Bundle: `dist/python/RF Map Viewer.app`
- Archive: `dist/python/RF_Map_Viewer-python-macos-arm64.zip`
- Bundle identifier: `org.local.rfmapping.viewer`
- Version: 1.9.0; architecture: Apple silicon (`arm64`)

Build without launching or installing:

```sh
script/build_python_macos_app.sh
```

The build pins PyInstaller, NumPy, Pillow and SciPy, verifies every Mach-O is
arm64-only, signs the bundle, checks its metadata and archive, and optionally
verifies a bundled `data/` directory byte-for-byte. Without `data/`, startup
waits for Finder OpenDocument or presents File > Open. Each opened JSON gets an
independent viewer state.

File > Export Figures opens a page composer. It supports current/all/custom
units; add/remove/rename/reorder pages; add/remove/reorder RF, Delay, RGB,
Timeline, HD and Probe plots; exact live preview; and PDF, ordered PNG, or SVG
output. Missing companion data remains visible as a placeholder instead of
silently removing a plot.

Legacy JSON without `stimulusPresentationCounts` remains valid in Spike count
mode. Per-presentation and rate modes are enabled only when that metadata is
present and valid.

## Web viewer

The Web application reports version `1.9.0-web`. Its full-screen figure
composer uses the same stable plot IDs and supports current/all/probe-filtered/
custom unit selection, page editing, live preview, PDF, ordered PNG manifests,
explicit conflict handling and overwrite confirmation.

Dataset APIs treat RF, HD and Probe sources as read-only. Figure publication is
separately confined by `RFMAPPING_FIGURE_EXPORT_ROOT` (default
`/mnt/senzailab`): the destination must already exist below that root, may not
contain symlink or traversal components, and defaults to no overwrite. A PNG
directory can be replaced only when its versioned manifest and every recorded
page checksum still validate, so a raw session directory is never an overwrite
target. Publication uses a kernel-level atomic exchange where available and a
journaled, crash-recoverable transaction on CIFS filesystems that do not support
directory exchange. See `deploy/README.md` for installation and acceptance.

## Swift source

The SwiftUI source under `Sources/RFMappingSwiftUI` contains the matching
per-unit RFMap API, strict time sum, spatial bump detector, unit-ID-aware window
pairing, and page composer. Its target is macOS 15 on Apple silicon:

```sh
script/build_macos_app.sh
```

## Validation

Follow `AGENTS.md`: run project code only on `ssh hhw9l84` with
`~/.virtualenvs/rfmapping`. MATLAB files are not runtime dependencies. Typical
Python/Web gates are:

```sh
ssh hhw9l84 'cd ~/Developer/rfmapping && \
  PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q'

ssh hhw9l84 'cd ~/Developer/rfmapping/web && npm ci --no-audit --no-fund && \
  npm test && npm run build'
```

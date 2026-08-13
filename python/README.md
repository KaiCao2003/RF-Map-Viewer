# Python/Tk RF Map Viewer 1.9.2 Full

Python 1.9.2 **Full** supports dedicated `.rfmap`, `.tc`, and `.probe` file
names while preserving the existing JSON and CSV inputs. It retains the
complete 1.8 desktop workflow and the
1.9 multi-unit, multi-page figure composer. It is the supported Python/Tk
viewer for Apple-silicon Macs. Scientific RF detection, permutation testing,
raw-trial reconstruction, notebooks, and MATLAB work remain outside this GUI
repository.

The supported installed state after this upgrade is Python 1.9.2 **Full**.
Installing it replaces the current 1.9.1 **Full** app at the same path.
Minimal and Full intentionally have the same app name and bundle identifier,
so they are not designed for side-by-side installation. Retain Minimal only as
an immutable tagged release/download rollback artifact, not as a second runtime
implementation in the main source tree.

## Full feature set

The Full viewer includes:

- rectangular and polar RF, delay/RGB, and full-axis timeline views;
- spike count, spikes/presentation, and firing-rate display modes;
- a default-on, current-window unit filter for unavailable or zero-spike
  native RF bins, with a persistent positive-integer threshold;
- persistent General, RF Map, and Tuning Curve settings;
- live HD tuning panels, legacy and versioned tuning JSON support, provenance,
  smoothing, binning, comparison scaling, attach, and drag-and-drop;
- live probe geometry, attach/drop, click and drag spatial filtering, unit-ID
  joins, and collapsible optional panels;
- synchronized multi-window navigation and display state;
- displayed-data CSV export; and
- the 1.9 figure composer: multi-unit page templates, live preview, and PDF,
  PNG, and SVG output for RF, delay/RGB, timeline, HD, and probe views.

RF maps may use `.rfmap` or `.json`; tuning curves may use `.tc` or `.json`;
and spike-position/probe files may use `.probe` or `.csv`. These files are
read-only inputs. The RF map is the primary document: open it first, then
attach or drop its `.tc` and `.probe` companions so unit IDs can be matched.
Plot-range controls
affect only the 2-D RF display; timeline views retain the complete time axis.
The unit filter is evaluated before display rebinning or smoothing. A unit is
hidden when its unavailable/zero-spike native spatial-bin count is greater than
or equal to the configured threshold; turn the filter off to restore the full
unit list. Settings rejects thresholds above the current map's native spatial
bin count.

## Install and run from source

```sh
cd ~/Developer/rfmapping_gui/python
~/.virtualenvs/rfmapping/bin/pip install -e '.[test]'
~/.virtualenvs/rfmapping/bin/python rfmapping_gui.py --self-test /path/to/map.rfmap
~/.virtualenvs/rfmapping/bin/python rfmapping_gui.py /path/to/map.rfmap
```

The GUI and Tk integration tests require an interpreter with working Tk and a
display. `tkinterdnd2==0.6.2` is a runtime dependency, not merely a test extra.

## Validate

Project code is run only on `hhw9l84` with the repository virtual environment.
That host currently lacks Tk, so its honest non-GUI gate is explicit:

```sh
ssh hhw9l84 'cd ~/Developer/rfmapping_gui/python && \
  PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q \
    tests/test_rfmapping_gui.py \
    tests/test_rf_dataset.py \
    tests/test_hd_tuning.py \
    tests/test_figure_export.py \
    tests/test_gui_figure_export.py \
    tests/test_full_legacy_model.py && \
  bash script/tests/test_python_macos_release_scripts.sh && \
  bash script/tests/test_python_macos_release_candidate.sh'
```

This is not a complete GUI validation. Run the complete suite with the same
Tk-enabled Python used for the app on the Apple-silicon build host:

```sh
cd ~/Developer/rfmapping_gui/python
PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q
```

A Full release is not green if the Tk availability test fails or the Tk tests
are skipped. The release build also runs `--self-test` and `--self-test-dnd`
through the frozen executable before signing.

## Build the macOS 14+ arm64 release

```sh
script/build_python_macos_app.sh
script/install_python_macos_app.sh --preflight
script/install_python_macos_app.sh --install
```

The build does not install or launch the app. It produces:

```text
dist/python/RF Map Viewer.app
dist/python/RF_Map_Viewer-python-1.9.2-full-macos-arm64.zip
dist/python/SHA256SUMS-python-1.9.2-full.txt
```

The archive contains `RFMappingReleaseEdition=Full`, requires macOS 14.0 or
later, bundles this README and the arm64 TkDND runtime, contains arm64 Mach-O
files only, and rejects `__MACOSX`/AppleDouble metadata. The app bundle remains
in `dist/python` only as input to the guarded installer and is unregistered
from Launch Services after the build.

By default the app is ad-hoc signed. Set `RF_MAPPING_CODESIGN_IDENTITY` (or
`CODE_SIGN_IDENTITY`) to a named identity to enable hardened-runtime signing
with a timestamp. The script verifies the signature but does not notarize the
app.

Before publishing, verify both files together:

```sh
cd dist/python
shasum -a 256 -c SHA256SUMS-python-1.9.2-full.txt
unzip -tq RF_Map_Viewer-python-1.9.2-full-macos-arm64.zip
```

Publish Full under an immutable `python-v1.9.2-full` tag. Retain 1.9.0 Minimal
under its own immutable tag and release; never rebuild an old tag with new
source.

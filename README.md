# RF Mapping Viewers

This repository contains the display and export applications for RF mapping
data. It is self-contained: none of its runtime paths import the sibling
`../rfmapping` analysis repository.

| Implementation | Directory | Primary entry point |
| --- | --- | --- |
| Python/Tk stable | `python/` | `python/rfmapping_gui.py` |
| SwiftUI | `swift/` | `swift/Package.swift` |
| Web | `web/` | FastAPI under `web/backend/`; React under `web/frontend/` |

Scientific RF detection, trial reconstruction, notebooks, and Matlab-related
sources remain in `../rfmapping`. The two repositories communicate only
through versioned file contracts, principally the RF formats described in
[`contracts/rf-json.md`](contracts/rf-json.md).

## Component versions

The stable Python source and macOS/Windows packaging target `1.11.1` (macOS
build `111001`, Windows build `11101`). Swift is `1.10.3`, and Web is `1.10.2`.
Component identity belongs in release tags and artifact names, not in a fourth
version component. See
[`release/README.md`](release/README.md) for the canonical mapping and tag
policy.

## Downloads

GitHub Actions builds the stable packages published on the
[Releases page](https://github.com/KaiCao2003/RF-Map-Viewer/releases):

- [Python 1.11.1 macOS app](https://github.com/KaiCao2003/RF-Map-Viewer/releases/tag/python-v1.11.1)
- [Python 1.11.1 Windows installer and portable ZIP](https://github.com/KaiCao2003/RF-Map-Viewer/releases/tag/python-v1.11.1)
- [Swift 1.10.3 macOS app](https://github.com/KaiCao2003/RF-Map-Viewer/releases/tag/swift-v1.10.3)
- [Web 1.10.2 package](https://github.com/KaiCao2003/RF-Map-Viewer/releases/tag/web-v1.10.2)

The Windows 1.11.1 packages use a packaging follow-up commit identified in the
release notes. The original `python-v1.11.1` tag and macOS assets are preserved.

This repository was recreated from a reviewed source snapshot on 2026-09-07;
previous release attachments were not imported. See [PRIVACY.md](PRIVACY.md)
before migrating an old clone or publishing a package.

## Python viewer entry point

The Python application and `rfmapping-viewer` command open the stable RF Map
Viewer. The separate Free-Moving alpha application and its HDF5 Square/Bar,
3D-sphere, and exposure-QA paths have been retired. Historical Git tags and
published artifacts are unaffected.

The offline session/EBC report remains available through
`python/script/render_session_overlay.py`; it consumes exported session-overlay
JSON independently of RF document viewing.

These patches align HD tuning with clockwise RF azimuth on screen and in exports:
RF −90° matches HD 270°, RF 90° matches HD 90°, and 0° matches 0°. The source TC
angles and firing rates are unchanged.

## Current RF format and filename aliases

Python, Swift, and Web 1.10.0 accept version-2 indexed NPZ `.rfmap` files
alongside existing JSON inputs. They display the first loaded unit while
caching remaining units in the background, expose progress and retry, and
prioritize a selected uncached unit. Multi-unit figure export becomes available
when the cache completes.

Python and Swift 1.10.2 and Web 1.10.1 accept JSON and indexed RF files that
omit the descriptive `spikeCountDefinition`, `occupancyTimeDefinition`, and
`occupancyTimeSecSize` fields. Explicit definitions and sizes must still match
the raw-count contract. Required response-unit and normalization markers,
integer counts, and occupancy data remain validated.

All three support independent A − B RF windows with saved timing defaults,
paired-window synchronization, and matching displayed-data and figure exports.
Negative differences display as gray NaN; zero stays zero. RF windows continue
to affect only the RF map, retaining the full Timeline and Delay / RGB axes.
The Delay / RGB maps find the first equal peak after spatial smoothing and
distinguish black zero responses from gray cells without occupancy.


Since stable version 1.9.6, the viewers require the current raw-count plus
`occupancyTimeSec` RF schema written by `Utils/RFmapping_core.m`. Earlier RF
payloads without occupancy metadata, including the previously normalized
vertical-bar format, are intentionally unsupported. Current Python source uses
`counts / (stimulusPresentationCounts × response-window seconds)` for Hz.
Files without presentation counts open in Spike count mode; occupancy alone
does not provide the response exposure. Swift and Web retain their existing
normalization pending separate updates.

The current payload and companion documents retain these filename aliases:

| Data | Preferred extension | Existing extension |
| --- | --- | --- |
| RF map (JSON or indexed NPZ) | `.rfmap` | `.json` |
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
  RF_MAPPING_TEST_PYTHON="$HOME/.virtualenvs/rfmapping/bin/python" \
    xvfb-run -a script/test_python_stable.sh'

ssh "$RFMAPPING_REMOTE_HOST" 'cd ~/Developer/rfmapping_gui/web && \
  PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q'

ssh "$RFMAPPING_REMOTE_HOST" 'cd ~/Developer/rfmapping_gui/web/frontend && \
  npm ci --no-audit --no-fund && npm test && npm run build'
```

The stable Python suite requires Tk. On a headless Linux host, Xvfb provides
the display for real window-interaction tests. Pull requests run the same
stable suite on macOS Apple Silicon. Building the macOS bundle requires a
Tk-enabled Apple-silicon Mac; the bundle has a macOS 14.0 deployment minimum.

See the implementation READMEs for target-specific install, build, and release
commands.

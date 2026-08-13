# RF Mapping Viewers

This repository contains the display and export applications for RF mapping
data. It is self-contained: none of its runtime paths import the sibling
`../rfmapping` analysis repository.

| Implementation | Directory | Primary entry point |
| --- | --- | --- |
| Python/Tk | `python/` | `python/rfmapping_gui.py` |
| SwiftUI | `swift/` | `swift/Package.swift` |
| Web | `web/` | FastAPI under `web/backend/`; React under `web/frontend/` |

Scientific RF detection, trial reconstruction, notebooks, and Matlab-related
sources remain in `../rfmapping`. The two repositories communicate only
through versioned file contracts, principally the RF JSON described in
[`contracts/rf-json.md`](contracts/rf-json.md).

## File compatibility

All three viewers accept the new filename extensions without changing the
underlying data encoding:

| Data | Preferred extension | Existing extension |
| --- | --- | --- |
| RF map (JSON) | `.rfmap` | `.json` |
| Tuning curve (JSON) | `.tc` | `.json` |
| Spike positions (CSV) | `.probe` | `.csv` |

When both companion names exist, `tuning_curves.tc` and `positions.probe` take
precedence over `tuning_curves.json` and `positions.csv`. Probe channel geometry
continues to use `channels.csv`. An RF map is the primary document; tuning and
probe files are opened as companions of a loaded RF map.

## Remote validation

Project code is run on `hhw9l84` with `~/.virtualenvs/rfmapping`:

```sh
ssh hhw9l84 'cd ~/Developer/rfmapping_gui/python && \
  PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q \
    tests/test_rfmapping_gui.py \
    tests/test_rf_dataset.py \
    tests/test_hd_tuning.py \
    tests/test_figure_export.py \
    tests/test_gui_figure_export.py \
    tests/test_full_legacy_model.py'

ssh hhw9l84 'cd ~/Developer/rfmapping_gui/web && \
  PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q'

ssh hhw9l84 'cd ~/Developer/rfmapping_gui/web/frontend && \
  npm ci --no-audit --no-fund && npm test && npm run build'
```

The remote host is Linux and has neither Tk nor Swift, so it validates Python
model/export logic, Web code, shell syntax, and static Swift layout. A real Tk
launch or signed Python bundle requires a Tk-enabled Apple-silicon Mac; the
Python bundle has a macOS 14.0 deployment minimum. Swift validation uses its
separately intended macOS 15 Apple-silicon build host.

See the implementation READMEs for target-specific install, build, and release
commands.

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

## Remote validation

Project code is run on `hhw9l84` with `~/.virtualenvs/rfmapping`:

```sh
ssh hhw9l84 'cd ~/Developer/rfmapping_gui/python && \
  PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q'

ssh hhw9l84 'cd ~/Developer/rfmapping_gui/web && \
  PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q'

ssh hhw9l84 'cd ~/Developer/rfmapping_gui/web/frontend && \
  npm ci --no-audit --no-fund && npm test && npm run build'
```

The remote host is Linux and has neither Tk nor Swift, so it validates Python
model/export logic, Web code, shell syntax, and static Swift layout. A real Tk
launch, Swift build, or signed macOS bundle requires the intended macOS 15
Apple-silicon build host.

See the implementation READMEs for target-specific install, build, and release
commands.

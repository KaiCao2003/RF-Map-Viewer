# Web Viewer

The Web implementation contains a FastAPI backend in `backend/` and a
React/Vite frontend in `frontend/`. The backend owns its figure renderer and
does not import the analysis repository or the Python/Tk implementation.

## Input files

The viewer opens RF mapping JSON payloads saved as `.rfmap` and continues to
open legacy `.json` files. HD tuning-curve JSON can be attached as `.tc` or as
the legacy `tuning_curves.json`; automatic discovery prefers
`tuning_curves.tc`. Spike-position CSV can be attached as `.probe` or as the
legacy `positions.csv`; automatic discovery prefers `positions.probe`.
RF maps are primary documents; use the HD and Probe companion choosers after an
RF map is open.
These are filename aliases only: the payload schemas are unchanged, and input
files remain read-only.

## Install, test, and run

```sh
cd ~/Developer/rfmapping_gui/web
~/.virtualenvs/rfmapping/bin/pip install -e '.[test]'
PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q

cd frontend
npm ci --no-audit --no-fund
npm test
npm run build

cd ..
MOUSELINE_LOGIN_ANSWER='local-development-only' \
MOUSELINE_AUTH_GENERATION='local-development-v1' \
RFMAPPING_GATE_DB='/tmp/rfmapping-local-development.sqlite3' \
PYTHONPATH=backend ~/.virtualenvs/rfmapping/bin/python -m rfmapping_web
```

The source layout serves `frontend/dist`; immutable production releases retain
the existing deployed `rfmapping_web/` plus `web/dist` layout.
The three inline authentication settings above are disposable development
values. Production reads its mode-600 access-gate environment file as described
in the deployment guide.

## Deployment

Deployment scripts remain scoped to the existing production identity and
storage root. From `~/Developer/rfmapping_gui/web`, follow
[`deploy/README.md`](deploy/README.md). A normal release is staged first;
activation is an explicit separate action.

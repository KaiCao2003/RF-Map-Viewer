# Web Viewer

The Web implementation contains a FastAPI backend in `backend/` and a
React/Vite frontend in `frontend/`. The backend owns its figure renderer and
does not import the analysis repository or the Python/Tk implementation.

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

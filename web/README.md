# Web Viewer 1.9.6

The Web implementation contains a FastAPI backend in `backend/` and a
React/Vite frontend in `frontend/`. The backend owns its figure renderer and
does not import the analysis repository or the Python/Tk implementation.
Its `1.9.6` version places it in the same stable feature generation as the
Python `1.9.x` reference; `web` remains an artifact/tag identity rather than a
version suffix.

Version 1.9.6 implements the occupancy-aware RF contract. Mean firing rate is
the default display and export value: selected raw counts are divided by
`occupancyTimeSec` in seconds. Count remains available. Spatial reduction and
smoothing combine count and occupancy separately before dividing, and the
default strongest cell is selected by firing rate rather than raw count.

The viewer also discovers the read-only schema-v4 waveform artifact for the
current probe, renders the selected unit's local average in the main workspace,
and exports it from Figure Composer with a symmetric microvolt scale shared by
the selected units. The channel selector matches Python's Same x column and
Same shank modes. Automatic HD discovery uses one exact positive session
(default `1`) without falling back to a different session. Press `P` to toggle
rectangular/polar spatial views and `Shift-P` to cycle the palette.

The native zero-spike unit filter is enabled by default at a threshold of one
bin. It evaluates the current 2-D RF sum window on the source `y × x` grid
before display rebinning or smoothing; the timeline keeps its independent full
time axis. Unit navigation, Probe selection, Figure Composer, and export all
use the same quality-visible unit set.

Figure Composer freezes its source/filter/companion snapshot. RF
Cartesian/Polar plots share scalar bounds across the selected units, waveform
plots share symmetric bounds per channel mode, and preview/final rendering use
the same recipe. PDF output embeds a lossless RGB raster and manifest metadata;
PNG and SVG directories include per-page integrity plus `manifest.json`, with
SVG explicitly recording its lossless embedded-PNG rendering contract.

## Input files

The viewer opens current RF mapping JSON payloads saved as `.rfmap` or `.json`.
The filename extension does not change the RF schema. HD tuning-curve JSON can be attached as `.tc` or as
the legacy `tuning_curves.json`; automatic discovery prefers
`tuning_curves.tc`. Spike-position CSV can be attached as `.probe` or as the
legacy `positions.csv`; automatic discovery prefers `positions.probe`.
RF maps are primary documents; use the HD and Probe companion choosers after an
RF map is open.
RF payloads must include raw finite non-negative integer `unitsSpikeCounts`,
the fixed count-semantics markers, and a finite non-negative
`occupancyTimeSec` matrix whose declared size matches the spatial axes. A
zero-occupancy cell must contain only zero counts, and at least one cell must
have positive occupancy. Payloads from earlier
versions that omit the occupancy contract or contain normalized response
values are intentionally rejected. MATLAB singleton encodings are accepted
for 1-by-1, 1-by-N, and N-by-1 occupancy matrices when the declared sizes make
the shape unambiguous. Input files remain read-only.

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

After the backend and frontend tests pass and `frontend/dist` has been built,
create the versioned GitHub Release payload with:

```sh
deploy/build_release_archive.sh
```

## Deployment

Deployment scripts remain scoped to the existing production identity and
storage root. From `~/Developer/rfmapping_gui/web`, follow
[`deploy/README.md`](deploy/README.md). A normal release is staged first;
activation is an explicit separate action.

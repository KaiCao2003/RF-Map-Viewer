# RF Mapping File Contract

All three viewers consume the same read-only RF JSON document. New files use
the `.rfmap` extension; the existing `.json` extension remains supported. The
extension changes only the filename, not the JSON payload described below.

Required top-level fields:

- `unitsSpikeCounts`: finite, non-negative JSON numbers with axes
  `(unit, y, x, time)`.
- `unitsSpikeCountsSize`: four positive integers matching that exact shape.
- `unitPool`: one unique integer recorded unit ID per first-axis entry.
- `xPositions` and `yPositions`: finite coordinates matching the spatial axes.
- `timeBinEdges`: finite seconds, strictly increasing, with `time + 1` values.

Optional `stimulusPresentationCounts` is a non-negative integer `(y, x)`
matrix. A cell with zero presentations must have zero spike counts for every
unit and time bin. Unknown top-level fields are metadata and must not invalidate
an otherwise valid document.

Unit array index and recorded unit ID are different namespaces. Time-window
sums use the half-open interval `[start, end)` and endpoints must correspond to
stored edges. Viewers convert seconds to milliseconds only for display.

Companion HD tuning JSON uses `tuning_curves.tc`, with the existing
`tuning_curves.json` name retained as a fallback. Spike-position CSV uses
`positions.probe`, with `positions.csv` retained as a fallback. Probe channels
remain in `channels.csv`. Session-relative companion discovery prefers the new
extensions and HD tuning data is matched by recorded unit ID. RF maps are the
primary viewer documents; `.tc` and `.probe` are attached to an open RF map so
their recorded unit IDs have a dataset context. Inputs are never modified by a
viewer.

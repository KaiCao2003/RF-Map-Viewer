# RF Mapping JSON Contract

All three viewers consume the same read-only RF JSON document.

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

Companion probe geometry is discovered from session-relative
`data/spike_position/ProbeX/positions.csv` and
`data/waveform/ProbeX/channels.csv`. HD tuning data is matched by recorded unit
ID. Inputs are never modified by a viewer.

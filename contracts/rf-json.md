# RF Mapping File Contract

All three stable viewers consume the same current, read-only RF JSON document.
The preferred extension is `.rfmap`; `.json` is also accepted because
`RFmapping_core.m` can write the same current payload under either filename.
The extension does not change the schema.

Required top-level fields:

- `unitsSpikeCounts`: finite, non-negative integer spike counts with axes
  `(unit, y, x, time)`.
- `unitsSpikeCountsSize`: four positive integers matching that exact shape.
- `unitPool`: one unique integer recorded unit ID per first-axis entry.
- `xPositions` and `yPositions`: finite coordinates matching the spatial axes.
- `timeBinEdges`: finite seconds, strictly increasing, with `time + 1` values.
- `responseUnits`: exactly `spike_count`.
- `responseNormalization`: exactly `none`.
- `spikeCountDefinition`: exactly
  `each_qualifying_trial_contributes_once_per_final_spatial_bin`.
- `occupancyTimeSec`: finite, non-negative seconds with axes `(y, x)`.
- `occupancyTimeSecSize`: two positive integers matching that exact shape.
- `occupancyTimeDefinition`: exactly
  `sum_of_qualifying_trial_durations_per_final_spatial_bin`.

At least one spatial cell must have positive occupancy. A cell with zero
occupancy must have zero spike counts for every unit and time bin. Unknown
top-level fields are metadata and do not invalidate an otherwise valid
document. Geometry-specific fields written for vertical bars remain metadata
in the shared base contract.

For a selected half-open time window, firing rate is the summed raw spike count
divided by `occupancyTimeSec`. A zero-occupancy cell is unavailable. When a
viewer combines or smooths spatial cells, it combines or smooths the raw count
and occupancy matrices separately before division; it never averages
already-normalized rates.

Version 1.9.5 intentionally does not support earlier RF payloads that omit this
occupancy-aware contract or store already-normalized values in
`unitsSpikeCounts`.

Unit array index and recorded unit ID are different namespaces. Time-window
sums use the half-open interval `[start, end)` and endpoints must correspond to
stored edges. Viewers convert seconds to milliseconds only for display.

Companion HD tuning JSON uses `tuning_curves.tc`, with `tuning_curves.json` as
a filename fallback. Spike-position CSV uses `positions.probe`, with
`positions.csv` as a filename fallback. Probe channels remain in
`channels.csv`. Session-relative companion discovery prefers the dedicated
extensions and HD tuning data is matched by recorded unit ID. RF maps are the
primary viewer documents; `.tc` and `.probe` are attached to an open RF map so
their recorded unit IDs have a dataset context. Inputs are never modified by a
viewer.

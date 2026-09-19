# Python / Swift parity fixes for 1.10.1

This review covered the Python/Tk and Swift viewers' RF input contracts,
temporal grouping, response smoothing, color ranges, exports, and Escape
behavior. It did not modify RF/HD/probe inputs or scientific analysis in the
sibling `rfmapping` repository.

## Corrected differences

| Area | Previous behavior | 1.10.1 behavior |
| --- | --- | --- |
| Nonuniform time bins | Swift grouped a fixed number of source bins; Python selected the nearest measured boundary. | Both choose the nearest measured edge to the quantized target duration, preferring the earlier edge on an exact tie. |
| Repeated RF smoothing | Swift filled missing centers temporarily and masked them only after smoothing; later passes could propagate responses through unsampled positions. | Missing centers remain missing on every pass, for count and occupancy-normalized response matrices and timeline frames. |
| Temporal smoothing | Both viewers treated unsampled positions as zero-response observations; this diluted neighbors and could create delay values at unsampled positions. | Delay/entropy smoothing preserves the exposure mask. A sampled silent position remains a valid zero-response observation; an unsampled position has no delay or entropy. |
| RF color scale | Python's color palettes started at zero; Swift used the displayed minimum. Both figure composers used minimum/maximum scales. | Viridis/Inferno use zero/maximum in the viewers and shared figure-export scales. Gray retains its existing contrast stretch and exact shared export bounds. |
| RGB constant response | Swift used the Gray range helper, which added one to the upper bound of a constant matrix. | RGB uses the true response maximum, subject to its existing minimum scale of one, matching Python. |
| Escape | Swift cleared the timeline even when a waveform zoom or probe filter was active. | Close waveform zoom first, otherwise clear the probe filter, otherwise clear the timeline selection. The RF plot interval remains independent. |

Python input-contract validation is addressed by the accompanying 1.10.1
data-loader changes: metadata that explicitly contradicts the spike-count and
occupancy interpretation must not be silently accepted.

## Numerical regression cases

- Edges `[0, 10, 30, 40]` ms, requested duration `20` ms: source groups are
  `[(0, 0), (1, 1), (2, 2)]`, not `[(0, 1), (2, 2)]`. With counts `[5, 8, 3]`,
  the first group's rate is highest and its delay is `5` ms. Changing to
  `30` ms yields `[(0, 1), (2, 2)]` and delay `15` ms; changing back restores
  the original result, including the cached timeline and RGB views.
- Counts `[4, 0, 16]`, occupancy `[1, 0, 1]`: Smooth 1–3 leaves the response
  `[4, missing, 16]`. Swift previously produced `[6, missing, 14]` at Smooth 2.
  The regression covers count/rate modes, timeline frames, and export scales.
- With histograms `[[0, 0], [9, 0], [0, 9]]`, occupancy `[0, 1, 1]`, and Smooth 1,
  the middle cell's smoothed total is `9`, rather than `6.75`. It exceeds a
  floor of `8`; the unsampled neighbor stays missing. Separate tests retain
  entropy zero for sampled silent cells.
- Responses `[5, 10]`: Gray uses `5–10`, color palettes use `0–10`. A constant
  response of `5` has color scale `0–5` and RGB maximum `5`.

## Related performance changes

- Swift caches exact RF bounds with the pooled matrix. Palette changes adjust
  two scalar limits without pooling, smoothing, or scanning the matrix again.
- Delay and RGB share a single bounded cache of count-derived totals, delays,
  and entropy. Changing the display mode or response floor no longer rebuilds
  and smooths the native time movie. The key includes the dataset, unit,
  quantized resolution, spatial grouping, orientation, and smoothing radius.
- Swift's finite range calculations scan values directly. Shared export ranges
  retain only running minima/maxima instead of collecting every selected
  unit's pixels into a second array.

These are reductions in repeated work and temporary allocation; no macOS
wall-clock speedup is claimed without a macOS benchmark.

## Verification

The Python baseline examples were executed on the configured remote host in
`~/.virtualenvs/rfmapping`, using the isolated 1.10.1 validation checkout and
synthetic files in temporary directories. This confirmed the grouping,
response-smoothing, palette differences, and previously permissive schema
handling. Experimental files were not changed.

Swift regressions are in `StableParityTests.swift` and `FigureExportTests.swift`.
They check physical grouping/cache invalidation, repeated missing-data masks,
sampled zero versus missing exposure, palette changes, shared preview/export
scales, and Escape priority. Swift runtime validation requires the macOS CI
job; the configured project execution host is Linux and cannot run SwiftUI.
The 1.10.1 release validation records the final CI result.

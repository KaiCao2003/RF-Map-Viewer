# SwiftUI Viewer 1.10.2

This is the native SwiftUI implementation for macOS 15 on Apple silicon. It
parses RF/HD/probe files itself and has no Python dependency. RF mapping files
use `.rfmap` (legacy JSON or indexed NPZ), tuning curves use `.tc` (JSON schema), and spike
positions use `.probe` (CSV schema). `.json` and `.csv` remain filename aliases,
but an RF map's extension never enables an older schema. RF maps are primary
documents; tuning and probe files are attached
to a loaded RF map in the figure composer so recorded unit IDs can be matched.
Its `1.10.2` version matches the stable Python macOS reference; `swift` remains
an artifact/tag identity rather than a version suffix.

Version 1.10.2 opens Finder/Launch Services document URLs directly in the
initial window instead of showing the file picker. Later external opens create
independent windows, while launching without a document still shows the picker.
JSON and indexed RF maps may omit the descriptive count/occupancy definitions
and occupancy size marker, matching Python. Explicit conflicting values remain
errors, and raw counts, occupancy values, and actual dimensions remain validated.

Version 1.10.1 fixes nonuniform time-bin grouping, preserves missing exposure
through every smoothing pass, and aligns colored RF ranges and RGB intensity
with Python in the viewer and exported figures. Escape closes waveform zoom,
then clears probe filtering, then restores the full timeline. Temporal results
are reused across Delay/RGB and floor changes; progressive unit loading updates
the cached list incrementally and resolves unit IDs through a lookup table.
See the [parity report](../release/python-swift-parity-1.10.1.md) for cases and impact.

Version 1.10.0 requires the current raw-count/occupancy RF schema. In addition to
the RF tensor, axes, and time edges, every RF map must contain:

- `occupancyTimeSec` matching the tensor's `[nY, nX]` spatial dimensions;
- `responseUnits == "spike_count"` and `responseNormalization == "none"`.

When supplied, `occupancyTimeSecSize` must equal `[nY, nX]`,
`spikeCountDefinition` must equal
`"each_qualifying_trial_contributes_once_per_final_spatial_bin"`, and
`occupancyTimeDefinition` must equal
`"sum_of_qualifying_trial_durations_per_final_spatial_bin"`.

`unitsSpikeCounts` must contain finite, non-negative integer counts. Files from
the earlier presentation-count/normalized RF schema are rejected rather than
converted, and an all-zero occupancy map is rejected. MATLAB's scalar encoding
is accepted only for a declared singleton `unitPool`, spatial axis, or 1-by-1
occupancy map. Its one-dimensional encoding
is accepted for 1-by-N and N-by-1 occupancy maps, disambiguated by the declared
shape.

Version 1.10.0 also opens indexed version-2 NPZ `.rfmap` files, detected by
signature. The native ZIP/NPY reader validates shared metadata and the first
unit before displaying the map; remaining units are cached on a worker. The
bottom-right progress bar reports cached units, navigation prioritizes the
latest uncached selection, and failed reads preserve loaded units with a Retry
button. Closing or replacing a document cancels pending work. Figure Composer
becomes available after the complete unit cache is ready. No Python runtime is
used for indexed input.

Press `-` or use **View → Subtract RF Windows (A − B)** to subtract two
independent, half-open response windows. The initial A/B windows are 80–160 ms
and 0–80 ms. Each window pools counts and occupancy and applies smoothing before
subtraction; negative differences are gray/missing, while zero stays zero.
Sum and difference modes restore their own last-used windows. **Save timing
defaults** under Display saves both modes and B for new windows and Reset.
Pair Windows, displayed CSV, and Figure Composer preserve the mode and both
windows. The zero-bin filter uses A; timeline and Delay/RGB retain their full
independent axes.

`D` shows/hides Display Options. `Command+Shift+.` shows/hides quality-filtered
units without resetting other controls. `Shift+,` makes time resolution
coarser and `Shift+.` finer by one source bin. Plain keys preserve text editing
and modified shortcuts. Delay/RGB derives peaks from spatially pooled and
smoothed count histograms, accounts for bin duration, and retains the first
equal peak. RGB distinguishes black zero response from gray missing occupancy.
Waveform navigation maintains one active read plus the latest pending unit.

Mean firing rate is the default response display and is computed as pooled raw
count divided by pooled occupancy seconds. Spatial reduction and smoothing pool
counts and occupancy independently before division, so unequal occupancy does
not bias the display or initial strongest-cell selection. Raw spike count
remains available as the alternate response display.

The main viewer discovers and independently parses the same read-only
companions as Python: probe positions, an exact positive HD tuning session
(default `1`, with no fallback to another session), and schema-v4 local-average
waveforms. Probe drag-selection filters units, HD line/polar views follow the
selected unit, and the compact waveform supports Same x column / Same shank
selection plus double-click enlargement. Figure Composer includes all three
companions and applies one symmetric microvolt scale across its selected units.
Press `P` to toggle rectangular/polar spatial plots and `Shift-P` to cycle the
palette.

The Python-compatible native zero-spike filter is enabled by default at a
threshold of one bin. It evaluates the current 2-D RF sum window on the source
`y × x` grid before display rebinning or smoothing; timeline views keep their
independent full time axis. Paired windows navigate the sorted union of the
units that pass each window's filter. Probe rows containing the explicit
`nan,nan` missing-position sentinel remain valid units but do not create a
spatial marker.

Document decoding runs on a cancellable worker, with cancellation checks during
count decoding and validation. Window titles use only the source filename and
`RF Map Viewer`. Bounded temporal count and spatial occupancy caches reuse the
same grouped data for timeline and delay views; counts beyond the exact integer
range of a `Double` prefix retain compensated slice summation. Changing the 2-D
RF plot range does not trim the timeline's full time axis.

Figure Composer freezes the eligible unit set and all read-only scientific
inputs when it opens. RF Cartesian/Polar pages share one scalar range across
the selected units, waveform pages share one symmetric microvolt range, and
preview/final rendering use the same frozen payloads. PDF, PNG-directory, and
SVG-directory exports record the RF source, companions, filter, rendering
recipe, and output integrity in the versioned manifest or PDF metadata.

Singleton-y Cartesian maps keep a `30:7` footprint, and the sole Polar row spans
seven radial units in main views, RGB maps, timelines, hit testing, selection,
and figure exports. Multirow geometry is unchanged.

```sh
cd ~/Developer/rfmapping_gui/swift
swift test
swift run RFMappingSwiftUI
```

To build the signed/ad-hoc `.app` bundle on a compatible macOS host:

```sh
script/build_macos_app.sh
```

A `data/` directory is optional. Without bundled RF data, the application
starts empty and opens current-schema `.rfmap` or `.json` files through the
normal document picker/Finder flow.

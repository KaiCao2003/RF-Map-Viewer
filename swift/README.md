# SwiftUI Viewer 1.9.6

This is the native SwiftUI implementation for macOS 15 on Apple silicon. It
parses RF/HD/probe files itself and has no Python dependency. RF mapping files
use `.rfmap` (JSON schema), tuning curves use `.tc` (JSON schema), and spike
positions use `.probe` (CSV schema). `.json` and `.csv` remain filename aliases,
but an RF map's extension never enables an older schema. RF maps are primary
documents; tuning and probe files are attached
to a loaded RF map in the figure composer so recorded unit IDs can be matched.
Its `1.9.6` version places it in the same stable feature generation as the
Python `1.9.x` reference; `swift` remains an artifact/tag identity rather than
a version suffix.

Version 1.9.6 requires the current raw-count/occupancy RF schema. In addition to
the RF tensor, axes, and time edges, every RF map must contain:

- `occupancyTimeSec` with declared shape `occupancyTimeSecSize == [nY, nX]`;
- `responseUnits == "spike_count"` and `responseNormalization == "none"`;
- `spikeCountDefinition == "each_qualifying_trial_contributes_once_per_final_spatial_bin"`;
- `occupancyTimeDefinition == "sum_of_qualifying_trial_durations_per_final_spatial_bin"`.

`unitsSpikeCounts` must contain finite, non-negative integer counts. Files from
the earlier presentation-count/normalized RF schema are rejected rather than
converted, and an all-zero occupancy map is rejected. MATLAB's scalar encoding
is accepted only for a declared singleton `unitPool`, spatial axis, or 1-by-1
occupancy map. Its one-dimensional encoding
is accepted for 1-by-N and N-by-1 occupancy maps, disambiguated by the declared
shape.

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

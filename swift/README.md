# SwiftUI Viewer 1.9.5

This is the native SwiftUI implementation for macOS 15 on Apple silicon. It
parses RF/HD/probe files itself and has no Python dependency. RF mapping files
use `.rfmap` (JSON schema), tuning curves use `.tc` (JSON schema), and spike
positions use `.probe` (CSV schema). `.json` and `.csv` remain filename aliases,
but an RF map's extension never enables an older schema. RF maps are primary
documents; tuning and probe files are attached
to a loaded RF map in the figure composer so recorded unit IDs can be matched.
Its `1.9.5` version places it in the same stable feature generation as the
Python `1.9.x` reference; `swift` remains an artifact/tag identity rather than
a version suffix.

Version 1.9.5 requires the current raw-count/occupancy RF schema. In addition to
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

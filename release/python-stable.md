## Python 1.10.0 stable: indexed RF files and progressive loading

Internal Python build: **110000**. Target: macOS Apple Silicon.

- Open both legacy JSON `.rfmap`/`.json` and version-2 compressed NPZ `.rfmap` files, detected by their contents. Recorded unit IDs, spatial coordinates, raw counts, occupancy normalization, and the complete timeline are preserved.
- Display the first loaded unit, then cache remaining units in a background reader. A small bottom-right progress bar shows loaded units; navigation gives the latest uncached selection priority.
- Reuse cached arrays without reading or decompressing them again. Compact integral counts losslessly using the existing unsigned count representation.
- Cancel pending reads when closing or replacing a document, preserve loaded units after errors, and offer Retry. Multi-unit Figure Composer becomes available once all units are cached.
- Keep Free-Moving alpha separate. Other platform releases are unchanged.

## Python 1.9.9 maintainability and regression coverage

Internal Python build: **10911**.

- Separate RF data, companion adapters, display calculations, settings, and figure composition from the Tk viewer.
- Initialize internal state explicitly and remove fallback paths for incomplete internal objects. Preserve input validation and asynchronous cancellation.
- Consolidate historical model and Tk tests, retain their distinct behavior checks, and share synthetic fixtures.
- Run the stable model, export, performance, and Tk suites on macOS Apple Silicon for pull requests using the same stable test command as release validation.

## Python 1.9.8 display consistency and responsiveness

Internal Python build: **10910**.

- Share Delay calculation between the GUI and Figure Composer. Smoothed equal peaks retain the GUI's first-interval selection instead of shifting because of floating-point prefix subtraction.
- Match RGB exports to the GUI: zero response is black; absent occupancy remains gray with a missing-data pattern.
- Batch spatial processing and reuse a bounded cache of temporal results. Spatial display controls leave companion panels unchanged, and default-cell selection uses an array reduction.
- Keep one waveform read in flight and replace pending requests with the latest selected unit. Discard stale results after document, channel-mode, visibility, or window changes.
- Explicitly exclude Free-Moving/3D and HDF5 modules from the stable macOS package. The separate alpha release is unchanged.

## Python 1.9.7 RF window subtraction

Internal Python build: **10909**.

- Press **-** to switch between the usual RF window sum and **A − B**. Difference mode exposes four time bounds, initially **(80–160 ms) − (0–80 ms)**.
- Settings saves the default mode and independent Sum, A, and B windows. Switching modes remembers their current ranges, and paired windows synchronize both difference windows.
- Negative RF differences display as gray **NaN**; zero remains zero. Displayed-data CSV and Figure Composer use the same result and include both windows. Timelines retain their full time axis.
- Press **D** to show or hide Display Options, with **Hide (D)** shown on the expanded panel.
- Press **Command+Shift+.** to hide or restore units excluded by the zero-bin filter. This works in the viewer and Settings, saves the preference, and preserves current display controls.
- Ships the Python stable build for macOS Apple Silicon with the existing occupancy-aware RF and companion contracts.

### Distribution notes

- The macOS archive is ad-hoc signed and is not Apple-notarized.
- A SHA-256 checksum file is included for the macOS archive.

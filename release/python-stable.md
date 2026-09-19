## Python 1.10.1: keyboard, data, display, and performance fixes

Version **1.10.1**. Target: macOS Apple Silicon (build **110001**).

- Focus the visible plot when a document opens, including saved Delay/Timeline startup tabs. Clicking a viewer control leaves text-entry focus so shortcuts resume.
- Accept the keypad/Fn flags macOS attaches to native arrow-key events, while preserving Command, Control, and Option chords.
- Keep F, D, and P working with Caps Lock. Only Shift+P cycles the palette.
- Preserve input-field editing, open-picker navigation, and window-scoped actions.
- Add native Cocoa keyboard regression coverage alongside Tk interaction tests.
- Keep missing-exposure cells missing through temporal smoothing, without excluding sampled zero responses. This corrects Delay/RGB values near holes in the sampling grid.
- Match colored RF exports to the screen's zero-based color scale; Gray keeps its data range.
- Allow nonuniform time bins to be grouped across their full physical duration; the resolution is no longer incorrectly capped by the number of source bins.
- Validate raw-count normalization/definition markers and occupancy dimensions for both JSON and indexed RF files. Preserve large integer IDs and prevent unsigned count-sum overflow.
- Refresh probe filtering, paired selections after background caching, and Auto tuning orientation immediately when their controls change.
- Reuse visible-unit results while rebuilding the picker. A synthetic 512-unit, 1000-bin refresh fell from 54.492 ms to 0.762 ms (remote Linux validation; median).

These fixes correct viewer calculations and contract enforcement; they do not
change RF analysis or experimental inputs. Swift receives the corresponding
fixes in its own 1.10.1 release. Windows remains available from the Python
1.10.0 release; Free-Moving alpha is unchanged.

## Python 1.10.0 stable: indexed RF files and progressive loading

Version **1.10.0**. Targets: macOS Apple Silicon (build **110000**) and
Windows x64 (build **11000**).

- Open both legacy JSON `.rfmap`/`.json` and version-2 compressed NPZ `.rfmap` files, detected by their contents. Recorded unit IDs, spatial coordinates, raw counts, occupancy normalization, and the complete timeline are preserved.
- Display the first loaded unit, then cache remaining units in a background reader. A small bottom-right progress bar shows loaded units; navigation gives the latest uncached selection priority.
- Reuse cached arrays without reading or decompressing them again. Compact integral counts losslessly using the existing unsigned count representation.
- Cancel pending reads when closing or replacing a document, preserve loaded units after errors, and offer Retry. Multi-unit Figure Composer becomes available once all units are cached.
- Includes a Windows installer and portable ZIP, built and smoke-tested by GitHub Actions.
- Preserve unfinished RF time-field edits during background plot redraws; normalize ranges when edits are committed.
- Keep Free-Moving alpha separate.

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
- SHA-256 checksum files are included for the macOS archive and Windows packages.

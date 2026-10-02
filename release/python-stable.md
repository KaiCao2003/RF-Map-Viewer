## Python 1.11.2: RF overlays and native macOS interaction

Version **1.11.2**. Target: macOS Apple Silicon (build **111002**, Full edition).

- Configure saved RF overlays in **Settings → RF Map**: None, 2D, 1D, or Both, with adjustable border width and colors. Defaults are green for 2D, yellow for 1D, and orange for their intersection. Borders follow the original bins even when display bins are grouped.
- Keep RF and RF Results plots at the same position and size for direct comparison when switching tabs. Preserve rectangle/polar coordinates and flips.
- Restore the native macOS close, minimize, and zoom controls on Welcome. Support trackpad and mouse-wheel scrolling throughout Settings forms and in Timeline.
- Open unit, response, and saved-result details from **View → Inspector…**. Start unavailable probe, waveform, and tuning-curve panels folded, and expand them when data is attached.
- Remove duplicate plot headings and shorten the unavailable-Hz message to “Showing spike count as Hz is unavailable.”

The implementation passed 831 native macOS tests and packaged loader, plot, and export smoke checks before this version bump. RF/Results alignment and overlays were checked with real recordings; Settings trackpad scrolling was confirmed on the local Mac. Inputs and saved analysis files remain read-only.

This release updates Python macOS only. Windows remains at 1.11.1, Swift at 1.10.3, and Web at 1.10.2. Historical release notes follow.

## Python 1.11.1: RF rate correction and saved analysis results

Version **1.11.1**, Full edition. Targets: macOS Apple Silicon (build **111001**)
and Windows x64 (build **11101**).

- Correct RF firing rate to `counts / (stimulusPresentationCounts × response-window seconds)`, including timeline frames, spatial pooling, smoothing, and exports. Files without saved presentation counts open in Spike count mode and explain why Hz is unavailable.
- Add **RF Results** for saved 1D/2D excitatory/inhibitory masks, centers, parameters, and available QC. Results are matched by recorded unit ID and read without rerunning analysis or modifying inputs.
- Choose visible tabs and the initial tab in **Settings → General**. Preferences persist and apply to open and new document windows; number shortcuts follow the visible tabs.
- Retire the separate Free-Moving alpha app, its HDF5 Square/Bar and spherical 3D views, exposure/trial and geometry QA, and alpha packaging. The stable viewer and standalone session/EBC HTML report remain available.

The Windows update also includes the Figure Studio, saved layouts, and viewer
improvements introduced since Windows 1.10.0. Its portable ZIP and Inno Setup
installer use the same Python viewer source. The Windows packages are built
from a packaging-only follow-up commit; the GitHub release body records that
commit when the verified artifacts are uploaded. The existing `python-v1.11.1`
tag and macOS assets remain unchanged.

Swift remains at 1.10.3 and Web at 1.10.2. Historical release notes follow.

## Python 1.11.0: Figure Studio, saved layouts, and viewer lifecycle

Version **1.11.0**. Target: macOS Apple Silicon (build **111000**).

- Arrange scientific figures in Figure Studio using draggable, resizable frames, named size presets, and multiple pages. Save and restore compositions as `.rfmlayout` files.
- Preview the current unit and page; export selected units with independent per-unit RF and waveform scaling, opaque white backgrounds, and recorded source provenance.
- Save viewer defaults in General, RF Map, Waveform, and Tuning Curve settings. Display saved HD Class 3 classifications and accept legacy rate-only tuning files without inventing spike counts or occupancy.
- Close Window and Close All Windows keep the app running. Reopening from the Dock with no open windows shows Welcome; Figure Studio can be reopened after closing.

Windows remains available from Python 1.10.0; Swift and the Free-Moving alpha are unchanged. The Web source receives settings, HD compatibility, recent-file actions, Probe selection, and paired-tab synchronization; its Figure Studio/export and CCG updates are excluded.

## Python 1.10.4: welcome window, independent CCG, and clockwise alignment

Version **1.10.4**. Target: macOS Apple Silicon (build **110004**).

- Start with a compact, single-column welcome window offering Open and native macOS recent documents. The file chooser opens only on request.
- Open Cross-correlogram from Utilities without an RF document. Closing the welcome or RF window leaves the utility running; unavailable recent files return to the welcome window.
- Align RF −90° with HD 270°, RF 90° with HD 90°, and 0° with 0° in line and polar views.
- Center HD line plots without mirroring their values; keep polar curves and angle labels clockwise in both the viewer and figure exports.
- Use opaque white HD plot backgrounds and preserve the source TC angles, counts, and firing rates.

Swift 1.10.3 receives the welcome window and corresponding alignment fixes. Web 1.10.2 receives the alignment fixes.

## Python 1.10.3: probe interaction and cross-correlograms

Version **1.10.3**. Target: macOS Apple Silicon (build **110003**).

- Drag across the probe panel to select a spatial region with a live rectangle; clicking selects the nearest visible unit and clears the region filter.
- Open **RF Map Viewer → Utilities → Cross-correlogram…** to compare two or three units from a session. Positive lag means the second unit fires after the first; plots can be saved as opaque-background PNGs.
- The utility reads the matching probe's ADC spike times and Kilosort cluster assignments through Pynapple.
- Includes a self-contained HTML renderer for exported session-overlay data.

Windows remains available from Python 1.10.0; Free-Moving alpha is unchanged.

## Python 1.10.2: RF metadata compatibility

Version **1.10.2**. Target: macOS Apple Silicon (build **110002**).

- Open JSON and indexed NPZ RF files when `spikeCountDefinition`, `occupancyTimeDefinition`, or `occupancyTimeSecSize` is absent. Validate occupancy against the spatial axes, including MATLAB singleton-axis JSON encoding.
- Reject explicit conflicting definitions or sizes. Continue requiring raw non-negative integer counts, `responseUnits=spike_count`, `responseNormalization=none`, and valid occupancy.
- Preserve read-only input handling; no RF analysis or experimental data changes are required.

Swift 1.10.2 and Web 1.10.1 receive the corresponding RF compatibility fixes.
Windows remains available from Python 1.10.0; Free-Moving alpha is unchanged.

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

# Python RF Map Viewers

This directory contains the Python/Tk RF Map Viewer `1.11.2`, launched by
`rfmapping_gui.py` or the installed `rfmapping-viewer` command. The separate
Free-Moving alpha and its HDF5 Square/Bar, 3D-sphere, and exposure-QA paths have
been retired. The offline session/EBC HTML report script remains available.

## User Guide — Stable RF Map Viewer

This guide is for the read-only Python/Tk RF Map Viewer. It walks through a
typical session from opening an RF map to inspecting units and exporting the
current view.

**At a glance:** open an RF map, choose a unit, select an RF time window, then
use the RF, Delay / RGB, or Timeline view to inspect the response, or RF Results
to inspect saved detections. Companion
files add probe positions, head-direction tuning, and local waveforms. The RF
input is never edited; exports are created only when you request them.

### Contents

- [Open an RF map](#open-an-rf-map)
- [Find your way around](#find-your-way-around)
- [Choose units and use the Probe layout](#choose-units-and-use-the-probe-layout)
- [Read the views](#read-the-views)
- [Set the response window and display](#set-the-response-window-and-display)
- [Add head-direction and waveform companions](#add-head-direction-and-waveform-companions)
- [Compare viewer windows](#compare-viewer-windows)
- [Export data and figures](#export-data-and-figures)
- [Cross-correlogram utility](#cross-correlogram-utility)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [When something is unavailable](#when-something-is-unavailable)

### Open an RF map

1. Launch **RF Map Viewer**. If no document is supplied, a nonmodal welcome
   window shows **Open RF Map…** and **Recent Documents**. The file chooser
   opens only when you request it, so **Utilities → Cross-correlogram…** is
   available immediately without opening an RF map.
2. Choose a current RF mapping document (.rfmap or .json). You can also use
   **Open…** in the toolbar or **File → Open RF Map in New Window…** to open
   another recording while keeping the current window available.
3. Wait for the first unit to appear. Large indexed .rfmap archives show a
   cache indicator while later units load. You can inspect units as they
   become available; **Export…** becomes available after the archive cache
   finishes. If caching reports an error, use **Retry**.

On macOS, recent documents use the application's native `NSDocumentController`
history. Open one from the welcome window or **File → Open Recent**.
**Clear Recent** clears that history without changing the files.
On macOS, **Close Window** and **Close All Windows** keep the app running.
Click its Dock icon or launch it again with no open windows to show Welcome.
Quit RF Map Viewer to exit the app. An open Cross-correlogram utility remains
available when its RF document closes. A missing or unreadable recent file
returns to the welcome window.

The viewer reads the RF data without changing the source file. A version-2
indexed archive can be named .rfmap or .json; the viewer detects its file
signature. Ordinary JSON RF maps remain supported.

### Find your way around

| Area | What it is for |
| --- | --- |
| Top toolbar | Move to the previous or next unit, choose a unit, open another map, or open Figure Composer. |
| Plot controls | Choose the displayed metric, target time width, and RF time window. The controls shown depend on the selected view. |
| Main tabs | **RF**, **Delay / RGB**, and **Timeline** show different summaries of the selected unit. |
| Left sidebar | Pair windows, view the Probe layout, select units by location, and inspect the local waveform. |
| Selected-cell details | Read the selected spatial cell's coordinates, response values, peak interval, and delay. |
| Status bar | See loading, cache progress, and the result of an export. |

Use the app's **Settings…** command (**⌘,** on macOS; **View → Settings…**
where that menu is shown) to save defaults for future viewer windows.
Settings are grouped under **General**, **RF Map**, **Waveform**, and
**Tuning Curve**.

### Choose units and use the Probe layout

Use the unit picker or the left and right toolbar buttons to move between
recorded units. The arrows and **[ / ]** keys do the same. The displayed
cluster ID is the unit's recorded ID; the three-digit index is its position in
the RF file's unit list.

The optional **Hide units with zero-spike RF bins** filter is enabled by
default with a threshold of one. It hides a unit when the number of source
spatial bins with no spikes over the active RF window, or with unavailable
occupancy, reaches the threshold. The count is measured on the original y-by-x grid,
before display rebinning or smoothing. At the default threshold, one such bin
is enough to hide a unit. The filter changes which units are available in the
picker; it does not remove data from the RF file. Toggle it with
**View → Show Filtered Units** (or **⌘⇧.**). Change the threshold in
**Settings → RF Map**; a higher threshold allows units with more zero bins
through. If no units remain, turn the filter off, raise its threshold, or
choose a different RF window.

Probe geometry is optional. Choose **File → Attach Probe Geometry…** or use
**Choose positions.probe or .csv…** in the sidebar. If matching geometry is
available beside the recording, the viewer may discover it automatically.
The Probe view shows channels and units with known positions:

- Drag a rectangle to restrict unit navigation and Figure Composer to units
  in that spatial region.
- Click near a plotted unit to select its nearest visible unit; this also
  clears an active rectangle filter.
- Use **Clear** to remove the region filter. **Esc** also clears it when a
  Probe region is active.

Units without usable positions remain part of the RF data but cannot be
selected by clicking or included by a Probe-region filter.

### Read the views

**RF** shows the selected unit's spatial response over the RF window. **Mean
firing rate (Hz)** is the default metric; choose **Spike count** to show the
raw count total. Hz divides counts by `stimulusPresentationCounts` times the
selected response-window duration. Spatial grouping and smoothing pool counts
and presentation-based exposure separately. Files without presentation counts
open in Spike count mode and explain why Hz is unavailable.

**RF Results** reads the saved analysis sidecars beside the open RF document.
Choose **2D / 1D / Both** and **excitatory / inhibitory** to inspect the current
unit's saved masks and centers. RF and RF Results share the same plot size,
coordinates, spatial grouping and polar/Cartesian layout, so switching tabs
keeps corresponding bins aligned. Saved 2D centers use crosses; 1D centers use
triangles at the axis edge. Unit matching uses recorded IDs. This tab does not
run detection. Legacy 1D projections require their saved analysis provenance.
Use **Reload** after the analysis producer updates the files.

**Settings → RF Map → Saved RF overlay** adds saved detections to RF as colored
bin borders. Choose **None / 2D / 1D / Both**, the detection polarity, border
width and colors. Defaults are green for 2D, yellow for 1D and orange for their
intersection. Borders retain the original detection bins when the RF display
combines bins; orange marks only an actual original-bin intersection. The saved
detection interval appears beside the RF response interval.

In **Settings → General → Visible tabs**, show or hide RF, Delay / RGB,
Timeline and RF Results. At least one tab must remain visible. The preference
persists and applies to open and new document windows. **Initial tab** selects
the starting page; if that page is hidden, the first visible page is used.

Click a map cell and open **View → Inspector…** to inspect it. **Selection** reports its
spatial indices and positions, value in the selected time bin, value over the
RF window, value over the full time support, peak response and peak interval,
and count-rate peak delay. If display bins combine multiple source cells, the
reported index ranges describe that group. **Saved results** contains detection
parameters, QC, source paths and compatibility diagnostics. Closing Inspector
keeps the document open.

**Delay / RGB** summarizes response timing over the full recorded time axis.
The Delay map colors each spatial location by the center of its peak
count-rate interval. Turn on **RGB composite** to encode the selected response
metric in red, count-rate peak delay in green, and temporal entropy in blue.
The RF time window does not crop this view.

**Timeline** pairs a response-over-time summary with small spatial maps for
successive time intervals. Select an interval to move the active time bin, or
select a cell in one of the small maps to inspect that location. Use **↑ / ↓**
to move through time bins. **Esc** or **Navigate → Show Full Timeline Range**
restores the full timeline selection. The Timeline always spans the full
physical time axis, regardless of the RF window.

The selected **Target width** groups adjacent source time bins into wider
intervals. It changes the time detail used by the plotted bins; it does not
discard source data. **Shift+,** makes the width coarser and **Shift+.** makes
it finer, one source bin at a time.

### Set the response window and display

The **RF window** selects the time interval used by the 2-D RF response map
and by the zero-bin unit filter. Its endpoints snap to source-bin edges, and
the ending edge is excluded. The initial Sum window is 0–200 ms when those
times are present in the recording. Use **Reset** to restore the saved default
for the active RF mode.

Choose **Sum** for the response within one window. Choose **A − B** (press
**-**) to compare two windows. The default comparison uses A = 80–160 ms and
B = 0–80 ms. Each window uses the selected metric and spatial display
settings before subtraction. Positive values indicate larger responses in A;
negative results are shown as unavailable gray cells, while zero remains a
valid value. The timeline and Delay / RGB views still use the full time axis.

Open **Display Options** with **D** to adjust the visual layout:

- **X bins** and **Y bins** control how many spatial bins are drawn. Smaller
  numbers combine more neighboring source bins.
- **Smooth** applies spatial smoothing; 0 leaves the map unsmoothed.
- **Palette** changes the RF heatmap colors.
- **Polar layout** displays the x dimension around a circle and the y
  dimension along its radius. Press **P** to toggle it.
- **Polar radius** chooses which y direction is placed toward the center.
- **Invert Y** flips the vertical orientation to match a MATLAB display; press
  **F** to toggle it.

Rebinning and smoothing affect the displayed map and its displayed-data
export. They do not rewrite the source counts or occupancy. A zero response
and a location with unavailable occupancy are different: unavailable cells
are marked separately. In A − B mode, a negative difference is also shown as
unavailable gray.

### Add head-direction and waveform companions

The RF map is the primary document. Head-direction tuning curves, Probe
positions, and local average waveforms are optional companion data and are
never required to open the RF map.

- **HD tuning curve:** the viewer can discover a matching tuning_curves.tc
  or tuning_curves.json, or you can choose **File → Attach Tuning Curves…**.
  Saved HD classes 0–3 are supported; Class 3 appears in the tuning-panel badge,
  and its saved κ cutoff appears in provenance. The viewer browses all RF units
  and does not apply an HD-class selection filter or recalculate classification.
  The **Tuning Curve Session** setting selects the exact positive session
  number used for discovery; it does not silently substitute another session.
  Use **Settings → Tuning Curve** to choose line or polar presentation,
  display-bin count, smoothing, a shared comparison scale, and layout.
- **Local average waveform:** when the matching read-only waveform artifact
  is available, the sidebar shows the selected unit's average signal across
  nearby channels. Use **Settings → Waveform** to show or hide it and choose
  **Same x column** or **Same shank**. Double-click the compact plot to enlarge
  it; double-click again or press **Esc** to close the enlarged view.
- **Probe positions:** attach a matching .probe or .csv position file to
  enable the spatial layout and region selection described above.

If a companion is missing or does not contain the selected unit, the RF map
remains usable. Attach the matching file or continue without that view.

### Compare viewer windows

Open a second RF map, then enable **Sync viewer windows** in each window's
sidebar. Paired windows synchronize unit navigation, selected spatial cell,
time selection, RF windows, and display settings. Each window continues to
show its own recording. When a selected cluster is absent or filtered in one
file, that window reports it as unavailable rather than substituting a
different unit.

### Export data and figures

**Export Displayed Data CSV…** (**⇧⌘E**) writes one row for each displayed
spatial cell in the current unit's RF map. The file includes the response
value, x/y indices and positions, value mode and units, occupancy range,
display grouping, time resolution, RF window, smoothing, orientation, palette,
and source identity. It represents the current displayed RF result; it is not
a replacement for the original RF map.

Use **Export…** or **File → Export Figures…** (**⌘E**) to open Figure Studio and build a reusable
multi-page figure layout:

1. Choose **All** or individual units. The available unit list
   follows the active unit and Probe filters.
2. Choose the output format: **PDF**, **PNG**, or **SVG**.
3. Add pages, give each page a clear name, and add the desired plot types.
   Drag a view on the page to move it. Drag its lower-right corner to resize,
   or choose **Small**, **Wide**, **Tall**, **Large**, or **Full** from the
   size menu. Frames snap to a shared grid; neighbouring views move when
   needed to make room. Arrow keys move the selected view. **Arrange**
   fills the page evenly. Each page supports up to nine views. **Option+↑/↓**
   reorders the selected page.
4. Choose the destination and select **Export**. Every selected unit receives
   the same page template. Check the page count and destination reported when
   the export finishes.

**Normalize per unit** is on by default. Each unit uses its own RF color range
and waveform amplitude range, matching the main viewer; the numeric values and
physical units stay unchanged. Turn it off to compare exported units with
shared limits. The preview always uses the displayed unit's own scale, and
shared limits are calculated when exporting.

Use the preview's unit picker or arrows to inspect a unit without changing the
export selection. **All** and **Clear** change only the output selection.
Preview renders only the current unit and current page. Dragging and resizing
track cached plot pixels immediately; a fresh render follows on release.

Use **Layout → Save Layout…** (**⌘S**) to save a `.rfmlayout` file, and
**Layout → Load Layout…** (**⌘⇧L**) to restore it. Layouts include page names,
plot types, positions, sizes, normalization, output format, RF sum/subtraction
ranges, Timeline range and active bin, time-bin grouping, spatial grouping,
palette, smoothing, and HD display settings. Loading keeps the current source,
attached companions, and selected units. The source's time and spatial axes
must match the saved layout so a bin cannot silently refer to a different
time or position. An incompatible layout leaves the current layout intact.

Figure Studio freezes the current viewer settings and companion selection
when it opens. Return to the main window and reopen the composer to capture a
different state, or load saved layout settings. Indexed RF maps must finish
caching before multi-unit figure export is available. PDF creates a multi-page file; PNG and SVG create
page-based output directories with a manifest. SVG embeds a lossless PNG.

### Cross-correlogram utility

On macOS, open **RF Map Viewer → Utilities → Cross-correlogram…** to compare
two or three units from one session and probe. Choose the session folder, the
probe, and the units; two units produce one pair and three units produce all
three pairwise plots. The default bin width is 1 ms and the default window is
±50 ms. Positive lag means the second unit fires after the first. The vertical
axis is conditional firing rate in Hz. Save the figure from the utility when
the comparison is ready.

The utility uses spike times in seconds from data/probeA/adc_spike_time.npy
or data/probeB/adc_spike_time.npy, matched to the selected session's
kilosort/ProbeA/kilosort_N/spike_clusters.npy or ProbeB equivalent. It is
separate from the RF map plots and does not change the selected unit or RF
document.

### Keyboard shortcuts

Open **Help → Keyboard Shortcuts** for the in-app list.

| Shortcut | Action |
| --- | --- |
| ← / → or [ / ] | Previous / next available unit |
| ↑ / ↓ | Previous / next timeline bin |
| Shift+, / Shift+. | Coarser / finer target time width |
| 1 / 2 / 3 / 4 | Switch between visible tabs in order |
| F | Invert Y |
| P / Shift+P | Toggle Polar layout / cycle palette |
| - | Toggle RF Sum / A − B |
| D | Show or hide Display Options |
| ⌘⇧. | Show or hide units filtered by RF bins |
| ⌘O | Open an RF map |
| ⌘E | Open Figure Composer |
| ⇧⌘E | Export displayed data as CSV |
| Esc | Close waveform zoom, clear Probe region, or restore the full Timeline range |

### When something is unavailable

- **No units pass the filter:** widen the RF window, raise the zero-bin
  threshold, or turn off **Hide units with zero-spike RF bins**.
- **A companion panel is empty:** attach a matching file and check that it
  includes the selected cluster and probe.
- **A unit is missing after pairing:** that cluster ID is not usable in this
  RF file or is hidden by its filter. Select another unit or adjust the
  filter in that window.
- **Figure export is not ready:** wait for indexed-unit caching to finish and
  confirm that at least one unit passes the active filters.
- **A cell is gray in A − B:** the difference is negative or the source
  occupancy does not support a value there. A zero difference is valid and is
  displayed as zero.

---

## Stable viewer 1.11.2

Version 1.11.2 restores native macOS welcome-window controls and trackpad
scrolling in Settings and Timeline. Settings can overlay saved 2D, 1D, or both
RF results using configurable bin borders and separate intersection colors.
RF and RF Results keep the same plot geometry when switching tabs.
Unit and response details are available from View → Inspector; unavailable
probe, waveform, and tuning-curve panels start folded.

Version 1.11.1 corrects RF Hz using saved presentation counts and the response
window duration, adds the saved RF Results tab and tab visibility preferences,
and retires the separate Free-Moving alpha application.
The Windows 1.11.1 packaging target brings these changes and the Figure Studio
features below to Windows x64, using the same Python viewer source.

Version 1.11.0 introduces movable, resizable figure widgets with preset frames,
saved layouts that retain their rendering settings,
per-unit normalization, and a current-unit preview independent of the output
selection. On macOS, Close All Windows keeps the application running; reopening
it from the Dock restores the welcome window when no other windows remain.

HD tuning and RF azimuth both increase clockwise: RF −90° aligns with HD 270°,
RF 90° with HD 90°, and 0° with 0°. Line plots wrap around zero without
mirroring the rates; polar curves and labels use the same direction on screen
and in figure exports. HD plots use opaque white backgrounds. TC data is unchanged.

Version 1.10.3 added probe-panel drag selection with a live rectangle. A click selects
the nearest visible unit and clears any region filter.

Open **RF Map Viewer → Utilities → Cross-correlogram…** on macOS to compare
two or three units independently of the RF plots. Choose a session folder,
probe, and units; three units produce the three pairwise plots. The default is
1 ms bins and a ±50 ms window. Positive lag means the second unit fires after
the first, and the y-axis is conditional firing rate in Hz. The utility reads
`data/probeA/adc_spike_time.npy` (seconds) and the matching session's
`kilosort/ProbeA/kilosort_N/spike_clusters.npy`, or the corresponding ProbeB
files, using Pynapple. Figures can be saved with an opaque white background.

Version 1.10.2 opens JSON and indexed RF files when the descriptive
`spikeCountDefinition`, `occupancyTimeDefinition`, or `occupancyTimeSecSize`
fields are absent. It still rejects explicit conflicting definitions and sizes,
normalized responses, invalid raw counts, and inconsistent occupancy. This
corrects compatibility with existing files without changing their data.

Version 1.10.1 restores macOS keyboard navigation, gives newly opened documents
focus on their visible plot, and returns keyboard focus to clicked viewer
controls after editing a field. Caps Lock preserves F/D/P actions; Shift+P
selects palette cycling. Native arrow-key keypad/Fn flags do not suppress
navigation. Colored RF exports retain the live view's zero-based color scale;
nonuniform time bins can be grouped across the full physical duration.
Missing-occupancy cells stay missing during temporal smoothing; sampled zero
responses still contribute normally. Raw-count schema markers are validated,
and large integral IDs and count sums retain their precision. Unit-picker
refreshes reuse visibility results; probe filtering, paired selections after
background loading, and Auto tuning orientation remain synchronized.

Version 1.10.0 adds indexed RF input and progressive caching.

Version 1.9.9 separates data, display calculations, settings, and figure
composition into focused modules. Stable regression tests share synthetic
fixtures and run once through the same command in remote validation and
macOS PR checks.

Version 1.9.8 shares Delay/RGB calculations between the viewer and Figure
Composer, preserving the first equal peak after smoothing and distinguishing
black zero-response cells from gray, marked cells with no occupancy. Temporal
results use a bounded cache, spatial display controls avoid redrawing companion
panels, and waveform navigation keeps one active read plus the latest pending
unit. The stable macOS package does not require HDF5.

The stable viewer accepts legacy JSON `.rfmap`/`.json` files and version-2
indexed NPZ `.rfmap` files. It detects the file signature rather than requiring
a `.npz` extension. The indexed reader decodes shared metadata and the first
unit, displays the plot, then caches the rest off the Tk thread. A small
bottom-right progress bar reports cached units. Selecting an uncached unit
prioritizes it; repeated visits reuse immutable arrays. Multi-unit Figure
Composer is available after the cache completes. Errors preserve loaded units
and expose Retry; closing or replacing a document cancels pending reads.

Legacy JSON still loads the complete document. Indexed archives store UTF-8
JSON in a `uint8` `metadata` entry, plus `unitPool`, `xPositions`, `yPositions`,
`timeBinEdges`, `occupancyTimeSec`, and one `unit_<ID>` array per recorded ID.
Each unit has axes `(y, x, time)`, including singleton dimensions. The reader
requires `formatVersion=2`; no transpose, time-axis trimming, or rate conversion
is applied while reading. See the shared file contract for details.

JSON documents and indexed archives may omit `spikeCountDefinition`,
`occupancyTimeDefinition`, and `occupancyTimeSecSize`. Occupancy is validated
against the spatial axes in `unitsSpikeCountsSize`, including MATLAB's
singleton-axis JSON encoding. Explicit definitions and sizes must still match
the raw-count contract. Both formats require `responseUnits=spike_count` and
`responseNormalization=none`, valid occupancy, and non-negative integer counts.

Counts are raw non-negative integers. Occupancy dimensions come from the
y-by-x axes in `unitsSpikeCountsSize`. Each qualifying trial contributes once
per final spatial bin, and occupancy sums the qualifying trial durations.
At least one spatial cell must have positive occupancy.

The default RF value is mean firing rate in Hz: counts in the selected response
window are divided by presentations times the window duration in seconds.
Spatial rebinning and smoothing pool counts and presentation-based exposure
independently before division. Legacy files without presentation counts are
viewable in count mode. Raw spike
count remains available as the other value mode. MATLAB `jsonencode` numeric
scalars are restored for singleton `unitPool`, `xPositions`, `yPositions`, and
`occupancyTimeSec` dimensions. A singleton-y RF map keeps the `30:7` Cartesian
footprint and seven-unit Polar ring across live plots, timeline thumbnails, hit
testing, and figure exports.

The RF tab can show a compact **Local Average Waveform** panel in the left
sidebar. Double-clicking the waveform opens a large, in-window view; another
double-click, **Esc**, or **Done** restores the compact panel. Unit information
and response timing are available in **View → Inspector…**. The probe/waveform
sidebar and HD pane start folded when their companion data are absent; attaching
data expands the corresponding pane. A manually folded pane stays folded. The
viewer auto-discovers the read-only schema-v4 SpikeInterface
artifact at `data/waveform/ProbeA` or `ProbeB` and shows the selected unit's
baseline-corrected average template on the best-PTP channel plus the four
nearest channels. **Settings → Waveform** controls whether the panel is shown
and switches between `Same x column` and `Same shank`; both modes intentionally
match the notebook's nearest-four selector rather than forcing two channels
above and two below. Figure Composer exports the same payload with a shared
symmetric µV scale across selected units and records the manifest, metadata
tables, and selected template files in provenance.

**Settings → Tuning Curve → Tuning Curve Session** selects the positive
same-date session number used for automatic tuning-curve discovery. For an RF
file below `260730_3`, for example, a value of `2` reads only from
`260730_2/data/tuning_curves/ProbeA` or `ProbeB`; it does not fall back to a
different session. The default is session `1`.

Press **P** to switch the RF display between Rectangle and Polar layouts.
Press **Shift+P** to cycle the color palette.
These also work while the closed, read-only unit or value-mode picker has
focus. Editable fields and open pickers keep their normal key handling.

With a plot, plot tab, or toolbar button focused, **← / →** selects the
previous / next unit, **↑ / ↓** selects the timeline bin, and **1–3** switches
plot tabs. **Shift+,** makes the time resolution coarser and **Shift+.** makes
it finer, one source bin at a time, matching the Navigate menu. Input fields
keep their normal editing keys; Command, Control, and Option combinations do
not trigger the plain-key viewer actions.

Press **-** (or **View → Subtract RF Windows (A − B)**) to toggle between
the usual RF window sum and the difference of two independently adjustable
windows. In difference mode the RF controls read
**(start ms – end ms) − (start ms – end ms)**. For example, set A to
**80–160 ms** and B to **0–80 ms** (the initial difference defaults).
**Settings → RF Map → Timing** saves the default mode, the Sum window, and
both A − B windows. Switching modes restores that mode's last used range;
new windows start with the saved defaults, and **Reset** restores the active
mode's defaults. All windows snap to source-bin edges and exclude their end
edge. Each window uses the selected
metric and spatial pooling/smoothing before subtraction; negative differences
display as **NaN** in gray, while zero remains zero. The same result and both
windows are included in displayed-data CSV and Figure Composer exports.
Pair Windows also synchronizes the subtraction mode and both windows. The
zero-spike unit filter continues to use window A. RF windows do not restrict
the timeline or alter Delay / RGB.

Press **D** to show or hide **Display Options**; the expanded button reads
**Hide (D)**. These shortcuts leave text entry, including negative time
values, available while an input has focus.

Press **Command+Shift+.** (or use **View → Show Filtered Units / Hide Units
with Zero RF Bins**) to toggle the Settings zero-bin unit filter. It restores
or hides the affected units while preserving the current time windows,
palette, and other display controls. The filter preference is saved and
updated in open viewer and Settings windows. **Shift+.** alone still adjusts
the target time width.

Run it from source with:

```sh
~/.virtualenvs/rfmapping/bin/python rfmapping_gui.py /path/to/result.rfmap
```

Opening the app without a path shows the native file chooser. Release packages
do not contain or auto-load sample RF data.

Its macOS identity is `RF Map Viewer.app`, bundle ID
`org.local.rfmapping.viewer`, and version/build `1.11.2` / `111002`. Build it with:

```sh
script/build_python_stable_macos_app.sh
```

Stable 1.11.1 targets Windows x64 as a portable ZIP and an Inno Setup installer
(Windows build 11101). On a Windows build host with Python 3.14, PyInstaller,
and Inno Setup 6 installed, build and smoke-test both artifacts with:

```powershell
script/build_python_stable_windows_app.ps1
```

The Windows artifacts are:

- `RF_Map_Viewer-python-1.11.1-full-windows-x64-portable.zip`
- `RF_Map_Viewer-python-1.11.1-full-windows-x64-setup.exe`
- `SHA256SUMS-python-1.11.1-full-windows-x64.txt`

The versioned outputs are written under `dist/windows/`; the builder verifies
the portable executable and a silent temporary installation with the RF
fixture, TkDND, and packaged PDF/PNG/CSV export smoke tests. Both macOS and
Windows builders also run `--self-test-isolated` to exercise the spawned
document loader inside the packaged executable.

The release workflow also accepts `python-stable-windows` to build only a
Windows candidate without publishing. See [release instructions](../release/README.md)
for the Windows follow-up to the existing `python-v1.11.1` release.

Windows and macOS use the same Python viewer source, including cancellable
large-file loading, compact count storage, cached time-window calculations,
and filename-based window titles.

### Stable development and validation

The Tk window and CLI entry point remain in `rfmapping_gui.py`. Supporting
code lives in `rfmapping_viewer/`:

| Module | Responsibility |
| --- | --- |
| `rf_model.py`, `companions.py` | Read-only data adapters and cached display values |
| `display.py` | Spatial grouping, colors, timelines, and raster calculations |
| `settings.py`, `settings_window.py` | Validated preferences and their Tk editor |
| `viewer_state.py` | Paired-window state and waveform worker results |
| `figure_composer.py`, `export_inputs.py` | Figure composition, input identity, and CSV publication |
| `constants.py`, `paths.py`, `tk_support.py` | Stable identity, discovery, and native integration |

Data and display modules can be imported without Tk or HDF5.
Tests import each function from its owning module and share synthetic fixtures
in `tests/gui_test_support.py`.

Run the complete stable regression suite on the configured remote host:

```sh
ssh "$RFMAPPING_REMOTE_HOST" 'cd ~/Developer/rfmapping_gui/python && \
  RF_MAPPING_TEST_PYTHON="$HOME/.virtualenvs/rfmapping/bin/python" \
    xvfb-run -a script/test_python_stable.sh'
```

The remote virtual environment must include Tk; Xvfb supplies a display, not
the Tk runtime. A missing Tk runtime fails the suite. `pytest-stable.ini`
selects stable tests, including all window interactions. The macOS PR check
and stable release job use the same test script without Xvfb.

## Install and launch

Install the stable viewer on the configured execution host:

```sh
ssh "$RFMAPPING_REMOTE_HOST"
cd ~/Developer/rfmapping_gui/python
~/.virtualenvs/rfmapping/bin/pip install -e '.[test]'
~/.virtualenvs/rfmapping/bin/rfmapping-viewer /path/to/result.rfmap
```

The macOS app uses the stable release metadata and build script:

```sh
script/build_python_stable_macos_app.sh
script/install_python_macos_app.sh --preflight
```

`script/build_and_run.sh` also targets this stable application. The default
installer action remains a read-only preflight; installation requires
`--install`.

## Offline session/EBC report

`script/render_session_overlay.py` remains a standalone renderer for exported
session-overlay JSON. Its self-contained HTML report includes session playback,
world geometry, stimulus timing, EBC maps, spike timelines, and PNG snapshots.
It is separate from the retired Free-Moving HDF5 viewer.

```sh
~/.virtualenvs/rfmapping/bin/python script/render_session_overlay.py \
  /path/to/session-overlay.json /path/to/session-overlay.html
```

## Python 1.11.16: shared result statistics and loading progress

Full edition · macOS Apple Silicon build **111016** · Windows x64 build **11117**

- Group results by dimension and exact analysis window. Share mean/SD only when their origin, baseline provenance, and values match; keep excitatory/inhibitory thresholds and result status separate.
- Read saved per-unit baseline mean/SD directly. Label saved Smoothing 1 and computed native RF statistics separately; do not substitute one for the other.
- Show tuning-curve, probe geometry, and waveform loading stages in document progress.
- During reload, show saved-result loading and the RF unit count without treating RF unit completion as completion of companion work.
- Retain the concise Inspector and its position above the owning viewer.

RF analysis, saved detections, and source data are unchanged. The macOS app,
Windows installer, and portable ZIP use the same viewer source. Swift and Web
releases are unchanged.

## Python 1.11.14: concise Inspector results

Full edition · macOS Apple Silicon build **111014** · Windows x64 build **11115**

- Keep Inspector above its owning viewer while the RF map is used.
- Put saved thresholds, computed mean/SD in Hz, detection status, and analysis windows in concise result summaries.
- Keep full paths, algorithm parameters, and provenance in secondary details.

The mean/SD calculations and saved-result overlays are unchanged. Computed
statistics describe the current RF source, not the original detector's stored
statistics. The macOS app, Windows installer, and portable ZIP use the same
viewer source. Swift and Web releases are unchanged.

## Python 1.11.13: Inspector result statistics

Full edition · macOS Apple Silicon build **111013** · Windows x64 build **11114**

- Show saved SD threshold multipliers in **Inspector → Saved results**, separately for 1D and 2D detections.
- Label mean and standard deviation as **computed current-source mean-rate statistics**, using the saved analysis time window and recorded spatial reference.
- Original detector numerical statistics and temporal aggregation were not saved. The computed values describe the current RF source and do not reconstruct the original detector's mean/SD.
- Show missing or unsupported window/reference provenance as unavailable. RF detection is not rerun, and source data is unchanged.

This update is confined to Inspector reporting. The macOS app, Windows
installer, and portable ZIP use the same viewer source. Swift and Web releases
are unchanged.

## Python 1.11.12: configurable RF result paths

Full edition · macOS Apple Silicon build **111012** · Windows x64 build **11113**

- Recognize RF documents in kind directories and read the four short-named saved result files: `rf_2d_ext.npz`, `rf_1d_ext.npz`, `rf_2d_inh.npz`, and `rf_1d_inh.npz`, plus optional `analysis_ext.json` and `analysis_inh.json`.
- Configure the RF path matching regex in **Settings → Paths**, with syntax validation, default restoration, and a preview for the current RF document.
- Use the same saved path rules in RF Results, saved overlays, and document reload. Older source-named result layouts remain supported.

RF inputs and analysis files remain read-only. The viewer reads saved detections;
it does not run RF analysis. The macOS app, Windows installer, and portable ZIP
use the same viewer source. Swift and Web releases are unchanged.

## Python 1.11.10: corrected Windows build 11112

Windows x64 · Full edition · version **1.11.10** · build **11112**

Keep Windows on the Mac 1.11.10 application version while retaining the four
tested fixes: Figure Studio previews, full-byte input checksums, Windows Arial
export fonts, and CSV Save dialog ownership. This replaces the recommended
Windows 1.11.11 download without removing its release history.

The corrected installer and portable ZIP use unique `build11112` filenames.
The existing 1.11.10 tag, macOS downloads, and earlier Windows assets remain
unchanged. Release notes identify this revision's own source commit and CI run.

## Python 1.11.11: Windows Figure Studio preview and exports

Windows x64 · Full edition · build **11111**

- Fix blank Figure Studio previews on Windows: themed preview frames could cover the rendered plots, including HD tuning curves.
- Hash frozen export inputs in binary mode on Windows so saved SHA-256 values match the original RF/TC/probe bytes, including CRLF and Ctrl+Z.
- Load the installed Windows Arial fonts for readable export labels instead of falling back to Pillow's small default font.
- Keep the CSV Save dialog attached to the active viewer so it remains visible above the document window.
- Preserve the shared Mac 1.11.10 viewer features.

The installer and portable ZIP are built from the same source. Publish the exact
Windows candidate artifacts after test-suite, packaging, and native preview
verification. Existing 1.11.10 release assets remain unchanged.

## Python 1.11.10: Windows feature parity

Windows x64 · Full edition · build **11110**

- Bring Windows up to the shared Mac 1.11.10 viewer source, including four independent excitatory/inhibitory RF overlay selections, saved overlay preferences, and multiple RF component centers.
- Annotate ADN units from Utilities with HD-class colors and filters, Undo/Clear, per-probe saving, and Only ADN filtering for viewing and figure export. Configure probe unit-info hover in Settings.
- Inspect unit and response details through View → Inspector, with aligned RF and RF Results plots.
- Reload the document and companions with **Ctrl+R**, preserving visible plots until the new data is ready. Indexed RF caching starts at the current unit and proceeds outward.
- Use the compact waveform header and taller plot, and restore keyboard focus after closing ADN annotation.
- Keep Windows Ctrl shortcuts working with CapsLock, NumLock, and ScrollLock. Exports retain opaque white backgrounds and dark labels.

The installer and portable ZIP contain the same viewer. Build verification covers the test suite, installed and portable loading, the spawned document loader, TkDND/TkAgg, PDF/PNG/CSV export, version resources, and SHA-256 checksums.

This release targets Windows. Existing macOS release assets and tags remain unchanged; Mac 1.11.10 source provenance is commit `b319ed3b5723f59ecea01914345cae49d11411ba`.

## Python 1.11.5: RF overlay multiselect

macOS Apple Silicon · Full edition · build **111005**

- Show excitatory and inhibitory 2D/1D RF overlays together using four independent checkboxes.
- Save selected overlays and distinguish inhibitory borders by color and dashes.
- Open saved RF results with multiple component centers per neuron.

[Download macOS app](https://github.com/KaiCao2003/RF-Map-Viewer/releases/download/python-v1.11.5/RF_Map_Viewer-python-1.11.5-full-macos-arm64.zip) · [SHA-256 checksum](https://github.com/KaiCao2003/RF-Map-Viewer/releases/download/python-v1.11.5/SHA256SUMS-python-1.11.5-full.txt)

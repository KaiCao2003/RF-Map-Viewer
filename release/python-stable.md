## Python 1.9.6 companion parity release

Internal Python build: **10908**.

- Adds a compact local-average waveform panel below **Spike Time** in the left sidebar on the RF page. It is no longer a separate main tab.
- Places **Unit Info** at the bottom-right below the HD tuning curve and removes the verbose tuning status line from the visible interface.
- Adds Settings controls to show or hide the panel and choose **Same x column** or **Same shank** channel selection.
- Reuses the viewer's validated waveform payload in Figure Composer, including full channel labels, symmetric shared microvolt scaling, manifest metadata, and companion provenance.
- Opens the native file chooser when the app starts without a file. Production packages contain no automatically loaded sample dataset.
- Ships the Python stable build for macOS Apple Silicon.
- Adds exact positive tuning-session selection (default `1`) and the `P` / `Shift-P` layout and palette shortcuts shared by all three stable viewers.
- Retains the occupancy-aware RF schema and spike-count / mean-firing-rate behavior from the original Python 1.9.5 release.

### Distribution notes

- The macOS archive is ad-hoc signed and is not Apple-notarized.
- A SHA-256 checksum file is included for the macOS archive.

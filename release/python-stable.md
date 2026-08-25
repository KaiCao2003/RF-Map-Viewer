## Python 1.9.5 waveform patch

Internal Python build: **10906**.

- Adds a compact local-average waveform panel directly below the HD tuning curve on the RF page. It is no longer a separate main tab.
- Adds Settings controls to show or hide the panel and choose **Same x column** or **Same shank** channel selection.
- Reuses the viewer's validated waveform payload in Figure Composer, including full channel labels, symmetric shared microvolt scaling, manifest metadata, and companion provenance.
- Opens the native file chooser when the app starts without a file. Production packages contain no automatically loaded sample dataset.
- Ships Python stable builds for macOS Apple Silicon and Windows x64. Windows includes both a portable ZIP and an installer.
- Retains the occupancy-aware RF schema and spike-count / mean-firing-rate behavior from the original Python 1.9.5 release.

### Distribution notes

- The macOS archive is ad-hoc signed and is not Apple-notarized.
- The Windows executables are not Authenticode-signed, so Windows may show a SmartScreen warning.
- SHA-256 checksum files are included for both platforms.

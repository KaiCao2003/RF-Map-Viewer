# SwiftUI Viewer

This is the native SwiftUI implementation for macOS 15 on Apple silicon. It
parses RF/HD/probe files itself and has no Python dependency. RF mapping files
use `.rfmap` (JSON schema), tuning curves use `.tc` (JSON schema), and spike
positions use `.probe` (CSV schema); legacy `.json` and `.csv` files remain
supported. RF maps are primary documents; tuning and probe files are attached
to a loaded RF map in the figure composer so recorded unit IDs can be matched.

```sh
cd ~/Developer/rfmapping_gui/swift
swift test
swift run RFMappingSwiftUI
```

To build the signed/ad-hoc `.app` bundle on a compatible macOS host:

```sh
script/build_macos_app.sh
```

A `data/` directory is optional. Without bundled JSON, the application starts
empty and opens `.rfmap` or legacy `.json` data through the normal document
picker/Finder flow.

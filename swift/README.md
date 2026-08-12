# SwiftUI Viewer

This is the native SwiftUI implementation for macOS 15 on Apple silicon. It
parses RF/HD/probe files itself and has no Python dependency.

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
empty and opens data through the normal document picker/Finder flow.

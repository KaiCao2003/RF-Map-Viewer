# RF Mapping Viewer

RF Mapping Viewer has native Swift/SwiftUI and Python/Tk macOS applications.
The build outputs are kept separate so either implementation can be rebuilt
without deleting or replacing the other.

## Native app

- Swift package entry point: `Sources/RFMappingSwiftUI`
- Native app: `dist/RF Mapping Viewer.app`
- Distributable archive: `dist/RF_Mapping_Viewer-macos-arm64.zip`
- Bundle identifier: `org.local.rfmapping.viewer.swift`
- Minimum system: macOS 15 Sequoia
- Architecture: Apple silicon (`arm64`)

Build the signed bundle without launching it:

```sh
script/build_macos_app.sh
```

The default build is ad-hoc signed. Set `RF_MAPPING_CODESIGN_IDENTITY` to a
Developer ID identity for distribution signing. `script/build_and_run.sh`
provides the existing run/debug/log helper modes.

## Python app

- Staged app: `dist/python/RF Mapping Viewer.app`
- Distributable archive:
  `dist/python/RF_Mapping_Viewer-python-macos-universal2.zip`
- Bundle identifier: `org.local.rfmapping.viewer`
- Architecture: Intel and Apple silicon (`universal2`)

Build the Python bundle without launching or installing it:

```sh
script/build_python_macos_app.sh
```

The Python build uses its own output directory and does not modify the native
Swift bundle. It also does not update `/Applications/RF Mapping Viewer.app`;
installation is a separate, explicit step after validation.

Both packaging scripts copy bundled JSON files from `data/` and verify them
byte-for-byte. Finder-opened JSON documents and File > Open each receive
independent viewer state. Legacy JSON without `stimulusPresentationCounts`
remains usable in Spike count mode; normalized values are enabled only when
that metadata is present and valid.

Python runtime checks must follow `AGENTS.md`: execute them on `ssh hhw9l84`
with `~/.virtualenvs/rfmapping`. MATLAB files are not runtime dependencies.

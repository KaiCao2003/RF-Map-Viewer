# Component versioning and releases

Python is the feature reference for this repository. The supported stable
feature generation is `1.10.x`; an implementation one complete feature
generation behind uses `1.9.x`. Patch numbers identify coordinated or
target-specific releases within that feature generation; supported input
contracts are stated explicitly rather than inferred from the patch number.

The separate Free-Moving viewer uses the `1.10` feature generation and is
currently an alpha. Its canonical release version is
`1.10.0-alpha.3`. Python packaging represents the same release as
`1.10.0a3`, while the macOS marketing version remains the Apple-compatible
three-integer `1.10.0`. Alpha 3 adds the explicit pre-load Square/Bar choice
and support for the latest `rfmapping_fm_bar_hdf5_v1` vertical-bar result.

Platform identity never becomes a fourth version component. It belongs in the
component tag and artifact name:

| Component | Release | Tag | Channel |
| --- | --- | --- | --- |
| Python stable (macOS) | `1.10.2` | `python-v1.10.2` | stable |
| Python Free-Moving | `1.10.0-alpha.3` | `python-v1.10.0-alpha.3` | alpha |
| Swift | `1.10.2` | `swift-v1.10.2` | stable |
| Web | `1.10.1` | `web-v1.10.1` | stable |

Published downloads and checksums are available from the repository
[Releases page](https://github.com/KaiCao2003/RF-Map-Viewer/releases). The root
[`README.md`](../README.md#downloads) links directly to each component archive.

Python and Swift 1.10.2 and Web 1.10.1 restore compatibility with JSON and
indexed RF files that omit descriptive count/occupancy markers or the redundant
occupancy-size field. Explicit conflicting markers and sizes are rejected;
raw integer counts, required response-unit and normalization markers, and
actual occupancy data retain their checks. The input files are unchanged.
Swift also handles macOS URL-open events directly, so opening an RF document
from Finder loads that document instead of leaving the initial file chooser open.
Python and Swift use macOS build 110002. Windows remains at 1.10.0, and the
Free-Moving alpha remains at 1.10.0-alpha.3.

Python and Swift 1.10.1 repair keyboard behavior, RF color scales, missing
exposure during smoothing, and progressive-loading consistency and performance.
Swift time grouping now uses physical bin edges as Python does. Python validates
the shared raw-count contract and preserves large integer counts. See the
[parity report](python-swift-parity-1.10.1.md) for reproducible cases and impact.
Those patch releases targeted macOS arm64; Windows and Web stayed at 1.10.0.

Stable 1.10.0 aligns Python, Swift, and Web on indexed version-2 RF input,
progressive unit caching, RF window subtraction, display shortcuts, and the
shared Delay/RGB display and export semantics. Python ships macOS arm64 and
Windows x64 packages. The separate Free-Moving alpha is unchanged.

Python 1.9.9 separates viewer responsibilities into focused modules, removes
internal fallback branches, consolidates historical tests, and adds a macOS
Apple Silicon PR regression check for the stable viewer.

Python 1.9.8 aligns exported Delay/RGB maps with the live GUI, shares and caches
temporal display calculations, and limits waveform loading to one active
read plus the latest pending selection. The stable macOS package explicitly
excludes the separate Free-Moving/HDF5 modules.

Python 1.9.7 added RF window subtraction with saved defaults, gray NaN display
for negative differences, and shortcuts for display options and the zero-bin
unit filter. Its macOS package retains the existing input contract.

Stable 1.9.6 introduced the coordinated companion-parity release for the current regular
`RFmapping_core.m` output. Python, Swift, and Web require raw spike counts plus
the spatial `occupancyTimeSec` matrix, normalize firing rate as
count/occupancy, and start in firing-rate mode. Earlier occupancy-free or
already-normalized RF payloads are intentionally outside this release.
Python, Swift, and Web additionally ship exact positive tuning-session
selection, the schema-v4 SpikeInterface waveform viewer/exporter, and matching
rectangle/polar and palette keyboard shortcuts.

Each active component records a `feature_generation_offset` from the Python
stable reference. The current Swift/Web offset is `0`, matching Python stable. A viewer verified
to be one complete generation behind would use offset `-1` and therefore the
`1.9.x` series. Free-Moving uses offset `0`, producing the `1.10.x` alpha series.

`versions.json` is the canonical machine-readable manifest. Validate every
runtime, package, and build declaration from the repository root with:

```sh
python3 release/verify_versions.py
```

Pushing one exact component tag invokes only that component's release job.
The Python stable 1.10.0 job builds and smoke-tests its macOS arm64 archive,
Windows x64 portable ZIP, and Windows installer. Windows uses build 11000
because each numeric VERSIONINFO component is limited to 16 bits; macOS uses
build 110000. Both identify the same Python 1.10.0 source release.
Manual workflow dispatch builds only the selected component candidate without
publishing a tag or GitHub Release; selecting Python stable runs its macOS job, plus Windows only when the
recorded versions match. Python alpha releases are marked as GitHub prereleases.

The alpha is written as `1.10.0-alpha.3`, not `1.10.0.3`: SemVer represents
preview status after a hyphen. Python package metadata uses the PEP 440 spelling
`1.10.0a3`, and the macOS bundle uses marketing version `1.10.0` plus build
`110003`; all three identify the same alpha release. Alpha 3 also gives a
singleton-elevation 2D map the legacy `30:7` visual footprint while leaving
the physical 3D sphere unchanged.

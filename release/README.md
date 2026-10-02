# Component versioning and releases

Python is the feature reference for this repository. The supported stable
feature generation is `1.11.x`; an implementation one complete feature
generation behind uses `1.10.x`. Patch numbers identify coordinated or
target-specific releases within that feature generation; supported input
contracts are stated explicitly rather than inferred from the patch number.

The Free-Moving alpha application has been retired from the active source and
release workflow. Its historical Git tags and published artifacts are retained.
The Python package and generic launcher now identify the stable viewer.

Python 1.11.2 adds configurable saved RF overlays and aligned RF/Results plots,
restores native macOS welcome controls, and supports trackpad scrolling in
Settings and Timeline. Unit and response details move to View → Inspector, and
missing companion panels start folded. The macOS build is 111002; Windows
remains at 1.11.1.

Python 1.11.1 corrects RF Hz to counts divided by saved
presentation counts and response-window seconds, adds the saved RF Results
tab, and lets users choose visible tabs in Settings. The macOS build is 111001;
the Windows x64 packaging target is 1.11.1, build 11101. Swift and Web retain
their existing release versions.

Platform identity never becomes a fourth version component. It belongs in the
component tag and artifact name:

| Component | Release | Tag | Channel |
| --- | --- | --- | --- |
| Python stable (macOS) | `1.11.2` | `python-v1.11.2` | stable |
| Python stable (Windows x64) | `1.11.1` | `python-v1.11.1` | stable |
| Swift | `1.10.3` | `swift-v1.10.3` | stable |
| Web | `1.10.2` | `web-v1.10.2` | stable |

Published downloads and checksums are available from the repository
[Releases page](https://github.com/KaiCao2003/RF-Map-Viewer/releases). The root
[`README.md`](../README.md#downloads) links directly to each component archive.

Python stable 1.11.0 adds Figure Studio with draggable/resizable preset frames,
saved `.rfmlayout` compositions, current-unit previews, and per-unit export
scaling. It also updates saved settings, HD Class 3 compatibility, and the
Close All/Welcome lifecycle. The Web source ports the non-export viewer updates;
its published package remains at 1.10.2 until a separate Web release.

Python stable 1.10.4, Swift 1.10.3, and Web 1.10.2 align HD tuning and RF azimuth
as clockwise angles: RF −90° corresponds to HD 270°, RF 90° to HD 90°, and 0° to 0°.
Centered line plots preserve the angle sign, and polar curves and labels agree
on screen and in exports. HD plots have opaque white backgrounds; source TC
data is unchanged. Python stable uses macOS build 110004; Swift uses build 110003.
Python 1.10.4 and Swift 1.10.3 include compact welcome windows with native
macOS recent documents. The Python Cross-correlogram utility stays open when
RF document windows close.

Python stable 1.10.3 adds probe-panel drag selection with a live rectangle and
nearest-visible-unit selection. Its macOS app includes a standalone
cross-correlogram utility for two or three units, with PNG export.

Python and Swift 1.10.2 and Web 1.10.1 restore compatibility with JSON and
indexed RF files that omit descriptive count/occupancy markers or the redundant
occupancy-size field. Explicit conflicting markers and sizes are rejected;
raw integer counts, required response-unit and normalization markers, and
actual occupancy data retain their checks. The input files are unchanged.
Swift also handles macOS URL-open events directly, so opening an RF document
from Finder loads that document instead of leaving the initial file chooser open.
Its waveform discovery stops at the filesystem root for Finder file-reference
URLs, avoiding a loading freeze after document decoding.
Windows remained at 1.10.0 for those patches.

Python and Swift 1.10.1 repair keyboard behavior, RF color scales, missing
exposure during smoothing, and progressive-loading consistency and performance.
Swift time grouping now uses physical bin edges as Python does. Python validates
the shared raw-count contract and preserves large integer counts. See the
[parity report](python-swift-parity-1.10.1.md) for reproducible cases and impact.
Those patch releases targeted macOS arm64; Windows and Web stayed at 1.10.0.

Stable 1.10.0 aligns Python, Swift, and Web on indexed version-2 RF input,
progressive unit caching, RF window subtraction, display shortcuts, and the
shared Delay/RGB display and export semantics. Python ships macOS arm64 and
Windows x64 packages.

Python 1.9.9 separates viewer responsibilities into focused modules, removes
internal fallback branches, consolidates historical tests, and adds a macOS
Apple Silicon PR regression check for the stable viewer.

Python 1.9.8 aligns exported Delay/RGB maps with the live GUI, shares and caches
temporal display calculations, and limits waveform loading to one active
read plus the latest pending selection. The stable macOS package does not
require HDF5.

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
stable reference. Swift and Web use offset `-1` from Python 1.11,
retaining their `1.10.x` series. The manifest records their released versions
independently of unreleased source updates.

`versions.json` is the canonical machine-readable manifest. Validate every
runtime, package, and build declaration from the repository root with:

```sh
python3 release/verify_versions.py
```

Pushing one exact component tag invokes only that component's release jobs.
The Python stable jobs build and smoke-test the macOS arm64 archive, Windows
x64 portable ZIP, and Windows installer. Windows 1.11.1 uses build 11101
because each numeric VERSIONINFO component is limited to 16 bits; macOS uses
build 111001.

Manual workflow dispatch builds candidates without publishing a tag or GitHub
Release. Selecting `python-stable` runs its macOS job, plus Windows when the
recorded versions match. Select `python-stable-windows` to build only Windows
from the chosen packaging commit:

```sh
gh workflow run component-release.yml --ref <packaging-branch> \
  -f component=python-stable-windows
```

The Windows 1.11.1 follow-up is a packaging-only commit after the existing
`python-v1.11.1` tag. Validate the candidate before attaching its portable ZIP,
installer, and Windows checksum file to that release. Preserve the tag and
all existing macOS assets; do not replace attachments. Record the Windows
build commit in the GitHub release body so both platforms' provenance remains
explicit.

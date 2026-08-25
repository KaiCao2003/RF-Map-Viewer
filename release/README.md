# Component versioning and releases

Python is the feature reference for this repository. The supported stable
feature generation is `1.9.x`; an implementation one complete feature
generation behind uses `1.8.x`. Patch numbers identify coordinated or
target-specific releases within that feature generation; supported input
contracts are stated explicitly rather than inferred from the patch number.

The Free-Moving viewer introduces the next `1.10` feature generation and is
currently an alpha. Its canonical release version is
`1.10.0-alpha.3`. Python packaging represents the same release as
`1.10.0a3`, while the macOS marketing version remains the Apple-compatible
three-integer `1.10.0`. Alpha 3 adds the explicit pre-load Square/Bar choice
and support for the latest `rfmapping_fm_bar_hdf5_v1` vertical-bar result.

Platform identity never becomes a fourth version component. It belongs in the
component tag and artifact name:

| Component | Release | Tag | Channel |
| --- | --- | --- | --- |
| Python stable | `1.9.5` | `python-v1.9.5` | stable |
| Python Free-Moving | `1.10.0-alpha.3` | `python-v1.10.0-alpha.3` | alpha |
| Swift | `1.9.5` | `swift-v1.9.5` | stable |
| Web | `1.9.5` | `web-v1.9.5` | stable |

Published downloads and checksums are available from the repository
[Releases page](https://github.com/KaiCao2003/RF-Map-Viewer/releases). The root
[`README.md`](../README.md#downloads) links directly to each component archive.

Stable 1.9.5 is a coordinated schema release for the current regular
`RFmapping_core.m` output. Python, Swift, and Web require raw spike counts plus
the spatial `occupancyTimeSec` matrix, normalize firing rate as
count/occupancy, and start in firing-rate mode. Earlier occupancy-free or
already-normalized RF payloads are intentionally outside this release.
The refreshed Python artifacts additionally ship the SpikeInterface waveform
viewer/exporter on both macOS arm64 and Windows x64; Swift and Web are
unchanged by that Python-only patch.

Each active component records a `feature_generation_offset` from the Python
stable reference. The current Swift/Web offset is `0`; a viewer verified to be
one complete generation behind would use offset `-1` and therefore the `1.8.x`
series. Free-Moving uses offset `+1`, producing the `1.10.x` alpha series.

`versions.json` is the canonical machine-readable manifest. Validate every
runtime, package, and build declaration from the repository root with:

```sh
python3 release/verify_versions.py
```

Pushing one exact component tag invokes only that component's release job.
The Python stable job builds and smoke-tests both its macOS arm64 archive and
Windows x64 portable/setup packages before it creates or updates the matching
component Release.
Manual workflow dispatch builds only the selected component candidate without
publishing a tag or GitHub Release; selecting Python stable runs its paired
macOS and Windows jobs. Python alpha releases are marked as GitHub prereleases.

The alpha is written as `1.10.0-alpha.3`, not `1.10.0.3`: SemVer represents
preview status after a hyphen. Python package metadata uses the PEP 440 spelling
`1.10.0a3`, and the macOS bundle uses marketing version `1.10.0` plus build
`110003`; all three identify the same alpha release. Alpha 3 also gives a
singleton-elevation 2D map the legacy `30:7` visual footprint while leaving
the physical 3D sphere unchanged.

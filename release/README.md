# Component versioning and releases

Python is the feature reference for this repository. The supported stable
feature generation is `1.9.x`; an implementation one complete feature
generation behind uses `1.8.x`. Patch numbers describe compatible fixes or
target-specific completion work within that feature generation.

The Free-Moving viewer introduces the next `1.10` feature generation and is
currently an alpha. Its canonical release version is
`1.10.0-alpha.1`. Python packaging represents the same release as
`1.10.0a1`, while the macOS marketing version remains the Apple-compatible
three-integer `1.10.0`.

Platform identity never becomes a fourth version component. It belongs in the
component tag and artifact name:

| Component | Release | Tag | Channel |
| --- | --- | --- | --- |
| Python stable | `1.9.2` | `python-v1.9.2` | stable |
| Python Free-Moving | `1.10.0-alpha.1` | `python-v1.10.0-alpha.1` | alpha |
| Swift | `1.9.0` | `swift-v1.9.0` | stable |
| Web | `1.9.0` | `web-v1.9.0` | stable |

Published downloads and checksums are available from the repository
[Releases page](https://github.com/KaiCao2003/RF-Map-Viewer/releases). The root
[`README.md`](../README.md#downloads) links directly to each component archive.

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
Manual workflow dispatch builds all four candidates without publishing a tag
or GitHub Release. Python alpha releases are marked as GitHub prereleases.

The alpha is written as `1.10.0-alpha.1`, not `1.10.0.1`: SemVer represents
preview status after a hyphen. Python package metadata uses the PEP 440 spelling
`1.10.0a1`, and the macOS bundle uses marketing version `1.10.0` plus build
`110001`; all three identify the same alpha release.

`indexed-v2-numpy.base64` is an explicitly synthetic compressed NPZ archive
generated with NumPy 2.4.6 `numpy.savez_compressed` on the configured remote
execution host. It contains three units in display order `[41, 7, 902]` with
shape `(3, 2, 2, 3)`, finite occupancy including one unavailable spatial cell,
and full time edges `[-0.1, 0, 0.1, 0.2]` seconds.

Unit 41 is little-endian C-order float64, unit 7 is big-endian Fortran-order
float64, and unit 902 is uint16. The ZIP uses actual NumPy deflate output and
the ZIP64 local headers emitted by NumPy's writer. No experiment data is
included.

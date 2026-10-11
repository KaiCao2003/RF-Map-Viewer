# ADN NumPy fixture

`adn-numpy.json` contains base64-encoded synthetic NumPy arrays, generated with
`numpy.lib.format.write_array(..., version=(1, 0)/(2, 0)/(3, 0), allow_pickle=False)`.
The empty entry uses `numpy.save(..., allow_pickle=False)`.

```python
rows = np.array(
    [('ProbeB', 42), ('ProbeA', 17), ('ProbeA', 42),
     ('ProbeA', 1099511627783), ('ProbeB', -7)],
    dtype=[('probe', 'U16'), ('unit_id', 'i8')],
)
empty = np.empty(0, dtype=rows.dtype)
```

The tests read all header versions and compare Swift output against the original
NumPy header and reordered record bytes, including an empty selection. There is
no recording-derived data in these fixtures.

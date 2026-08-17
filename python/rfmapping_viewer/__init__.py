"""Implementation-local support package shared by the independent viewers."""

# Import concrete modules explicitly. Keeping the package root empty prevents
# the stable JSON viewer from loading the Free-Moving HDF5 stack at startup.
__all__: list[str] = []

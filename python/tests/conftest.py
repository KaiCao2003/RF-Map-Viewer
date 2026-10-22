"""Keep destroyed Tk objects out of later background-worker collections."""

import gc

import pytest


@pytest.fixture(autouse=True)
def collect_destroyed_tk_objects():
    yield
    # Tk variables can retain cycles after a window is destroyed. Finalize them
    # on the test's main thread before another test starts a cache/reload worker.
    gc.collect()

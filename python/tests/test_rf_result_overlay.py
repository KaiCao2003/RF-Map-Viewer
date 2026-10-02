from __future__ import annotations

from unittest import mock

import numpy as np
import pytest

import rfmapping_viewer.rf_result_overlay as overlay_module
from rfmapping_viewer.rf_result_overlay import RFResultOverlayCache, load_rf_result_overlay
from test_rf_results import source, write_result, write_summary


def test_overlay_matches_recorded_unit_ids_and_preserves_saved_files(source):
    path, _ = write_result(source, ids=(42, 17))
    original = {item: item.read_bytes() for item in (source.path, path)}

    selected = load_rf_result_overlay(source, 42, mode="2D")
    expected = np.array([[0, 0, 0], [0, 0, 1]], dtype=np.uint8)
    np.testing.assert_array_equal(selected.flags, expected)
    assert selected.available
    assert selected.unavailable == ()
    assert selected.flags.dtype == np.uint8
    assert not selected.flags.flags.writeable
    with pytest.raises(ValueError):
        selected.flags[0, 0] = 1

    negative = load_rf_result_overlay(source, 17, mode="2D")
    assert negative.available
    assert negative.unavailable == ()
    np.testing.assert_array_equal(negative.flags, np.zeros((2, 3), dtype=np.uint8))
    assert {item: item.read_bytes() for item in original} == original


@pytest.mark.parametrize("axis,expected", [
    ("x", [[0, 0, 2], [0, 0, 2]]),
    ("y", [[0, 0, 0], [2, 2, 2]]),
])
def test_1d_overlay_broadcasts_along_its_saved_axis(source, axis, expected):
    write_result(source, "1d", axis=axis)

    overlay = load_rf_result_overlay(source, 42, mode="1D")

    assert overlay.available
    assert overlay.unavailable == ()
    np.testing.assert_array_equal(overlay.flags, np.asarray(expected, dtype=np.uint8))


def test_both_overlay_marks_each_detection_and_their_overlap(source):
    write_result(source)
    write_result(source, "1d")

    overlay = load_rf_result_overlay(source, 42, mode="Both")

    assert overlay.available
    assert overlay.unavailable == ()
    np.testing.assert_array_equal(overlay.flags, [[0, 0, 2], [0, 0, 3]])


def test_overlay_uses_the_requested_polarity(source):
    write_result(source)
    mask = np.zeros((2, 2, 3), dtype=np.uint8)
    mask[0, 0, 0] = 1
    write_result(source, rf_type="inhibitory", mask=mask)

    overlay = load_rf_result_overlay(source, 42, mode="2D", rf_type="inhibitory")

    assert overlay.available
    assert overlay.unavailable == ()
    np.testing.assert_array_equal(overlay.flags, [[1, 0, 0], [0, 0, 0]])


def test_saved_negative_detection_is_available_but_an_absent_unit_is_not(source):
    write_result(source)
    write_result(source, "1d")

    negative = load_rf_result_overlay(source, 17)
    absent = load_rf_result_overlay(source, 99)

    assert negative.available
    assert negative.unavailable == ()
    assert not negative.flags.any()
    assert not absent.available
    assert absent.flags is None
    assert absent.unavailable == ("2D", "1D")


def test_missing_results_stay_unavailable_without_creating_sidecars(source):
    overlay = load_rf_result_overlay(source, 42)

    assert not overlay.available
    assert overlay.flags is None
    assert overlay.unavailable == ("2D", "1D")
    assert list(source.path.parent.iterdir()) == [source.path]


def test_disabled_overlay_does_not_report_missing_results(source):
    overlay = load_rf_result_overlay(source, 42, mode="None")

    assert not overlay.available
    assert overlay.flags is None
    assert overlay.unavailable == ()
    assert list(source.path.parent.iterdir()) == [source.path]


@pytest.mark.parametrize("available_dimension,expected,unavailable", [
    ("2d", [[0, 0, 0], [0, 0, 1]], ("1D",)),
    ("1d", [[0, 0, 2], [0, 0, 2]], ("2D",)),
])
def test_both_retains_an_available_dimension(source, available_dimension, expected, unavailable):
    write_result(source, available_dimension)

    overlay = load_rf_result_overlay(source, 42)

    assert overlay.available
    assert overlay.unavailable == unavailable
    np.testing.assert_array_equal(overlay.flags, expected)


def test_invalid_2d_result_does_not_hide_a_valid_1d_result(source):
    path, _ = write_result(source)
    path.write_bytes(b"invalid saved result")
    write_result(source, "1d")

    overlay = load_rf_result_overlay(source, 42)

    assert overlay.available
    assert overlay.unavailable == ("2D",)
    np.testing.assert_array_equal(overlay.flags, [[0, 0, 2], [0, 0, 2]])


def test_legacy_1d_projection_requires_its_saved_provenance(source):
    path, manifest = write_result(source, "1d", projection=True)
    unavailable = load_rf_result_overlay(source, 42, mode="1D")
    assert unavailable.flags is None
    assert unavailable.unavailable == ("1D",)

    write_summary(source, path, manifest, dimension="1d", projection=True)
    overlay = load_rf_result_overlay(source, 42, mode="1D")

    assert overlay.available
    assert overlay.unavailable == ()
    np.testing.assert_array_equal(overlay.flags, [[0, 0, 2], [0, 0, 2]])


def test_explicit_reload_discovers_a_previously_missing_result(source):
    cache = RFResultOverlayCache(source)
    assert not cache.get(42, mode="2D").available
    write_result(source)
    assert not cache.get(42, mode="2D").available

    cache.clear()

    selected = cache.get(42, mode="2D")
    negative = cache.get(17, mode="2D")
    assert selected.available
    np.testing.assert_array_equal(selected.flags, [[0, 0, 0], [0, 0, 1]])
    assert negative.available
    assert not negative.flags.any()


def test_result_overlay_and_inspector_share_one_cached_read(source):
    write_result(source)
    cache = RFResultOverlayCache(source)

    with mock.patch.object(
        overlay_module, "load_saved_rf_result", wraps=overlay_module.load_saved_rf_result,
    ) as reader:
        result = cache.result("2d", "excitatory")
        assert result is not None
        assert cache.get(42, mode="2D").available
        assert cache.get(17, mode="2D").available
        assert cache.result("2d", "excitatory") is result
        assert cache.error("2d", "excitatory") is None
        reader.assert_called_once_with(source, dimension="2d", rf_type="excitatory")


def test_cached_load_error_remains_until_explicit_reload(source):
    cache = RFResultOverlayCache(source)
    assert cache.result("1d", "excitatory") is None
    error = cache.error("1d", "excitatory")
    assert error
    write_result(source, "1d")
    assert not cache.get(42, mode="1D").available
    assert cache.error("1d", "excitatory") == error

    cache.clear()

    assert cache.get(42, mode="1D").available
    assert cache.error("1d", "excitatory") is None

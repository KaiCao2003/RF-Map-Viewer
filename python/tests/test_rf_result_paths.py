"""Configurable saved-result layout selection keeps reads in one layout."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from rfmapping_viewer.rf_results import (
    DEFAULT_RF_RESULT_LAYOUT_PATTERN,
    RFResultSource,
    load_saved_rf_result,
    rf_result_path,
    validate_rf_result_layout_pattern,
)
from test_rf_results import source, write_result, write_summary


CUSTOM_LAYOUT_PATTERN = r"horizontal-test/scan-\d{8}-run\d+"


@pytest.fixture
def custom_source(source):
    folder = source.path.parent / "horizontal-test"
    folder.mkdir()
    path = folder / "scan-20261026-run2.rfmap"
    path.write_text("read-only source placeholder")
    return replace(source, path=path, layout_pattern=CUSTOM_LAYOUT_PATTERN)


@pytest.mark.parametrize("kind", [
    "regular", "bar_3deg", "bar_3.5deg", "rotation", "rotation_30",
    "egocentric", "egocentric_30", "allocentric_pixelbins",
    "allocentric_pixelbins_30", "free_moving",
])
@pytest.mark.parametrize("suffix", [".rfmap", ".json", ".JSON"])
def test_default_pattern_preserves_supported_kinds(tmp_path, kind, suffix):
    path = tmp_path / kind / f"{kind}_261026_2{suffix}"
    assert rf_result_path(path) == path.with_name("rf_2d_ext.npz")


@pytest.mark.parametrize("folder,stem,suffix", [
    ("regular", "regular_261026_2_extra", ".rfmap"),
    ("regular", "bar_3deg_261026_2", ".rfmap"),
    ("prefix_regular", "regular_261026_2", ".rfmap"),
    ("regular", "regular_20261026_2", ".rfmap"),
    ("other", "other_261026_2", ".rfmap"),
    ("regular", "regular_261026_2", ".txt"),
])
def test_default_pattern_preserves_source_named_layout(tmp_path, folder, stem, suffix):
    path = tmp_path / folder / f"{stem}{suffix}"
    assert rf_result_path(path) == path.with_suffix(".npz")


@pytest.mark.parametrize("dimension,rf_type,polarity", [
    ("2d", "excitatory", "ext"), ("1d", "excitatory", "ext"),
    ("2d", "inhibitory", "inh"), ("1d", "inhibitory", "inh"),
])
def test_custom_layout_loads_all_results_and_matching_summary(
    custom_source, dimension, rf_type, polarity,
):
    expected = custom_source.path.with_name(f"rf_{dimension}_{polarity}.npz")
    assert rf_result_path(
        custom_source.path, dimension=dimension, rf_type=rf_type,
        layout_pattern=CUSTOM_LAYOUT_PATTERN,
    ) == expected
    # The existing fixture writer uses the default source-named layout here.
    legacy_path, manifest = write_result(custom_source, dimension, rf_type)
    legacy_path.rename(expected)
    summary_path, summary = write_summary(
        custom_source, expected, manifest, dimension=dimension, rf_type=rf_type,
        filename=f"analysis_{polarity}.json",
    )

    result = load_saved_rf_result(custom_source, dimension=dimension, rf_type=rf_type)

    assert result.path == expected
    assert result.source.layout_pattern == CUSTOM_LAYOUT_PATTERN
    assert result.summary == summary
    assert result.summary_warning is None
    assert result.for_unit(42)[0].sum() == 1
    assert summary_path.is_file()
    assert custom_source.path.read_text() == "read-only source placeholder"


def test_matching_custom_layout_does_not_fall_back_to_source_named_result(custom_source):
    legacy_path, _ = write_result(custom_source)
    assert legacy_path.is_file()
    with pytest.raises(FileNotFoundError):
        load_saved_rf_result(custom_source)


def test_custom_pattern_replaces_default_layout_selection(tmp_path):
    path = tmp_path / "regular" / "regular_261026_2.rfmap"
    assert rf_result_path(path, layout_pattern=CUSTOM_LAYOUT_PATTERN) == path.with_suffix(".npz")


def test_custom_pattern_requires_full_parent_stem_match(custom_source):
    path = custom_source.path.with_name("scan-20261026-run2-copy.rfmap")
    assert rf_result_path(path, layout_pattern=CUSTOM_LAYOUT_PATTERN) == path.with_suffix(".npz")


def test_from_document_captures_custom_pattern(source):
    document = SimpleNamespace(
        path=source.path, unit_pool=source.unit_ids,
        x_positions=source.x_positions, y_positions=source.y_positions,
        time_bin_edges=source.time_bin_edges,
    )
    assert RFResultSource.from_document(document).layout_pattern == DEFAULT_RF_RESULT_LAYOUT_PATTERN
    configured = RFResultSource.from_document(document, layout_pattern=CUSTOM_LAYOUT_PATTERN)
    assert configured == replace(source, layout_pattern=CUSTOM_LAYOUT_PATTERN)


@pytest.mark.parametrize("pattern", ["", "   ", None, 7, "[", r"(?P=missing)"])
def test_invalid_layout_pattern_has_actionable_error(pattern):
    with pytest.raises(ValueError, match="Saved RF layout regex"):
        validate_rf_result_layout_pattern(pattern)


def test_valid_pattern_is_not_rewritten():
    assert validate_rf_result_layout_pattern(CUSTOM_LAYOUT_PATTERN) == CUSTOM_LAYOUT_PATTERN

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest

from rfmapping_viewer.rf_results import RFResultSource, load_saved_rf_result, rf_result_path


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "mapping.rfmap"
    path.write_text("read-only source placeholder")
    return RFResultSource(path, (17, 42, 99), (-90., 0., 90.), (-15., 15.), (-.1, 0., .1, .2))


def write_result(source, dimension="2d", rf_type="excitatory", *, ids=(42, 17), axis="x",
                 projection=False, mask=None, center=None, manifest_changes=None):
    shape = (2, 3) if dimension == "2d" or projection else ((1, 3) if axis == "x" else (2, 1))
    if mask is None:
        mask = np.zeros((len(ids), *shape), dtype=np.uint8)
        mask[0].flat[-1] = 1
    if center is None:
        center = mask.copy()
    parameters = dict(alternative="less" if rf_type == "inhibitory" else "greater",
                      cluster_forming_z=1.8 if dimension == "2d" else 1.,
                      drop_bins=2, wrap_x=True, is_shuffle=False, exclude_zero_bins=False)
    manifest = dict(schema_name="rfmapping-rf-result", detector_algorithm="pooled-spatial-z-v3-zero-filled",
                    center_algorithm="response-weighted-medoid-v1", nonshuffle_filter="drop-small-components-inclusive-v1",
                    grid_shape=list(shape), storage_shape=[len(ids), *shape], time_range_s=[0., .2],
                    response_units="spike_count", response_normalization="none", parameters=parameters)
    if dimension == "1d" and not projection:
        manifest["collapse_axis"] = axis
    manifest.update(manifest_changes or {})
    path = rf_result_path(source.path, dimension=dimension, rf_type=rf_type)
    np.savez_compressed(path, schema_version=np.asarray(1), mask_2d=mask, center_2d=center,
                        unit_ids=np.asarray(ids), cache_key=np.asarray("saved-input-hash"),
                        manifest_json=np.asarray(json.dumps(manifest, sort_keys=True, separators=(",", ":"))))
    return path, manifest


def write_summary(source, path, manifest, *, dimension="2d", rf_type="excitatory", projection=False):
    stem = source.path.stem + ("_inhibitory" if rf_type == "inhibitory" else "")
    summary = dict(source_path=str(source.path), probe="A", kept_unit_ids=[17, 42],
                   parameters=dict(rf_type=rf_type, time_range_s=[0., .2], max_zero_bins=2,
                                   cluster_forming_z_2d=1.8, cluster_forming_z_1d=1.,
                                   drop_bins=2, wrap_x=True, is_shuffle=False,
                                   exclude_zero_bins=False, collapse_from_2d=projection),
                   bin_qc=dict(unit_ids=[17, 42, 99], zero_bins=[0, 1, 5], valid_bins=[6, 5, 1],
                               keep=[True, True, False]), output_paths={f"result_{dimension}": str(path)})
    summary_path = source.path.with_name(stem + "_analysis.json")
    summary_path.write_text(json.dumps(summary))
    return summary_path, summary


@pytest.mark.parametrize("dimension,rf_type,name", [
    ("2d", "excitatory", "mapping.npz"), ("1d", "excitatory", "mapping_1d.npz"),
    ("2d", "inhibitory", "mapping_inhibitory.npz"), ("1d", "inhibitory", "mapping_inhibitory_1d.npz"),
])
def test_four_saved_modes_and_unit_identity(source, dimension, rf_type, name):
    path, _ = write_result(source, dimension, rf_type)
    result = load_saved_rf_result(source, dimension=dimension, rf_type=rf_type)
    assert path.name == name
    assert result.for_unit(42)[0].sum() == 1
    assert result.for_unit(17)[0].sum() == 0  # Valid saved negative detection.
    assert result.for_unit(99) is None       # No row-index fallback.
    assert not result.for_unit(42)[0].flags.writeable
    assert source.path.read_text() == "read-only source placeholder"


def test_missing_sidecar_does_not_compute_or_create_a_result(source):
    with pytest.raises(FileNotFoundError):
        load_saved_rf_result(source)
    assert list(source.path.parent.iterdir()) == [source.path]


def test_qc_lookup_uses_its_own_unit_ids(source):
    path, manifest = write_result(source)
    write_summary(source, path, manifest)
    result = load_saved_rf_result(source)
    assert result.unit_qc(42) == dict(zero_bins=1, valid_bins=5, keep=True)
    assert result.unit_qc(99)["keep"] is False


def test_recording_can_move_with_its_sidecars_and_preserve_original_provenance(source):
    path, manifest = write_result(source)
    summary_path, summary = write_summary(source, path, manifest)
    summary["source_path"] = "/mnt/recording/session/mapping.rfmap"
    summary["output_paths"]["result_2d"] = "/mnt/recording/session/mapping.npz"
    summary_path.write_text(json.dumps(summary))
    result = load_saved_rf_result(source)
    assert result.path == path
    assert result.summary["source_path"] == "/mnt/recording/session/mapping.rfmap"


def test_contradictory_optional_summary_is_ignored_without_losing_saved_mask(source):
    path, manifest = write_result(source)
    summary_path, summary = write_summary(source, path, manifest)
    summary["output_paths"]["result_2d"] = "/mnt/recording/session/another_mapping.npz"
    summary_path.write_text(json.dumps(summary))
    result = load_saved_rf_result(source)
    assert result.summary is None
    assert "output path" in result.summary_warning
    assert result.for_unit(42)[0].sum() == 1
    assert result.unit_qc(42) is None


def test_summary_uses_requested_time_and_manifest_uses_canonical_source_edges(source):
    path, manifest = write_result(source)
    summary_path, summary = write_summary(source, path, manifest)
    summary["parameters"]["time_range_s"][1] += 1e-14
    summary_path.write_text(json.dumps(summary))
    assert load_saved_rf_result(source).summary is not None


@pytest.mark.parametrize("mutation,error", [
    ({"time_range_s": [-.2, .2]}, "time range"),
    ({"time_range_s": [.025, .2]}, "bin edges"),
    ({"grid_shape": [3, 2]}, "grid/storage"),
    ({"collapse_axis": "x"}, "collapsed axis"),
    ({"parameters": {"alternative": "less"}}, "polarity"),
])
def test_rejects_misaligned_manifest(source, mutation, error):
    write_result(source, manifest_changes=mutation)
    with pytest.raises(ValueError, match=error):
        load_saved_rf_result(source)


@pytest.mark.parametrize("ids", [(42, 42), (42, 123)])
def test_rejects_duplicate_or_foreign_ids(source, ids):
    write_result(source, ids=ids)
    with pytest.raises(ValueError, match="unit IDs"):
        load_saved_rf_result(source)


def test_rejects_center_outside_mask(source):
    center = np.zeros((2, 2, 3), dtype=np.uint8)
    center[0, 0, 0] = 1
    write_result(source, center=center)
    with pytest.raises(ValueError, match="center inside"):
        load_saved_rf_result(source)


def test_checks_grid_against_open_document(source):
    write_result(source)
    with pytest.raises(ValueError, match="axes"):
        load_saved_rf_result(replace(source, x_positions=(-90., 90.)))


@pytest.mark.parametrize("field,value,error", [
    ("source_path", "/different/source.rfmap", "source_path"),
    ("kept_unit_ids", [17], "kept units"),
])
def test_discards_stale_analysis_summary(source, field, value, error):
    path, manifest = write_result(source)
    summary_path, summary = write_summary(source, path, manifest)
    summary[field] = value
    summary_path.write_text(json.dumps(summary))
    result = load_saved_rf_result(source)
    assert result.summary is None
    assert error in result.summary_warning
    assert result.unit_qc(42) is None


def test_saved_inhibitory_result_survives_summary_from_a_different_time_window(source):
    path, manifest = write_result(source, rf_type="inhibitory")
    summary_path, summary = write_summary(source, path, manifest, rf_type="inhibitory")
    summary["parameters"]["time_range_s"] = [.1, .2]
    summary_path.write_text(json.dumps(summary))
    result = load_saved_rf_result(source, rf_type="inhibitory")
    assert result.manifest["time_range_s"] == [0., .2]
    assert result.for_unit(42)[0].sum() == 1
    assert result.summary is None
    assert "time range" in result.summary_warning


def test_direct_1d_y_axis_keeps_y_coordinates(source):
    write_result(source, "1d", axis="y")
    result = load_saved_rf_result(source, dimension="1d")
    assert result.axis == "y"
    np.testing.assert_array_equal(result.for_unit(42)[0], [0, 1])


def test_legacy_projection_requires_saved_provenance(source):
    path, manifest = write_result(source, "1d", projection=True)
    with pytest.raises(ValueError, match="requires its analysis summary"):
        load_saved_rf_result(source, dimension="1d")
    write_summary(source, path, manifest, dimension="1d", projection=True)
    result = load_saved_rf_result(source, dimension="1d")
    assert result.projected_from_2d
    np.testing.assert_array_equal(result.for_unit(42)[0], [0, 0, 1])


def test_malformed_archive_is_reported(source):
    rf_result_path(source.path).write_bytes(b"not an npz archive")
    with pytest.raises(ValueError, match="Cannot read"):
        load_saved_rf_result(source)


def test_render_uses_saved_coordinates_and_opaque_white_background(source):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from rfmapping_viewer.rf_results_view import draw_saved_rf_result

    write_result(source)
    result = load_saved_rf_result(source)
    figure = Figure(facecolor="black")
    FigureCanvasAgg(figure)
    assert draw_saved_rf_result(figure, result, 42)
    figure.canvas.draw()
    assert figure.get_facecolor() == (1., 1., 1., 1.)
    assert figure.axes[0].get_facecolor() == (1., 1., 1., 1.)
    assert figure.axes[0].get_box_aspect() == pytest.approx(2 / 3)
    np.testing.assert_allclose(figure.axes[0].collections[1].get_offsets(), [[90., 15.]])
    for width, height in ((9, 3), (4, 8)):
        figure.set_size_inches(width, height)
        figure.canvas.draw()
        box = figure.axes[0].get_window_extent()
        assert box.height / box.width == pytest.approx(2 / 3)
    assert not draw_saved_rf_result(figure, result, 99)
    assert len(figure.axes[0].collections) == 0


def test_singleton_2d_and_one_dimensional_render(source):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from rfmapping_viewer.rf_results_view import draw_saved_rf_result

    figure = Figure()
    FigureCanvasAgg(figure)
    write_result(source, "1d")
    result = load_saved_rf_result(source, dimension="1d")
    draw_saved_rf_result(figure, result, 42)
    np.testing.assert_allclose(figure.axes[0].lines[0].get_xdata(), source.x_positions)
    single = replace(source, y_positions=(0.,))
    _, manifest = write_result(single, "1d")
    # Exercise a source-native singleton 2-D map with the same stored array.
    manifest.pop("collapse_axis")
    write_result(single, mask=np.array([[[0, 0, 1]], [[0, 0, 0]]]),
                 manifest_changes={**manifest})
    result = load_saved_rf_result(single)
    draw_saved_rf_result(figure, result, 42)
    figure.canvas.draw()
    assert figure.axes[0].get_ylim() == (-.5, .5)
    assert figure.axes[0].get_box_aspect() == pytest.approx(7 / 30)

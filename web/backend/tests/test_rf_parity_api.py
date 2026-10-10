from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from rfmapping_web.exports import (
    ExportValidationError, VALUE_MODE_COUNT, VALUE_MODE_RATE, _prepared_response_matrix,
)
from rfmapping_web.rf_results import RFResultSource, load_saved_rf_result
from test_rf_results import write_result, write_summary
from test_rfmapping_web import (
    app, authenticated_client, settings, sample_payload, write_indexed, write_json,
)


@pytest.mark.parametrize("storage", ["json", "indexed"])
def test_presentation_metadata_preserved_and_inputs_unchanged(app, settings, storage):
    payload = sample_payload()
    presentations = [[2, 3], [4, 5]]
    if storage == "json":
        payload["stimulusPresentationCounts"] = presentations
        source = write_json(settings.rf_root / "mapping.rfmap", payload)
    else:
        source = write_indexed(settings.rf_root / "mapping.rfmap", payload,
                               stimulusPresentationCounts=np.asarray(presentations))
    original = source.read_bytes()
    with authenticated_client(app) as client:
        opened = client.post("/api/datasets/open", json={"path": str(source)})
        assert opened.status_code == 200, opened.text
        meta = opened.json()
        assert meta["stimulusPresentationCounts"] == presentations
        assert client.get(f"/api/datasets/{meta['id']}/meta").json()["stimulusPresentationCounts"] == presentations
        client.delete(f"/api/datasets/{meta['id']}")
    assert source.read_bytes() == original


@pytest.mark.parametrize("value", [-1, 1.5, True, None, [[1, 2, 3]]])
def test_invalid_presentation_counts_rejected(app, settings, value):
    payload = sample_payload()
    payload["stimulusPresentationCounts"] = value
    source = write_json(settings.rf_root / "invalid.json", payload)
    with authenticated_client(app) as client:
        response = client.post("/api/datasets/open", json={"path": str(source)})
    assert response.status_code == 422
    assert "stimulusPresentationCounts" in response.json()["detail"]


@pytest.mark.parametrize("storage", ["json", "indexed"])
def test_zero_presentations_reject_nonzero_response(app, settings, storage):
    payload = sample_payload()
    counts = [[0, 3], [4, 5]]
    if storage == "json":
        payload["stimulusPresentationCounts"] = counts
        source = write_json(settings.rf_root / "invalid.rfmap", payload)
    else:
        source = write_indexed(settings.rf_root / "invalid.rfmap", payload,
                               stimulusPresentationCounts=np.asarray(counts))
    with authenticated_client(app) as client:
        response = client.post("/api/datasets/open", json={"path": str(source)})
    assert response.status_code == 422
    assert "stimulusPresentationCounts is zero" in response.json()["detail"]


@pytest.mark.parametrize("shape,presentations,expected", [
    ([1, 1, 1, 1], 4, [[4]]),
    ([1, 1, 2, 1], [4, 8], [[4, 8]]),
    ([1, 2, 1, 1], [4, 8], [[4], [8]]),
])
def test_matlab_presentation_singletons(app, settings, shape, presentations, expected):
    payload = dict(unitsSpikeCounts=np.ones(shape, dtype=int).tolist(), unitsSpikeCountsSize=shape,
                   unitPool=[17], xPositions=list(range(shape[2])), yPositions=list(range(shape[1])),
                   timeBinEdges=[0, .1], occupancyTimeSec=np.ones(shape[1:3]).tolist(),
                   responseUnits="spike_count", responseNormalization="none",
                   stimulusPresentationCounts=presentations)
    source = write_json(settings.rf_root / "singleton.json", payload)
    with authenticated_client(app) as client:
        response = client.post("/api/datasets/open", json={"path": str(source)})
    assert response.status_code == 200, response.text
    assert response.json()["stimulusPresentationCounts"] == expected


def test_legacy_document_loads_count_only(app, settings):
    source = write_json(settings.rf_root / "legacy.rfmap")
    with authenticated_client(app) as client:
        response = client.post("/api/datasets/open", json={"path": str(source)})
        assert response.status_code == 200
        assert response.json()["stimulusPresentationCounts"] is None


def test_csv_rates_follow_response_window_and_pool_exposure():
    metadata = dict(shape=[1, 1, 2, 2], timeBinEdges=[0, .05, .2],
                    occupancyTimeSec=[[7, 9]], stimulusPresentationCounts=[[2, 6]])
    counts = np.array([[[2, 6], [6, 18]]])
    # Equal firing rates with different presentations and unequal lag widths.
    for start, end in [(0, 0), (1, 1), (0, 1)]:
        np.testing.assert_allclose(
            _prepared_response_matrix(counts, metadata, start, end, VALUE_MODE_RATE,
                                      [(0, 0)], [(0, 1)], 0), [[20.]],
        )
    assert _prepared_response_matrix(counts, metadata, 0, 1, VALUE_MODE_COUNT,
                                     [(0, 0)], [(0, 1)], 0) == [[16.]]
    metadata.pop("stimulusPresentationCounts")
    with pytest.raises(ExportValidationError, match="stimulusPresentationCounts"):
        _prepared_response_matrix(counts, metadata, 0, 1, VALUE_MODE_RATE,
                                  [(0, 0)], [(0, 1)], 0)


def test_csv_smoothing_preserves_unpresented_cells_as_missing():
    metadata = dict(shape=[1, 1, 3, 1], timeBinEdges=[0, .1],
                    occupancyTimeSec=[[1, 1, 1]], stimulusPresentationCounts=[[2, 0, 4]])
    counts = np.array([[[4], [0], [8]]])
    assert _prepared_response_matrix(
        counts, metadata, 0, 0, VALUE_MODE_RATE, [(0, 0)], [(0, 0), (1, 1), (2, 2)], 1,
    ) == [[20., None, 20.]]


def _result_source(settings):
    source = settings.rf_root / "mapping.rfmap"
    payload = dict(unitsSpikeCounts=np.ones((3, 2, 3, 3), dtype=int).tolist(),
                   unitsSpikeCountsSize=[3, 2, 3, 3], unitPool=[17, 42, 99],
                   xPositions=[-90, 0, 90], yPositions=[-15, 15], timeBinEdges=[-.1, 0, .1, .2],
                   occupancyTimeSec=[[1, 1, 1], [1, 1, 1]],
                   responseUnits="spike_count", responseNormalization="none")
    write_json(source, payload)
    return RFResultSource(source, (17, 42, 99), (-90., 0., 90.), (-15., 15.), (-.1, 0., .1, .2))


@pytest.mark.parametrize("dimension", ["1d", "2d"])
@pytest.mark.parametrize("polarity", ["excitatory", "inhibitory"])
def test_saved_result_api_preserves_native_masks_and_ids(app, settings, dimension, polarity):
    source = _result_source(settings)
    result_path, manifest = write_result(source, dimension, polarity)
    write_summary(source, result_path, manifest, dimension=dimension, rf_type=polarity)
    before = {path: path.read_bytes() for path in settings.rf_root.iterdir()}
    with authenticated_client(app) as client:
        meta = client.post("/api/datasets/open", json={"path": str(source.path)}).json()
        response = client.get(f"/api/datasets/{meta['id']}/rf-results",
                              params=dict(dimension=dimension, polarity=polarity))
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["available"] is True
        assert result["unitIds"] == [42, 17]
        assert result["manifest"] == manifest
        assert result["axis"] == ("x" if dimension == "1d" else None)
        assert result["summary"]["bin_qc"]["unit_ids"] == [17, 42, 99]
        assert np.asarray(result["masks"])[0].sum() == 1
        assert np.asarray(result["masks"])[1].sum() == 0
    assert {path: path.read_bytes() for path in settings.rf_root.iterdir()} == before


def test_saved_result_cache_changes_only_on_reload(app, settings):
    source = _result_source(settings)
    with authenticated_client(app) as client:
        meta = client.post("/api/datasets/open", json={"path": str(source.path)}).json()
        endpoint = f"/api/datasets/{meta['id']}/rf-results"
        assert client.get(endpoint).json()["available"] is False
        write_result(source)
        assert client.get(endpoint).json()["available"] is False
        assert client.get(endpoint, params={"force_reload": True}).json()["available"] is True
        assert client.get(endpoint, params={"dimension": "3d"}).status_code == 422
        assert client.get("/api/datasets/unknown/rf-results").status_code == 404


def test_sidecar_and_summary_symlinks_respect_data_root(app, settings, tmp_path):
    source = _result_source(settings)
    result_path, manifest = write_result(source)
    outside = tmp_path / "outside.npz"
    result_path.rename(outside)
    result_path.symlink_to(outside)
    with authenticated_client(app) as client:
        meta = client.post("/api/datasets/open", json={"path": str(source.path)}).json()
        result = client.get(f"/api/datasets/{meta['id']}/rf-results").json()
        assert not result["available"]
        assert "outside" in result["detail"]
    result_path.unlink()
    outside.rename(result_path)
    summary_path, _ = write_summary(source, result_path, manifest)
    outside_summary = tmp_path / "outside-analysis.json"
    summary_path.rename(outside_summary)
    summary_path.symlink_to(outside_summary)
    result = load_saved_rf_result(source, scope_root=settings.rf_root)
    assert result.summary is None
    assert "outside" in result.summary_warning

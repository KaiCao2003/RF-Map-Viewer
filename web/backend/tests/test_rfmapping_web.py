from __future__ import annotations

import csv
import errno
import gzip
import hashlib
import io
import json
import math
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader

from rfmapping_web import shared_figure_export as shared_figure_export_module
from deploy import validate_real_data as real_data_validation
from rfmapping_web import datasets as datasets_module
from rfmapping_web import figure_exports as figure_exports_module
from rfmapping_web.app import create_app
from rfmapping_web.companions import load_tuning_curve
from rfmapping_web.config import DEFAULT_ALLOWED_NETWORKS, Settings
from rfmapping_web.datasets import DatasetValidationError
from rfmapping_web.exports import CSV_HEADERS


def occupancy_contract(
    occupancy: object, size: list[int]
) -> dict[str, object]:
    return {
        "responseUnits": "spike_count",
        "responseNormalization": "none",
        "spikeCountDefinition": (
            "each_qualifying_trial_contributes_once_per_final_spatial_bin"
        ),
        "occupancyTimeSec": occupancy,
        "occupancyTimeSecSize": size,
        "occupancyTimeDefinition": (
            "sum_of_qualifying_trial_durations_per_final_spatial_bin"
        ),
    }


def sample_payload() -> dict[str, object]:
    counts = np.arange(24, dtype=int).reshape(2, 2, 2, 3)
    return {
        "unitsSpikeCounts": counts.tolist(),
        "unitsSpikeCountsSize": [2, 2, 2, 3],
        "unitPool": [11, 22],
        "xPositions": [-10, 10],
        "yPositions": [-5, 5],
        "timeBinEdges": [-0.1, 0.0, 0.1, 0.2],
        **occupancy_contract([[0.2, 0.3], [0.4, 0.5]], [2, 2]),
    }


def write_json(path: Path, payload: dict[str, object] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload or sample_payload()), encoding="utf-8")
    return path


def tuning_payload(
    unit_ids: tuple[int, ...] = (11, 22),
    *,
    zero_occupancy_bin: int | None = 179,
) -> dict[str, object]:
    occupancy_samples = [100] * 180
    if zero_occupancy_bin is not None:
        occupancy_samples[zero_occupancy_bin] = 0
    occupancy = [samples / 100.0 for samples in occupancy_samples]
    spike_counts: list[list[int]] = []
    firing_rate_hz: list[list[float | None]] = []
    unit_data: dict[str, list[object]] = {
        "hd_class": [],
        "rate_mvl": [],
        "spike_angle_mrl": [],
        "rayleigh_score": [],
        "rayleigh_p": [],
        "rayleigh_significant": [],
        "shuffle_p": [],
        "shuffle_significant": [],
    }
    for unit_offset, unit_id in enumerate(unit_ids, start=1):
        counts = [unit_offset + (index % 3) for index in range(180)]
        rates: list[float | None] = [
            count / occupied if occupied else None
            for count, occupied in zip(counts, occupancy)
        ]
        if zero_occupancy_bin is not None:
            counts[zero_occupancy_bin] = 0
        hd_class = unit_offset % 3
        rayleigh_significant = hd_class in {1, 2}
        shuffle_significant = hd_class == 2
        spike_counts.append(counts)
        firing_rate_hz.append(rates)
        unit_data["hd_class"].append(hd_class)
        unit_data["rate_mvl"].append(0.1 * unit_offset)
        unit_data["spike_angle_mrl"].append(0.08 * unit_offset)
        unit_data["rayleigh_score"].append(8.0 if rayleigh_significant else 0.5)
        unit_data["rayleigh_p"].append(0.01 if rayleigh_significant else 0.5)
        unit_data["rayleigh_significant"].append(rayleigh_significant)
        unit_data["shuffle_p"].append(0.005 if shuffle_significant else 0.5)
        unit_data["shuffle_significant"].append(shuffle_significant)
    return {
        "metadata": {
            "session": "260630_1",
            "probe": "A",
            "epoch": "arena",
            "epoch_intervals_s": [[0.0, 12.5]],
            "headplate": {"animal": "m15", "mount": "fixture"},
            "timebase": "open_ephys_adc_t0_relative_seconds",
            "timestamp_reference": "motive_exposure_ttl_midpoint",
            "num_angle_bins": 180,
            "feature_fs_hz": 100.0,
            "classification": {
                "method": "fixture",
                "rayleigh_alpha": 0.05,
                "num_shuffle": 1000,
                "forward_compatible_note": "preserved",
            },
            "ttl_qc": {
                "ttl_pulse_count": 100,
                "measured_rate_hz": 120.0,
                "camera_ttl_active_high": True,
                "source_clock": {"name": "fixture"},
            },
        },
        "angle_bin_edges_deg": [float(index * 2) for index in range(181)],
        "occupancy_samples": occupancy_samples,
        "occupancy_time_s": occupancy,
        "unit_id": list(unit_ids),
        "spike_counts": spike_counts,
        "firing_rate_hz": firing_rate_hz,
        "unit_data": unit_data,
    }


def test_streaming_parser_never_uses_cpython_yajl_extension(tmp_path: Path) -> None:
    source = write_json(tmp_path / "rf.json")
    assert datasets_module.IJSON_BACKEND.backend in {"yajl2_cffi", "python"}
    assert datasets_module.IJSON_BACKEND.backend != "yajl2_c"

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                datasets_module._read_metadata_stream,
                [source] * 12,
            )
        )
    assert all(result["unitPool"] == [11, 22] for result in results)


def test_streaming_parser_type_error_becomes_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_json(tmp_path / "rf.json")

    def broken_parse(*_args, **_kwargs):
        raise TypeError("backend iterator failure")

    monkeypatch.setattr(datasets_module.IJSON_BACKEND, "parse", broken_parse)
    with pytest.raises(DatasetValidationError, match="backend iterator failure"):
        datasets_module._read_metadata_stream(source)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    root = tmp_path / "rf-root"
    root.mkdir()
    output_root = tmp_path / "exports"
    output_root.mkdir()
    figure_export_root = tmp_path / "figure-exports"
    figure_export_root.mkdir()
    return Settings(
        rf_root=root,
        cache_root=tmp_path / "cache",
        output_root=output_root,
        figure_export_root=figure_export_root,
        gate_db_path=tmp_path / "access-gate.sqlite3",
        cache_max_bytes=20 * 1024 * 1024,
        directory_page_size_max=10,
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@contextmanager
def authenticated_client(application):
    with TestClient(application, client=("127.0.0.1", 50000)) as client:
        token, csrf_token = application.state.access_gate.issue_session()
        client.headers["Cookie"] = f"rfmapping_session={token}"
        client.headers["X-CSRF-Token"] = csrf_token
        client.headers["Sec-Fetch-Site"] = "same-origin"
        yield client


def test_access_gate_login_cookie_api_and_session_persistence(
    settings: Settings,
) -> None:
    application = create_app(settings)
    with TestClient(application, client=("127.0.0.1", 50000)) as client:
        page_redirect = client.get("/rfmapping/", follow_redirects=False)
        assert page_redirect.status_code == 303
        assert page_redirect.headers["location"].startswith(
            "/rfmapping/login?next="
        )

        api_denied = client.get("/api/fs/list")
        assert api_denied.status_code == 401
        assert api_denied.json()["code"] == "login_required"

        login_page = client.get("/rfmapping/login")
        assert login_page.status_code == 200
        assert "What's the PI's first name?" in login_page.text
        assert "test-only-answer" not in login_page.text

        wrong = client.post(
            "/rfmapping/login",
            data={"answer": "wrong", "next": "/rfmapping/"},
            follow_redirects=False,
        )
        assert wrong.status_code == 401
        assert "set-cookie" not in wrong.headers

        accepted = client.post(
            "/rfmapping/login",
            data={"answer": "  TEST-ONLY-ANSWER  ", "next": "/rfmapping/"},
            follow_redirects=False,
        )
        assert accepted.status_code == 303
        cookie_header = accepted.headers["set-cookie"]
        assert "rfmapping_session=" in cookie_header
        assert "Max-Age=2592000" in cookie_header
        assert "Path=/rfmapping" in cookie_header
        assert "HttpOnly" in cookie_header
        assert "SameSite=Strict" in cookie_header
        token = client.cookies.get("rfmapping_session")
        csrf_token = client.cookies.get("rfmapping_csrf")
        assert token
        assert csrf_token

        authenticated_api = client.get(
            "/rfmapping/api/fs/list",
            params={"path": str(settings.rf_root)},
        )
        assert authenticated_api.status_code == 200
        csrf_denied = client.post(
            "/rfmapping/logout",
            headers={"Sec-Fetch-Site": "same-origin"},
            follow_redirects=False,
        )
        assert csrf_denied.status_code == 403
        assert csrf_denied.json()["code"] == "invalid_csrf"

    reopened = create_app(settings)
    with TestClient(reopened, client=("127.0.0.1", 50000)) as client:
        client.headers["Cookie"] = (
            f"rfmapping_session={token}; rfmapping_csrf={csrf_token}"
        )
        client.headers["Sec-Fetch-Site"] = "same-origin"
        client.headers["X-CSRF-Token"] = csrf_token
        assert client.get("/api/fs/list").status_code == 200
        logout = client.post("/rfmapping/logout", follow_redirects=False)
        assert logout.status_code == 303
        assert "Max-Age=0" in logout.headers["set-cookie"]

    replay = create_app(settings)
    with TestClient(replay, client=("127.0.0.1", 50000)) as client:
        client.headers["Cookie"] = f"rfmapping_session={token}"
        assert client.get("/api/fs/list").status_code == 401


def test_network_filter_precedes_login_and_health_is_loopback_only(app) -> None:
    with TestClient(app, client=("127.0.0.1", 50000)) as loopback:
        assert loopback.get("/api/health").status_code == 200

    with TestClient(app, client=("165.124.111.50", 50000)) as allowed_remote:
        assert allowed_remote.get("/api/health").status_code == 401
        login = allowed_remote.get("/login")
        assert login.status_code == 200

    # Browsers resolving fsmhhw9l84.local through mDNS may prefer IPv6. These
    # non-routable link-local clients must behave like the allowed IPv4 LANs.
    with TestClient(app, client=("fe80::ca0:bb18:1016:bcd4", 50000)) as link_local:
        assert link_local.get("/api/health").status_code == 401
        assert link_local.get("/login").status_code == 200

    with TestClient(app, client=("127.0.0.1", 50000)) as proxied_loopback:
        response = proxied_loopback.get(
            "/api/health",
            headers={"X-Forwarded-For": "165.124.111.50"},
        )
        assert response.status_code == 401

    with TestClient(app, client=("192.0.2.10", 50000)) as blocked:
        assert blocked.get("/login").status_code == 403
        assert blocked.get("/api/health").status_code == 403

    with TestClient(app, client=("2001:db8::10", 50000)) as blocked_ipv6:
        assert blocked_ipv6.get("/login").status_code == 403
        assert blocked_ipv6.get("/api/health").status_code == 403


def test_deployment_network_defaults_include_ipv6_link_local() -> None:
    project_root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "deploy" / "nginx-rfmapping-location.conf").is_file()
    )
    env_example = (project_root / "deploy" / "rfmapping-web.env.example").read_text(
        encoding="utf-8"
    )
    env_allowlist = next(
        line.split("=", 1)[1]
        for line in env_example.splitlines()
        if line.startswith("RFMAPPING_ALLOWED_NETWORKS=")
    )
    assert tuple(env_allowlist.split(",")) == DEFAULT_ALLOWED_NETWORKS

    nginx = (project_root / "deploy" / "nginx-rfmapping-location.conf").read_text(
        encoding="utf-8"
    )
    assert nginx.count("allow fe80::/10;") == 2
    assert nginx.count("deny all;") == 2
    assert "allow ::/0;" not in nginx


def test_health_and_lazy_browse_are_root_confined(
    app, settings: Settings, tmp_path: Path
) -> None:
    (settings.rf_root / "B-dir").mkdir()
    (settings.rf_root / "a-dir").mkdir()
    write_json(settings.rf_root / "z.json")
    write_json(settings.rf_root / "A.json")
    write_json(settings.rf_root / "tuning_curves.json", tuning_payload((11,)))
    (settings.rf_root / "positions.csv").write_text(
        "unit_index,unit_id,x_um,y_um\n0,11,1,2\n", encoding="utf-8"
    )
    (settings.rf_root / "channels.csv").write_text(
        "channel_index,channel_id,raw_channel_index,x_um,y_um,shank_id\n0,0,0,1,2,0\n",
        encoding="utf-8",
    )
    (settings.rf_root / "notes.txt").write_text("hidden", encoding="utf-8")
    (settings.rf_root / "._copy.json").write_text("hidden", encoding="utf-8")
    outside = write_json(tmp_path / "outside.json")
    (settings.rf_root / "outside-link.json").symlink_to(outside)

    with authenticated_client(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert "img-src 'self' data: blob:" in health.headers[
            "content-security-policy"
        ]
        assert health.json() == {
            "status": "ok",
            "version": "1.9.6",
            "rfRoot": str(settings.rf_root),
            "rfRootAvailable": True,
            "outputRoot": str(settings.output_root),
            "outputRootAvailable": True,
            "figureExportRoot": str(settings.figure_export_root),
            "figureExportRootAvailable": True,
        }
        prefixed_health = client.get("/rfmapping/api/health")
        assert prefixed_health.status_code == 200
        assert prefixed_health.json() == health.json()

        first = client.get(
            "/api/fs/list", params={"path": str(settings.rf_root), "limit": 2}
        )
        assert first.status_code == 200
        body = first.json()
        assert body["root"] == str(settings.rf_root.resolve())
        assert body["path"] == str(settings.rf_root.resolve())
        assert [item["name"] for item in body["entries"]] == ["a-dir", "B-dir"]
        assert all(Path(item["path"]).is_absolute() for item in body["entries"])
        assert body["nextCursor"]

        second = client.get(
            "/api/fs/list",
            params={
                "path": str(settings.rf_root),
                "limit": 2,
                "cursor": body["nextCursor"],
            },
        )
        assert second.status_code == 200
        assert [item["name"] for item in second.json()["entries"]] == [
            "A.json",
            "z.json",
        ]
        assert second.json()["nextCursor"] is None

        write_json(settings.rf_root / "alias.rfmap")
        write_json(settings.rf_root / "session.tc", tuning_payload((11,)))
        (settings.rf_root / "units.probe").write_text(
            "unit_index,unit_id,x_um,y_um\n0,11,3,4\n", encoding="utf-8"
        )
        rf_files = client.get(
            "/api/fs/list",
            params={"path": str(settings.rf_root), "kind": "rf-json"},
        )
        assert rf_files.status_code == 200
        assert [
            item["name"]
            for item in rf_files.json()["entries"]
            if item["type"] == "file"
        ] == ["A.json", "alias.rfmap", "z.json"]

        tuning_files = client.get(
            "/api/fs/list",
            params={"path": str(settings.rf_root), "kind": "tuning-json"},
        )
        assert tuning_files.status_code == 200
        assert [
            item["name"]
            for item in tuning_files.json()["entries"]
            if item["type"] == "file"
        ] == ["session.tc", "tuning_curves.json"]
        position_files = client.get(
            "/api/fs/list",
            params={"path": str(settings.rf_root), "kind": "positions-csv"},
        )
        assert position_files.status_code == 200
        assert [
            item["name"]
            for item in position_files.json()["entries"]
            if item["type"] == "file"
        ] == ["positions.csv", "units.probe"]
        assert all(
            item["name"] != "channels.csv" for item in position_files.json()["entries"]
        )
        assert (
            client.get(
                "/api/fs/list",
                params={"path": str(settings.rf_root), "kind": "anything"},
            ).status_code
            == 422
        )

        assert client.get("/api/fs/list", params={"path": "../"}).status_code == 400
        assert (
            client.get("/api/fs/list", params={"cursor": "not-base64"}).status_code
            == 400
        )

    with TestClient(app, client=("192.0.2.10", 50000)) as blocked:
        response = blocked.get("/api/health")
        assert response.status_code == 403
        assert response.json()["detail"] == "Client network is not allowed"


def _create_remote_fixture(root: Path) -> Path:
    session_root = root / "Kai" / "260630" / "260630_3"
    data_root = session_root / "data"
    source = write_json(
        data_root / "rfmapping" / "good" / "window" / "ProbeA" / "rf.json"
    )
    waveform_root = data_root / "waveform" / "ProbeA"
    waveform_root.mkdir(parents=True)
    (waveform_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_name": "rfmapping-spikeinterface-waveforms",
                "schema_version": 4,
                "recording": {
                    "sampling_frequency_hz": 30_000.0,
                    "num_frames": 1_800_000,
                    "duration_minutes": 1.0,
                },
                "units": {"scope": "good", "count": 2},
                "waveform": {"nbefore": 2, "num_samples": 4},
                "files": {"units": "units.csv"},
            }
        ),
        encoding="utf-8",
    )
    (waveform_root / "channels.csv").write_text(
        "channel_index,channel_id,raw_channel_index,x_um,y_um,shank_id\n"
        "0,0,0,10,20,1\n1,1,1,10,24,1\n",
        encoding="utf-8",
    )
    (waveform_root / "waveform_time.csv").write_text(
        "sample_index,sample_offset,time_ms\n"
        "0,-2,-0.5\n1,-1,-0.25\n2,0,0\n3,1,0.25\n",
        encoding="utf-8",
    )
    (waveform_root / "units.csv").write_text(
        "unit_index,unit_id,quality,total_spike_count,selected_spike_count,"
        "time_coverage_percent,best_channel_index,best_channel_id,"
        "best_channel_x_um,best_channel_y_um,max_ptp_uv,unit_data_dir\n"
        "0,11,good,1000,100,90,0,0,10,20,42,Unit11\n"
        "1,22,good,1100,110,91,1,1,10,24,45,Unit22\n",
        encoding="utf-8",
    )
    for unit_id in (11, 22):
        unit_root = waveform_root / f"Unit{unit_id}"
        unit_root.mkdir()
        with gzip.open(
            unit_root / "template_uv.csv.gz",
            "wt",
            encoding="utf-8",
            newline="",
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(("sample_index", "chidx_000_uv", "chidx_001_uv"))
            for sample_index, scale in enumerate((1.0, 2.0, -4.0, 3.0)):
                writer.writerow(
                    (sample_index, scale + unit_id, 2 * scale + unit_id)
                )
    positions = data_root / "spike_position" / "ProbeA" / "positions.csv"
    positions.parent.mkdir(parents=True)
    positions.write_text(
        "unit_index,unit_id,x_um,y_um\n0,11,10.5,21\n1,22,12,24\n",
        encoding="utf-8",
    )
    write_json(
        root
        / "Kai"
        / "260630"
        / "260630_1"
        / "data"
        / "tuning_curves"
        / "ProbeA"
        / "tuning_curves.json",
        tuning_payload((22, 11)),
    )
    image = data_root / "tc_curve" / "ProbeA" / "HD tuning curve - cluster 11.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    return source


def _open(client: TestClient, source: Path) -> dict[str, object]:
    opened = client.post("/api/datasets/open", json={"path": str(source)})
    assert opened.status_code == 200, opened.text
    return opened.json()


def test_open_metadata_binary_probe_and_hd(app, settings: Settings) -> None:
    source = _create_remote_fixture(settings.rf_root)
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        assert metadata == {
            "id": metadata["id"],
            "name": "rf.json",
            "sourcePath": str(source.resolve()),
            "shape": [2, 2, 2, 3],
            "unitPool": [11, 22],
            "xPositions": [-10.0, 10.0],
            "yPositions": [-5.0, 5.0],
            "timeBinEdges": [-0.1, 0.0, 0.1, 0.2],
            "occupancyTimeSec": [[0.2, 0.3], [0.4, 0.5]],
            "responseUnits": "spike_count",
            "responseNormalization": "none",
            "capabilities": {
                "probe": True,
                "hd": True,
                "waveform": True,
                "occupancy": True,
            },
        }
        dataset_id = metadata["id"]

        meta_again = client.get(f"/api/datasets/{dataset_id}/meta")
        assert meta_again.status_code == 200
        assert meta_again.json() == metadata

        unit = client.get(f"/api/datasets/{dataset_id}/units/22")
        assert unit.status_code == 200
        assert unit.headers["x-rf-dtype"] == "<f8"
        assert unit.headers["x-rf-shape"] == "2,2,3"
        assert unit.headers["x-rf-cluster-id"] == "22"
        decoded = np.frombuffer(unit.content, dtype="<f8").reshape(2, 2, 3)
        np.testing.assert_array_equal(decoded, np.arange(12, 24).reshape(2, 2, 3))
        assert client.get(f"/api/datasets/{dataset_id}/units/999").status_code == 404

        probe = client.get(f"/api/datasets/{dataset_id}/probe")
        assert probe.status_code == 200
        assert probe.json()["probe"] == "ProbeA"
        assert probe.json()["channels"][0] == {
            "channelId": 0,
            "x": 10.0,
            "y": 20.0,
            "shank": 1,
        }
        assert probe.json()["units"][0]["unitId"] == 11
        data_root = next(parent for parent in source.parents if parent.name == "data")
        positions_path = data_root / "spike_position" / "ProbeA" / "positions.csv"
        manual_probe = client.get(
            f"/api/datasets/{dataset_id}/probe",
            params={"path": str(positions_path)},
        )
        assert manual_probe.status_code == 200, manual_probe.text
        assert manual_probe.json()["channels"] == probe.json()["channels"]
        assert manual_probe.json()["units"] == probe.json()["units"]

        hd_dataset = client.get(f"/api/datasets/{dataset_id}/hd")
        assert hd_dataset.status_code == 200, hd_dataset.text
        tuning = hd_dataset.json()
        assert tuning["available"] is True
        assert tuning["sourcePath"].endswith(
            "/260630_1/data/tuning_curves/ProbeA/tuning_curves.json"
        )
        assert "schemaVersion" not in tuning
        assert tuning["metadata"]["session"] == "260630_1"
        assert tuning["metadata"]["epoch"] == "arena"
        assert tuning["metadata"]["epoch_intervals_s"] == [[0.0, 12.5]]
        assert tuning["metadata"]["headplate"]["animal"] == "m15"
        assert (
            tuning["metadata"]["classification"]["forward_compatible_note"]
            == "preserved"
        )
        assert tuning["metadata"]["ttl_qc"]["source_clock"] == {"name": "fixture"}
        assert tuning["occupancyTimeS"][-1] == 0.0
        assert [unit["unitId"] for unit in tuning["units"]] == [22, 11]
        assert tuning["units"][0]["rates"][-1] is None
        assert tuning["units"][0]["spikeCounts"][-1] == 0
        assert tuning["units"][0]["hdClass"] == 1
        assert tuning["units"][1]["spikeCounts"][0] == 2
        assert tuning["units"][1]["hdClass"] == 2

        hd = client.get(f"/api/datasets/{dataset_id}/hd/11")
        assert hd.status_code == 200, hd.text
        assert hd.json() == {
            "available": True,
            "sourcePath": tuning["sourcePath"],
            "rates": tuning["units"][1]["rates"],
            "spikeCounts": tuning["units"][1]["spikeCounts"],
            "occupancyTimeS": tuning["occupancyTimeS"],
            "hdClass": 2,
            "metadata": tuning["metadata"],
        }
        missing_hd = client.get(f"/api/datasets/{dataset_id}/hd/999")
        assert missing_hd.status_code == 200
        assert missing_hd.json()["available"] is False
        assert missing_hd.json()["sourcePath"] == tuning["sourcePath"]
        assert missing_hd.json()["rates"] is None

        waveform = client.get(
            f"/api/datasets/{dataset_id}/waveform/11",
            params={"mode": "same_x_column"},
        )
        assert waveform.status_code == 200, waveform.text
        waveform_payload = waveform.json()
        assert waveform_payload["available"] is True
        assert waveform_payload["unitId"] == 11
        assert waveform_payload["mode"] == "same_x_column"
        assert waveform_payload["channelLabels"] == ["ch 1", "ch 0"]
        assert waveform_payload["bestChannelRow"] == 1
        assert waveform_payload["maxPtpUv"] == 42.0
        assert len(waveform_payload["valuesUv"]) == 2
        assert len(waveform_payload["valuesUv"][0]) == 4
        assert waveform_payload["valuesUv"][0][:2] == [-1.0, 1.0]
        missing_waveform = client.get(f"/api/datasets/{dataset_id}/waveform/999")
        assert missing_waveform.status_code == 200
        assert missing_waveform.json()["available"] is False
        assert (
            client.get(
                f"/api/datasets/{dataset_id}/waveform/11",
                params={"mode": "same_row"},
            ).status_code
            == 422
        )

        image_url = f"/api/datasets/{dataset_id}/hd/11/image"
        image = client.get(image_url)
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert "content-disposition" not in image.headers

    cache_files = list(settings.cache_root.glob("*.f64"))
    assert len(cache_files) == 1
    assert cache_files[0].stat().st_size == 2 * 2 * 2 * 3 * 8


def test_current_extensions_open_and_win_companion_discovery(
    app, settings: Settings
) -> None:
    session_root = settings.rf_root / "Kai" / "260630" / "260630_3"
    data_root = session_root / "data"
    source = write_json(
        data_root / "rfmapping" / "good" / "window" / "ProbeA" / "rf.rfmap"
    )
    positions_root = data_root / "spike_position" / "ProbeA"
    positions_root.mkdir(parents=True)
    (positions_root / "positions.probe").write_text(
        "unit_index,unit_id,x_um,y_um\n0,11,101,201\n1,22,102,202\n",
        encoding="utf-8",
    )
    (positions_root / "positions.csv").write_text(
        "unit_index,unit_id,x_um,y_um\n0,11,1,2\n1,22,3,4\n",
        encoding="utf-8",
    )
    tuning_root = (
        settings.rf_root
        / "Kai"
        / "260630"
        / "260630_1"
        / "data"
        / "tuning_curves"
        / "ProbeA"
    )
    current_tuning = tuning_payload((11, 22))
    current_tuning["metadata"]["session"] = "current-tc"  # type: ignore[index]
    write_json(tuning_root / "tuning_curves.tc", current_tuning)
    legacy_tuning = tuning_payload((11, 22))
    legacy_tuning["metadata"]["session"] = "legacy-json"  # type: ignore[index]
    write_json(tuning_root / "tuning_curves.json", legacy_tuning)
    second_tuning = tuning_payload((22, 11))
    second_tuning["metadata"]["session"] = "second-session"  # type: ignore[index]
    write_json(
        settings.rf_root
        / "Kai"
        / "260630"
        / "260630_2"
        / "data"
        / "tuning_curves"
        / "ProbeA"
        / "tuning_curves.json",
        second_tuning,
    )

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        assert metadata["name"] == "rf.rfmap"
        assert metadata["capabilities"] == {
            "probe": True,
            "hd": True,
            "waveform": False,
            "occupancy": True,
        }
        probe = client.get(f"/api/datasets/{metadata['id']}/probe")
        assert probe.status_code == 200, probe.text
        assert probe.json()["units"][0] == {"unitId": 11, "x": 101.0, "y": 201.0}
        tuning = client.get(f"/api/datasets/{metadata['id']}/hd")
        assert tuning.status_code == 200, tuning.text
        assert tuning.json()["sourcePath"].endswith("/tuning_curves.tc")
        assert tuning.json()["metadata"]["session"] == "current-tc"
        session_two = client.get(
            f"/api/datasets/{metadata['id']}/hd", params={"session": 2}
        )
        assert session_two.status_code == 200, session_two.text
        assert session_two.json()["metadata"]["session"] == "second-session"
        assert [unit["unitId"] for unit in session_two.json()["units"]] == [22, 11]
        missing_session = client.get(
            f"/api/datasets/{metadata['id']}/hd", params={"session": 3}
        )
        assert missing_session.status_code == 200
        assert missing_session.json()["available"] is False


def test_input_suffix_validation_accepts_aliases_and_rejects_other_files(
    app, settings: Settings
) -> None:
    source = write_json(settings.rf_root / "manual" / "ProbeA" / "rf.rfmap")
    invalid_rf = write_json(settings.rf_root / "manual" / "ProbeA" / "rf.txt")
    tuning = write_json(settings.rf_root / "manual" / "curves.tc", tuning_payload((11,)))
    invalid_tuning = write_json(
        settings.rf_root / "manual" / "curves.txt", tuning_payload((11,))
    )
    probe = settings.rf_root / "manual" / "locations.probe"
    probe.write_text(
        "unit_index,unit_id,x_um,y_um\n0,11,1,2\n", encoding="utf-8"
    )
    invalid_probe = settings.rf_root / "manual" / "locations.txt"
    invalid_probe.write_text(probe.read_text(encoding="utf-8"), encoding="utf-8")

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        dataset_id = metadata["id"]
        assert client.post(
            "/api/datasets/open", json={"path": str(invalid_rf)}
        ).status_code == 422
        assert client.get(
            f"/api/datasets/{dataset_id}/hd", params={"path": str(tuning)}
        ).status_code == 200
        assert client.get(
            f"/api/datasets/{dataset_id}/hd", params={"path": str(invalid_tuning)}
        ).status_code == 422
        assert client.get(
            f"/api/datasets/{dataset_id}/probe", params={"path": str(probe)}
        ).status_code == 200
        assert client.get(
            f"/api/datasets/{dataset_id}/probe", params={"path": str(invalid_probe)}
        ).status_code == 422


def test_hd_discovery_uses_first_numeric_session_for_same_date(
    app, settings: Settings
) -> None:
    source = write_json(
        settings.rf_root
        / "Kai"
        / "260729"
        / "260729_4"
        / "data"
        / "rfmapping"
        / "good"
        / "window"
        / "ProbeA"
        / "rf.json"
    )
    first_payload = tuning_payload((11,))
    first_payload["metadata"]["session"] = "first"  # type: ignore[index]
    first = write_json(
        settings.rf_root
        / "Kai"
        / "260729"
        / "260729_1"
        / "data"
        / "tuning_curves"
        / "ProbeA"
        / "tuning_curves.json",
        first_payload,
    )
    second_payload = tuning_payload((11,))
    second_payload["metadata"]["session"] = "second"  # type: ignore[index]
    write_json(
        settings.rf_root
        / "Kai"
        / "260729"
        / "260729_2"
        / "data"
        / "tuning_curves"
        / "ProbeA"
        / "tuning_curves.json",
        second_payload,
    )

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        assert metadata["capabilities"]["hd"] is True
        response = client.get(f"/api/datasets/{metadata['id']}/hd")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["sourcePath"] == str(first.resolve())
        assert "schemaVersion" not in payload
        assert payload["metadata"]["session"] == "first"
        assert payload["occupancyTimeS"] == first_payload["occupancy_time_s"]
        assert payload["units"][0]["unitId"] == 11
        assert payload["units"][0]["spikeCounts"] == first_payload["spike_counts"][0]  # type: ignore[index]


def test_manual_tuning_path_is_root_confined_and_does_not_need_auto_discovery(
    app, settings: Settings, tmp_path: Path
) -> None:
    source = write_json(settings.rf_root / "manual" / "ProbeB" / "rf.json")
    tuning = write_json(
        settings.rf_root / "chosen" / "tuning_curves.json",
        tuning_payload((11,)),
    )
    malformed = write_json(
        settings.rf_root / "chosen" / "bad.json", {"11": [1.0] * 179}
    )
    outside = write_json(tmp_path / "outside-tuning.json", tuning_payload())

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        dataset_id = metadata["id"]
        assert metadata["capabilities"]["hd"] is False
        unavailable = client.get(f"/api/datasets/{dataset_id}/hd")
        assert unavailable.status_code == 200
        assert unavailable.json() == {
            "available": False,
            "sourcePath": None,
            "metadata": None,
            "occupancyTimeS": None,
            "units": [],
        }
        selected = client.get(
            f"/api/datasets/{dataset_id}/hd", params={"path": str(tuning)}
        )
        assert selected.status_code == 200, selected.text
        assert selected.json()["sourcePath"] == str(tuning.resolve())
        cluster = client.get(
            f"/api/datasets/{dataset_id}/hd/11", params={"path": str(tuning)}
        )
        assert cluster.status_code == 200
        assert cluster.json()["rates"] == selected.json()["units"][0]["rates"]
        assert (
            client.get(
                f"/api/datasets/{dataset_id}/hd", params={"path": str(malformed)}
            ).status_code
            == 422
        )
        assert (
            client.get(
                f"/api/datasets/{dataset_id}/hd", params={"path": str(outside)}
            ).status_code
            == 400
        )


def test_probe_positions_only_and_invalid_channels_fall_back_to_units(
    app, settings: Settings, tmp_path: Path
) -> None:
    data_root = settings.rf_root / "Kai" / "260615" / "260615_3" / "data"
    source = write_json(
        data_root / "rfmapping" / "good" / "window" / "ProbeA" / "rf.json"
    )
    positions = data_root / "spike_position" / "ProbeA" / "positions.csv"
    positions.parent.mkdir(parents=True)
    positions.write_text(
        "unit_index,unit_id,x_um,y_um\n0,11,10.5,21\n1,22,12,24\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside-positions.csv"
    outside.write_text(positions.read_text(encoding="utf-8"), encoding="utf-8")

    with authenticated_client(app) as client:
        first = _open(client, source)
        assert first["capabilities"]["probe"] is True
        probe = client.get(f"/api/datasets/{first['id']}/probe")
        assert probe.status_code == 200, probe.text
        assert probe.json()["channels"] == []
        assert [unit["unitId"] for unit in probe.json()["units"]] == [11, 22]

        channels = data_root / "waveform" / "ProbeA" / "channels.csv"
        channels.parent.mkdir(parents=True)
        channels.write_text("wrong,columns\n1,2\n", encoding="utf-8")
        second_source = write_json(source.with_name("rf_second.json"))
        second = _open(client, second_source)
        fallback = client.get(f"/api/datasets/{second['id']}/probe")
        assert fallback.status_code == 200, fallback.text
        assert fallback.json()["channels"] == []
        assert len(fallback.json()["units"]) == 2

        manual = client.get(
            f"/api/datasets/{second['id']}/probe", params={"path": str(positions)}
        )
        assert manual.status_code == 200, manual.text
        assert manual.json()["channels"] == []
        assert (
            client.get(
                f"/api/datasets/{second['id']}/probe",
                params={"path": str(outside)},
            ).status_code
            == 400
        )


def test_probe_geometry_preserves_explicitly_unpositioned_units(
    app, settings: Settings
) -> None:
    data_root = settings.rf_root / "Kai" / "260619" / "260619_1" / "data"
    source = write_json(
        data_root / "rfmapping" / "good" / "window" / "ProbeA" / "rf.json"
    )
    positions = data_root / "spike_position" / "ProbeA" / "positions.csv"
    positions.parent.mkdir(parents=True)
    positions.write_text(
        "unit_index,unit_id,x_um,y_um\n"
        "0,11,10,20\n"
        "1,22,nan,nan\n"
        "2,999,30,40\n",
        encoding="utf-8",
    )
    channels = data_root / "waveform" / "ProbeA" / "channels.csv"
    channels.parent.mkdir(parents=True)
    channels.write_text(
        "channel_index,channel_id,raw_channel_index,x_um,y_um,shank_id\n"
        "0,0,0,5,15,0\n",
        encoding="utf-8",
    )

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        assert metadata["capabilities"]["probe"] is True
        geometry = client.get(f"/api/datasets/{metadata['id']}/probe")
        assert geometry.status_code == 200, geometry.text
        assert geometry.json()["units"] == [
            {"unitId": 11, "x": 10.0, "y": 20.0},
            {"unitId": 22, "x": None, "y": None},
        ]
        assert [channel["channelId"] for channel in geometry.json()["channels"]] == [0]


@pytest.mark.parametrize(
    ("x_value", "y_value"),
    (("nan", "20"), ("10", "nan"), ("inf", "inf"), ("bad", "bad")),
)
def test_probe_geometry_rejects_malformed_coordinate_pairs(
    app, settings: Settings, x_value: str, y_value: str
) -> None:
    data_root = settings.rf_root / "Kai" / "260620" / "260620_1" / "data"
    source = write_json(
        data_root / "rfmapping" / "good" / "window" / "ProbeA" / "rf.json"
    )
    positions = data_root / "spike_position" / "ProbeA" / "positions.csv"
    positions.parent.mkdir(parents=True)
    positions.write_text(
        "unit_index,unit_id,x_um,y_um\n"
        f"0,11,{x_value},{y_value}\n",
        encoding="utf-8",
    )

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        geometry = client.get(f"/api/datasets/{metadata['id']}/probe")
        assert geometry.status_code == 422
        assert "positions.csv value on row 2" in geometry.json()["detail"]


def test_probe_geometry_still_rejects_duplicate_unpositioned_unit_ids(
    app, settings: Settings
) -> None:
    data_root = settings.rf_root / "Kai" / "260621" / "260621_1" / "data"
    source = write_json(
        data_root / "rfmapping" / "good" / "window" / "ProbeA" / "rf.json"
    )
    positions = data_root / "spike_position" / "ProbeA" / "positions.csv"
    positions.parent.mkdir(parents=True)
    positions.write_text(
        "unit_index,unit_id,x_um,y_um\n"
        "0,11,nan,nan\n"
        "1,11,10,20\n",
        encoding="utf-8",
    )

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        geometry = client.get(f"/api/datasets/{metadata['id']}/probe")
        assert geometry.status_code == 422
        assert geometry.json()["detail"] == "Duplicate unit_id 11 in positions.csv"


def test_probe_discovery_never_falls_back_past_current_session_data(
    app, settings: Settings
) -> None:
    date_root = settings.rf_root / "Kai" / "260616"
    data_root = date_root / "260616_3" / "data"
    source = write_json(
        data_root / "rfmapping" / "good" / "window" / "ProbeA" / "rf.json"
    )
    unrelated = date_root / "positions.csv"
    unrelated.write_text(
        "unit_index,unit_id,x_um,y_um\n0,11,10,20\n1,22,30,40\n",
        encoding="utf-8",
    )

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        assert metadata["capabilities"]["probe"] is False
        assert client.get(f"/api/datasets/{metadata['id']}/probe").status_code == 404


def test_probe_geometry_filters_rf_units_and_missing_selected_unit_is_placeholder(
    app, settings: Settings
) -> None:
    data_root = settings.rf_root / "Kai" / "260617" / "260617_1" / "data"
    source = write_json(
        data_root / "rfmapping" / "good" / "window" / "ProbeA" / "rf.json"
    )
    positions = data_root / "spike_position" / "ProbeA" / "positions.csv"
    positions.parent.mkdir(parents=True)
    positions.write_text(
        "unit_index,unit_id,x_um,y_um\n0,11,10,20\n1,999,30,40\n",
        encoding="utf-8",
    )
    pages = _figure_pages("probe")

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        geometry = client.get(f"/api/datasets/{metadata['id']}/probe")
        assert geometry.status_code == 200, geometry.text
        assert [unit["unitId"] for unit in geometry.json()["units"]] == [11]

        endpoint = f"/api/datasets/{metadata['id']}/figure-exports/preview"
        available = client.post(
            endpoint,
            json={
                "specVersion": 1,
                "clusterId": 11,
                "pageIndex": 0,
                "pages": pages,
            },
        )
        assert available.status_code == 200, available.text
        assert "x-rf-placeholder-count" not in available.headers

        missing = client.post(
            endpoint,
            json={
                "specVersion": 1,
                "clusterId": 22,
                "pageIndex": 0,
                "pages": pages,
            },
        )
        assert missing.status_code == 200, missing.text
        assert missing.headers["x-rf-placeholder-count"] == "1"


def test_probe_geometry_with_no_rf_unit_overlap_fails_closed(
    app, settings: Settings
) -> None:
    data_root = settings.rf_root / "Kai" / "260618" / "260618_1" / "data"
    source = write_json(
        data_root / "rfmapping" / "good" / "window" / "ProbeA" / "rf.json"
    )
    positions = data_root / "spike_position" / "ProbeA" / "positions.csv"
    positions.parent.mkdir(parents=True)
    positions.write_text(
        "unit_index,unit_id,x_um,y_um\n0,999,10,20\n",
        encoding="utf-8",
    )

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        geometry = client.get(f"/api/datasets/{metadata['id']}/probe")
        assert geometry.status_code == 422
        assert "no unit IDs" in geometry.json()["detail"]
        preview = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports/preview",
            json={
                "specVersion": 1,
                "clusterId": 11,
                "pageIndex": 0,
                "pages": _figure_pages("probe"),
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.headers["x-rf-placeholder-count"] == "1"


def test_ad_hoc_hd_csv_or_png_does_not_claim_tuning_capability(
    app, settings: Settings
) -> None:
    data_root = settings.rf_root / "Kai" / "260101" / "260101_1" / "data"
    source = write_json(
        data_root / "rfmapping" / "good" / "window" / "ProbeA" / "rf.json"
    )
    (data_root / "tc_summary_ProbeA.csv").write_text(
        "cluster_id,R\n11,0.7\n", encoding="utf-8"
    )
    (data_root / "hd_tuning_curves.csv").write_text(
        "angle_deg,11\n0,1\n", encoding="utf-8"
    )
    image = data_root / "tc_curve" / "ProbeA" / "HD curve - cluster 11.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        assert metadata["capabilities"]["hd"] is False
        hd = client.get(f"/api/datasets/{metadata['id']}/hd")
        assert hd.status_code == 200
        assert hd.json()["available"] is False
        assert (
            client.get(f"/api/datasets/{metadata['id']}/hd/11/image").status_code == 200
        )


def test_tuning_loader_accepts_nullable_unit_metrics(tmp_path: Path) -> None:
    payload = tuning_payload((11,))
    unit_data = payload["unit_data"]
    for key in ("rate_mvl", "spike_angle_mrl", "rayleigh_score", "rayleigh_p"):
        unit_data[key][0] = None  # type: ignore[index]
    for key in ("shuffle_p", "rayleigh_significant", "shuffle_significant", "hd_class"):
        unit_data[key][0] = None  # type: ignore[index]
    path = write_json(tmp_path / "nullable-tuning.json", payload)

    loaded = load_tuning_curve(path)

    assert loaded.units[0].unit_id == 11
    assert loaded.units[0].hd_class is None


def test_tuning_loader_tolerates_roundoff_at_unit_metric_upper_bound(
    tmp_path: Path,
) -> None:
    payload = tuning_payload((11,))
    unit_data = payload["unit_data"]
    unit_data["rate_mvl"][0] = 1.0000000000000002  # type: ignore[index]
    unit_data["spike_angle_mrl"][0] = 1.0000000000000002  # type: ignore[index]

    loaded = load_tuning_curve(write_json(tmp_path / "rounded-mrl.json", payload))

    assert loaded.units[0].unit_id == 11


def test_tuning_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    serialized = json.dumps(tuning_payload((11,)))
    path = tmp_path / "duplicate-key.json"
    path.write_text('{"metadata":{},' + serialized[1:], encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate tuning-curve JSON key"):
        load_tuning_curve(path)


@pytest.mark.parametrize(
    "case",
    [
        "legacy-mapping",
        "old-units-array",
        "obsolete-schema-version",
        "missing-top-level-key",
        "unexpected-top-level-key",
        "unequal-edges",
        "nonpositive-feature-fs",
        "feature-fs-mismatch",
        "non-integer-occupancy-sample",
        "occupancy-zero-mask",
        "all-zero-occupancy",
        "duplicate-unit",
        "count-row-mismatch",
        "rate-row-mismatch",
        "rate-bin-mismatch",
        "missing-unit-data-key",
        "unexpected-unit-data-key",
        "unit-data-length-mismatch",
        "non-integer-count",
        "zero-occupancy-mismatch",
        "rate-mismatch",
        "bad-unit-metric",
        "non-boolean-significance",
        "inconsistent-significance",
        "bad-hd-class",
        "non-finite-metadata",
    ],
)
def test_tuning_loader_rejects_malformed_current_contract(
    tmp_path: Path, case: str
) -> None:
    if case == "legacy-mapping":
        payload: dict[str, object] = {"11": [1.0] * 180}
    elif case == "old-units-array":
        current = tuning_payload((11,))
        payload = {
            "schema_version": 2,
            "metadata": current["metadata"],
            "angle_bin_edges_deg": current["angle_bin_edges_deg"],
            "occupancy_samples": current["occupancy_samples"],
            "occupancy_time_s": current["occupancy_time_s"],
            "units": [
                {
                    "unit_id": 11,
                    "spike_counts": current["spike_counts"][0],  # type: ignore[index]
                    "firing_rate_hz": current["firing_rate_hz"][0],  # type: ignore[index]
                    "hd_class": current["unit_data"]["hd_class"][0],  # type: ignore[index]
                }
            ],
        }
    else:
        payload = tuning_payload((11, 22) if case == "duplicate-unit" else (11,))
        if case == "obsolete-schema-version":
            payload["schema_version"] = 2
        elif case == "missing-top-level-key":
            payload.pop("occupancy_samples")
        elif case == "unexpected-top-level-key":
            payload["unexpected"] = None
        elif case == "unequal-edges":
            payload["angle_bin_edges_deg"][1] = 3.0  # type: ignore[index]
        elif case == "nonpositive-feature-fs":
            payload["metadata"]["feature_fs_hz"] = 0.0  # type: ignore[index]
        elif case == "feature-fs-mismatch":
            payload["metadata"]["feature_fs_hz"] = 99.0  # type: ignore[index]
        elif case == "non-integer-occupancy-sample":
            payload["occupancy_samples"][0] = 1.5  # type: ignore[index]
        elif case == "occupancy-zero-mask":
            payload["occupancy_samples"][0] = 0  # type: ignore[index]
        elif case == "all-zero-occupancy":
            payload["occupancy_samples"] = [0] * 180
            payload["occupancy_time_s"] = [0.0] * 180
        elif case == "duplicate-unit":
            payload["unit_id"][1] = 11  # type: ignore[index]
        elif case == "count-row-mismatch":
            payload["spike_counts"].clear()  # type: ignore[union-attr]
        elif case == "rate-row-mismatch":
            payload["firing_rate_hz"].clear()  # type: ignore[union-attr]
        elif case == "rate-bin-mismatch":
            payload["firing_rate_hz"][0].pop()  # type: ignore[index]
        elif case == "missing-unit-data-key":
            payload["unit_data"].pop("rate_mvl")  # type: ignore[union-attr]
        elif case == "unexpected-unit-data-key":
            payload["unit_data"]["unexpected"] = [None]  # type: ignore[index]
        elif case == "unit-data-length-mismatch":
            payload["unit_data"]["rate_mvl"].clear()  # type: ignore[index,union-attr]
        elif case == "non-integer-count":
            payload["spike_counts"][0][0] = 1.5  # type: ignore[index]
        elif case == "zero-occupancy-mismatch":
            payload["spike_counts"][0][-1] = 1  # type: ignore[index]
        elif case == "rate-mismatch":
            payload["firing_rate_hz"][0][0] = 999.0  # type: ignore[index]
        elif case == "bad-unit-metric":
            payload["unit_data"]["rate_mvl"][0] = 1.5  # type: ignore[index]
        elif case == "non-boolean-significance":
            payload["unit_data"]["rayleigh_significant"][0] = 1  # type: ignore[index]
        elif case == "inconsistent-significance":
            payload["unit_data"]["rayleigh_significant"][0] = False  # type: ignore[index]
        elif case == "bad-hd-class":
            payload["unit_data"]["hd_class"][0] = 3  # type: ignore[index]
        elif case == "non-finite-metadata":
            payload["metadata"]["invalid"] = float("nan")  # type: ignore[index]
    path = write_json(tmp_path / f"{case}.json", payload)
    with pytest.raises(ValueError):
        load_tuning_curve(path)


@pytest.mark.parametrize(
    "case",
    ["nan", "overflow-token", "huge-occupancy-sample", "huge-spike-count"],
)
def test_hd_api_returns_422_for_non_finite_or_overflowing_tuning_numbers(
    app, settings: Settings, case: str
) -> None:
    source = write_json(settings.rf_root / "session" / "rf.json")
    payload = tuning_payload((11,))
    if case == "nan":
        payload["metadata"]["invalid"] = float("nan")  # type: ignore[index]
    elif case == "overflow-token":
        payload["metadata"]["invalid"] = "OVERFLOW_TOKEN"  # type: ignore[index]
    elif case == "huge-occupancy-sample":
        payload["occupancy_samples"][0] = 10**1000  # type: ignore[index]
    elif case == "huge-spike-count":
        payload["spike_counts"][0][0] = 10**1000  # type: ignore[index]

    tuning_path = settings.rf_root / f"{case}-tuning_curves.json"
    serialized = json.dumps(payload)
    if case == "overflow-token":
        serialized = serialized.replace('"OVERFLOW_TOKEN"', "1e999")
    tuning_path.write_text(serialized, encoding="utf-8")

    with authenticated_client(app) as client:
        opened = _open(client, source)
        response = client.get(
            f"/api/datasets/{opened['id']}/hd",
            params={"path": str(tuning_path)},
        )

    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "mutate,detail",
    [
        (lambda payload: payload.pop("timeBinEdges"), "Missing JSON keys"),
        (
            lambda payload: payload.__setitem__("unitsSpikeCountsSize", [2, 2, 2, 4]),
            "timeBinEdges",
        ),
        (
            lambda payload: payload["unitsSpikeCounts"][0][0][0].__setitem__(0, -1),
            "negative",
        ),
        (
            lambda payload: payload["unitsSpikeCounts"][0][0][0].__setitem__(0, True),
            "non-numeric",
        ),
        (
            lambda payload: payload["unitsSpikeCounts"][0][0][0].__setitem__(0, 0.5),
            "non-integer",
        ),
        (
            lambda payload: payload.__setitem__("responseUnits", "Hz"),
            "responseUnits",
        ),
        (
            lambda payload: payload.__setitem__("occupancyTimeSecSize", [1, 4]),
            "occupancyTimeSecSize",
        ),
    ],
)
def test_invalid_rf_json_is_rejected(
    app, settings: Settings, mutate, detail: str
) -> None:
    payload = sample_payload()
    mutate(payload)
    source = write_json(settings.rf_root / "bad.json", payload)
    with authenticated_client(app) as client:
        response = client.post("/api/datasets/open", json={"path": str(source)})
    assert response.status_code == 422
    assert detail.casefold() in response.json()["detail"].casefold()
    assert not list(settings.cache_root.glob("*.f64"))


def test_singleton_occupancy_scalar_is_normalized(app, settings: Settings) -> None:
    payload = {
        "unitsSpikeCounts": [[[[4, 5]]]],
        "unitsSpikeCountsSize": [1, 1, 1, 2],
        "unitPool": [7],
        "xPositions": [0],
        "yPositions": [0],
        "timeBinEdges": [0, 0.1, 0.2],
        **occupancy_contract(3, [1, 1]),
    }
    source = write_json(settings.rf_root / "singleton.json", payload)
    with authenticated_client(app) as client:
        response = client.post("/api/datasets/open", json={"path": str(source)})
    assert response.status_code == 200, response.text
    assert response.json()["occupancyTimeSec"] == [[3.0]]


@pytest.mark.parametrize(
    ("x_positions", "y_positions", "expected_x", "expected_y"),
    [
        (list(range(120)), 0, [float(value) for value in range(120)], [0.0]),
        (0, [-3, 3], [0.0], [-3.0, 3.0]),
    ],
)
def test_scalar_singleton_spatial_axes_are_normalized(
    app,
    settings: Settings,
    x_positions,
    y_positions,
    expected_x: list[float],
    expected_y: list[float],
) -> None:
    n_x = len(expected_x)
    n_y = len(expected_y)
    payload = {
        "unitsSpikeCounts": np.arange(n_y * n_x * 2, dtype=int)
        .reshape(1, n_y, n_x, 2)
        .tolist(),
        "unitsSpikeCountsSize": [1, n_y, n_x, 2],
        "unitPool": [7],
        "xPositions": x_positions,
        "yPositions": y_positions,
        "timeBinEdges": [0, 0.1, 0.2],
        **occupancy_contract([1.0] * (n_y * n_x), [n_y, n_x]),
    }
    source = write_json(settings.rf_root / "singleton-axis.rfmap", payload)

    with authenticated_client(app) as client:
        response = client.post("/api/datasets/open", json={"path": str(source)})

    assert response.status_code == 200, response.text
    assert response.json()["xPositions"] == expected_x
    assert response.json()["yPositions"] == expected_y
    assert response.json()["occupancyTimeSec"] == (
        [[1.0] * n_x] if n_y == 1 else [[1.0] for _ in range(n_y)]
    )


@pytest.mark.parametrize("axis", ["xPositions", "yPositions"])
def test_scalar_spatial_axis_is_rejected_for_non_singleton_dimension(
    app,
    settings: Settings,
    axis: str,
) -> None:
    payload = sample_payload()
    payload[axis] = 0
    source = write_json(settings.rf_root / f"invalid-{axis}.rfmap", payload)

    with authenticated_client(app) as client:
        response = client.post("/api/datasets/open", json={"path": str(source)})

    assert response.status_code == 422
    assert "scalar only" in response.json()["detail"]


def test_source_change_requires_reopen(app, settings: Settings) -> None:
    source = write_json(settings.rf_root / "rf.json")
    with authenticated_client(app) as client:
        opened = _open(client, source)
        source.write_text(source.read_text(encoding="utf-8") + " ", encoding="utf-8")
        changed = client.get(f"/api/datasets/{opened['id']}/meta")
    assert changed.status_code == 409
    assert "reopen" in changed.json()["detail"].casefold()


def _csv_export_payload(**updates) -> dict[str, object]:
    payload: dict[str, object] = {
        "clusterId": 22,
        "valueMode": "Spike count",
        "rfStartMs": -100,
        "rfEndMs": 100,
        "timeResolutionMs": 200,
        "xBins": 1,
        "yBins": 1,
        "smoothRadius": 0,
        "flipY": False,
        "palette": "Gray",
    }
    payload.update(updates)
    return payload


def test_displayed_csv_is_written_on_linux_with_exact_tk_schema(
    app, settings: Settings
) -> None:
    source = write_json(settings.rf_root / "rf.json")
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        endpoint = f"/api/datasets/{metadata['id']}/exports/displayed-csv"
        exported = client.post(endpoint, json=_csv_export_payload())
        assert exported.status_code == 200, exported.text

        body = exported.json()
        target = Path(body["path"])
        assert target == settings.output_root / (
            "unit_001_cluster_22_spike_count_displayed.csv"
        )
        assert body == {
            "path": str(target),
            "name": target.name,
            "rows": 1,
            "bytes": target.stat().st_size,
            "overwritten": False,
        }
        with target.open("r", encoding="utf-8", newline="") as handle:
            raw_rows = list(csv.reader(handle))
        assert raw_rows[0] == list(CSV_HEADERS)
        assert len(raw_rows[1]) == len(CSV_HEADERS)
        row = dict(zip(raw_rows[0], raw_rows[1], strict=True))
        assert row == {
            "unit_index": "1",
            "cluster_id": "22",
            "y_index_0based": "0",
            "y_index_matlab": "1",
            "y_position": "0.0",
            "x_index_0based": "0",
            "x_index_matlab": "1",
            "x_position": "0.0",
            "value": "34.0",
            "value_mode": "Spike count",
            "value_unit": "spikes",
            "occupancy_time_sec_min": "0.2",
            "occupancy_time_sec_max": "0.5",
            "mode": "Spike count: -100 to 100 ms",
            "display_y_index_0based": "0",
            "source_y_start_0based": "0",
            "source_y_end_0based": "1",
            "source_y_start_matlab": "1",
            "source_y_end_matlab": "2",
            "y_position_start": "-5.0",
            "y_position_end": "5.0",
            "display_x_index_0based": "0",
            "source_x_start_0based": "0",
            "source_x_end_0based": "1",
            "source_x_start_matlab": "1",
            "source_x_end_matlab": "2",
            "x_position_start": "-10.0",
            "x_position_end": "10.0",
            "export_space": "displayed",
            "time_resolution_ms": "200",
            "rf_range_start_group_0based": "0",
            "rf_range_end_group_0based": "0",
            "rf_range_start_ms": "-100.0",
            "rf_range_end_ms": "100.0",
            "display_x_bins": "1",
            "display_y_bins": "1",
            "smooth_radius": "0",
            "flip_y": "False",
            "palette": "Gray",
            "source_json": str(source.resolve()),
        }

        conflict = client.post(endpoint, json=_csv_export_payload())
        assert conflict.status_code == 409
        replaced = client.post(endpoint, json=_csv_export_payload(overwrite=True))
        assert replaced.status_code == 200
        assert replaced.json()["overwritten"] is True
        assert not list(settings.output_root.glob(".*.tmp"))


def test_csv_export_normalization_and_output_path_confinement(
    app, settings: Settings, tmp_path: Path
) -> None:
    source = write_json(settings.rf_root / "rf.json")
    outside = tmp_path / "outside"
    outside.mkdir()
    (settings.output_root / "escape-link").symlink_to(outside, target_is_directory=True)
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        endpoint = f"/api/datasets/{metadata['id']}/exports/displayed-csv"
        rate = client.post(
            endpoint,
            json=_csv_export_payload(
                valueMode="Mean firing rate (Hz)",
                outputPath="session/rate-export",
            ),
        )
        assert rate.status_code == 200, rate.text
        target = settings.output_root / "session" / "rate-export.csv"
        assert Path(rate.json()["path"]) == target
        with target.open("r", encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle))
        assert row["value_mode"] == "Mean firing rate (Hz)"
        assert row["value_unit"] == "Hz"
        assert float(row["value"]) == pytest.approx(136.0 / 1.4)

        for output_path in (
            "../outside.csv",
            str(tmp_path / "absolute.csv"),
            "escape-link/outside.csv",
        ):
            refused = client.post(
                endpoint, json=_csv_export_payload(outputPath=output_path)
            )
            assert refused.status_code == 400, refused.text
        assert not (outside / "outside.csv").exists()


def test_csv_export_rejects_removed_per_presentation_mode(
    app, settings: Settings
) -> None:
    source = write_json(settings.rf_root / "rf.json")
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        response = client.post(
            f"/api/datasets/{metadata['id']}/exports/displayed-csv",
            json=_csv_export_payload(valueMode="Spikes / presentation"),
        )
    assert response.status_code == 422


def test_csv_export_matches_tk_flip_and_weighted_smoothing(
    app, settings: Settings
) -> None:
    source = write_json(settings.rf_root / "rf.json")
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        endpoint = f"/api/datasets/{metadata['id']}/exports/displayed-csv"
        exported = client.post(
            endpoint,
            json=_csv_export_payload(
                xBins=2,
                yBins=2,
                smoothRadius=1,
                flipY=True,
                outputPath="smoothed.csv",
            ),
        )
        assert exported.status_code == 200, exported.text

    with (settings.output_root / "smoothed.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert [float(row["value"]) for row in rows] == [35.0, 37.0, 31.0, 33.0]
    assert [int(row["display_y_index_0based"]) for row in rows] == [0, 0, 1, 1]
    assert [int(row["source_y_start_0based"]) for row in rows] == [1, 1, 0, 0]
    assert [int(row["source_x_start_0based"]) for row in rows] == [0, 1, 0, 1]
    assert all(row["flip_y"] == "True" for row in rows)
    assert all(row["smooth_radius"] == "1" for row in rows)


def test_open_rejects_legacy_occupancy_free_schema(
    app, settings: Settings
) -> None:
    payload = sample_payload()
    for field in occupancy_contract([], []):
        payload.pop(field)
    payload["stimulusPresentationCounts"] = [[2, 3], [4, 5]]
    source = write_json(settings.rf_root / "legacy-counts.json", payload)
    with authenticated_client(app) as client:
        unavailable = client.post("/api/datasets/open", json={"path": str(source)})
    assert unavailable.status_code == 422
    assert "occupancyTimeSec" in unavailable.json()["detail"]


def test_hd_image_save_writes_only_under_linux_output_root(
    app, settings: Settings
) -> None:
    source = _create_remote_fixture(settings.rf_root)
    data_root = next(parent for parent in source.parents if parent.name == "data")
    original = data_root / "tc_curve" / "ProbeA" / "HD tuning curve - cluster 11.png"
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        endpoint = f"/api/datasets/{metadata['id']}/hd/11/save-image"
        saved = client.post(endpoint, json={"outputPath": "images/cluster-11"})
        assert saved.status_code == 200, saved.text
        target = settings.output_root / "images" / "cluster-11.png"
        assert saved.json() == {
            "path": str(target),
            "name": "cluster-11.png",
            "bytes": len(b"\x89PNG\r\n\x1a\nfixture"),
            "overwritten": False,
        }
        assert target.read_bytes() == original.read_bytes()
        assert (
            client.post(endpoint, json={"outputPath": "images/cluster-11"}).status_code
            == 409
        )
        overwritten = client.post(
            endpoint,
            json={"outputPath": "images/cluster-11", "overwrite": True},
        )
        assert overwritten.status_code == 200
        assert overwritten.json()["overwritten"] is True


def _figure_pages(*types: str) -> list[dict[str, object]]:
    return [
        {
            "title": "Overview",
            "plots": [{"type": type_id, "settings": {}} for type_id in types],
        }
    ]


def _figure_export_payload(**updates) -> dict[str, object]:
    payload: dict[str, object] = {
        "specVersion": 1,
        "clusterIds": [22, 11],
        "order": "page-major",
        "format": "png",
        "pages": [
            {
                "title": "RF and HD",
                "plots": [
                    {
                        "type": "rf.cartesian",
                        "settings": {
                            "rfStartMs": -100,
                            "rfEndMs": 100,
                            "palette": "Gray",
                        },
                    },
                    {"type": "hd.line", "settings": {}},
                ],
            },
            {
                "title": "Delay",
                "plots": [
                    {
                        "type": "delay.polar",
                        "settings": {"timeResolutionMs": 100},
                    }
                ],
            },
        ],
        "destination": {
            "directory": "session",
            "baseName": "selected_units",
            "overwrite": False,
        },
    }
    payload.update(updates)
    return payload


def test_figure_export_registry_covers_every_view_and_all_types_render(
    app, settings: Settings
) -> None:
    expected = {
        "rf.cartesian",
        "rf.polar",
        "delay.cartesian",
        "delay.polar",
        "rgb.cartesian",
        "rgb.polar",
        "timeline.current",
        "hd.line",
        "hd.polar",
        "probe",
        "waveform.local_average",
    }
    source = _create_remote_fixture(settings.rf_root)
    with authenticated_client(app) as client:
        registry_response = client.get("/api/figure-exports/spec")
        assert registry_response.status_code == 200
        registry = registry_response.json()
        assert registry["specVersion"] == 1
        assert {entry["id"] for entry in registry["figureTypes"]} == expected
        rf_definition = next(
            entry for entry in registry["figureTypes"] if entry["id"] == "rf.cartesian"
        )
        assert rf_definition["settings"]["valueMode"] == {
            "type": "string",
            "default": "Mean firing rate (Hz)",
            "description": "Displayed response normalization.",
            "choices": ["Mean firing rate (Hz)", "Spike count"],
        }
        assert registry["formats"] == ["pdf", "png"]
        assert registry["pageOrders"] == ["unit-major", "page-major"]

        metadata = _open(client, source)
        response = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports/preview",
            json={
                "specVersion": 1,
                "clusterId": 11,
                "pageIndex": 0,
                "pages": _figure_pages(*sorted(expected)),
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert "x-rf-placeholder-count" not in response.headers
        assert response.headers["x-rf-render-sha256"] == hashlib.sha256(
            response.content
        ).hexdigest()


def test_figure_response_preparation_matches_live_pooled_observations() -> None:
    counts = np.asarray(
        [
            [[4, 6], [0, 0], [499, 500], [3, 5]],
            [[2, 4], [1, 3], [5, 7], [444, 444]],
        ],
        dtype=np.float64,
    )
    metadata = {
        "shape": [1, 2, 4, 2],
        "timeBinEdges": [0.0, 0.1, 0.3],
        "occupancyTimeSec": [[1, 9, 0, 2], [3, 1, 4, 0]],
    }
    base_settings = {
        "rfStartMs": 0.0,
        "rfEndMs": 300.0,
        "xBins": 2,
        "yBins": 1,
        "smoothRadius": 0,
        "flipY": False,
    }
    expected_by_mode = {
        "Spike count": [[5.0, 10.0]],
        "Mean firing rate (Hz)": [[10.0 / 7.0, 10.0 / 3.0]],
    }
    for value_mode, expected in expected_by_mode.items():
        matrix, x_groups, y_groups, bounds = figure_exports_module._prepared_response(
            counts,
            metadata,
            {**base_settings, "valueMode": value_mode},
        )
        np.testing.assert_allclose(matrix, expected)
        assert x_groups == [(0, 1), (2, 3)]
        assert y_groups == [(0, 1)]
        assert bounds == (0.0, 300.0)

    smoothed, _x, _y, _bounds = figure_exports_module._prepared_response(
        np.asarray([[[100.0], [9.0]]]),
        {
            "shape": [1, 1, 2, 1],
            "timeBinEdges": [0.0, 0.1],
            "occupancyTimeSec": [[100, 1]],
        },
        {
            "rfStartMs": 0.0,
            "rfEndMs": 100.0,
            "valueMode": "Mean firing rate (Hz)",
            "xBins": 2,
            "yBins": 1,
            "smoothRadius": 1,
            "flipY": False,
        },
    )
    np.testing.assert_allclose(
        smoothed,
        [[1.0398009950248756, 1.1568627450980392]],
    )


def _temporal_parity_fixture() -> tuple[np.ndarray, dict[str, object]]:
    counts = np.full((4, 4, 3), 100.0, dtype=np.float64)
    occupancy = np.zeros((4, 4), dtype=np.float64)
    for y, x, exposure, histogram in (
        (0, 0, 9, [9, 0, 0]),
        (0, 2, 1, [0, 5, 0]),
        (1, 3, 5, [0, 7, 0]),
        (2, 2, 2, [1, 2, 3]),
        (3, 3, 7, [5, 4, 3]),
    ):
        occupancy[y, x] = exposure
        counts[y, x, :] = histogram
    metadata: dict[str, object] = {
        "shape": [1, 4, 4, 3],
        "timeBinEdges": [0.0, 0.01, 0.02, 0.03],
        "occupancyTimeSec": occupancy.tolist(),
        "xPositions": [0.0, 1.0, 2.0, 3.0],
        "yPositions": [0.0, 1.0, 2.0, 3.0],
    }
    return counts, metadata


def test_figure_temporal_preparation_matches_live_slice_first_smoothing() -> None:
    counts, metadata = _temporal_parity_fixture()
    delays, entropy, response, x_groups, y_groups = (
        figure_exports_module._prepared_temporal(
            counts,
            metadata,
            {
                "timeResolutionMs": 10.0,
                "valueMode": "Mean firing rate (Hz)",
                "xBins": 2,
                "yBins": 2,
                "smoothRadius": 1,
                "flipY": False,
                "responseFloor": 0.0,
            },
        )
    )

    assert x_groups == [(0, 1), (2, 3)]
    assert y_groups == [(0, 1), (2, 3)]
    np.testing.assert_allclose(delays, [[5.0, 15.0], [5.0, 15.0]])

    smoothed_histograms = (
        ([13.0, 5.0, 1.0], [8.0, 10.0, 2.0]),
        ([8.0, 4.0, 2.0], [7.0, 8.0, 4.0]),
    )

    def normalized_entropy(histogram: tuple[float, ...] | list[float]) -> float:
        values = np.asarray(histogram, dtype=np.float64)
        probabilities = values[values > 0] / values.sum()
        return -float(np.sum(probabilities * np.log(probabilities))) / math.log(3)

    expected_entropy = [
        [normalized_entropy(histogram) for histogram in row]
        for row in smoothed_histograms
    ]
    np.testing.assert_allclose(entropy, expected_entropy)
    np.testing.assert_allclose(
        response,
        [[26.0 / 19.0, 17.0 / 10.0], [np.nan, 35.0 / 19.0]],
        equal_nan=True,
    )


@pytest.mark.parametrize(
    ("value_mode", "expected"),
    (
        ("Spike count", [3.0, 4.0, 0.0]),
        ("Mean firing rate (Hz)", [0.6, 0.8, 0.0]),
    ),
)
def test_timeline_selected_curve_matches_live_group_response_modes(
    value_mode: str,
    expected: list[float],
) -> None:
    counts, parity_metadata = _temporal_parity_fixture()
    settings_snapshot = figure_exports_module._normalized_settings(
        "timeline.current",
        {
            "timeResolutionMs": 10.0,
            "valueMode": value_mode,
            "xBins": 2,
            "yBins": 2,
            "smoothRadius": 1,
            "spatialProjection": {
                "yStart": 0,
                "yEnd": 1,
                "xStart": 0,
                "xEnd": 3,
            },
        },
        parity_metadata,
    )
    record = SimpleNamespace(cache=SimpleNamespace(metadata=parity_metadata))
    renderer = figure_exports_module.FigurePageRenderer(
        record,
        tuning=None,
        probe=None,
    )

    payload, _title = renderer._timeline_data(counts, settings_snapshot)

    np.testing.assert_allclose(payload["selected"], expected)


def test_probe_points_emit_channels_and_only_the_current_export_unit(
    app, settings: Settings
) -> None:
    source = write_json(settings.rf_root / "probe-order.json")
    store = app.state.services.datasets
    with authenticated_client(app) as client:
        opened = _open(client, source)
    record = store.get(opened["id"])
    renderer = figure_exports_module.FigurePageRenderer(
        record,
        tuning=None,
        probe={
            "probe": "ProbeA",
            "channels": [
                {"x": 10.0, "y": 20.0},
                {"x": 10.0, "y": 20.0},
            ],
            "units": [
                {"unitId": 11, "x": 10.0, "y": 20.0},
                {"unitId": 22, "x": 10.0, "y": 20.0},
            ],
        },
    )
    expected_channels = [
        {"x": 10.0, "y": 20.0, "label": "", "color": "#94a3b8"},
        {"x": 10.0, "y": 20.0, "label": "", "color": "#94a3b8"},
    ]
    for cluster_id in (11, 22):
        _unit_index, counts = store.unit_array(record, cluster_id)
        placeholders: list[str] = []
        plot = renderer._shared_spec(
            cluster_id,
            counts,
            figure_exports_module.FigurePlot("probe", {}),
            placeholders,
        )

        assert placeholders == []
        assert plot.data["points"] == [
            *expected_channels,
            {
                "x": 10.0,
                "y": 20.0,
                "label": str(cluster_id),
                "color": "#dc2626",
            },
        ]


def test_probe_export_keeps_channel_background_and_labels_nan_position(
    app, settings: Settings
) -> None:
    source = write_json(settings.rf_root / "probe-nan.json")
    store = app.state.services.datasets
    with authenticated_client(app) as client:
        opened = _open(client, source)
    record = store.get(opened["id"])
    renderer = figure_exports_module.FigurePageRenderer(
        record,
        tuning=None,
        probe={
            "probe": "ProbeA",
            "channels": [{"x": 10.0, "y": 20.0}],
            "units": [{"unitId": 11, "x": None, "y": None}],
        },
    )
    _unit_index, counts = store.unit_array(record, 11)
    placeholders: list[str] = []

    plot = renderer._shared_spec(
        11,
        counts,
        figure_exports_module.FigurePlot("probe", {}),
        placeholders,
    )

    assert placeholders == []
    assert plot.data == {
        "points": [
            {"x": 10.0, "y": 20.0, "label": "", "color": "#94a3b8"}
        ],
        "missingPosition": True,
    }


def test_timeline_figure_payload_preserves_selection_and_active_group(
    app, settings: Settings
) -> None:
    source = write_json(settings.rf_root / "rf.json")
    store = app.state.services.datasets
    with authenticated_client(app) as client:
        metadata = _open(client, source)
    record = store.get(metadata["id"])
    _unit_index, counts = store.unit_array(record, 11)
    settings_snapshot = figure_exports_module._normalized_settings(
        "timeline.current",
        {
            "timelineStartMs": 0,
            "timelineEndMs": 100,
            "activeTimeCenterMs": 150,
            "timeResolutionMs": 100,
        },
        record.cache.metadata,
    )
    renderer = figure_exports_module.FigurePageRenderer(
        record,
        tuning=None,
        probe=None,
    )

    payload, title = renderer._timeline_data(counts, settings_snapshot)

    assert payload["selection_start_index"] == 1
    assert payload["selection_end_index"] == 1
    assert payload["active_index"] == 2
    assert payload["times"] == [-50.0, 50.0, 150.0]
    assert title.startswith("Timeline 0–100 ms")


def test_timeline_group_range_keeps_noisy_shared_edges_half_open() -> None:
    metadata = {
        "timeBinEdges": np.linspace(-0.1, 0.4, 501).tolist(),
        "shape": [1, 1, 1, 500],
    }
    groups = figure_exports_module._time_groups(metadata, 10.0)

    assert len(groups) == 50
    assert groups[29] == (290, 299)
    assert metadata["timeBinEdges"][300] == pytest.approx(0.2)
    assert figure_exports_module._time_group_range_for_ms(
        metadata,
        groups,
        0.0,
        200.0,
    ) == (10, 29)


def test_timeline_current_preview_and_final_are_byte_identical(
    app, settings: Settings
) -> None:
    (settings.figure_export_root / "session").mkdir()
    source = write_json(settings.rf_root / "rf.json")
    pages = [
        {
            "title": "Frozen timeline",
            "plots": [
                {
                    "type": "timeline.current",
                    "settings": {
                        "timelineStartMs": 0,
                        "timelineEndMs": 100,
                        "activeTimeCenterMs": 150,
                        "timeResolutionMs": 100,
                        "spatialProjection": {
                            "yStart": 0,
                            "yEnd": 0,
                            "xStart": 1,
                            "xEnd": 1,
                        },
                    },
                }
            ],
        }
    ]
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        preview = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports/preview",
            json={
                "specVersion": 1,
                "clusterId": 11,
                "pageIndex": 0,
                "pages": pages,
            },
        )
        assert preview.status_code == 200, preview.text
        exported = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports",
            json={
                "specVersion": 1,
                "clusterIds": [11],
                "order": "unit-major",
                "format": "png",
                "pages": pages,
                "destination": {
                    "directory": "session",
                    "baseName": "frozen-timeline",
                    "overwrite": False,
                },
            },
        )
        assert exported.status_code == 200, exported.text

    body = exported.json()
    target = settings.figure_export_root / "session" / "frozen-timeline"
    page_path = target / body["manifest"]["pages"][0]["file"]
    assert page_path.read_bytes() == preview.content
    assert body["manifest"]["pages"][0]["sha256"] == preview.headers[
        "x-rf-render-sha256"
    ]


def test_figure_preview_and_final_use_explicit_current_companion_paths(
    app, settings: Settings
) -> None:
    (settings.figure_export_root / "session").mkdir()
    source = write_json(settings.rf_root / "rf.json")
    attached = settings.rf_root / "attached"
    tuning_path = write_json(
        attached / "tuning_curves.tc", tuning_payload((11, 22))
    )
    positions_path = attached / "positions.probe"
    positions_path.write_text(
        "unit_index,unit_id,x_um,y_um\n0,11,10,20\n1,22,30,40\n",
        encoding="utf-8",
    )
    pages = _figure_pages("hd.line", "probe")
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        preview_endpoint = (
            f"/api/datasets/{metadata['id']}/figure-exports/preview"
        )
        unavailable = client.post(
            preview_endpoint,
            json={
                "specVersion": 1,
                "clusterId": 11,
                "pageIndex": 0,
                "pages": pages,
            },
        )
        assert unavailable.status_code == 200, unavailable.text
        assert unavailable.headers["x-rf-placeholder-count"] == "2"

        current = client.post(
            preview_endpoint,
            json={
                "specVersion": 1,
                "clusterId": 11,
                "pageIndex": 0,
                "pages": pages,
                "hdPath": str(tuning_path),
                "probePositionsPath": str(positions_path),
            },
        )
        assert current.status_code == 200, current.text
        assert "x-rf-placeholder-count" not in current.headers

        exported = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports",
            json={
                "specVersion": 1,
                "clusterIds": [11],
                "order": "unit-major",
                "format": "png",
                "pages": pages,
                "hdPath": str(tuning_path),
                "probePositionsPath": str(positions_path),
                "destination": {
                    "directory": "session",
                    "baseName": "attached-companions",
                    "overwrite": False,
                },
            },
        )
        assert exported.status_code == 200, exported.text
        assert exported.json()["manifest"]["pages"][0]["placeholders"] == []

        escaped = client.post(
            preview_endpoint,
            json={
                "specVersion": 1,
                "clusterId": 11,
                "pageIndex": 0,
                "pages": pages,
                "hdPath": "../outside/tuning_curves.json",
            },
        )
        assert escaped.status_code == 400


def test_multi_unit_png_export_order_preview_parity_and_missing_hd_placeholder(
    app, settings: Settings
) -> None:
    (settings.figure_export_root / "session").mkdir()
    source = write_json(settings.rf_root / "rf.json")
    source_before = source.read_bytes()
    payload = _figure_export_payload()
    preview_payload = {
        "specVersion": 1,
        "clusterId": 22,
        "pageIndex": 0,
        "pages": payload["pages"],
    }
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        endpoint = f"/api/datasets/{metadata['id']}/figure-exports"
        preview = client.post(f"{endpoint}/preview", json=preview_payload)
        assert preview.status_code == 200, preview.text
        assert preview.headers["x-rf-placeholder-count"] == "1"

        exported = client.post(endpoint, json=payload)
        assert exported.status_code == 200, exported.text
        body = exported.json()
        assert body["format"] == "png"
        assert body["pageCount"] == 4
        target = settings.figure_export_root / "session" / "selected_units"
        assert Path(body["path"]) == target
        manifest = body["manifest"]
        assert [
            (page["clusterId"], page["pageIndex"])
            for page in manifest["pages"]
        ] == [(22, 0), (11, 0), (22, 1), (11, 1)]
        first_page = target / manifest["pages"][0]["file"]
        assert first_page.read_bytes() == preview.content
        assert manifest["pages"][0]["sha256"] == preview.headers[
            "x-rf-render-sha256"
        ]
        assert "HD tuning" in manifest["pages"][0]["placeholders"][0]
        assert json.loads((target / "manifest.json").read_text(encoding="utf-8")) == manifest
        assert all(
            (target / page["file"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
            for page in manifest["pages"]
        )

        assert client.post(endpoint, json=payload).status_code == 409
        payload["destination"]["overwrite"] = True
        replaced = client.post(endpoint, json=payload)
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["overwritten"] is True
    assert source.read_bytes() == source_before
    assert not list((settings.figure_export_root / "session").glob(".*.tmp"))
    assert not list((settings.figure_export_root / "session").glob(".*.backup"))


def test_web_png_overwrite_refuses_raw_session_directory(
    app, settings: Settings
) -> None:
    parent = settings.figure_export_root / "session"
    parent.mkdir()
    target = parent / "selected_units"
    target.mkdir()
    raw_source = target / "spike_clusters.npy"
    raw_source.write_bytes(b"source-of-truth")
    source = write_json(settings.rf_root / "rf.json")
    payload = _figure_export_payload(
        clusterIds=[11],
        pages=_figure_pages("rf.cartesian"),
    )
    payload["destination"]["overwrite"] = True

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        response = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports",
            json=payload,
        )

    assert response.status_code == 400, response.text
    assert raw_source.read_bytes() == b"source-of-truth"
    assert set(target.iterdir()) == {raw_source}


def test_web_png_verified_output_allows_different_recipe_overwrite(
    app, settings: Settings
) -> None:
    (settings.figure_export_root / "session").mkdir()
    source = write_json(settings.rf_root / "rf.json")
    first_payload = _figure_export_payload()
    changed_payload = _figure_export_payload(
        clusterIds=[11],
        order="unit-major",
        pages=_figure_pages("timeline.current"),
    )
    changed_payload["destination"]["overwrite"] = True

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        endpoint = f"/api/datasets/{metadata['id']}/figure-exports"
        first = client.post(endpoint, json=first_payload)
        assert first.status_code == 200, first.text
        replaced = client.post(endpoint, json=changed_payload)

    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    assert body["overwritten"] is True
    assert body["pageCount"] == 1
    assert body["manifest"]["producer"] == "rfmapping.web.figure-export"
    assert body["manifest"]["spec"]["pages"][0]["plots"][0]["type"] == "timeline.current"
    target = settings.figure_export_root / "session" / "selected_units"
    assert {path.name for path in target.iterdir()} == {
        "manifest.json",
        body["manifest"]["pages"][0]["file"],
    }


def test_web_png_cifs_fallback_keeps_overwrite_contract_and_cleans_transaction(
    app,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = settings.figure_export_root / "session"
    parent.mkdir()
    source = write_json(settings.rf_root / "rf.json")
    payload = _figure_export_payload(
        clusterIds=[11],
        pages=_figure_pages("rf.cartesian"),
    )
    original = figure_exports_module._shared_atomic_directory_rename

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        endpoint = f"/api/datasets/{metadata['id']}/figure-exports"
        first = client.post(endpoint, json=payload)
        assert first.status_code == 200, first.text

        def reject_exchange(
            staged: Path,
            target: Path,
            *,
            exchange: bool,
            parent_fd: int | None = None,
        ) -> None:
            if exchange:
                raise OSError(errno.EINVAL, "CIFS has no RENAME_EXCHANGE")
            original(staged, target, exchange=False, parent_fd=parent_fd)

        monkeypatch.setattr(
            figure_exports_module,
            "_shared_atomic_directory_rename",
            reject_exchange,
        )
        payload["destination"]["overwrite"] = True
        replaced = client.post(endpoint, json=payload)

    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["overwritten"] is True
    assert not (parent / ".selected_units.figure-export-journal.json").exists()
    assert not list(parent.glob(".selected_units.backup-*"))
    assert not list(parent.glob(".selected_units.tmp-*"))


@pytest.mark.parametrize("replacement_kind", ["symlink", "directory"])
def test_web_png_target_replacement_during_render_fails_closed(
    app,
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    parent = settings.figure_export_root / "session"
    parent.mkdir()
    target = parent / "selected_units"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "raw.bin"
    sentinel.write_bytes(b"do-not-touch")
    source = write_json(settings.rf_root / "rf.json")
    payload = _figure_export_payload(
        clusterIds=[11],
        pages=_figure_pages("rf.cartesian"),
    )
    original = figure_exports_module.FigurePageRenderer.render_png

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        endpoint = f"/api/datasets/{metadata['id']}/figure-exports"
        first = client.post(endpoint, json=payload)
        assert first.status_code == 200, first.text
        replaced_target = False

        def replace_target(renderer, *args, **kwargs):
            nonlocal replaced_target
            if not replaced_target:
                replaced_target = True
                shutil.rmtree(target)
                if replacement_kind == "symlink":
                    target.symlink_to(outside, target_is_directory=True)
                else:
                    target.mkdir()
                    (target / "recording.bin").write_bytes(b"recording")
            return original(renderer, *args, **kwargs)

        monkeypatch.setattr(
            figure_exports_module.FigurePageRenderer,
            "render_png",
            replace_target,
        )
        payload["destination"]["overwrite"] = True
        response = client.post(endpoint, json=payload)

    assert response.status_code == 400, response.text
    assert sentinel.read_bytes() == b"do-not-touch"
    if replacement_kind == "directory":
        assert (target / "recording.bin").read_bytes() == b"recording"


def test_web_png_parent_replacement_during_render_fails_closed(
    app,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = settings.figure_export_root / "session"
    moved_parent = settings.figure_export_root / "session-original"
    parent.mkdir()
    source = write_json(settings.rf_root / "rf.json")
    payload = _figure_export_payload(
        clusterIds=[11],
        pages=_figure_pages("rf.cartesian"),
    )
    original = figure_exports_module.FigurePageRenderer.render_png
    replaced_parent = False

    def replace_parent(renderer, *args, **kwargs):
        nonlocal replaced_parent
        if not replaced_parent:
            replaced_parent = True
            parent.rename(moved_parent)
            parent.mkdir()
        return original(renderer, *args, **kwargs)

    monkeypatch.setattr(
        figure_exports_module.FigurePageRenderer,
        "render_png",
        replace_parent,
    )
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        response = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports",
            json=payload,
        )

    assert response.status_code == 400, response.text
    assert not (parent / "selected_units").exists()
    assert not list(moved_parent.glob(".selected_units.tmp-*"))


def test_multi_page_pdf_has_one_page_per_unit_template_and_unit_major_order(
    app, settings: Settings
) -> None:
    (settings.figure_export_root / "session").mkdir()
    source = write_json(settings.rf_root / "rf.json")
    source_before = source.read_bytes()
    payload = _figure_export_payload(format="pdf", order="unit-major")
    payload["destination"]["baseName"] = "report.pdf"
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        response = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports", json=payload
        )
        assert response.status_code == 200, response.text
        conflict = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports", json=payload
        )
        assert conflict.status_code == 409, conflict.text
    body = response.json()
    assert body["pageCount"] == 4
    assert [
        (page["clusterId"], page["pageIndex"])
        for page in body["manifest"]["pages"]
    ] == [(22, 0), (22, 1), (11, 0), (11, 1)]
    target = settings.figure_export_root / "session" / "report.pdf"
    assert Path(body["path"]) == target
    pdf = target.read_bytes()
    assert pdf.startswith(b"%PDF-")
    page_counts = re.findall(rb"/Type\s*/Pages\s*/Count\s+(\d+)", pdf)
    assert page_counts and int(page_counts[-1]) == 4
    assert b"/Title (\xfe\xff" + "report".encode("utf-16-be") in pdf
    reader = PdfReader(target)
    assert len(reader.pages) == 4
    assert all(
        float(page.mediabox.width) > 0 and float(page.mediabox.height) > 0
        for page in reader.pages
    )
    assert source.read_bytes() == source_before


def test_six_page_web_pdf_uses_shared_streaming_writer_without_append(
    app,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pillow 10.2 corrupts its trailer on the fifth incremental append."""

    (settings.figure_export_root / "session").mkdir()
    source = write_json(settings.rf_root / "rf.json")
    payload = _figure_export_payload(format="pdf", order="unit-major")
    payload["pages"] = [
        *payload["pages"],
        {
            "title": "Probe",
            "plots": [{"type": "probe", "settings": {}}],
        },
    ]
    payload["destination"]["baseName"] = "six-pages.pdf"

    original_save = Image.Image.save
    incremental_attempts = 0
    jpeg_pages = 0

    def reject_incremental_pdf(self, fp, format=None, **params):
        nonlocal incremental_attempts, jpeg_pages
        normalized = str(format).upper()
        if normalized == "PDF" and params.get("append"):
            incremental_attempts += 1
            if incremental_attempts >= 4:
                raise RuntimeError("trailer loop found")
        if normalized == "JPEG":
            jpeg_pages += 1
        return original_save(self, fp, format=format, **params)

    monkeypatch.setattr(Image.Image, "save", reject_incremental_pdf)
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        response = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports",
            json=payload,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pageCount"] == 6
    assert len(body["manifest"]["pages"]) == 6
    assert incremental_attempts == 0
    assert jpeg_pages == 6
    assert all(page["file"] == "six-pages.pdf" for page in body["manifest"]["pages"])
    assert any(page["placeholders"] for page in body["manifest"]["pages"])
    target = settings.figure_export_root / "session" / "six-pages.pdf"
    assert len(PdfReader(target, strict=True).pages) == 6
    assert not list(target.parent.glob(".six-pages.pdf.tmp-*"))


def test_web_pdf_stable_overwrite_accepts_cifs_path_scoped_hardlink_inode(
    app,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CIFS may report a different inode for a second path to one hard link."""

    parent = settings.figure_export_root / "session"
    parent.mkdir()
    source = write_json(settings.rf_root / "rf.json")
    payload = _figure_export_payload(
        format="pdf",
        clusterIds=[11],
        pages=_figure_pages("rf.cartesian"),
    )
    payload["destination"]["baseName"] = "stable.pdf"

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        endpoint = f"/api/datasets/{metadata['id']}/figure-exports"
        first = client.post(endpoint, json=payload)
        assert first.status_code == 200, first.text

        original_lstat = shared_figure_export_module._entry_lstat
        original_open = shared_figure_export_module.os.open
        original_fstat = shared_figure_export_module.os.fstat
        pending_fd_kind: dict[int, str] = {}
        observed_fd_link_counts: list[tuple[str, int]] = []

        def cifs_path_scoped_inode(parent_directory, name: str):
            result = original_lstat(parent_directory, name)
            if result is not None and (
                name == "stable.pdf"
                or name.startswith(".stable.pdf.backup-")
            ):
                values = list(result)
                if name.startswith(".stable.pdf.backup-"):
                    values[1] += 1
                values[3] = 1
                return os.stat_result(values)
            return result

        def cifs_open(path, flags, *args, **kwargs):
            descriptor = original_open(path, flags, *args, **kwargs)
            name = Path(os.fspath(path)).name
            if name == "stable.pdf":
                pending_fd_kind[descriptor] = "destination"
            elif name.startswith(".stable.pdf.backup-"):
                pending_fd_kind[descriptor] = "backup"
            return descriptor

        def cifs_fstat(descriptor: int):
            result = original_fstat(descriptor)
            kind = pending_fd_kind.pop(descriptor, None)
            if kind is None:
                return result
            values = list(result)
            if kind == "backup":
                values[1] += 1
            values[3] = 1
            synthetic = os.stat_result(values)
            observed_fd_link_counts.append((kind, synthetic.st_nlink))
            return synthetic

        monkeypatch.setattr(
            shared_figure_export_module,
            "_entry_lstat",
            cifs_path_scoped_inode,
        )
        monkeypatch.setattr(shared_figure_export_module.os, "open", cifs_open)
        monkeypatch.setattr(shared_figure_export_module.os, "fstat", cifs_fstat)
        payload["destination"]["overwrite"] = True
        replaced = client.post(endpoint, json=payload)

    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["overwritten"] is True
    target = parent / "stable.pdf"
    assert len(PdfReader(target).pages) == 1
    assert ("destination", 1) in observed_fd_link_counts
    assert ("backup", 1) in observed_fd_link_counts
    assert not list(parent.glob(".stable.pdf.backup-*"))
    assert not list(parent.glob(".stable.pdf.tmp-*"))


def test_web_pdf_overwrite_rejects_symlink_target(
    app, settings: Settings, tmp_path: Path
) -> None:
    parent = settings.figure_export_root / "session"
    parent.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"source-of-truth")
    (parent / "report.pdf").symlink_to(outside)
    source = write_json(settings.rf_root / "rf.json")
    payload = _figure_export_payload(
        format="pdf",
        clusterIds=[11],
        pages=_figure_pages("rf.cartesian"),
    )
    payload["destination"]["baseName"] = "report.pdf"
    payload["destination"]["overwrite"] = True

    with authenticated_client(app) as client:
        metadata = _open(client, source)
        response = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports",
            json=payload,
        )

    assert response.status_code == 400, response.text
    assert outside.read_bytes() == b"source-of-truth"


@pytest.mark.parametrize(
    ("directory", "base_name"),
    [
        ("../outside", "safe"),
        ("/tmp", "safe"),
        (".hidden", "safe"),
        ("session/", "safe"),
        ("session/./nested", "safe"),
        ("missing", "safe"),
        ("escape", "safe"),
        ("", ".hidden"),
        ("", "../outside"),
        ("", "folder/name"),
    ],
)
def test_figure_export_rejects_unsafe_destination_paths(
    app, settings: Settings, tmp_path: Path, directory: str, base_name: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    escape = settings.figure_export_root / "escape"
    if not escape.exists():
        escape.symlink_to(outside, target_is_directory=True)
    source = write_json(settings.rf_root / "rf.json")
    payload = _figure_export_payload(
        clusterIds=[11],
        pages=_figure_pages("rf.cartesian"),
        destination={
            "directory": directory,
            "baseName": base_name,
            "overwrite": False,
        },
    )
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        response = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports", json=payload
        )
    assert response.status_code == 400, response.text
    assert not any(outside.iterdir())


def test_figure_directory_browser_lists_only_real_directories(
    app, settings: Settings, tmp_path: Path
) -> None:
    (settings.figure_export_root / "A").mkdir()
    (settings.figure_export_root / "z").mkdir()
    (settings.figure_export_root / "file.txt").write_text("not a directory")
    (settings.figure_export_root / ".hidden").mkdir()
    outside = tmp_path / "outside-directory"
    outside.mkdir()
    (settings.figure_export_root / "link").symlink_to(outside, target_is_directory=True)
    with authenticated_client(app) as client:
        response = client.get("/api/figure-exports/directories")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["path"] == ""
        assert body["writable"] is True
        assert [(entry["name"], entry["path"]) for entry in body["entries"]] == [
            ("A", "A"),
            ("z", "z"),
        ]
        assert all(type(entry["writable"]) is bool for entry in body["entries"])
        assert client.get(
            "/api/figure-exports/directories", params={"path": "link"}
        ).status_code == 400


def test_figure_export_loads_only_one_unit_at_a_time_in_expanded_order(
    app, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    (settings.figure_export_root / "session").mkdir()
    source = write_json(settings.rf_root / "rf.json")
    store = app.state.services.datasets
    original = store.unit_array
    load_order: list[int] = []

    def tracked_unit_array(record, cluster_id):
        load_order.append(cluster_id)
        return original(record, cluster_id)

    monkeypatch.setattr(store, "unit_array", tracked_unit_array)
    payload = _figure_export_payload(
        pages=[
            {"title": "one", "plots": [{"type": "rf.cartesian", "settings": {}}]},
            {"title": "two", "plots": [{"type": "timeline.current", "settings": {}}]},
        ]
    )
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        response = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports", json=payload
        )
    assert response.status_code == 200, response.text
    assert load_order == [22, 11, 22, 11]


def test_source_change_during_export_aborts_before_atomic_publish(
    app, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    (settings.figure_export_root / "session").mkdir()
    source = write_json(settings.rf_root / "rf.json")
    store = app.state.services.datasets
    original = store.unit_array
    changed = False

    def mutate_after_read(record, cluster_id):
        nonlocal changed
        result = original(record, cluster_id)
        if not changed:
            source.write_text(source.read_text(encoding="utf-8") + " ", encoding="utf-8")
            changed = True
        return result

    monkeypatch.setattr(store, "unit_array", mutate_after_read)
    payload = _figure_export_payload(
        clusterIds=[11],
        pages=_figure_pages("rf.cartesian"),
    )
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        response = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports", json=payload
        )
    assert response.status_code == 409, response.text
    assert not (settings.figure_export_root / "session" / "selected_units").exists()
    assert not list((settings.figure_export_root / "session").glob(".*.tmp"))


@pytest.mark.parametrize(
    "update",
    [
        {"specVersion": 2},
        {"clusterIds": [11, 11]},
        {"clusterIds": [999]},
        {"clusterIds": ["11"]},
        {"pages": _figure_pages("unknown.view")},
        {
            "pages": [
                {
                    "title": "bad setting",
                    "plots": [
                        {"type": "rf.cartesian", "settings": {"notASetting": True}}
                    ],
                }
            ]
        },
    ],
)
def test_figure_export_spec_is_strict(
    app, settings: Settings, update: dict[str, object]
) -> None:
    source = write_json(settings.rf_root / "rf.json")
    payload = _figure_export_payload(
        clusterIds=[11],
        pages=_figure_pages("rf.cartesian"),
        destination={"directory": "", "baseName": "strict", "overwrite": False},
    )
    payload.update(update)
    with authenticated_client(app) as client:
        metadata = _open(client, source)
        response = client.post(
            f"/api/datasets/{metadata['id']}/figure-exports", json=payload
        )
    assert response.status_code == 422, response.text


def test_upload_api_and_bundle_open_contract_are_absent(
    app, settings: Settings
) -> None:
    source = write_json(settings.rf_root / "rf.json")
    paths = app.openapi()["paths"]
    assert all(not path.startswith("/api/uploads") for path in paths)
    assert all("json" not in path.casefold() or path == "/api/datasets/open" for path in paths)
    with authenticated_client(app) as client:
        bundled = client.post(
            "/api/datasets/open",
            json={"path": str(source), "bundleId": "removed"},
        )
    assert bundled.status_code == 422


def test_real_data_error_probe_reuses_authenticated_opener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = real_data_validation.ApiClient("http://127.0.0.1:3005/rfmapping", None)
    opened: list[str] = []

    class AuthenticatedOpener:
        def open(self, request, timeout):
            opened.append(request.full_url)
            raise real_data_validation.urllib.error.HTTPError(
                request.full_url,
                422,
                "Unprocessable Entity",
                hdrs=None,
                fp=io.BytesIO(b'{"detail":"Missing tuning-curve keys"}'),
            )

    client.opener = AuthenticatedOpener()
    monkeypatch.setattr(
        real_data_validation.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("global urlopen bypassed login cookies"),
    )

    payload = client.expect_error("api/datasets/example/hd", status=422)

    assert payload["detail"] == "Missing tuning-curve keys"
    assert opened == ["http://127.0.0.1:3005/rfmapping/api/datasets/example/hd"]


def test_lru_eviction_invalidates_live_dataset_record(settings: Settings) -> None:
    first = write_json(settings.rf_root / "first.json")
    second = write_json(settings.rf_root / "second.json")
    tiny_app = create_app(replace(settings, cache_max_bytes=256))
    with authenticated_client(tiny_app) as client:
        opened_first = client.post("/api/datasets/open", json={"path": str(first)})
        assert opened_first.status_code == 200, opened_first.text
        first_id = opened_first.json()["id"]
        first_cache = next(settings.cache_root.glob("*.f64"))

        opened_second = client.post("/api/datasets/open", json={"path": str(second)})
        assert opened_second.status_code == 200, opened_second.text
        assert not first_cache.exists()
        assert client.get(f"/api/datasets/{first_id}/meta").status_code == 404
        assert client.get(f"/api/datasets/{first_id}/units/11").status_code == 404
        assert (
            client.get(
                f"/api/datasets/{opened_second.json()['id']}/units/11"
            ).status_code
            == 200
        )


def test_zero_occupancy_with_nonzero_counts_is_rejected(
    app, settings: Settings
) -> None:
    payload = sample_payload()
    payload["occupancyTimeSec"] = [[0, 0.3], [0.4, 0.5]]
    source = write_json(settings.rf_root / "invalid-occupancy.json", payload)
    with authenticated_client(app) as client:
        response = client.post("/api/datasets/open", json={"path": str(source)})
    assert response.status_code == 422
    assert "zero where spike counts are nonzero" in response.json()["detail"]


def test_all_zero_rf_occupancy_is_rejected(app, settings: Settings) -> None:
    payload = sample_payload()
    payload["unitsSpikeCounts"] = np.zeros((2, 2, 2, 3), dtype=int).tolist()
    payload["occupancyTimeSec"] = [[0, 0], [0, 0]]
    source = write_json(settings.rf_root / "all-zero-occupancy.rfmap", payload)
    with authenticated_client(app) as client:
        response = client.post("/api/datasets/open", json={"path": str(source)})
    assert response.status_code == 422
    assert "at least one positive" in response.json()["detail"]

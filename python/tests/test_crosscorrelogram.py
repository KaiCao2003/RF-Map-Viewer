from __future__ import annotations

import io
from pathlib import Path

import matplotlib as mpl
import numpy as np
import pytest
from matplotlib.colors import to_rgba
from PIL import Image

from rfmapping_viewer.crosscorrelogram import (
    available_unit_ids,
    compute_crosscorrelograms,
    make_crosscorrelogram_figure,
)


def _session(tmp_path: Path, probe_name: str = "A") -> Path:
    session = tmp_path / "m20" / "260922" / "260922_1"
    data = session / "data" / f"probe{probe_name}"
    kilosort = session / "kilosort" / f"Probe{probe_name}" / "kilosort_1"
    data.mkdir(parents=True)
    kilosort.mkdir(parents=True)
    # Each reference spike is followed by units 302 and 11 at +5 and +9 ms.
    times = (np.arange(0.1, 0.5, 0.1)[:, None] + [0.0, 0.005, 0.009]).ravel()
    clusters = np.tile([368, 302, 11], 4)
    np.save(data / "adc_spike_time.npy", times[:, None])
    np.save(kilosort / "spike_clusters.npy", clusters[:, None])
    return session


def test_pair_direction_and_rates_follow_selection_order(tmp_path: Path) -> None:
    session = _session(tmp_path)
    ccg = compute_crosscorrelograms(session, "A", [368, 302, 11], window_size=0.02)

    assert available_unit_ids(session, "A") == [11, 302, 368]
    assert list(ccg.columns) == [(368, 302), (368, 11), (302, 11)]
    np.testing.assert_allclose(ccg.idxmax(), [0.005, 0.009, 0.004])
    np.testing.assert_allclose(ccg.max(), 1000.0)

    reverse = compute_crosscorrelograms(session, "A", [302, 368], window_size=0.02)
    assert list(reverse.columns) == [(302, 368)]
    assert reverse[(302, 368)].idxmax() == pytest.approx(-0.005)


def test_probe_b_reads_its_own_spikes(tmp_path: Path) -> None:
    session = _session(tmp_path, "B")
    assert available_unit_ids(session, "B") == [11, 302, 368]
    assert list(compute_crosscorrelograms(session, "B", [368, 11]).columns) == [(368, 11)]


@pytest.mark.parametrize("units", [[368], [368, 368], [1, 2, 3, 4]])
def test_selection_requires_two_or_three_distinct_units(tmp_path: Path, units: list[int]) -> None:
    with pytest.raises(ValueError, match="two or three distinct"):
        compute_crosscorrelograms(tmp_path, "A", units)


@pytest.mark.parametrize("value", [0.0, -0.001, float("nan"), float("inf")])
@pytest.mark.parametrize("parameter", ["bin_size", "window_size"])
def test_invalid_bin_or_window_is_rejected(tmp_path: Path, parameter: str, value: float) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        compute_crosscorrelograms(tmp_path, "A", [368, 302], **{parameter: value})


def test_missing_unit_and_mismatched_arrays_are_rejected(tmp_path: Path) -> None:
    session = _session(tmp_path)
    with pytest.raises(ValueError, match="Unit 999 has no spikes"):
        compute_crosscorrelograms(session, "A", [368, 999])
    np.save(session / "data" / "probeA" / "adc_spike_time.npy", [0.1])
    with pytest.raises(ValueError, match="different lengths"):
        compute_crosscorrelograms(session, "A", [368, 302])


@pytest.mark.parametrize("units", [[368, 302], [368, 302, 11]])
def test_figures_and_exports_stay_white_under_dark_style(tmp_path: Path, units: list[int]) -> None:
    session = _session(tmp_path)
    ccg = compute_crosscorrelograms(session, "A", units)
    with mpl.rc_context({
        "figure.facecolor": "black", "axes.facecolor": "black",
        "axes.edgecolor": "white", "axes.labelcolor": "white",
        "text.color": "white", "xtick.color": "white", "ytick.color": "white",
        "savefig.facecolor": "black", "savefig.transparent": True,
    }):
        figure = make_crosscorrelogram_figure(ccg, session, "A", 0.001, 0.05)
        assert len(figure.axes) == len(ccg.columns)
        assert figure.get_facecolor() == to_rgba("white")
        for ax in figure.axes:
            assert ax.get_facecolor() == to_rgba("white")
            assert to_rgba(ax.xaxis.label.get_color()) == to_rgba("black")
            assert to_rgba(ax.yaxis.label.get_color()) == to_rgba("black")
            assert ax.get_xlim() == (-50.0, 50.0)
        image_bytes = io.BytesIO()
        figure.savefig(image_bytes, format="png")
        image_bytes.seek(0)
        rendered = Image.open(image_bytes).convert("RGBA")
        assert rendered.getpixel((0, 0)) == (255, 255, 255, 255)
        assert rendered.getchannel("A").getextrema() == (255, 255)

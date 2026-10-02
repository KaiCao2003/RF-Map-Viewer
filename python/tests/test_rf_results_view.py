from types import SimpleNamespace
from unittest import mock

import pytest

from gui_test_support import tk_test_root
from rfmapping_viewer.tk_support import TK_AVAILABLE, ttk
from test_rf_results import source, write_result


@pytest.mark.skipif(not TK_AVAILABLE, reason="Tk is unavailable")
def test_saved_result_selectors_share_cache_and_reload_without_a_second_canvas(source):
    from rfmapping_viewer.rf_results_view import RFResultsPane

    root = tk_test_root()
    host = ttk.Frame(root)
    controls_host = ttk.Frame(host)
    changed = mock.Mock()
    data = SimpleNamespace(
        path=source.path, unit_pool=source.unit_ids, x_positions=source.x_positions,
        y_positions=source.y_positions, time_bin_edges=source.time_bin_edges,
    )
    pane = RFResultsPane(host, data, controls_parent=controls_host, on_change=changed)
    try:
        assert pane.controls.master is controls_host
        assert not hasattr(pane, "canvas")
        pane.set_unit(42)
        assert pane.status.get() == "2D unavailable"
        write_result(source)
        pane.refresh()
        changed.assert_called_once_with()
        assert pane.cache.get(42, mode="2D").available
        assert pane.status.get() == "0–200 ms"
        assert "Unit 42" in pane.details.get()
        pane.dimension.set("Both")
        pane._selection_changed()
        assert pane.status.get() == "0–200 ms · 1D unavailable"
        assert changed.call_count == 2
        pane.set_document(None)
        pane.set_unit(None)
        assert pane.cache is None
        assert pane.status.get() == "No unit selected"
        assert pane.details.get() == ""
    finally:
        host.destroy()
    root.update_idletasks()

from __future__ import annotations

import pytest

from gui_test_support import tk_test_root
from rfmapping_viewer.tk_support import TK_AVAILABLE, tk


@pytest.mark.skipif(not TK_AVAILABLE, reason="Tk is unavailable")
def test_pane_destruction_cancels_pending_matplotlib_render():
    from rfmapping_viewer.rf_results_view import RFResultsPane

    try:
        root = tk_test_root()
    except tk.TclError as error:
        pytest.skip(str(error))
    pane = RFResultsPane(root)
    pane.canvas.draw_idle()
    callback = pane.canvas._idle_draw_id
    assert callback is not None
    pane.destroy()
    assert pane.canvas._idle_draw_id is None
    assert callback not in root.tk.call("after", "info")
    root.update_idletasks()

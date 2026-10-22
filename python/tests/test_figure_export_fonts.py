from __future__ import annotations

import pytest

from rfmapping_viewer import figure_export


@pytest.mark.parametrize("bold", [False, True])
def test_export_font_renders_scientific_label_symbols(bold):
    font = figure_export._font(22, bold=bold)
    missing = font.getmask("\uffff")
    missing_glyph = (missing.size, bytes(missing))
    for symbol in ("\u2013", "\u00b5", "\u00b0", "\u2212", "\u00d7"):
        glyph = font.getmask(symbol)
        assert glyph.getbbox() is not None
        assert (glyph.size, bytes(glyph)) != missing_glyph, repr(symbol)


@pytest.mark.parametrize("bold, filename", [(False, "arial.ttf"), (True, "arialbd.ttf")])
def test_export_font_finds_windows_arial_when_other_fonts_are_absent(monkeypatch, bold, filename):
    windows_font = object()

    def load_font(candidate, *, size):
        if candidate == filename:
            return windows_font
        raise OSError("Font is not installed")

    monkeypatch.setattr(figure_export.ImageFont, "truetype", load_font)
    assert figure_export._font(22, bold=bold) is windows_font

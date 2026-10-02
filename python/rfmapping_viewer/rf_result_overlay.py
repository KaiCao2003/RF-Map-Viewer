"""Native-grid overlays from read-only saved RF detections."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from rfmapping_viewer.rf_results import RFResultSource, SavedRFResult, load_saved_rf_result
from rfmapping_viewer.settings import RF_RESULT_OVERLAY_MODES, RF_RESULT_OVERLAY_POLARITIES


@dataclass(frozen=True)
class RFResultOverlay:
    # Bit 1 is a saved 2-D mask; bit 2 is a saved 1-D mask broadcast by axis.
    flags: np.ndarray | None
    unavailable: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.flags is not None


class RFResultOverlayCache:
    """Read each saved sidecar once per document until an explicit reload."""

    def __init__(self, source: RFResultSource):
        self.source = source
        self._results: dict[tuple[str, str], SavedRFResult | None] = {}
        self._errors: dict[tuple[str, str], str] = {}

    def clear(self) -> None:
        self._results.clear()
        self._errors.clear()

    def result(self, dimension: str, rf_type: str) -> SavedRFResult | None:
        """Share the saved result with selectors and the information inspector."""

        if dimension not in ("2d", "1d") or rf_type not in RF_RESULT_OVERLAY_POLARITIES:
            raise ValueError("Choose a supported saved RF dimension and polarity")
        key = (dimension, rf_type)
        if key not in self._results:
            try:
                self._results[key] = load_saved_rf_result(self.source, dimension=dimension, rf_type=rf_type)
            except (OSError, ValueError, TypeError, KeyError) as error:
                self._results[key] = None
                self._errors[key] = str(error)
        return self._results[key]

    def error(self, dimension: str, rf_type: str) -> str | None:
        self.result(dimension, rf_type)
        return self._errors.get((dimension, rf_type))

    def get(self, unit_id: int, *, mode: str = "Both", rf_type: str = "excitatory") -> RFResultOverlay:
        if mode not in RF_RESULT_OVERLAY_MODES or rf_type not in RF_RESULT_OVERLAY_POLARITIES:
            raise ValueError("Choose a supported saved RF overlay and polarity")
        dimensions = {"None": (), "2D": ("2d",), "1D": ("1d",), "Both": ("2d", "1d")}[mode]
        flags = None
        unavailable = []
        shape = (len(self.source.y_positions), len(self.source.x_positions))
        for dimension in dimensions:
            result = self.result(dimension, rf_type)
            saved = result.for_unit(unit_id) if result is not None else None
            if saved is None:
                unavailable.append(dimension.upper())
                continue
            mask, _center = saved
            if dimension == "1d":
                # The saved collapse axis determines which native coordinate
                # remains; broadcasting only changes its display footprint.
                mask = mask[np.newaxis, :] if result.axis == "x" else mask[:, np.newaxis]
                mask = np.broadcast_to(mask, shape)
            if flags is None:
                flags = np.zeros(shape, dtype=np.uint8)
            flags[mask.astype(bool)] |= 1 if dimension == "2d" else 2
        if flags is not None:
            flags.setflags(write=False)
        return RFResultOverlay(flags, tuple(unavailable))


def load_rf_result_overlay(
    source: RFResultSource, unit_id: int, *, mode: str = "Both", rf_type: str = "excitatory",
) -> RFResultOverlay:
    return RFResultOverlayCache(source).get(unit_id, mode=mode, rf_type=rf_type)

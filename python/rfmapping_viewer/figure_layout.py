"""Shared page geometry for the figure canvas and exported figures."""

from __future__ import annotations

from collections.abc import Sequence
from types import MappingProxyType
from typing import TypeAlias

GRID_COLUMNS = 6
GRID_ROWS = 6
FRAME_PRESETS = MappingProxyType(
    {"Small": (2, 2), "Wide": (4, 2), "Tall": (2, 4), "Large": (4, 4), "Full": (6, 6)}
)
Frame: TypeAlias = tuple[int, int, int, int]


def _frame(value: Sequence[int]) -> Frame:
    if (
        not isinstance(value, Sequence)
        or len(value) != 4
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise ValueError("a frame must contain four integers: column, row, width, height")
    column, row, width, height = value
    if (
        column < 0
        or row < 0
        or width < 1
        or height < 1
        or column + width > GRID_COLUMNS
        or row + height > GRID_ROWS
    ):
        raise ValueError("frames must fit inside the 6 × 6 page grid")
    return column, row, width, height


def _mask(frame: Frame) -> int:
    column, row, width, height = frame
    return sum(
        ((1 << width) - 1) << (column + y * GRID_COLUMNS)
        for y in range(row, row + height)
    )


def validate_frames(frames: Sequence[Sequence[int]]) -> tuple[Frame, ...]:
    """Return validated immutable frames, rejecting overlap and clipped plots."""
    result = tuple(_frame(frame) for frame in frames)
    occupied = 0
    for frame in result:
        mask = _mask(frame)
        if occupied & mask:
            raise ValueError("plot frames must not overlap")
        occupied |= mask
    return result


def automatic_frames(count: int) -> tuple[Frame, ...]:
    """Distribute newly added plots across the page without unused outer space."""
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 9:
        raise ValueError("a page can contain up to nine small plots")
    if count == 0:
        return ()
    columns = 1 if count == 1 else 2 if count in (2, 4) else 3
    rows = (count + columns - 1) // columns
    width, height = GRID_COLUMNS // columns, GRID_ROWS // rows
    return tuple(
        (index % columns * width, index // columns * height, width, height)
        for index in range(count)
    )


def first_available_frame(
    frames: Sequence[Sequence[int]], size: tuple[int, int] = (2, 2)
) -> Frame:
    """Find the first free position for a newly added widget."""
    current = validate_frames(frames)
    width, height = size
    _frame((0, 0, width, height))
    occupied = sum(_mask(frame) for frame in current)
    for row in range(GRID_ROWS - height + 1):
        for column in range(GRID_COLUMNS - width + 1):
            candidate = column, row, width, height
            if not occupied & _mask(candidate):
                return candidate
    raise ValueError("No room for this frame. Use a smaller size or add another page.")


def arrange_frames(
    frames: Sequence[Sequence[int]], index: int, target: Sequence[int]
) -> tuple[Frame, ...]:
    """Move or resize one widget, relocating its neighbours only when needed.

    The requested widget stays at its snapped position. Other widgets first
    retain their existing position; displaced widgets prefer the nearest free
    position. The tiny fixed grid permits a complete search when a simple
    displacement cannot fit, so a valid composition is never rejected merely
    because the first free slot was inconvenient.
    """
    current = validate_frames(frames)
    if not 0 <= index < len(current):
        raise IndexError("plot index is outside the page")
    anchor = _frame(target)
    required_cells = anchor[2] * anchor[3] + sum(
        frame[2] * frame[3]
        for position, frame in enumerate(current)
        if position != index
    )
    if required_cells > GRID_COLUMNS * GRID_ROWS:
        raise ValueError("No room for this frame. Use a smaller size or add another page.")
    result = list(current)
    result[index] = anchor
    candidates = {}
    for position, (old_column, old_row, width, height) in enumerate(current):
        if position == index:
            continue
        choices = [
            (column, row, width, height)
            for row in range(GRID_ROWS - height + 1)
            for column in range(GRID_COLUMNS - width + 1)
        ]
        choices.sort(
            key=lambda frame: (
                abs(frame[0] - old_column) + abs(frame[1] - old_row),
                frame[1],
                frame[0],
            )
        )
        candidates[position] = tuple((frame, _mask(frame)) for frame in choices)

    occupied = _mask(anchor)
    displaced = []
    for position, frame in enumerate(current):
        if position == index:
            continue
        mask = _mask(frame)
        if occupied & mask:
            displaced.append(position)
        else:
            occupied |= mask
    for position in displaced:
        choice = next(
            ((frame, mask) for frame, mask in candidates[position] if not occupied & mask),
            None,
        )
        if choice is None:
            break
        result[position] = choice[0]
        occupied |= choice[1]
    else:
        return tuple(result)

    failed = set()

    def place(remaining: tuple[int, ...], occupied: int) -> bool:
        if not remaining:
            return True
        # Identical frame sizes share a packing state, regardless of plot kind.
        state = occupied, tuple(sorted(current[position][2:] for position in remaining))
        if state in failed:
            return False
        available = {
            position: tuple(
                (frame, mask)
                for frame, mask in candidates[position]
                if not occupied & mask
            )
            for position in remaining
        }
        position = min(remaining, key=lambda item: len(available[item]))
        rest = tuple(item for item in remaining if item != position)
        for frame, mask in available[position]:
            result[position] = frame
            if place(rest, occupied | mask):
                return True
        failed.add(state)
        return False

    if place(tuple(candidates), _mask(anchor)):
        return tuple(result)
    raise ValueError("This frame cannot fit here. Move it to a free grid position or choose a smaller size.")


def page_grid_box(page_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Return the page's drawable grid after its title and outside margins."""
    width, height = page_size
    margin = max(18, round(min(width, height) * 0.025))
    header_height = max(50, round(height * 0.07))
    return margin, margin + header_height, width - margin, height - margin


def frame_pixel_box(
    frame: Sequence[int], page_size: tuple[int, int]
) -> tuple[int, int, int, int]:
    """Map one grid frame to the exact panel bounds used in preview and export."""
    column, row, width, height = _frame(frame)
    left, top, right, bottom = page_grid_box(page_size)
    gap = max(10, round(min(page_size) * 0.014))
    return (
        left + round((right - left) * column / GRID_COLUMNS) + gap // 2,
        top + round((bottom - top) * row / GRID_ROWS) + gap // 2,
        left + round((right - left) * (column + width) / GRID_COLUMNS) - gap // 2,
        top + round((bottom - top) * (row + height) / GRID_ROWS) - gap // 2,
    )

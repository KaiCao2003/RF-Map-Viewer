"""Cancellable process isolation for large RF document decoding.

No Tk objects or GUI locks cross the process boundary. Validated count arrays
are transferred in bounded binary chunks so unpickling a large nested payload
cannot stall the interface after decoding finishes.
"""

from __future__ import annotations

import multiprocessing
from concurrent.futures import CancelledError
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Callable

import numpy as np

from .rf_dataset import RFMapList, _make_rf_map, _readonly_array, load_rf_maps


_TRANSFER_BYTES = 1024 * 1024


def _decode_worker(path: str, connection: Connection) -> None:
    try:
        maps = load_rf_maps(path)
        first = maps[0]
        connection.send((
            "header",
            (
                len(maps), first.x_positions, first.y_positions,
                first.time_bin_edges_s, first.occupancy_time_s, dict(first.metadata),
            ),
        ))
        for unit in maps:
            counts = np.ascontiguousarray(unit.spike_counts)
            connection.send((
                "unit",
                (unit.unit_index, unit.unit_id, counts.shape, counts.dtype.str),
            ))
            view = memoryview(counts).cast("B")
            for offset in range(0, len(view), _TRANSFER_BYTES):
                connection.send_bytes(view[offset : offset + _TRANSFER_BYTES])
        connection.send(("done", None))
    except Exception as exc:
        try:
            connection.send(("error", str(exc)))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def load_rf_maps_isolated(
    path: str | Path, *, cancelled: Callable[[], bool] | None = None
) -> RFMapList:
    """Load in a spawned worker; cancellation terminates and reaps it."""

    if cancelled is not None and cancelled():
        raise CancelledError("RF document load cancelled")
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_decode_worker,
        args=(str(path), sender),
        name="rfmap-decode",
        daemon=True,
    )

    def wait_for_data() -> None:
        while True:
            if cancelled is not None and cancelled():
                raise CancelledError("RF document load cancelled")
            if receiver.poll(0.03):
                return
            if not process.is_alive():
                raise RuntimeError("RF document decoder exited before completing")

    def receive(expected: str):
        wait_for_data()
        kind, payload = receiver.recv()
        if kind == "error":
            raise ValueError(payload)
        if kind != expected:
            raise RuntimeError("Unexpected RF document decoder response")
        return payload

    try:
        process.start()
        sender.close()
        n_units, x, y, edges, occupancy, metadata = receive("header")
        occupancy = _readonly_array(occupancy)
        maps = []
        for _ in range(n_units):
            index, unit_id, shape, dtype = receive("unit")
            counts = np.empty(shape, dtype=np.dtype(dtype))
            view = memoryview(counts).cast("B")
            for offset in range(0, len(view), _TRANSFER_BYTES):
                wait_for_data()
                target = view[offset : offset + _TRANSFER_BYTES]
                if receiver.recv_bytes_into(target) != len(target):
                    raise RuntimeError("Incomplete RF count array transfer")
            maps.append(
                _make_rf_map(
                    unit_index=index,
                    unit_id=unit_id,
                    spike_counts=counts,
                    x_positions=x,
                    y_positions=y,
                    time_bin_edges_s=edges,
                    occupancy_time_s=occupancy,
                    metadata=metadata,
                    source_path=path,
                )
            )
        receive("done")
        return RFMapList(maps, path)
    except EOFError as exc:
        raise RuntimeError("RF document decoder exited before completing") from exc
    finally:
        receiver.close()
        sender.close()
        if process.pid is not None:
            process.join(timeout=0.2)
            if process.is_alive():
                process.terminate()
                process.join()
            process.close()

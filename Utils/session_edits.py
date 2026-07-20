from __future__ import annotations

import numpy as np

"""Store delete/interpolation parameters for each recording session."""

from collections.abc import Sequence
from pathlib import Path
import sqlite3
from typing import TypeAlias

# Interpolation execution lives in recording.
# This module stores only its DB parameters.
from Utils.recording import interp_replace

# One normalized row-level edit read from or written to the new DB schema.
StoredEdit: TypeAlias = dict[str, object]


class SessionEditStore:
    """Read and write only the new ``session_edits`` schema."""

    def __init__(
            self,
            database_file: str | Path,
            mouse_id: str | None = None,
    ):
        self.database_file = Path(database_file).expanduser().resolve()
        self.mouse_id = (
            self.database_file.parent.name
            if mouse_id is None
            else str(mouse_id).strip()
        )
        if not self.mouse_id:
            raise ValueError("mouse_id cannot be empty")

    @staticmethod
    def _connect(database_file: str | Path) -> sqlite3.Connection:
        """Connect with dot-file locking, which works on the lab CIFS mount."""

        database_path = Path(database_file).expanduser().resolve()
        database_uri = f"{database_path.as_uri()}?vfs=unix-dotfile"
        database_connection = sqlite3.connect(database_uri, uri=True, timeout=30)
        database_connection.execute("PRAGMA foreign_keys = ON")
        return database_connection

    @staticmethod
    def is_database_initialized(database_file: str | Path) -> bool:
        database_path = Path(database_file).expanduser().resolve()
        if not database_path.is_file():
            return False

        try:
            with SessionEditStore._connect(database_path) as database_connection:
                table_names = {
                    row[0]
                    for row in database_connection.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                        """
                    )
                }
                if not {"session_edits", "session_delete_frames"}.issubset(
                        table_names
                ):
                    return False

                edit_columns = {
                    row[1]
                    for row in database_connection.execute(
                        "PRAGMA table_info(session_edits)"
                    )
                }
                required_edit_columns = {
                    "edit_id",
                    "mouse_id",
                    "date",
                    "session_id",
                    "edit_order",
                    "operation",
                    "interp_start_frame",
                    "interp_end_frame",
                    "interp_frames_between",
                }
                return required_edit_columns.issubset(edit_columns)
        except (OSError, sqlite3.Error):
            return False

    @staticmethod
    def create_database(database_file: str | Path) -> bool:
        database_path = Path(database_file).expanduser().resolve()

        try:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            with SessionEditStore._connect(database_path) as database_connection:
                database_connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS session_edits
                    (
                        edit_id
                        INTEGER
                        PRIMARY
                        KEY
                        AUTOINCREMENT,
                        mouse_id
                        TEXT
                        NOT
                        NULL,
                        date
                        TEXT
                        NOT
                        NULL,
                        session_id
                        TEXT
                        NOT
                        NULL,
                        edit_order
                        INTEGER
                        NOT
                        NULL,
                        operation
                        TEXT
                        NOT
                        NULL
                        CHECK (
                        operation
                        IN
                    (
                        'delete',
                        'interpolate'
                    )),
                        interp_start_frame INTEGER,
                        interp_end_frame INTEGER,
                        interp_frames_between INTEGER,
                        created_at TEXT NOT NULL,
                        last_changed_at TEXT NOT NULL,
                        UNIQUE
                    (
                        mouse_id,
                        date,
                        session_id,
                        edit_order
                    ),
                        CHECK
                    (
                    (
                        operation =
                        'delete'
                        AND
                        interp_start_frame
                        IS
                        NULL
                        AND
                        interp_end_frame
                        IS
                        NULL
                        AND
                        interp_frames_between
                        IS
                        NULL
                    )
                        OR
                    (
                        operation =
                        'interpolate'
                        AND
                        interp_start_frame
                        IS
                        NOT
                        NULL
                        AND
                        interp_end_frame
                        IS
                        NOT
                        NULL
                        AND
                        interp_frames_between
                        IS
                        NOT
                        NULL
                        AND
                        interp_frames_between
                        >=
                        0
                    )
                        )
                        )
                    """
                )
                database_connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS session_delete_frames
                    (
                        edit_id
                        INTEGER
                        NOT
                        NULL,
                        frame_order
                        INTEGER
                        NOT
                        NULL,
                        frame_index
                        INTEGER
                        NOT
                        NULL,
                        PRIMARY
                        KEY
                    (
                        edit_id,
                        frame_order
                    ),
                        FOREIGN KEY
                    (
                        edit_id
                    )
                        REFERENCES session_edits
                    (
                        edit_id
                    )
                        ON DELETE CASCADE
                        )
                    """
                )
        except (OSError, sqlite3.Error):
            return False

        return SessionEditStore.is_database_initialized(database_path)

    @staticmethod
    def _normalize_session_key(
            date: str,
            session_id: str,
    ) -> tuple[str, str]:
        normalized_date = str(date).strip()
        normalized_session_id = str(session_id).strip()
        if not normalized_date:
            raise ValueError("date cannot be empty")
        if not normalized_session_id:
            raise ValueError("session_id cannot be empty")
        return normalized_date, normalized_session_id

    @staticmethod
    def _insert_delete_edit(
            database_connection: sqlite3.Connection,
            mouse_id: str,
            date: str,
            session_id: str,
            edit_order: int,
            frames: Sequence[int],
    ) -> None:
        edit_cursor = database_connection.execute(
            """
            INSERT INTO session_edits (mouse_id, date, session_id, edit_order, operation,
                                       interp_start_frame, interp_end_frame, interp_frames_between,
                                       created_at, last_changed_at)
            VALUES (?, ?, ?, ?, 'delete',
                    NULL, NULL, NULL,
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            """,
            (mouse_id, date, session_id, int(edit_order)),
        )
        edit_id = int(edit_cursor.lastrowid)
        database_connection.executemany(
            """
            INSERT INTO session_delete_frames (edit_id, frame_order, frame_index)
            VALUES (?, ?, ?)
            """,
            [
                (edit_id, frame_order, int(frame_index))
                for frame_order, frame_index in enumerate(frames)
            ],
        )

    @staticmethod
    def _insert_interpolation_edit(
            database_connection: sqlite3.Connection,
            mouse_id: str,
            date: str,
            session_id: str,
            edit_order: int,
            start_frame: int,
            end_frame: int,
            frames_between: int,
    ) -> None:
        database_connection.execute(
            """
            INSERT INTO session_edits (mouse_id, date, session_id, edit_order, operation,
                                       interp_start_frame, interp_end_frame, interp_frames_between,
                                       created_at, last_changed_at)
            VALUES (?, ?, ?, ?, 'interpolate', ?, ?, ?,
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            """,
            (
                mouse_id,
                date,
                session_id,
                int(edit_order),
                int(start_frame),
                int(end_frame),
                int(frames_between),
            ),
        )

    def get_edits(self, date: str, session_id: str) -> list[StoredEdit] | None:
        normalized_date, normalized_session_id = self._normalize_session_key(
            date,
            session_id,
        )

        if not self.is_database_initialized(self.database_file):
            raise RuntimeError(
                f"session edit database is not initialized: {self.database_file}"
            )

        with self._connect(self.database_file) as database_connection:
            edit_rows = database_connection.execute(
                """
                SELECT edit_id,
                       operation,
                       interp_start_frame,
                       interp_end_frame,
                       interp_frames_between
                FROM session_edits
                WHERE mouse_id = ? AND date = ? AND session_id = ?
                ORDER BY edit_order
                """,
                (self.mouse_id, normalized_date, normalized_session_id),
            ).fetchall()

            if not edit_rows:
                return None

            edits: list[StoredEdit] = []

            for (
                    edit_id,
                    operation,
                    start_frame,
                    end_frame,
                    frames_between,
            ) in edit_rows:
                if operation == "delete":
                    frames = [
                        int(row[0])
                        for row in database_connection.execute(
                            """
                            SELECT frame_index
                            FROM session_delete_frames
                            WHERE edit_id = ?
                            ORDER BY frame_order
                            """,
                            (edit_id,),
                        )
                    ]
                    edits.append(
                        {
                            "operation": "delete",
                            "frames": frames,
                        }
                    )
                elif operation == "interpolate":
                    edits.append(
                        {
                            "operation": "interpolate",
                            "start_frame": int(start_frame),
                            "end_frame": int(end_frame),
                            "frames_between": int(frames_between),
                        }
                    )
                else:
                    raise ValueError(f"Unknown saved operation: {operation}")

        return edits

    def _next_edit_order(
            self,
            database_connection: sqlite3.Connection,
            date: str,
            session_id: str,
    ) -> int:
        saved_maximum = database_connection.execute(
            """
            SELECT MAX(edit_order)
            FROM session_edits
            WHERE mouse_id = ? AND date = ? AND session_id = ?
            """,
            (self.mouse_id, date, session_id),
        ).fetchone()[0]
        return 0 if saved_maximum is None else int(saved_maximum) + 1

    def add_delete_frames(
            self,
            date: str,
            session_id: str,
            frames: Sequence[int],
    ) -> tuple[bool, str | None]:
        """Append one delete operation using the exact supplied frame list."""

        try:
            normalized_date, normalized_session_id = self._normalize_session_key(
                date,
                session_id,
            )
            normalized_frames = normalize_delete_frames(frames)
        except (TypeError, ValueError) as edit_error:
            return False, str(edit_error)

        existing_edits = self.get_edits(normalized_date, normalized_session_id) or []
        if any(edit["operation"] == "interpolate" for edit in existing_edits):
            return (
                False,
                "clear saved interpolations before adding delete frames, because "
                "interpolation indices are fixed after all deletes",
            )

        # Identical delete groups are valid: groups run sequentially, so deleting
        # the same index again intentionally removes the next frame.
        # ---end---

        try:
            with self._connect(self.database_file) as database_connection:
                edit_order = self._next_edit_order(
                    database_connection,
                    normalized_date,
                    normalized_session_id,
                )
                self._insert_delete_edit(
                    database_connection,
                    self.mouse_id,
                    normalized_date,
                    normalized_session_id,
                    edit_order,
                    normalized_frames,
                )
        except sqlite3.Error as database_error:
            return False, str(database_error)

        return True, None

    def add_interpolation(
            self,
            date: str,
            session_id: str,
            start_frame: int,
            end_frame: int,
            frames_between: int,
    ) -> tuple[bool, str | None]:
        """Append interpolation anchors measured after every delete group."""

        try:
            normalized_date, normalized_session_id = self._normalize_session_key(
                date,
                session_id,
            )
            normalized_start = int(start_frame)
            normalized_end = int(end_frame)
            normalized_count = int(frames_between)
            if normalized_count < 0:
                raise ValueError("frames_between must be greater than or equal to zero")
        except (TypeError, ValueError) as edit_error:
            return False, str(edit_error)

        existing_edits = self.get_edits(normalized_date, normalized_session_id) or []
        new_edit: StoredEdit = {
            "operation": "interpolate",
            "start_frame": normalized_start,
            "end_frame": normalized_end,
            "frames_between": normalized_count,
        }
        if new_edit in existing_edits:
            return False, f"identical interpolation edit already exists: {new_edit}"

        try:
            with self._connect(self.database_file) as database_connection:
                edit_order = self._next_edit_order(
                    database_connection,
                    normalized_date,
                    normalized_session_id,
                )
                self._insert_interpolation_edit(
                    database_connection,
                    self.mouse_id,
                    normalized_date,
                    normalized_session_id,
                    edit_order,
                    normalized_start,
                    normalized_end,
                    normalized_count,
                )
        except sqlite3.Error as database_error:
            return False, str(database_error)

        return True, None

    def replace_edits(
            self,
            date: str,
            session_id: str,
            edits: Sequence[StoredEdit],
    ) -> tuple[bool, str | None]:
        """Replace a complete edit plan; used by migration and explicit rewrites."""

        try:
            normalized_date, normalized_session_id = self._normalize_session_key(
                date,
                session_id,
            )
            normalized_edits = normalize_session_edits(edits)
        except (KeyError, TypeError, ValueError) as edit_error:
            return False, str(edit_error)

        try:
            with self._connect(self.database_file) as database_connection:
                database_connection.execute(
                    """
                    DELETE
                    FROM session_edits
                    WHERE mouse_id = ? AND date = ? AND session_id = ?
                    """,
                    (self.mouse_id, normalized_date, normalized_session_id),
                )

                for edit_order, edit in enumerate(normalized_edits):
                    if edit["operation"] == "delete":
                        self._insert_delete_edit(
                            database_connection,
                            self.mouse_id,
                            normalized_date,
                            normalized_session_id,
                            edit_order,
                            edit["frames"],
                        )
                    else:
                        self._insert_interpolation_edit(
                            database_connection,
                            self.mouse_id,
                            normalized_date,
                            normalized_session_id,
                            edit_order,
                            edit["start_frame"],
                            edit["end_frame"],
                            edit["frames_between"],
                        )
        except sqlite3.Error as database_error:
            return False, str(database_error)

        return True, None

    def clear_interpolations(
            self,
            date: str,
            session_id: str,
    ) -> tuple[bool, str | None]:
        """Remove only interpolation edits and keep every delete group."""

        try:
            normalized_date, normalized_session_id = self._normalize_session_key(
                date,
                session_id,
            )
            with self._connect(self.database_file) as database_connection:
                saved_interpolation_count = int(
                    database_connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM session_edits
                        WHERE mouse_id = ?
                          AND date = ?
                          AND session_id = ?
                          AND operation = 'interpolate'
                        """,
                        (self.mouse_id, normalized_date, normalized_session_id),
                    ).fetchone()[0]
                )
                if saved_interpolation_count == 0:
                    return False, "no saved interpolations to clear"

                database_connection.execute(
                    """
                    DELETE
                    FROM session_edits
                    WHERE mouse_id = ?
                      AND date = ?
                      AND session_id = ?
                      AND operation = 'interpolate'
                    """,
                    (self.mouse_id, normalized_date, normalized_session_id),
                )
        except (OSError, sqlite3.Error, TypeError, ValueError) as edit_error:
            return False, str(edit_error)

        return True, None


def normalize_delete_frames(frames: Sequence[int]) -> list[int]:
    if isinstance(frames, (str, bytes)):
        raise TypeError("frames must be a sequence of integers")

    normalized_frames = [int(frame) for frame in frames]
    if not normalized_frames:
        raise ValueError("frames cannot be empty")
    if len(set(normalized_frames)) != len(normalized_frames):
        raise ValueError(f"frames contains duplicates: {normalized_frames}")
    return normalized_frames


def normalize_session_edits(edits: Sequence[StoredEdit]) -> list[StoredEdit]:
    if isinstance(edits, (str, bytes)):
        raise TypeError("edits must be a sequence")

    normalized_edits: list[StoredEdit] = []

    for edit_index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise TypeError(f"edit {edit_index} must be a dictionary")

        operation = str(edit.get("operation", "")).strip().lower()

        if operation == "delete":
            normalized_edits.append(
                {
                    "operation": "delete",
                    "frames": normalize_delete_frames(edit["frames"]),
                }
            )
        elif operation == "interpolate":
            frames_between = int(edit["frames_between"])
            if frames_between < 0:
                raise ValueError("frames_between must be greater than or equal to zero")
            normalized_edits.append(
                {
                    "operation": "interpolate",
                    "start_frame": int(edit["start_frame"]),
                    "end_frame": int(edit["end_frame"]),
                    "frames_between": frames_between,
                }
            )
        else:
            raise ValueError(
                f"edit {edit_index} has unknown operation: {operation!r}"
            )

    return normalized_edits


# The DB module stores edit parameters only. The notebook deliberately performs
# every np.delete group first and calls interp_replace only after deletes finish.


class SessionEdit:
    """Bind one date/recording to its delete and interpolation parameters.

    Read methods return parameters directly. Write methods return
    ``(success, message)`` so the notebook can keep its existing control style.
    """

    def __init__(
            self,
            edit_store: SessionEditStore,
            date: str | int,
            num_of_rec: str | int,
    ):
        normalized_date, normalized_num_of_rec = edit_store._normalize_session_key(
            date,
            num_of_rec,
        )
        self._edit_store = edit_store
        self.date = normalized_date
        self.num_of_rec = normalized_num_of_rec

    def getSessionInfo(self, *, target: list | np.ndarray | None = None) -> tuple[bool, list] | tuple[
        tuple[bool, list], list | np.ndarray]:
        deleteData = self.getDelete()
        interpolData = self.getInterp()
        result_info = []

        if target is not None:
            has_changed = False
            if deleteData:
                target = np.delete(target, deleteData)
                result_info.append("delete")
                has_changed = True
            if len(interpolData) > 0:
                result_info.append("interpolate")
                has_changed = True
                for interpInfo in interpolData:
                    target = interp_replace(
                        target,
                        interpInfo[0],
                        interpInfo[1],
                        interpInfo[2]
                    )
            return (has_changed, result_info), target

        else:
            if deleteData:
                result_info.append("delete")
            if interpolData:
                result_info.append("interpolate")
            return (not result_info == []), result_info

    def getDelete(self) -> list[list[int]]:
        """Return delete groups in the order in which np.delete must run."""

        saved_edits = (
                self._edit_store.get_edits(self.date, self.num_of_rec) or []
        )
        return [
            [int(frame) for frame in edit["frames"]]
            for edit in saved_edits
            if edit["operation"] == "delete"
        ]

    def getInterp(self) -> list[tuple[int, int, int]]:
        """Return interpolation parameters from higher to lower start index."""

        saved_edits = (
                self._edit_store.get_edits(self.date, self.num_of_rec) or []
        )
        interpolation_edits = [
            (
                int(edit["start_frame"]),
                int(edit["end_frame"]),
                int(edit["frames_between"]),
            )
            for edit in saved_edits
            if edit["operation"] == "interpolate"
        ]
        return sorted(
            interpolation_edits,
            key=lambda interpolation: interpolation[0],
            reverse=True,
        )

    def checkSaved(self) -> tuple[bool, str | None]:
        saved_edits = self._edit_store.get_edits(self.date, self.num_of_rec)
        if saved_edits is None:
            return False, (
                f"no saved edits for {self.date} rec {self.num_of_rec}"
            )
        return True, None

    def deleteByRange(
            self,
            startFrame: int,
            endFrame: int | None,
    ) -> tuple[bool, str | None]:
        """Store the frames excluded by target[startFrame:endFrame]."""

        try:
            normalized_start = int(startFrame)
            normalized_end = None if endFrame is None else int(endFrame)
            if normalized_start < 0:
                raise ValueError("startFrame must be greater than or equal to zero")
            if normalized_end is not None and normalized_end >= 0:
                raise ValueError("endFrame must be negative or None")

            frame_list = list(range(normalized_start))
            if normalized_end is not None:
                frame_list.extend(range(normalized_end, 0))
        except (TypeError, ValueError) as edit_error:
            return False, str(edit_error)

        return self.deleteByFrame(frame_list)

    def deleteByFrame(
            self,
            frameList: Sequence[int],
    ) -> tuple[bool, str | None]:
        """Store one exact delete group without flattening earlier groups."""

        return self._edit_store.add_delete_frames(
            self.date,
            self.num_of_rec,
            frameList,
        )

    def interp(
            self,
            startFrame: int,
            endFrame: int,
            framesInBetween: int,
    ) -> tuple[bool, str | None]:
        """Store interpolation indices measured after all deletes."""

        try:
            normalized_start = int(startFrame)
            normalized_end = int(endFrame)
            normalized_count = int(framesInBetween)
        except (TypeError, ValueError) as edit_error:
            return False, str(edit_error)

        if not 0 <= normalized_start < normalized_end:
            return False, "interpolation requires 0 <= startFrame < endFrame"
        if normalized_count < 0:
            return False, "framesInBetween must be greater than or equal to zero"

        for saved_start, saved_end, _ in self.getInterp():
            ranges_are_separate = (
                    normalized_end < saved_start or normalized_start > saved_end
            )
            if not ranges_are_separate:
                return False, (
                    "interpolation range overlaps a saved interpolation: "
                    f"({saved_start}, {saved_end})"
                )

        return self._edit_store.add_interpolation(
            self.date,
            self.num_of_rec,
            normalized_start,
            normalized_end,
            normalized_count,
        )

    def clearInterps(self) -> tuple[bool, str | None]:
        return self._edit_store.clear_interpolations(
            self.date,
            self.num_of_rec,
        )


def _check_session_edit_database(
        database_path: str | Path,
) -> SessionEditStore:
    if not SessionEditStore.is_database_initialized(database_path):
        database_was_created = SessionEditStore.create_database(database_path)
        if not database_was_created:
            raise RuntimeError(
                f"Could not initialize session-edit database: {database_path}"
            )
    return SessionEditStore(database_path)


def check_session_edits(
        database_path: str | Path,
        date: str | int,
        num_of_rec: str | int,
) -> SessionEdit:
    """Return one session-bound edit object; date/recording are not repeated."""

    edit_store = _check_session_edit_database(database_path)
    return SessionEdit(edit_store, date, num_of_rec)


__all__ = [
    "SessionEdit",
    "check_session_edits",
    "interp_replace",
]

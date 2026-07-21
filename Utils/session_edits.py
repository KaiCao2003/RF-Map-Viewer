"""Store one flattened delete/interpolation record per recording session."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from itertools import chain
import json
from operator import index
from pathlib import Path
import sqlite3
from typing import Iterator, TypeAlias, TypedDict

import numpy as np

from Utils.recording import interp_replace


# Compatibility shape for callers that use SessionEditStore.get_edits().
StoredEdit: TypeAlias = dict[str, object]


class StoredSessionEdit(TypedDict):
    """The Python representation of one ``session_edits`` table row."""

    date: str
    session_id: str
    delete_frames: list[int]
    interp_start_end: tuple[int, int] | None
    interp_frames_between: int | None


_SESSION_EDIT_COLUMN_SPECS = [
    ("date", "TEXT", True, 1),
    ("session_id", "TEXT", True, 2),
    ("delete_frames", "TEXT", True, 0),
    ("interp_start_end", "TEXT", False, 0),
    ("interp_frames_between", "INTEGER", False, 0),
]

_LEGACY_OPERATION_COLUMNS = {
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

_CREATE_SESSION_EDITS_SQL = """
CREATE TABLE IF NOT EXISTS session_edits (
    date TEXT NOT NULL,
    session_id TEXT NOT NULL,
    delete_frames TEXT NOT NULL DEFAULT '[]'
        CHECK (
            CASE
                WHEN json_valid(delete_frames)
                THEN json_type(delete_frames) = 'array'
                ELSE 0
            END
        ),
    interp_start_end TEXT
        CHECK (
            interp_start_end IS NULL
            OR CASE
                WHEN json_valid(interp_start_end)
                THEN json_type(interp_start_end) = 'array'
                     AND json_array_length(interp_start_end) = 2
                     AND json_type(interp_start_end, '$[0]') = 'integer'
                     AND json_type(interp_start_end, '$[1]') = 'integer'
                     AND json_extract(interp_start_end, '$[0]') >= 0
                     AND json_extract(interp_start_end, '$[0]')
                         < json_extract(interp_start_end, '$[1]')
                ELSE 0
            END
        ),
    interp_frames_between INTEGER
        CHECK (
            interp_frames_between IS NULL
            OR (
                typeof(interp_frames_between) = 'integer'
                AND interp_frames_between >= 0
            )
        ),
    PRIMARY KEY (date, session_id),
    CHECK (
        (
            interp_start_end IS NULL
            AND interp_frames_between IS NULL
        )
        OR (
            interp_start_end IS NOT NULL
            AND interp_frames_between IS NOT NULL
        )
    )
)
"""


def _encode_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _normalize_integer(value: object, field_name: str) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise TypeError(f"{field_name} must be an integer")
    try:
        return index(value)
    except TypeError as conversion_error:
        raise TypeError(f"{field_name} must be an integer") from conversion_error


def _decode_delete_frames(encoded_frames: str) -> list[int]:
    try:
        decoded_frames = json.loads(encoded_frames)
    except (TypeError, json.JSONDecodeError) as decode_error:
        raise ValueError("saved delete_frames is not valid JSON") from decode_error

    if not isinstance(decoded_frames, list):
        raise ValueError("saved delete_frames must be a JSON array")

    normalized_frames: list[int] = []
    for frame in decoded_frames:
        if isinstance(frame, bool) or not isinstance(frame, int):
            raise ValueError("saved delete_frames must contain only integers")
        normalized_frames.append(frame)

    if len(set(normalized_frames)) != len(normalized_frames):
        raise ValueError("saved delete_frames contains duplicate indices")

    return normalized_frames


def _decode_interp_start_end(
    encoded_start_end: str | None,
) -> tuple[int, int] | None:
    if encoded_start_end is None:
        return None

    try:
        decoded_start_end = json.loads(encoded_start_end)
    except (TypeError, json.JSONDecodeError) as decode_error:
        raise ValueError("saved interp_start_end is not valid JSON") from decode_error

    if (
        not isinstance(decoded_start_end, list)
        or len(decoded_start_end) != 2
        or any(
            isinstance(frame, bool) or not isinstance(frame, int)
            for frame in decoded_start_end
        )
    ):
        raise ValueError("saved interp_start_end must contain exactly two integers")

    start_frame, end_frame = decoded_start_end
    if not 0 <= start_frame < end_frame:
        raise ValueError("saved interpolation requires 0 <= start_frame < end_frame")
    return start_frame, end_frame


def _merge_unique_frames(
    existing_frames: Sequence[int],
    new_frames: Sequence[int],
) -> list[int]:
    """Append frames while preserving first appearance and removing overlap."""

    merged_frames: list[int] = []
    seen_frames: set[int] = set()
    for frame in chain(existing_frames, new_frames):
        normalized_frame = _normalize_integer(frame, "delete frame")
        if normalized_frame not in seen_frames:
            merged_frames.append(normalized_frame)
            seen_frames.add(normalized_frame)
    return merged_frames


class SessionEditStore:
    """Read and write one flattened row per date/session pair."""

    def __init__(
        self,
        database_file: str | Path,
        mouse_id: str | None = None,
    ):
        self.database_file = Path(database_file).expanduser().resolve()

        # Keep this compatibility attribute for older callers. The database is
        # already scoped by its mouse directory, so mouse_id is not persisted.
        self.mouse_id = (
            self.database_file.parent.name
            if mouse_id is None
            else str(mouse_id).strip()
        )
        if not self.mouse_id:
            raise ValueError("mouse_id cannot be empty")

    @staticmethod
    @contextmanager
    def _connect(
        database_file: str | Path,
    ) -> Iterator[sqlite3.Connection]:
        """Connect with dot-file locking, which works on the lab CIFS mount."""

        database_path = Path(database_file).expanduser().resolve()
        database_uri = f"{database_path.as_uri()}?vfs=unix-dotfile"
        database_connection = sqlite3.connect(database_uri, uri=True, timeout=30)
        database_connection.execute("PRAGMA foreign_keys = ON")
        try:
            with database_connection:
                yield database_connection
        finally:
            database_connection.close()

    @staticmethod
    def _table_names(database_connection: sqlite3.Connection) -> set[str]:
        return {
            str(row[0])
            for row in database_connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            )
        }

    @staticmethod
    def _table_columns(
        database_connection: sqlite3.Connection,
        table_name: str,
    ) -> set[str]:
        return {
            str(row[1])
            for row in database_connection.execute(
                f"PRAGMA table_info({table_name})"
            )
        }

    @staticmethod
    def _has_current_schema(
        database_connection: sqlite3.Connection,
    ) -> bool:
        table_info = database_connection.execute(
            "PRAGMA table_info(session_edits)"
        ).fetchall()
        actual_specs = [
            (
                str(row[1]),
                str(row[2]).upper(),
                bool(row[3]),
                int(row[5]),
            )
            for row in table_info
        ]
        return actual_specs == _SESSION_EDIT_COLUMN_SPECS

    @staticmethod
    def is_database_initialized(database_file: str | Path) -> bool:
        database_path = Path(database_file).expanduser().resolve()
        if not database_path.is_file():
            return False

        try:
            with SessionEditStore._connect(database_path) as database_connection:
                table_names = SessionEditStore._table_names(database_connection)
                if "session_edits" not in table_names:
                    return False
                if "session_delete_frames" in table_names:
                    return False
                return SessionEditStore._has_current_schema(database_connection)
        except (OSError, sqlite3.Error):
            return False

    @staticmethod
    def has_legacy_operation_schema(database_file: str | Path) -> bool:
        database_path = Path(database_file).expanduser().resolve()
        if not database_path.is_file():
            return False

        try:
            with SessionEditStore._connect(database_path) as database_connection:
                table_names = SessionEditStore._table_names(database_connection)
                if not {"session_edits", "session_delete_frames"}.issubset(
                    table_names
                ):
                    return False
                return _LEGACY_OPERATION_COLUMNS.issubset(
                    SessionEditStore._table_columns(
                        database_connection,
                        "session_edits",
                    )
                )
        except (OSError, sqlite3.Error):
            return False

    @staticmethod
    def create_database(database_file: str | Path) -> bool:
        """Create the five-column table without altering an existing schema."""

        database_path = Path(database_file).expanduser().resolve()

        try:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            with SessionEditStore._connect(database_path) as database_connection:
                table_names = SessionEditStore._table_names(database_connection)
                if "session_edits" not in table_names:
                    if "session_delete_frames" in table_names:
                        return False
                    database_connection.execute(_CREATE_SESSION_EDITS_SQL)
        except (OSError, sqlite3.Error):
            return False

        return SessionEditStore.is_database_initialized(database_path)

    @staticmethod
    def _normalize_session_key(
        date: str | int,
        session_id: str | int,
    ) -> tuple[str, str]:
        normalized_date = str(date).strip()
        normalized_session_id = str(session_id).strip()
        if not normalized_date:
            raise ValueError("date cannot be empty")
        if not normalized_session_id:
            raise ValueError("session_id cannot be empty")
        return normalized_date, normalized_session_id

    @staticmethod
    def _decode_row(
        date: str,
        session_id: str,
        row: tuple[object, object, object],
    ) -> StoredSessionEdit:
        encoded_delete_frames, encoded_start_end, frames_between = row
        delete_frames = _decode_delete_frames(str(encoded_delete_frames))
        interp_start_end = _decode_interp_start_end(
            None if encoded_start_end is None else str(encoded_start_end)
        )
        normalized_frames_between = (
            None
            if frames_between is None
            else _normalize_integer(
                frames_between,
                "saved interp_frames_between",
            )
        )

        if (interp_start_end is None) != (normalized_frames_between is None):
            raise ValueError(
                "saved interpolation pair and frames_between must both be set or null"
            )
        if (
            normalized_frames_between is not None
            and normalized_frames_between < 0
        ):
            raise ValueError("saved interp_frames_between cannot be negative")

        return {
            "date": date,
            "session_id": session_id,
            "delete_frames": delete_frames,
            "interp_start_end": interp_start_end,
            "interp_frames_between": normalized_frames_between,
        }

    def get_session_edit(
        self,
        date: str | int,
        session_id: str | int,
    ) -> StoredSessionEdit | None:
        normalized_date, normalized_session_id = self._normalize_session_key(
            date,
            session_id,
        )

        if not self.is_database_initialized(self.database_file):
            raise RuntimeError(
                f"session edit database is not initialized: {self.database_file}"
            )

        with self._connect(self.database_file) as database_connection:
            row = database_connection.execute(
                """
                SELECT delete_frames,
                       interp_start_end,
                       interp_frames_between
                FROM session_edits
                WHERE date = ? AND session_id = ?
                """,
                (normalized_date, normalized_session_id),
            ).fetchone()

        if row is None:
            return None
        return self._decode_row(normalized_date, normalized_session_id, row)

    def get_edits(
        self,
        date: str | int,
        session_id: str | int,
    ) -> list[StoredEdit] | None:
        """Return the old facade shape, backed by one flattened table row."""

        session_edit = self.get_session_edit(date, session_id)
        if session_edit is None:
            return None

        edits: list[StoredEdit] = []
        if session_edit["delete_frames"]:
            edits.append(
                {
                    "operation": "delete",
                    "frames": list(session_edit["delete_frames"]),
                }
            )

        interp_start_end = session_edit["interp_start_end"]
        if interp_start_end is not None:
            edits.append(
                {
                    "operation": "interpolate",
                    "start_frame": interp_start_end[0],
                    "end_frame": interp_start_end[1],
                    "frames_between": session_edit["interp_frames_between"],
                }
            )
        return edits

    def add_delete_frames(
        self,
        date: str | int,
        session_id: str | int,
        frames: Sequence[int],
    ) -> tuple[bool, str | None]:
        """Append frames to the session's one flat delete list."""

        try:
            normalized_date, normalized_session_id = self._normalize_session_key(
                date,
                session_id,
            )
            normalized_frames = normalize_delete_frames(frames)

            with self._connect(self.database_file) as database_connection:
                database_connection.execute("BEGIN IMMEDIATE")
                row = database_connection.execute(
                    """
                    SELECT delete_frames, interp_start_end
                    FROM session_edits
                    WHERE date = ? AND session_id = ?
                    """,
                    (normalized_date, normalized_session_id),
                ).fetchone()

                if row is None:
                    existing_frames: list[int] = []
                else:
                    existing_frames = _decode_delete_frames(str(row[0]))
                    if row[1] is not None:
                        return (
                            False,
                            "clear the saved interpolation before adding delete "
                            "frames because its indices are measured after deletes",
                        )

                merged_frames = _merge_unique_frames(
                    existing_frames,
                    normalized_frames,
                )
                encoded_frames = _encode_json(merged_frames)

                database_connection.execute(
                    """
                    INSERT INTO session_edits (
                        date,
                        session_id,
                        delete_frames,
                        interp_start_end,
                        interp_frames_between
                    )
                    VALUES (?, ?, ?, NULL, NULL)
                    ON CONFLICT(date, session_id) DO UPDATE SET
                        delete_frames = excluded.delete_frames
                    """,
                    (
                        normalized_date,
                        normalized_session_id,
                        encoded_frames,
                    ),
                )
        except (OSError, sqlite3.Error, TypeError, ValueError) as edit_error:
            return False, str(edit_error)

        return True, None

    def add_interpolation(
        self,
        date: str | int,
        session_id: str | int,
        start_frame: int,
        end_frame: int,
        frames_between: int,
    ) -> tuple[bool, str | None]:
        """Set or replace the session's single interpolation."""

        try:
            normalized_date, normalized_session_id = self._normalize_session_key(
                date,
                session_id,
            )
            normalized_start = _normalize_integer(start_frame, "start_frame")
            normalized_end = _normalize_integer(end_frame, "end_frame")
            normalized_count = _normalize_integer(
                frames_between,
                "frames_between",
            )
            if not 0 <= normalized_start < normalized_end:
                raise ValueError(
                    "interpolation requires 0 <= start_frame < end_frame"
                )
            if normalized_count < 0:
                raise ValueError(
                    "frames_between must be greater than or equal to zero"
                )

            encoded_start_end = _encode_json(
                [normalized_start, normalized_end]
            )
            with self._connect(self.database_file) as database_connection:
                database_connection.execute("BEGIN IMMEDIATE")
                saved_interpolation = database_connection.execute(
                    """
                    SELECT interp_start_end, interp_frames_between
                    FROM session_edits
                    WHERE date = ? AND session_id = ?
                    """,
                    (normalized_date, normalized_session_id),
                ).fetchone()

                if saved_interpolation == (
                    encoded_start_end,
                    normalized_count,
                ):
                    return False, "identical interpolation is already saved"

                database_connection.execute(
                    """
                    INSERT INTO session_edits (
                        date,
                        session_id,
                        delete_frames,
                        interp_start_end,
                        interp_frames_between
                    )
                    VALUES (?, ?, '[]', ?, ?)
                    ON CONFLICT(date, session_id) DO UPDATE SET
                        interp_start_end = excluded.interp_start_end,
                        interp_frames_between = excluded.interp_frames_between
                    """,
                    (
                        normalized_date,
                        normalized_session_id,
                        encoded_start_end,
                        normalized_count,
                    ),
                )
        except (OSError, sqlite3.Error, TypeError, ValueError) as edit_error:
            return False, str(edit_error)

        return True, None

    def replace_edits(
        self,
        date: str | int,
        session_id: str | int,
        edits: Sequence[StoredEdit],
    ) -> tuple[bool, str | None]:
        """Replace one session from the compatibility operation-list shape."""

        try:
            normalized_date, normalized_session_id = self._normalize_session_key(
                date,
                session_id,
            )
            normalized_edits = normalize_session_edits(edits)
            delete_frames: list[int] = []
            has_delete_operation = False
            interpolation: tuple[int, int, int] | None = None

            for edit in normalized_edits:
                if edit["operation"] == "delete":
                    if has_delete_operation:
                        raise ValueError(
                            "replace_edits accepts one already-flattened delete "
                            "list; multiple sequential delete groups cannot be "
                            "merged safely"
                        )
                    delete_frames = list(edit["frames"])
                    has_delete_operation = True
                else:
                    if interpolation is not None:
                        raise ValueError(
                            "one session row can contain only one interpolation"
                        )
                    interpolation = (
                        _normalize_integer(edit["start_frame"], "start_frame"),
                        _normalize_integer(edit["end_frame"], "end_frame"),
                        _normalize_integer(
                            edit["frames_between"],
                            "frames_between",
                        ),
                    )

            with self._connect(self.database_file) as database_connection:
                database_connection.execute("BEGIN IMMEDIATE")
                if not delete_frames and interpolation is None:
                    database_connection.execute(
                        """
                        DELETE FROM session_edits
                        WHERE date = ? AND session_id = ?
                        """,
                        (normalized_date, normalized_session_id),
                    )
                else:
                    interp_start_end = (
                        None
                        if interpolation is None
                        else _encode_json(interpolation[:2])
                    )
                    interp_frames_between = (
                        None if interpolation is None else interpolation[2]
                    )
                    database_connection.execute(
                        """
                        INSERT INTO session_edits (
                            date,
                            session_id,
                            delete_frames,
                            interp_start_end,
                            interp_frames_between
                        )
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(date, session_id) DO UPDATE SET
                            delete_frames = excluded.delete_frames,
                            interp_start_end = excluded.interp_start_end,
                            interp_frames_between = excluded.interp_frames_between
                        """,
                        (
                            normalized_date,
                            normalized_session_id,
                            _encode_json(delete_frames),
                            interp_start_end,
                            interp_frames_between,
                        ),
                    )
        except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as edit_error:
            return False, str(edit_error)

        return True, None

    def clear_interpolations(
        self,
        date: str | int,
        session_id: str | int,
    ) -> tuple[bool, str | None]:
        """Clear the interpolation columns while retaining delete_frames."""

        try:
            normalized_date, normalized_session_id = self._normalize_session_key(
                date,
                session_id,
            )
            with self._connect(self.database_file) as database_connection:
                database_connection.execute("BEGIN IMMEDIATE")
                update_cursor = database_connection.execute(
                    """
                    UPDATE session_edits
                    SET interp_start_end = NULL,
                        interp_frames_between = NULL
                    WHERE date = ?
                      AND session_id = ?
                      AND interp_start_end IS NOT NULL
                    """,
                    (normalized_date, normalized_session_id),
                )
                if update_cursor.rowcount == 0:
                    return False, "no saved interpolation to clear"

                # An interpolation-only record becomes an empty record after the
                # update, so remove it to keep checkSaved/getSessionInfo truthful.
                database_connection.execute(
                    """
                    DELETE FROM session_edits
                    WHERE date = ?
                      AND session_id = ?
                      AND json_array_length(delete_frames) = 0
                      AND interp_start_end IS NULL
                    """,
                    (normalized_date, normalized_session_id),
                )
        except (OSError, sqlite3.Error, TypeError, ValueError) as edit_error:
            return False, str(edit_error)

        return True, None


def normalize_delete_frames(frames: Sequence[int]) -> list[int]:
    if isinstance(frames, (str, bytes)):
        raise TypeError("frames must be a sequence of integers")

    normalized_frames = [
        _normalize_integer(frame, "delete frame")
        for frame in frames
    ]

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
            start_frame = _normalize_integer(edit["start_frame"], "start_frame")
            end_frame = _normalize_integer(edit["end_frame"], "end_frame")
            frames_between = _normalize_integer(
                edit["frames_between"],
                "frames_between",
            )
            if not 0 <= start_frame < end_frame:
                raise ValueError(
                    "interpolation requires 0 <= start_frame < end_frame"
                )
            if frames_between < 0:
                raise ValueError(
                    "frames_between must be greater than or equal to zero"
                )
            normalized_edits.append(
                {
                    "operation": "interpolate",
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "frames_between": frames_between,
                }
            )
        else:
            raise ValueError(
                f"edit {edit_index} has unknown operation: {operation!r}"
            )
    return normalized_edits


class SessionEdit:
    """Bind one date/recording to its flattened edit parameters."""

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

    def getSessionInfo(
        self,
        *,
        target: list | np.ndarray | None = None,
    ) -> (
        tuple[bool, list[str]]
        | tuple[tuple[bool, list[str]], list | np.ndarray]
    ):
        session_edit = self._edit_store.get_session_edit(
            self.date,
            self.num_of_rec,
        )

        if session_edit is None:
            if target is None:
                return False, []
            return (False, []), target

        if target is None:
            return True, session_edit


        delete_frames = session_edit["delete_frames"]
        if delete_frames:
            target = np.delete(target, delete_frames)

        interp_start_end = session_edit["interp_start_end"]

        if interp_start_end is not None:
            start_frame, end_frame = interp_start_end
            frames_between = session_edit["interp_frames_between"]

            target = interp_replace(target, start_frame, end_frame, new_length=frames_between+2)

        return (True, session_edit), target

    def getDelete(self) -> list[int]:
        """Return the one flat list of original target indices to delete."""

        session_edit = self._edit_store.get_session_edit(
            self.date,
            self.num_of_rec,
        )
        return [] if session_edit is None else list(session_edit["delete_frames"])

    def getInterp(self) -> list[tuple[int, int, int]]:
        """Return zero or one interpolation tuple for notebook compatibility."""

        session_edit = self._edit_store.get_session_edit(
            self.date,
            self.num_of_rec,
        )
        if session_edit is None or session_edit["interp_start_end"] is None:
            return []

        start_frame, end_frame = session_edit["interp_start_end"]
        frames_between = session_edit["interp_frames_between"]
        if frames_between is None:
            raise ValueError("saved interpolation is missing interp_frames_between")
        return [(start_frame, end_frame, frames_between)]

    def checkSaved(self) -> tuple[bool, str | None]:
        saved_edit = self._edit_store.get_session_edit(
            self.date,
            self.num_of_rec,
        )
        if saved_edit is None:
            return False, f"no saved edits for {self.date} rec {self.num_of_rec}"
        return True, None

    def deleteByRange(
        self,
        startFrame: int,
        endFrame: int | None,
    ) -> tuple[bool, str | None]:
        """Append the frames excluded by target[startFrame:endFrame]."""

        try:
            normalized_start = _normalize_integer(startFrame, "startFrame")
            normalized_end = (
                None
                if endFrame is None
                else _normalize_integer(endFrame, "endFrame")
            )
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
        """Append exact original target indices to the saved flat delete list."""

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
        """Set the one interpolation measured after all saved deletes."""

        try:
            normalized_start = _normalize_integer(startFrame, "startFrame")
            normalized_end = _normalize_integer(endFrame, "endFrame")
            normalized_count = _normalize_integer(
                framesInBetween,
                "framesInBetween",
            )
        except (TypeError, ValueError) as edit_error:
            return False, str(edit_error)

        if not 0 <= normalized_start < normalized_end:
            return False, "interpolation requires 0 <= startFrame < endFrame"
        if normalized_count < 0:
            return False, "framesInBetween must be greater than or equal to zero"

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
    if SessionEditStore.has_legacy_operation_schema(database_path):
        raise RuntimeError(
            "legacy ordered session-edit schema detected; it cannot be flattened "
            "safely without remapping sequential delete indices. Back up or "
            "migrate the database before using the flat session_edits schema: "
            f"{Path(database_path).expanduser().resolve()}"
        )

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
    """Return one session-bound edit object."""

    edit_store = _check_session_edit_database(database_path)
    return SessionEdit(edit_store, date, num_of_rec)


__all__ = [
    "SessionEdit",
    "check_session_edits",
    "interp_replace",
]

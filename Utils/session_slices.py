from __future__ import annotations

"""Store one Python slice for each recording session."""

from pathlib import Path
import sqlite3
from typing import TypeAlias


SliceBounds: TypeAlias = tuple[int, int | None]


class SessionSliceStore:
    """Read and write session slice boundaries in a SQLite database.

    ``mouse_id`` is always the name of the database's parent folder. For
    example, ``data/m14/session_slices.sqlite3`` uses ``mouse_id="m14"``.
    """

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
        return sqlite3.connect(database_uri, uri=True, timeout=30)

    def get(self, date: str, session_id: str) -> SliceBounds | None:
        """Return the saved ``(slice_start, slice_end)``, or ``None``."""

        normalized_date = str(date).strip()
        normalized_session_id = str(session_id).strip()
        if not normalized_date or not normalized_session_id:
            return None
        if not self.is_database_initialized(self.database_file):
            return None

        try:
            with self._connect(self.database_file) as database_connection:
                saved_slice = database_connection.execute(
                    """
                    SELECT slice_start, slice_end
                    FROM session_slices
                    WHERE mouse_id = ? AND date = ? AND session_id = ?
                    """,
                    (self.mouse_id, normalized_date, normalized_session_id),
                ).fetchone()
        except sqlite3.Error:
            return None

        if saved_slice is None:
            return None

        slice_start = int(saved_slice[0])
        slice_end = None if saved_slice[1] is None else int(saved_slice[1])
        return slice_start, slice_end

    @staticmethod
    def is_database_initialized(database_file: str | Path) -> bool:
        """Return ``True`` only when the file and ``session_slices`` table exist."""

        database_path = Path(database_file).expanduser().resolve()
        if not database_path.is_file():
            return False

        try:
            with SessionSliceStore._connect(database_path) as database_connection:
                table_exists = database_connection.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'session_slices'
                    """
                ).fetchone()
        except (OSError, sqlite3.Error):
            return False

        return table_exists is not None

    @staticmethod
    def create_database(database_file: str | Path) -> bool:
        """Create the database and table; return whether initialization succeeded."""

        database_path = Path(database_file).expanduser().resolve()

        try:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            with SessionSliceStore._connect(database_path) as database_connection:
                database_connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS session_slices (
                        mouse_id TEXT NOT NULL,
                        date TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        last_changed_at TEXT NOT NULL,
                        slice_start INTEGER NOT NULL,
                        slice_end INTEGER,
                        PRIMARY KEY (mouse_id, date, session_id)
                    )
                    """
                )
        except (OSError, sqlite3.Error):
            return False

        return SessionSliceStore.is_database_initialized(database_path)

    def write(
        self,
        date: str,
        session_id: str,
        slice_start: int,
        slice_end: int | None,
    ) -> tuple[bool, str | None]:
        """Insert or overwrite a slice; return ``(success, error_message)``."""

        normalized_date = str(date).strip()
        normalized_session_id = str(session_id).strip()

        try:
            normalized_slice_start = int(slice_start)
            normalized_slice_end = None if slice_end is None else int(slice_end)
        except (TypeError, ValueError) as conversion_error:
            return False, str(conversion_error)

        if not normalized_date:
            return False, "date cannot be empty"
        if not normalized_session_id:
            return False, "session_id cannot be empty"
        if not self.is_database_initialized(self.database_file):
            return False, f"database is not initialized: {self.database_file}"

        try:
            with self._connect(self.database_file) as database_connection:
                database_connection.execute(
                    """
                    INSERT INTO session_slices (
                        mouse_id, date, session_id, created_at,
                        last_changed_at, slice_start, slice_end
                    )
                    VALUES (
                        ?, ?, ?,
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        ?, ?
                    )
                    ON CONFLICT(mouse_id, date, session_id) DO UPDATE SET
                        last_changed_at = strftime(
                            '%Y-%m-%dT%H:%M:%fZ', 'now'
                        ),
                        slice_start = excluded.slice_start,
                        slice_end = excluded.slice_end
                    """,
                    (
                        self.mouse_id,
                        normalized_date,
                        normalized_session_id,
                        normalized_slice_start,
                        normalized_slice_end,
                    ),
                )
        except sqlite3.Error as database_error:
            return False, str(database_error)

        return True, None



def check_session_slices(database_path: str | Path) -> SessionSliceStore:
    if not SessionSliceStore.is_database_initialized(database_path):
        database_was_created = SessionSliceStore.create_database(database_path)
        if not database_was_created:
            raise RuntimeError(
                f"Could not initialize session-slice database: {database_path}"
            )
    session_slice_store = SessionSliceStore(database_path)
    return session_slice_store


__all__ = ["SessionSliceStore", "SliceBounds", "check_session_slices"]

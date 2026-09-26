"""The application's macOS recent documents, managed by AppKit."""

from __future__ import annotations

import ctypes
import os
import sys
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path


class _MacRecentDocuments:
    def __init__(self) -> None:
        self._appkit = ctypes.CDLL(
            "/System/Library/Frameworks/AppKit.framework/AppKit"
        )
        self._objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        self._objc.objc_getClass.argtypes = (ctypes.c_char_p,)
        self._objc.objc_getClass.restype = ctypes.c_void_p
        self._objc.sel_registerName.argtypes = (ctypes.c_char_p,)
        self._objc.sel_registerName.restype = ctypes.c_void_p
        self._message_address = ctypes.cast(
            self._objc.objc_msgSend, ctypes.c_void_p
        ).value

    def _send(
        self, receiver, selector, result_type=ctypes.c_void_p,
        argument_types=(), *arguments,
    ):
        # objc_msgSend must use each method's concrete ABI on Apple Silicon.
        call = ctypes.CFUNCTYPE(
            result_type, ctypes.c_void_p, ctypes.c_void_p, *argument_types,
        )(self._message_address)
        return call(receiver, self._objc.sel_registerName(selector), *arguments)

    @contextmanager
    def _pool(self):
        pool = self._send(self._objc.objc_getClass(b"NSAutoreleasePool"), b"new")
        try:
            yield
        finally:
            self._send(pool, b"drain", None)

    def _controller(self):
        return self._send(
            self._objc.objc_getClass(b"NSDocumentController"),
            b"sharedDocumentController",
        )

    def list(self) -> list[Path]:
        with self._pool():
            urls = self._send(self._controller(), b"recentDocumentURLs")
            count = self._send(urls, b"count", ctypes.c_ulong)
            paths = []
            for index in range(count):
                url = self._send(
                    urls, b"objectAtIndex:", ctypes.c_void_p,
                    (ctypes.c_ulong,), index,
                )
                if self._send(url, b"isFileURL", ctypes.c_bool):
                    path = self._send(url, b"fileSystemRepresentation", ctypes.c_char_p)
                    paths.append(Path(os.fsdecode(path)))
            return paths

    def record(self, path: Path) -> None:
        with self._pool():
            string = self._send(
                self._objc.objc_getClass(b"NSString"), b"stringWithUTF8String:",
                ctypes.c_void_p, (ctypes.c_char_p,), str(path).encode("utf-8"),
            )
            url = self._send(
                self._objc.objc_getClass(b"NSURL"), b"fileURLWithPath:",
                ctypes.c_void_p, (ctypes.c_void_p,), string,
            )
            self._send(
                self._controller(), b"noteNewRecentDocumentURL:",
                None, (ctypes.c_void_p,), url,
            )

    def clear(self) -> None:
        with self._pool():
            self._send(
                self._controller(), b"clearRecentDocuments:",
                None, (ctypes.c_void_p,), None,
            )


@lru_cache(maxsize=1)
def _mac_recent_documents() -> _MacRecentDocuments:
    return _MacRecentDocuments()


def list_recent_documents() -> list[Path]:
    """Return native recent files in AppKit order; call on the Tk main thread."""

    if sys.platform != "darwin":
        return []
    return _mac_recent_documents().list()


def record_recent_document(path: str | Path) -> None:
    """Record a successfully opened document on the Tk main thread."""

    if sys.platform == "darwin":
        _mac_recent_documents().record(Path(path).expanduser().resolve())


def clear_recent_documents() -> None:
    """Clear this application's native recent list on the Tk main thread."""

    if sys.platform == "darwin":
        _mac_recent_documents().clear()

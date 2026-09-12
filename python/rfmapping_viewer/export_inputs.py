"""Input identity, atomic CSV publication, and export jobs."""

from __future__ import annotations

import csv
import errno
import hashlib
import os
import stat
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk

from rfmapping_viewer.constants import _USE_PATH_CSV_PUBLICATION


@dataclass(frozen=True)
class FrozenFileIdentity:
    """Stable identity of a regular input captured while its data is loaded."""

    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    mode: int
    handle_device: int
    handle_inode: int
    handle_size: int
    handle_mtime_ns: int
    handle_mode: int

    @classmethod
    def capture(cls, path: str | Path) -> FrozenFileIdentity:
        source = Path(path).expanduser().resolve(strict=True)
        path_before = os.stat(source, follow_symlinks=False)
        if not stat.S_ISREG(path_before.st_mode):
            raise ValueError(f"Scientific input is not a regular file: {source}")
        descriptor = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            handle_info = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        path_after = os.stat(source, follow_symlinks=False)
        if not stat.S_ISREG(handle_info.st_mode):
            raise ValueError(f"Scientific input is not a regular file: {source}")
        if (
            handle_info.st_size != path_after.st_size
            or cls.path_signature(path_after) != cls.path_signature(path_before)
        ):
            raise ValueError(f"Scientific input changed while it was opened: {source}")
        return cls(
            source,
            int(path_after.st_dev),
            int(path_after.st_ino),
            int(path_after.st_size),
            int(path_after.st_mtime_ns),
            int(path_after.st_ctime_ns),
            int(stat.S_IFMT(path_after.st_mode)),
            int(handle_info.st_dev),
            int(handle_info.st_ino),
            int(handle_info.st_size),
            int(handle_info.st_mtime_ns),
            int(stat.S_IFMT(handle_info.st_mode)),
        )

    @staticmethod
    def path_signature(info: os.stat_result) -> tuple[int, ...]:
        return (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(info.st_ctime_ns) if os.name != "nt" else 0,
            int(stat.S_IFMT(info.st_mode)),
        )

    def matches(self, info: os.stat_result) -> bool:
        stable_fields_match = (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(stat.S_IFMT(info.st_mode)),
        ) == (
            self.device,
            self.inode,
            self.size,
            self.mtime_ns,
            self.mode,
        )
        # Windows reports creation/change timestamps inconsistently between
        # path stat and an open file handle.  Size, mtime, file identity, and
        # the provenance digest still detect scientific-input mutations.
        return stable_fields_match and (
            os.name == "nt" or int(info.st_ctime_ns) == self.ctime_ns
        )

    def matches_open_file(self, info: os.stat_result) -> bool:
        return (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(stat.S_IFMT(info.st_mode)),
        ) == (
            self.handle_device,
            self.handle_inode,
            self.handle_size,
            self.handle_mtime_ns,
            self.handle_mode,
        )

    @staticmethod
    def open_file_signature(info: os.stat_result) -> tuple[int, ...]:
        return (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(stat.S_IFMT(info.st_mode)),
        )

    def verify_path(self) -> None:
        try:
            info = os.stat(self.path, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError(f"Scientific input is no longer available: {self.path}") from exc
        if not self.matches(info):
            raise RuntimeError(
                f"Scientific input changed after it was loaded; reopen it before exporting: {self.path}"
            )

    def metadata(self, sha256: str) -> dict[str, object]:
        return {
            "path": str(self.path),
            "sha256": sha256,
            "sizeBytes": self.size,
            "device": self.device,
            "inode": self.inode,
            "mtimeNs": self.mtime_ns,
            "ctimeNs": self.ctime_ns,
        }


def _hash_frozen_file(
    identity: FrozenFileIdentity,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    """Hash exactly the frozen input, cooperatively aborting stale previews."""

    def check_cancelled() -> None:
        if cancelled is not None and cancelled():
            raise RuntimeError("Preview superseded by a newer recipe")

    check_cancelled()
    identity.verify_path()
    descriptor = os.open(
        identity.path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if not identity.matches_open_file(before):
            raise RuntimeError(
                f"Scientific input changed after it was loaded; reopen it before exporting: {identity.path}"
            )
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            check_cancelled()
            digest.update(chunk)
        check_cancelled()
        after = os.fstat(descriptor)
        if (
            not identity.matches_open_file(after)
            or identity.open_file_signature(after)
            != identity.open_file_signature(before)
        ):
            raise RuntimeError(
                f"Scientific input changed while provenance was computed: {identity.path}"
            )
    finally:
        os.close(descriptor)
    identity.verify_path()
    return digest.hexdigest()


def _export_executor(root: tk.Misc) -> ThreadPoolExecutor:
    executor = getattr(root, "_rfm_export_executor", None)
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rfmap-export")
        root._rfm_export_executor = executor
        root._rfm_export_jobs = {}
        root._rfm_export_jobs_lock = threading.Lock()
    return executor


def _submit_daemon_future(action: Callable[[], object], *, name: str) -> Future:
    """Run cancellable preview work without keeping the interpreter alive."""

    future: Future = Future()

    def run() -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            result = action()
        except BaseException as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)

    threading.Thread(target=run, name=name, daemon=True).start()
    return future


def _register_export_job(root: tk.Misc, viewer: object, future: Future) -> None:
    _export_executor(root)
    with root._rfm_export_jobs_lock:
        root._rfm_export_jobs[future] = viewer


def _unregister_export_job(root: tk.Misc, future: Future | None) -> None:
    if future is None:
        return
    lock = getattr(root, "_rfm_export_jobs_lock", None)
    if lock is None:
        return
    with lock:
        root._rfm_export_jobs.pop(future, None)


def _active_export_jobs(root: tk.Misc, viewer: object | None = None) -> tuple[Future, ...]:
    jobs = getattr(root, "_rfm_export_jobs", {})
    lock = getattr(root, "_rfm_export_jobs_lock", None)
    if lock is None:
        return ()
    with lock:
        return tuple(
            future
            for future, owner in jobs.items()
            if viewer is None or owner is viewer
        )


def _shutdown_export_executor(root: tk.Misc) -> None:
    executor = getattr(root, "_rfm_export_executor", None)
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)
        root._rfm_export_executor = None


def _path_is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _path_stat_signature(result: os.stat_result | None) -> tuple | None:
    if result is None:
        return None
    return (
        result.st_dev,
        result.st_ino,
        stat.S_IFMT(result.st_mode),
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _path_lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_csv_path_backend(
    target: Path,
    write_rows: Callable[[csv.writer], None],
    *,
    before_publish: Callable[[], None] | None,
) -> Path:
    """Windows-safe sibling staging and atomic file replacement."""

    parent = target.parent if str(target.parent) else Path(".")
    parent_before = _path_lstat(parent)
    if (
        parent_before is None
        or _path_is_link_like(parent)
        or not stat.S_ISDIR(parent_before.st_mode)
    ):
        raise ValueError("CSV parent must be a real directory")
    existing = _path_lstat(target)
    if existing is not None and (
        _path_is_link_like(target) or not stat.S_ISREG(existing.st_mode)
    ):
        raise ValueError("CSV destination must be a regular file")
    existing_signature = _path_stat_signature(existing)
    temporary_path = parent / f".{target.name}.tmp-{uuid.uuid4().hex}"
    descriptor: int | None = os.open(
        temporary_path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            descriptor = None
            write_rows(csv.writer(stream))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, 0o660)
        staged = temporary_path.lstat()
        staged_digest = _file_sha256(temporary_path)
        if before_publish is not None:
            before_publish()
        current = _path_lstat(target)
        if _path_stat_signature(current) != existing_signature:
            raise RuntimeError("CSV destination changed while the export was being written")
        parent_now = _path_lstat(parent)
        if (
            parent_now is None
            or _path_is_link_like(parent)
            or (
                parent_now.st_dev,
                parent_now.st_ino,
                stat.S_IFMT(parent_now.st_mode),
            )
            != (
                parent_before.st_dev,
                parent_before.st_ino,
                stat.S_IFMT(parent_before.st_mode),
            )
        ):
            raise RuntimeError("CSV parent directory changed while the export was being written")
        try:
            os.replace(temporary_path, target)
        except OSError:
            published = _path_lstat(target)
            if (
                temporary_path.exists()
                or published is None
                or published.st_size != staged.st_size
                or _file_sha256(target) != staged_digest
            ):
                raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
    return target


def _atomic_write_csv(
    destination: str | Path,
    write_rows: Callable[[csv.writer], None],
    *,
    before_publish: Callable[[], None] | None = None,
) -> Path:
    """Publish one complete CSV atomically.

    Failures before ``os.replace`` preserve the previous destination. A lost
    replace reply is recognized from the staged inode. A later durability
    failure is reported explicitly even though the complete new file is visible.
    """

    target = Path(destination).expanduser()
    if not target.name or target.name in {".", ".."}:
        raise ValueError("CSV destination must name a file")
    if _USE_PATH_CSV_PUBLICATION:
        return _atomic_write_csv_path_backend(
            target,
            write_rows,
            before_publish=before_publish,
        )
    parent = target.parent if str(target.parent) else Path(".")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(parent, directory_flags)
    temporary = f".{target.name}.tmp-{uuid.uuid4().hex}"
    descriptor: int | None = None
    try:
        try:
            existing = os.stat(target.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise ValueError("CSV destination must be a regular file")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            descriptor = None
            write_rows(csv.writer(stream))
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o660)
            os.fsync(stream.fileno())
        if before_publish is not None:
            before_publish()
        try:
            current = os.stat(target.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if (existing is None) != (current is None) or (
            existing is not None
            and current is not None
            and (
                existing.st_dev,
                existing.st_ino,
                stat.S_IFMT(existing.st_mode),
                existing.st_size,
                existing.st_mtime_ns,
                existing.st_ctime_ns,
            )
            != (
                current.st_dev,
                current.st_ino,
                stat.S_IFMT(current.st_mode),
                current.st_size,
                current.st_mtime_ns,
                current.st_ctime_ns,
            )
        ):
            raise RuntimeError("CSV destination changed while the export was being written")
        parent_now = os.stat(parent, follow_symlinks=False)
        parent_open = os.fstat(directory_fd)
        if (parent_now.st_dev, parent_now.st_ino) != (parent_open.st_dev, parent_open.st_ino):
            raise RuntimeError("CSV parent directory changed while the export was being written")
        staged = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
        try:
            os.replace(
                temporary,
                target.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        except OSError:
            # CIFS/NFS can commit the rename and lose only its success reply.
            # Treat that as success iff the complete staged inode is now the
            # destination and the temporary name disappeared.
            try:
                published = os.stat(
                    target.name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                published = None
            try:
                os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
                temporary_still_exists = True
            except FileNotFoundError:
                temporary_still_exists = False
            if (
                published is None
                or temporary_still_exists
                or (published.st_dev, published.st_ino, published.st_size)
                != (staged.st_dev, staged.st_ino, staged.st_size)
            ):
                raise
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            unsupported = {errno.EINVAL, errno.EOPNOTSUPP}
            if hasattr(errno, "ENOTSUP"):
                unsupported.add(errno.ENOTSUP)
            if exc.errno not in unsupported:
                raise RuntimeError(
                    "CSV was atomically published, but directory durability "
                    "could not be confirmed"
                ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(directory_fd)
    return target

# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Durable atomic file writes and lightweight inter-process locking.

Temporary files are created beside their destination so ``os.replace`` stays
on one filesystem. Callers that perform a read-modify-write transaction should
hold :class:`InterProcessLock` for the whole transaction.
"""

from __future__ import annotations

import errno
import os
import stat
import tempfile
import time
from pathlib import Path
from types import TracebackType

_UNSUPPORTED_DIRECTORY_FSYNC = {
    errno.EACCES,
    errno.EBADF,
    errno.EINVAL,
    errno.EISDIR,
    errno.ENOTSUP,
    errno.EPERM,
}
_LOCK_BUSY_ERRNOS = {errno.EACCES, errno.EAGAIN}
if hasattr(errno, "EDEADLK"):
    _LOCK_BUSY_ERRNOS.add(errno.EDEADLK)


class LockTimeoutError(TimeoutError):
    """Raised when an inter-process lock cannot be acquired in time."""


def atomic_write_bytes(
    path: str | os.PathLike[str],
    data: bytes | bytearray | memoryview,
    *,
    mode: int | None = None,
    create_parents: bool = True,
) -> None:
    """Atomically and durably replace *path* with *data*.

    Existing file permissions are preserved unless *mode* is provided. New
    files default to mode ``0o600`` because ``mkstemp`` creates private files.
    """
    destination = Path(path)
    parent = destination.parent
    if create_parents:
        parent.mkdir(parents=True, exist_ok=True)

    existing_mode = _existing_mode(destination) if mode is None else None
    temporary: Path | None = None
    descriptor = -1
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=parent,
        )
        temporary = Path(temporary_name)
        if mode is not None:
            os.chmod(temporary, mode)
        elif existing_mode is not None:
            os.chmod(temporary, existing_mode)

        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temporary, destination)
        temporary = None
        _fsync_directory(parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def atomic_write_text(
    path: str | os.PathLike[str],
    text: str,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
    newline: str | None = None,
    mode: int | None = None,
    create_parents: bool = True,
) -> None:
    """Encode *text* and atomically replace *path*."""
    if newline is not None:
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)
    atomic_write_bytes(
        path,
        text.encode(encoding, errors),
        mode=mode,
        create_parents=create_parents,
    )


class InterProcessLock:
    """Advisory file lock implemented with ``flock`` or ``msvcrt.locking``."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        timeout: float | None = None,
        poll_interval: float = 0.05,
    ) -> None:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative or None")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self.path = Path(path)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._descriptor: int | None = None

    @property
    def acquired(self) -> bool:
        return self._descriptor is not None

    def acquire(self) -> None:
        """Acquire the lock, waiting until *timeout* when configured."""
        if self.acquired:
            raise RuntimeError("lock is already acquired")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                _prepare_windows_lock_file(descriptor)
            deadline = None if self.timeout is None else time.monotonic() + self.timeout
            while True:
                try:
                    _try_lock(descriptor)
                    self._descriptor = descriptor
                    return
                except OSError as exc:
                    if exc.errno not in _LOCK_BUSY_ERRNOS:
                        raise
                    if deadline is not None and time.monotonic() >= deadline:
                        raise LockTimeoutError(
                            f"timed out acquiring lock: {self.path}"
                        ) from exc
                    delay = self.poll_interval
                    if deadline is not None:
                        delay = min(delay, max(0.0, deadline - time.monotonic()))
                    time.sleep(delay)
        except BaseException:
            os.close(descriptor)
            raise

    def release(self) -> None:
        """Release the lock and close its file descriptor."""
        descriptor = self._descriptor
        if descriptor is None:
            raise RuntimeError("lock is not acquired")
        self._descriptor = None
        try:
            _unlock(descriptor)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "InterProcessLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def _existing_mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return None


def _fsync_directory(directory: Path) -> None:
    """Sync directory metadata where the host supports directory handles."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(directory, flags)
    except OSError as exc:
        if exc.errno in _UNSUPPORTED_DIRECTORY_FSYNC:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in _UNSUPPORTED_DIRECTORY_FSYNC:
                raise
    finally:
        os.close(descriptor)


def _prepare_windows_lock_file(descriptor: int) -> None:
    if os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b"\0")
        os.fsync(descriptor)
    os.lseek(descriptor, 0, os.SEEK_SET)


def _try_lock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)

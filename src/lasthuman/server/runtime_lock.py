"""Exclusive process locking and non-following runtime path validation."""

from __future__ import annotations

import os
import re
import stat
import sys
from itertools import combinations
from pathlib import Path
from threading import Lock
from types import TracebackType
from typing import TextIO

from .registration import RegistrationOperationalError

RUNTIME_LOCK_NAME = ".lasthuman-runtime.lock"
IMPORT_JOURNAL_GLOB = ".lasthuman-import-*.json"
_HELD_LOCK_PATHS: set[Path] = set()
_HELD_LOCKS_LOCK = Lock()
_PID_CONTENT = re.compile(rb"[1-9][0-9]{0,19}\n")


class StatePathError(RegistrationOperationalError):
    """Mutable state must never follow a link into another tenant or source DB."""


def validate_state_path(path: Path, *, directory: bool = False) -> None:
    absolute = path.absolute()
    if ".." in absolute.parts:
        raise StatePathError("runtime paths must not contain parent traversal")
    for current in (*reversed(absolute.parents), absolute):
        try:
            details = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(details.st_mode) or (
            getattr(details, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise StatePathError("runtime paths and their ancestors must not be symlinks or reparse points")
        if current != absolute or directory:
            if not stat.S_ISDIR(details.st_mode):
                raise StatePathError("runtime directory path is not a directory")
        elif not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise StatePathError("runtime files must be regular files without hardlinks")


def reject_path_aliases(*paths: Path) -> None:
    for left, right in combinations(paths, 2):
        try:
            same_path = left.resolve(strict=False) == right.resolve(strict=False)
        except RuntimeError as error:
            raise StatePathError("runtime paths cannot contain symlink loops") from error
        if same_path:
            raise StatePathError("source, registry, destination and runtime lock paths must differ")
        try:
            aliases = left.samefile(right)
        except FileNotFoundError:
            aliases = False
        if aliases:
            raise StatePathError("source, registry, destination and runtime lock paths must differ")


def prepare_private_directory(path: Path) -> None:
    validate_state_path(path, directory=True)
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for current in reversed(missing):
        current.mkdir(mode=0o700, exist_ok=True)
    validate_state_path(path, directory=True)
    if sys.platform != "win32":
        details = path.lstat()
        if details.st_uid != os.getuid():
            raise StatePathError("runtime directory must be owned by the current user")
        if stat.S_IMODE(details.st_mode) != 0o700:
            os.chmod(path, 0o700)


def pending_import_journals(state_root: Path) -> tuple[Path, ...]:
    return tuple(sorted(state_root.glob(IMPORT_JOURNAL_GLOB)))


class DataDirectoryLock:
    def __init__(
        self, state_root: str | Path, *, name: str = RUNTIME_LOCK_NAME, allow_pending_import: bool = False,
    ) -> None:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise StatePathError("runtime lock name must be a file name")
        self.state_root = Path(state_root)
        self.path = self.state_root / name
        self._allow_pending_import = allow_pending_import
        self._handle: TextIO | None = None
        self._held_path: Path | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        validate_state_path(self.state_root, directory=True)
        validate_state_path(self.path)
        self._check_journal()
        prepare_private_directory(self.state_root)
        path = self.path.resolve()
        handle = _open_lock(self.path)
        with _HELD_LOCKS_LOCK:
            if path in _HELD_LOCK_PATHS:
                handle.close()
                raise RuntimeError("another Last Human gateway already holds the data directory lock")
            _HELD_LOCK_PATHS.add(path)
        try:
            _lock_handle(handle)
            _validate_lock_file(handle, self.path)
            self._check_journal()
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n")
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            with _HELD_LOCKS_LOCK:
                _HELD_LOCK_PATHS.discard(path)
            handle.close()
            raise
        self._handle = handle
        self._held_path = path

    def _check_journal(self) -> None:
        if not self._allow_pending_import and pending_import_journals(self.state_root):
            raise StatePathError("pending legacy import; retry import-legacy without --dry-run before startup")

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            _unlock_handle(handle)
        finally:
            with _HELD_LOCKS_LOCK:
                if self._held_path is not None:
                    _HELD_LOCK_PATHS.discard(self._held_path)
                    self._held_path = None
            handle.close()

    def __enter__(self) -> DataDirectoryLock:
        self.acquire()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None,
    ) -> None:
        self.release()


def _open_lock(path: Path) -> TextIO:
    before = path.lstat() if path.exists() else None
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        details = os.fstat(descriptor)
        after = path.lstat()
        identity = (details.st_dev, details.st_ino)
        same_original = before is None or (before.st_dev, before.st_ino) == identity
        if not all((
            stat.S_ISREG(details.st_mode), details.st_nlink == 1, not stat.S_ISLNK(after.st_mode),
            identity == (after.st_dev, after.st_ino), same_original,
        )):
            raise StatePathError("runtime lock path changed while opening")
        return os.fdopen(descriptor, "r+", encoding="ascii", newline="\n")
    except BaseException:
        os.close(descriptor)
        raise


def _validate_lock_file(handle: TextIO, path: Path) -> None:
    validate_state_path(path)
    details = os.fstat(handle.fileno())
    current = path.lstat()
    if (
        not stat.S_ISREG(details.st_mode) or details.st_nlink != 1
        or (details.st_dev, details.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise StatePathError("runtime lock must be an unchanged regular file without hardlinks")
    if sys.platform != "win32" and (
        details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077
    ):
        raise StatePathError("runtime lock must be private and owned by the current user")
    if details.st_size > 21:
        raise StatePathError("runtime lock contains non-PID data; refusing to overwrite it")
    handle.seek(0)
    content = os.read(handle.fileno(), 22)
    if content and _PID_CONTENT.fullmatch(content) is None:
        raise StatePathError("runtime lock contains non-PID data; refusing to overwrite it")


if sys.platform == "win32":
    import msvcrt  # pylint: disable=import-error

    def _lock_handle(handle: TextIO) -> None:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("another Last Human gateway already holds the data directory lock") from error

    def _unlock_handle(handle: TextIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_handle(handle: TextIO) -> None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("another Last Human gateway already holds the data directory lock") from error

    def _unlock_handle(handle: TextIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

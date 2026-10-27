from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import os
from pathlib import Path
import sys

from verilex_runtime.providers.errors import provider_error


_LOCK_WAIT_SECONDS = 30.0
_LOCK_POLL_SECONDS = 0.05


class ProviderUpdateLock:
    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._local = asyncio.Lock()

    @asynccontextmanager
    async def hold(self):
        async with self._local:
            if self._path is None:
                yield
                return

            descriptor = _open_private_lock_file(self._path)
            locked = False
            try:
                deadline = asyncio.get_running_loop().time() + _LOCK_WAIT_SECONDS
                while not (locked := _try_lock(descriptor)):
                    if asyncio.get_running_loop().time() >= deadline:
                        raise provider_error("provider_busy")
                    await asyncio.sleep(_LOCK_POLL_SECONDS)
                yield
            finally:
                if locked:
                    _unlock(descriptor)
                os.close(descriptor)


def _open_private_lock_file(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT
    for optional_flag in ("O_CLOEXEC", "O_NOFOLLOW"):
        flags |= getattr(os, optional_flag, 0)
    descriptor = os.open(path, flags, 0o600)
    if sys.platform != "win32":
        os.fchmod(descriptor, 0o600)
    elif os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b"\0")
    return descriptor


def _try_lock(descriptor: int) -> bool:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(descriptor: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)

"""Atomic file replacement: temp file in the same directory, fsync, rename (INTERFACES 5.3)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pdr.crashpoints import crashpoint


def atomic_write(path: str | os.PathLike, data: str | bytes, *, crash_name: str = "output.before_rename") -> Path:
    """Replace `path` with `data` so a reader sees the old file or the new one, never a partial one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        crashpoint(crash_name)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    try:  # persist the rename itself
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass
    return path

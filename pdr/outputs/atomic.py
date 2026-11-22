"""Atomic file replacement (INTERFACES 5.3): temp file in `<dir>/.tmp/` (same filesystem), fsync, rename."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pdr.crashpoints import crashpoint

TMP_DIR = ".tmp"  # temp files live here, out of the output listing


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _drop_tmp_dir(d: Path) -> None:
    try:
        (d / TMP_DIR).rmdir()  # only succeeds when empty
    except OSError:
        pass


def cleanup_stale(out_dir: str | os.PathLike) -> int:
    """Delete temp files left by a killed write (and the empty `.tmp/`); returns how many were removed."""
    d = Path(out_dir)
    if not d.is_dir():
        return 0
    stale = list((d / TMP_DIR).glob("*")) if (d / TMP_DIR).is_dir() else []
    stale += list(d.glob(".*.tmp"))  # earlier layout: temp files beside the outputs
    n = 0
    for p in stale:
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    _drop_tmp_dir(d)
    return n


def atomic_write(path: str | os.PathLike, data: str | bytes, *, crash_name: str = "output.before_rename") -> Path:
    """Replace `path` with `data` so a reader sees the old file or the new one, never a partial one."""
    path = Path(path)
    tmp_dir = path.parent / TMP_DIR
    tmp_dir.mkdir(parents=True, exist_ok=True)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".part", dir=tmp_dir)
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
        _drop_tmp_dir(path.parent)
        raise
    _fsync_dir(path.parent)
    _drop_tmp_dir(path.parent)
    return path

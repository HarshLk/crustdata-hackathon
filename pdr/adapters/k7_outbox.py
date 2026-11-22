"""K7 local outbox (D-15): one file per action, <path>/<safe action_id>.json, written by atomic rename."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def safe_name(action_id: str) -> str:
    """File-safe form of an action id (alnum, '-', '_', '.' kept; everything else '_')."""
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in action_id)


TMP_DIR = ".tmp"  # temp files live here, outside the message listing, on the same filesystem


class LocalOutbox:
    """Idempotent by construction: existence of the file is the check, rename is the commit."""

    def __init__(self, path: str | Path = "data/outbox") -> None:
        self.path = Path(path)

    def capabilities(self) -> dict:
        return {"can_check": True, "dedupes": True}

    def _file(self, action_id: str) -> Path:
        return self.path / f"{safe_name(action_id)}.json"

    def exists(self, action_id: str) -> bool:
        return self._file(action_id).is_file()

    def messages(self) -> list[Path]:
        """Only delivered messages: `<action_id>.json` files, never temp files or folders."""
        if not self.path.is_dir():
            return []
        return sorted(p for p in self.path.iterdir()
                      if p.is_file() and p.suffix == ".json" and not p.name.startswith("."))

    files = messages  # older name

    def cleanup(self) -> int:
        """Delete temp files left by a write that was killed before its rename; returns how many."""
        n = 0
        stale = list((self.path / TMP_DIR).glob("*")) if (self.path / TMP_DIR).is_dir() else []
        stale += list(self.path.glob(".tmp-*")) if self.path.is_dir() else []  # pre-.tmp/ layout
        for p in stale:
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
        return n

    def deliver(self, action_id: str, body: dict, attachments: list[Path]) -> None:
        """Write the message once; a second deliver of the same id is a no-op."""
        if self.exists(action_id):
            return
        msg: dict[str, Any] = {**body, "action_id": action_id, "attachments": [str(a) for a in attachments]}
        from pdr import guard  # nothing leaves the machine without the guard (DESIGN 10)
        guard.check("k7", msg, body.get("client_id"))
        tmp_dir = self.path / TMP_DIR
        tmp_dir.mkdir(parents=True, exist_ok=True)
        data = json.dumps(msg, ensure_ascii=False, indent=2, sort_keys=True, default=str).encode("utf-8")
        fd, tmp = tempfile.mkstemp(prefix="msg-", suffix=".part", dir=tmp_dir)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            from pdr.crashpoints import crashpoint
            crashpoint("send.outbox_before_rename")
            target = self._file(action_id)
            if target.exists():  # a concurrent deliver won; keep the first
                os.unlink(tmp)
                return
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        try:  # make the rename durable
            dfd = os.open(self.path, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass


def get_k7(settings: Any) -> Any:
    """K7 adapter named by settings.adapters.k7.kind."""
    k7 = settings.adapters.k7
    if k7.kind == "local_outbox":
        path = Path(k7.path)
        if not path.is_absolute() and os.environ.get("PDR_DATA_DIR") and path.parts[:1] == ("data",):
            path = Path(os.environ["PDR_DATA_DIR"]).joinpath(*path.parts[1:])
        return LocalOutbox(path)
    raise ValueError(f"unknown K7 adapter kind {k7.kind!r}")

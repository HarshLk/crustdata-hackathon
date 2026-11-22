"""Alert sinks: one interface (capabilities / exists / deliver); local file sink by default, WhatsApp off."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

TMP_DIR = ".tmp"


class SinkRejected(Exception):
    """The sink clearly did not accept the message (connection refused, 429, 5xx without id): safe to retry."""


class SinkUnavailable(Exception):
    """The sink is not configured; nothing was attempted."""


def safe_name(key: str) -> str:
    """File-safe form of an alert key."""
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key)


def mask(contact: str | None) -> str:
    """Mask a phone number or address for logs: keep only the last 2 characters."""
    s = str(contact or "")
    return ("*" * max(0, len(s) - 2) + s[-2:]) if s else ""


class LocalAlertSink:
    """One file per alert key under <path>/; temp in <path>/.tmp/ then atomic rename (same as k7_outbox)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def capabilities(self) -> dict:
        return {"can_check": True, "available": True, "name": "local"}

    def _file(self, key: str) -> Path:
        return self.path / f"{safe_name(key)}.json"

    def exists(self, key: str) -> bool:
        return self._file(key).is_file()

    def messages(self) -> list[Path]:
        if not self.path.is_dir():
            return []
        return sorted(p for p in self.path.iterdir() if p.is_file() and p.suffix == ".json" and not p.name.startswith("."))

    def cleanup(self) -> None:
        """Drop temp files left by a write killed before its rename."""
        t = self.path / TMP_DIR
        if t.is_dir():
            for p in t.iterdir():
                try:
                    p.unlink()
                except OSError:
                    pass
            try:
                os.rmdir(t)
            except OSError:
                pass

    def deliver(self, key: str, message: dict) -> None:
        """Write the message once; an existing file means already delivered."""
        if self.exists(key):
            return
        tmp_dir = self.path / TMP_DIR
        tmp_dir.mkdir(parents=True, exist_ok=True)
        data = json.dumps({**message, "alert_key": key}, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        fd, tmp = tempfile.mkstemp(prefix="alert-", suffix=".part", dir=tmp_dir)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data.encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
            target = self._file(key)
            if target.exists():
                os.unlink(tmp)
            else:
                os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        finally:
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass


class WhatsAppSink:
    """STUB: Meta Cloud API sink behind the same interface. Off by default; no credentials -> unavailable, no network.

    The real request (POST graph.facebook.com/<ver>/<phone_id>/messages via pdr.broker.paid_call, provider
    "whatsapp") is not implemented in this build; with credentials present deliver() raises SinkRejected.
    """

    def __init__(self, settings: Any = None, env: dict | None = None) -> None:
        env = os.environ if env is None else env
        self.token = env.get("WHATSAPP_TOKEN") or ""
        self.phone_id = env.get("WHATSAPP_PHONE_ID") or ""

    def capabilities(self) -> dict:
        # neither Meta nor Twilio can be queried by our own id (findings §2): can_check is False
        return {"can_check": False, "available": bool(self.token and self.phone_id), "name": "whatsapp"}

    def exists(self, key: str) -> bool:
        return False

    def cleanup(self) -> None:
        return None

    def deliver(self, key: str, message: dict) -> None:
        if not self.capabilities()["available"]:
            raise SinkUnavailable("whatsapp: no credentials configured")
        raise SinkRejected("whatsapp: provider not implemented in this build (stub)")


def alerts_dir(settings: Any) -> Path:
    """$PDR_DATA_DIR/alerts unless settings.alerts.path is absolute."""
    p = Path(str(getattr(_alerts(settings), "path", "data/alerts") or "data/alerts"))
    if p.is_absolute():
        return p
    base = os.environ.get("PDR_DATA_DIR")
    if base and p.parts[:1] == ("data",):
        return Path(base).joinpath(*p.parts[1:])
    return p


def _alerts(settings: Any) -> Any:
    return getattr(settings, "alerts", None)


def get_sink(settings: Any) -> Any:
    """The configured sink; WhatsApp only when switched on, else the local file sink."""
    a = _alerts(settings)
    if a is not None and str(getattr(a, "sink", "local")) == "whatsapp":
        return WhatsAppSink(settings)
    return LocalAlertSink(alerts_dir(settings))

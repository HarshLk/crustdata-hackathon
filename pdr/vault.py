"""Driver personal data vault: names, phones and licences live only in $PDR_DATA_DIR/vault.db."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS drivers (driver_ref TEXT PRIMARY KEY, client_id TEXT, name TEXT, phone TEXT, licence TEXT);
CREATE TABLE IF NOT EXISTS pii_access_log (id INTEGER PRIMARY KEY, at TEXT, who TEXT, driver_ref TEXT, trip_id TEXT, purpose TEXT);
"""


def data_dir() -> Path:
    """The runtime data folder ($PDR_DATA_DIR, default ./data)."""
    return Path(os.environ.get("PDR_DATA_DIR", "data"))


def vault_path() -> Path:
    return data_dir() / "vault.db"


def _secret() -> bytes:
    env = os.environ.get("PDR_VAULT_SECRET")
    if env:
        return env.encode()
    f = data_dir() / "vault.secret"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_hex(32))
        try:
            f.chmod(0o600)
        except OSError:
            pass
    return f.read_text().strip().encode()


def _connect() -> sqlite3.Connection:
    vault_path().parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(vault_path())
    con.executescript(_SCHEMA)
    return con


def norm_phone(phone: str | None) -> str:
    """Last 10 digits of a phone number ('' if fewer than 10 digits)."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


def make_driver_ref(phone: str | None, name: str | None = None, licence: str | None = None) -> str:
    """HMAC(secret, phone)[:12]; falls back to licence, then name, when no phone is given."""
    key = norm_phone(phone) or re.sub(r"[^A-Z0-9]", "", (licence or "").upper()) or (name or "").strip().lower()
    if not key:
        raise ValueError("driver_ref needs a phone, licence or name")
    return "D" + hmac.new(_secret(), key.encode(), hashlib.sha256).hexdigest()[:11]


def put_driver(client_id: str | None, name: str | None, phone: str | None, licence: str | None = None) -> str | None:
    """Stores a driver's identifiers in the vault and returns the driver_ref (None if nothing given)."""
    name, phone, licence = (name or "").strip() or None, (phone or "").strip() or None, (licence or "").strip() or None
    if not (name or phone or licence):
        return None
    ref = make_driver_ref(phone, name, licence)
    with _connect() as con:
        old = con.execute("SELECT name, phone, licence FROM drivers WHERE driver_ref=?", (ref,)).fetchone()
        if old:
            name, phone, licence = name or old[0], phone or old[1], licence or old[2]
        con.execute("INSERT OR REPLACE INTO drivers VALUES (?,?,?,?,?)", (ref, client_id, name, phone, licence))
    return ref


def all_drivers(client_id: str | None = None) -> list[dict]:
    """Every vault row (optionally one client's). Internal: used by the guard's scan only."""
    if not vault_path().exists():
        return []
    with _connect() as con:
        q = "SELECT driver_ref, client_id, name, phone, licence FROM drivers"
        rows = con.execute(q + (" WHERE client_id=?" if client_id else ""), (client_id,) if client_id else ()).fetchall()
    return [dict(zip(("driver_ref", "client_id", "name", "phone", "licence"), r)) for r in rows]


def vault_read(driver_ref: str, who: str, purpose: str, trip_id: str | None = None) -> dict:
    """Returns the driver's identifiers and writes one pii_access_log row."""
    with _connect() as con:
        con.execute("INSERT INTO pii_access_log (at, who, driver_ref, trip_id, purpose) VALUES (?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(), who, driver_ref, trip_id, purpose))
        r = con.execute("SELECT driver_ref, client_id, name, phone, licence FROM drivers WHERE driver_ref=?",
                        (driver_ref,)).fetchone()
    return dict(zip(("driver_ref", "client_id", "name", "phone", "licence"), r)) if r else {}


def access_log() -> list[dict]:
    if not vault_path().exists():
        return []
    with _connect() as con:
        rows = con.execute("SELECT at, who, driver_ref, trip_id, purpose FROM pii_access_log ORDER BY id").fetchall()
    return [dict(zip(("at", "who", "driver_ref", "trip_id", "purpose"), r)) for r in rows]

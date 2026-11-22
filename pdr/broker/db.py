"""The broker's own connection to the store's database (tables per INTERFACES section 6)."""
from __future__ import annotations

import sqlite3
import threading
import uuid
import weakref
from typing import Any

DDL = [
    "CREATE TABLE IF NOT EXISTS call_intents (cache_key TEXT PRIMARY KEY, provider TEXT, endpoint TEXT, request TEXT, "
    "status TEXT, run_id TEXT, dispute_key TEXT, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS call_cache (cache_key TEXT PRIMARY KEY, status INTEGER, headers TEXT, body TEXT, "
    "stored_at TEXT)",
    "CREATE TABLE IF NOT EXISTS ledger (entry_id TEXT PRIMARY KEY, run_id TEXT, dispute_key TEXT, cache_key TEXT, "
    "body TEXT NOT NULL, cost_inr TEXT, cache_hit INTEGER, started_at TEXT)",
    "CREATE TABLE IF NOT EXISTS throttle (provider TEXT, at REAL)",
]

_local = threading.local()
_mem_lock = threading.Lock()
_mem_uris: dict[int, tuple[str, sqlite3.Connection]] = {}  # id(store) -> (uri, anchor connection)


def db_path(store: Any) -> str:
    return str(getattr(store, "path", ":memory:") or ":memory:")


def is_memory(store_or_path: Any) -> bool:
    p = store_or_path if isinstance(store_or_path, str) else db_path(store_or_path)
    return p == ":memory:" or p.startswith("file:pdr_broker_")


def _target(store_or_path: Any) -> str:
    if isinstance(store_or_path, str):
        return store_or_path
    path = db_path(store_or_path)
    if path != ":memory:":
        return path
    with _mem_lock:  # one private memory db per store object, one connection shared by threads (tx locks)
        if id(store_or_path) not in _mem_uris:
            uri = f"file:pdr_broker_{uuid.uuid4().hex}?mode=memory&cache=shared"
            anchor = sqlite3.connect(uri, uri=True, check_same_thread=False, isolation_level=None)
            for stmt in DDL:
                anchor.execute(stmt)
            _mem_uris[id(store_or_path)] = (uri, anchor)
            try:  # forget the db when the store goes away, so a reused id() never sees stale rows
                weakref.finalize(store_or_path, _mem_uris.pop, id(store_or_path), None)
            except TypeError:
                pass
        return _mem_uris[id(store_or_path)][0]


def connect(store_or_path: Any) -> sqlite3.Connection:
    """Per-thread autocommit connection with the broker tables created if missing."""
    target = _target(store_or_path)
    for uri, anchor in list(_mem_uris.values()):
        if uri == target:
            return anchor
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    conn = conns.get(target)
    if conn is None:
        conn = sqlite3.connect(target, uri=target.startswith("file:"), timeout=30, isolation_level=None,
                               check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=30000")
        for stmt in DDL:
            conn.execute(stmt)
        conns[target] = conn
    return conn


_tx_locks: dict[int, threading.RLock] = {}


class tx:
    """BEGIN IMMEDIATE ... COMMIT (ROLLBACK on error); serialised per connection across threads."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        with _mem_lock:
            self.lock = _tx_locks.setdefault(id(conn), threading.RLock())

    def __enter__(self) -> sqlite3.Connection:
        self.lock.acquire()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
        except BaseException:
            self.lock.release()
            raise
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.lock.release()

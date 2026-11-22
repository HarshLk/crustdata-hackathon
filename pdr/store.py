"""SQLite Store (INTERFACES 3.1, 6): same behaviour as MemStore, durable across kill -9."""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from pdr.codes import STAGE_ORDER, ActionStatus, Stage
from pdr.models import (
    ENTITY_CLASSES,
    Action,
    CheckResult,
    Deduction,
    Verdict,
    canonical_json,
    entity_id,
    entity_kind,
    sha256_hex,
)

GENESIS = "0" * 64
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Two lookup columns per kind (k1, k2); find() narrows on these in SQL, then filters exactly in Python.
LOOKUP_KEYS: dict[str, tuple[str, str]] = {
    "Client": ("legal_name", "crustdata_company_id"),
    "Agreement": ("doc_type", "supersedes"),
    "Term": ("agreement_id", "term_type"),
    "Trip": ("lr_no", "invoice_no"),
    "Deduction": ("event_fp", "deduction_id"),
    "Evidence": ("kind", "term_id"),
    "Verdict": ("dispute_key", "deduction_id"),
    "Approval": ("action_id", "role_required"),
    "Action": ("status", "kind"),
    "SchemaMap": ("source_kind", "header_hash"),
    "LedgerEntry": ("run_id", "dispute_key"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _col(value: Any) -> str | None:
    """Value as stored in a lookup column."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    return canonical_json(value)


class SqliteStore:
    """Store over one SQLite file (WAL, synchronous=FULL, busy_timeout=5000, foreign keys on)."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        c = self._conn
        c.execute("PRAGMA busy_timeout=5000")
        if self.path != ":memory:":
            c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=FULL")
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------- low level
    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One SQLite transaction (BEGIN IMMEDIATE); nested calls join the outer one."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        """Read helper for views (economics, status): rows as dicts."""
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def execute(self, sql: str, params: tuple | list = ()) -> None:
        """Write helper for platform tables (runs, settings_versions); runs in its own transaction."""
        with self.tx() as c:
            c.execute(sql, params)

    # ------------------------------------------------------------- entities
    def _load(self, kind: str, body: str) -> Any:
        return ENTITY_CLASSES[kind].model_validate_json(body)

    def put(self, entity: Any) -> None:
        kind = entity_kind(entity)
        eid = entity_id(entity)
        k1f, k2f = LOOKUP_KEYS.get(kind, ("", ""))
        body = entity.model_dump_json()
        with self.tx() as c:
            if kind == "Verdict":
                row = c.execute("SELECT body FROM entities WHERE kind=? AND id=?", (kind, eid)).fetchone()
                if row is not None:
                    old = self._load(kind, row["body"])
                    # verdicts are immutable; the only permitted change is marking one superseded
                    if old.model_copy(update={"superseded_by": entity.superseded_by}) != entity:
                        raise ValueError(f"verdict {eid} is immutable")
            created = getattr(entity, "created_at", None)
            c.execute(
                "INSERT INTO entities(kind,id,client_id,k1,k2,body,created_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(kind,id) DO UPDATE SET client_id=excluded.client_id, k1=excluded.k1, "
                "k2=excluded.k2, body=excluded.body, created_at=excluded.created_at",
                (kind, eid, _col(getattr(entity, "client_id", None)),
                 _col(getattr(entity, k1f, None)) if k1f else None,
                 _col(getattr(entity, k2f, None)) if k2f else None,
                 body, created.isoformat() if created else None),
            )

    def get(self, cls: Any, id: str) -> Any | None:
        kind = entity_kind(cls)
        rows = self.query("SELECT body FROM entities WHERE kind=? AND id=?", (kind, str(id)))
        return self._load(kind, rows[0]["body"]) if rows else None

    def find(self, cls: Any, **eq: Any) -> list[Any]:
        kind = entity_kind(cls)
        sql, params = "SELECT body FROM entities WHERE kind=?", [kind]
        k1f, k2f = LOOKUP_KEYS.get(kind, ("", ""))
        for f, v in eq.items():
            if v is None or isinstance(v, (bool, list, dict, tuple)):
                continue
            col = "client_id" if f == "client_id" else "k1" if f == k1f else "k2" if f == k2f else None
            if col is not None:
                sql += f" AND {col}=?"
                params.append(_col(v))
        sql += " ORDER BY rowid"
        out = []
        for r in self.query(sql, params):
            obj = self._load(kind, r["body"])
            if all(getattr(obj, f, None) == v for f, v in eq.items()):
                out.append(obj)
        return out

    # ------------------------------------------------------------- stages
    def save_stage(self, dispute_key: str, stage: Stage, inputs_hash: str, results: list[CheckResult],
                   settings_version: str | None = None) -> None:
        stage = Stage(stage)
        payload = json.dumps([r.model_dump(mode="json") for r in results], ensure_ascii=False)
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO stage_results(dispute_key,stage,inputs_hash,results,settings_version,saved_at) "
                "VALUES(?,?,?,?,?,?)",
                (dispute_key, stage.value, inputs_hash, payload, settings_version, _now()),
            )
            row = c.execute("SELECT body FROM entities WHERE kind='Deduction' AND id=?", (dispute_key,)).fetchone()
            if row is not None:
                ded = self._load("Deduction", row["body"])
                if STAGE_ORDER.index(stage) > STAGE_ORDER.index(Stage(ded.stage)):
                    c.execute("UPDATE entities SET body=? WHERE kind='Deduction' AND id=?",
                              (ded.model_copy(update={"stage": stage}).model_dump_json(), dispute_key))

    def load_stage(self, dispute_key: str, stage: Stage, inputs_hash: str) -> list[CheckResult] | None:
        rows = self.query("SELECT results FROM stage_results WHERE dispute_key=? AND stage=? AND inputs_hash=?",
                          (dispute_key, Stage(stage).value, inputs_hash))
        if not rows:
            return None
        return [CheckResult.model_validate(r) for r in json.loads(rows[0]["results"])]

    def unfinished(self) -> list[Deduction]:
        decided = {v.dispute_key for v in self.find(Verdict) if v.superseded_by is None}
        return [d for d in self.find(Deduction) if d.dispute_key not in decided]

    # ------------------------------------------------------------- actions and trail
    def actions_in(self, status: ActionStatus) -> list[Action]:
        return self.find(Action, status=ActionStatus(status))

    def trail_append(self, event: str, actor: str, subject_id: str, detail: dict) -> None:
        with self.tx() as c:
            last = c.execute("SELECT seq, row_hash FROM audit_trail ORDER BY seq DESC LIMIT 1").fetchone()
            row = {
                "seq": (last["seq"] + 1) if last else 1,
                "at": _now(),
                "event": event,
                "actor": actor,
                "subject_id": subject_id,
                "detail": json.loads(canonical_json(detail)),
                "prev_hash": last["row_hash"] if last else GENESIS,
            }
            row["row_hash"] = sha256_hex(canonical_json(row))
            c.execute(
                "INSERT INTO audit_trail(seq,at,event,actor,subject_id,detail,prev_hash,row_hash) VALUES(?,?,?,?,?,?,?,?)",
                (row["seq"], row["at"], event, actor, subject_id, canonical_json(row["detail"]),
                 row["prev_hash"], row["row_hash"]),
            )

    def trail(self) -> list[dict]:
        rows = self.query("SELECT seq,at,event,actor,subject_id,detail,prev_hash,row_hash FROM audit_trail ORDER BY seq")
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r["detail"] is not None else None
        return rows

    def trail_verify(self) -> bool:
        prev = GENESIS
        for row in self.trail():
            body = {k: v for k, v in row.items() if k != "row_hash"}
            if row["prev_hash"] != prev or sha256_hex(canonical_json(body)) != row["row_hash"]:
                return False
            prev = row["row_hash"]
        return True

    # ------------------------------------------------------------- snapshots
    def snapshot_put(self, content_hash: str, url: str, text: str, html_path: str | None) -> None:
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO snapshots(content_hash,url,text,html_path,stored_at) VALUES(?,?,?,?,?)",
                      (content_hash, url, text, html_path, _now()))

    def snapshot_get(self, content_hash: str) -> dict | None:
        rows = self.query("SELECT content_hash,url,text,html_path,stored_at FROM snapshots WHERE content_hash=?",
                          (content_hash,))
        return rows[0] if rows else None


def resolve_data_dir(data_dir: str | os.PathLike | None = None) -> Path:
    """The runtime data directory: argument, else $PDR_DATA_DIR, else ./data (created)."""
    d = Path(data_dir or os.environ.get("PDR_DATA_DIR") or "data")
    d.mkdir(parents=True, exist_ok=True)
    return d


def open_store(data_dir: str | os.PathLike | None = None) -> SqliteStore:
    """Open `<data dir>/state.db` (DESIGN 4)."""
    return SqliteStore(resolve_data_dir(data_dir) / "state.db")


__all__ = ["SqliteStore", "open_store", "resolve_data_dir", "LOOKUP_KEYS", "ENTITY_CLASSES"]

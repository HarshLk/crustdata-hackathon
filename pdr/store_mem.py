"""In-memory Store with the same methods as the SQLite store (INTERFACES 3.1). For tests and hour one."""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
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


class MemStore:
    def __init__(self) -> None:
        self._entities: dict[tuple[str, str], Any] = {}
        self._stages: dict[tuple[str, str, str], list[CheckResult]] = {}
        self._trail: list[dict] = []
        self._snapshots: dict[str, dict] = {}

    # ------------------------------------------------------------- entities
    @contextlib.contextmanager
    def tx(self) -> Iterator[None]:
        yield

    def put(self, entity: Any) -> None:
        kind = entity_kind(entity)
        key = (kind, entity_id(entity))
        if kind == "Verdict" and key in self._entities:
            old = self._entities[key]
            # verdicts are immutable; the only permitted change is marking one superseded
            if old.model_copy(update={"superseded_by": entity.superseded_by}) != entity:
                raise ValueError(f"verdict {key[1]} is immutable")
        self._entities[key] = entity.model_copy(deep=True)

    def get(self, cls: Any, id: str) -> Any | None:
        obj = self._entities.get((entity_kind(cls), str(id)))
        return obj.model_copy(deep=True) if obj is not None else None

    def find(self, cls: Any, **eq: Any) -> list[Any]:
        kind = entity_kind(cls)
        out = []
        for (k, _), obj in self._entities.items():
            if k == kind and all(getattr(obj, f, None) == v for f, v in eq.items()):
                out.append(obj.model_copy(deep=True))
        return out

    # ------------------------------------------------------------- stages
    def save_stage(self, dispute_key: str, stage: Stage, inputs_hash: str, results: list[CheckResult]) -> None:
        stage = Stage(stage)
        with self.tx():
            self._stages[(dispute_key, stage.value, inputs_hash)] = [r.model_copy(deep=True) for r in results]
            ded = self._entities.get(("Deduction", dispute_key))
            if ded is not None and STAGE_ORDER.index(stage) > STAGE_ORDER.index(Stage(ded.stage)):
                self._entities[("Deduction", dispute_key)] = ded.model_copy(update={"stage": stage})

    def load_stage(self, dispute_key: str, stage: Stage, inputs_hash: str) -> list[CheckResult] | None:
        rows = self._stages.get((dispute_key, Stage(stage).value, inputs_hash))
        return [r.model_copy(deep=True) for r in rows] if rows is not None else None

    def unfinished(self) -> list[Deduction]:
        decided = {v.dispute_key for v in self.find(Verdict) if v.superseded_by is None}
        return [d for d in self.find(Deduction) if d.dispute_key not in decided]

    # ------------------------------------------------------------- actions and trail
    def actions_in(self, status: ActionStatus) -> list[Action]:
        return self.find(Action, status=ActionStatus(status))

    def trail_append(self, event: str, actor: str, subject_id: str, detail: dict) -> None:
        row = {
            "seq": len(self._trail) + 1,
            "at": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "actor": actor,
            "subject_id": subject_id,
            "detail": detail,
            "prev_hash": self._trail[-1]["row_hash"] if self._trail else GENESIS,
        }
        row["row_hash"] = sha256_hex(canonical_json(row))
        self._trail.append(row)

    def trail_verify(self) -> bool:
        prev = GENESIS
        for row in self._trail:
            body = {k: v for k, v in row.items() if k != "row_hash"}
            if row["prev_hash"] != prev or sha256_hex(canonical_json(body)) != row["row_hash"]:
                return False
            prev = row["row_hash"]
        return True

    def trail(self) -> list[dict]:
        return [dict(r) for r in self._trail]

    # ------------------------------------------------------------- snapshots
    def snapshot_put(self, content_hash: str, url: str, text: str, html_path: str | None) -> None:
        self._snapshots.setdefault(content_hash, {
            "content_hash": content_hash, "url": url, "text": text, "html_path": html_path,
            "stored_at": datetime.now(timezone.utc).isoformat(),
        })

    def snapshot_get(self, content_hash: str) -> dict | None:
        s = self._snapshots.get(content_hash)
        return dict(s) if s is not None else None


__all__ = ["MemStore", "ENTITY_CLASSES"]

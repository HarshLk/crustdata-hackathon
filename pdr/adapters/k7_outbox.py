"""K7 local outbox (lane C): data/outbox/<action_id>.json. Plain write for now; lane C makes it atomic."""
from __future__ import annotations

import json
from pathlib import Path


class LocalOutbox:
    def __init__(self, path: str | Path = "data/outbox") -> None:
        self.path = Path(path)

    def capabilities(self) -> dict:
        return {"can_check": True, "dedupes": True}

    def _file(self, action_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in action_id)
        return self.path / f"{safe}.json"

    def exists(self, action_id: str) -> bool:
        return self._file(action_id).exists()

    def deliver(self, action_id: str, body: dict, attachments: list[Path]) -> None:
        if self.exists(action_id):
            return  # idempotent on action_id
        self.path.mkdir(parents=True, exist_ok=True)
        msg = {**body, "action_id": action_id, "attachments": [str(a) for a in attachments]}
        self._file(action_id).write_text(json.dumps(msg, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

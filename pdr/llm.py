"""Model adapter (lane A). Stub: returns None ("the check did not finish", never a negative finding).

Goes through paid_call. Prompts live in pdr/prompts/ as files.
"""
from __future__ import annotations

from pydantic import BaseModel

from pdr.contracts import LlmTask, RunContext


def llm_json(task: LlmTask, source_text: str, schema: type[BaseModel], ctx: RunContext) -> BaseModel | None:
    return None

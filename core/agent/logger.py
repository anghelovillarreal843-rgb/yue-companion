"""Bitácora JSONL auditable del agente."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from .models import AgentTask, TaskExecutionResult


class AgentLogger:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, event: str, **payload) -> None:
        record = {"ts": time.time(), "event": event, **payload}
        if not self.path:
            return
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def task_result(self, run_id: str, plan_id: str, task: AgentTask, result: TaskExecutionResult) -> None:
        self.event(
            "task_result",
            run_id=run_id,
            plan_id=plan_id,
            task_id=task.task_id,
            action=task.action,
            params=self._safe_params(task),
            ok=result.ok,
            duration=round(result.duration, 4),
            error=result.error,
            detail=self._safe_detail(task, result.detail),
            attempts=result.attempts,
            retries=max(0, result.attempts - 1),
            skipped=result.skipped,
            recovered=result.recovered,
        )

    @staticmethod
    def _fingerprint(value) -> dict:
        raw = str(value)
        return {
            "chars": len(raw),
            "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
        }

    @classmethod
    def _safe_params(cls, task: AgentTask) -> dict:
        params = dict(task.params)
        for key in ("text", "content", "clipboard", "password", "credential"):
            if key in params:
                params[key] = cls._fingerprint(params[key])
        return {k: v for k, v in params.items() if not str(k).startswith("_")}

    @classmethod
    def _safe_detail(cls, task: AgentTask, detail: str) -> str | dict:
        if task.action in {"type_text", "office_write", "office_read"}:
            return cls._fingerprint(detail)
        return str(detail)[:300]

"""Memoria temporal y transaccional de una ejecución."""
from __future__ import annotations

import threading
import time
import uuid
from copy import deepcopy
from typing import Callable

from .models import AgentTask, TaskExecutionResult


class ContextManager:
    def __init__(self, instruction: str = "", state_probe: Callable[[], dict] | None = None):
        self.run_id = f"run_{uuid.uuid4().hex[:12]}"
        self.instruction = instruction
        self.started_at = time.time()
        self._state_probe = state_probe
        self._lock = threading.RLock()
        self._state = {
            "active_app": "",
            "active_window": "",
            "open_apps": [],
            "active_file": "",
            "current_path": "",
            "active_document": "",
            "generated_text": "",
            "current_process": "",
            "running_tasks": {},
            "last_task_id": "",
            "values": {},
        }
        self._completed_signatures: set[str] = set()
        self._results: dict[str, TaskExecutionResult] = {}

    def scope_for(self, task: AgentTask) -> str:
        with self._lock:
            # La misma escritura en dos documentos diferentes no es repetición.
            if task.action in {"type_text", "office_write", "office_save"}:
                return "|".join((
                    str(self._state.get("active_document", "")),
                    str(self._state.get("active_file", "")),
                    str(self._state.get("active_window", "")),
                ))
            if task.action in {
                "open_app", "open_url", "open_path", "search_web",
                "focus_window", "close_window", "move_window",
            }:
                return "|".join((
                    str(self._state.get("active_window", "")),
                    "::".join(sorted(self._state.get("open_apps", []))),
                ))
            return ""

    def was_executed(self, signature: str) -> bool:
        with self._lock:
            return signature in self._completed_signatures

    def mark_executed(self, signature: str) -> None:
        with self._lock:
            self._completed_signatures.add(signature)

    def before(self, task: AgentTask) -> None:
        with self._lock:
            self._state["running_tasks"][task.task_id] = task.action
            self._state["current_process"] = ", ".join(self._state["running_tasks"].values())
            self._state["last_task_id"] = task.task_id

    def after(self, task: AgentTask, result: TaskExecutionResult) -> None:
        with self._lock:
            self._results[task.task_id] = result
            self._state["running_tasks"].pop(task.task_id, None)
            self._state["current_process"] = ", ".join(self._state["running_tasks"].values())
            if not result.ok:
                return
            p = task.params
            action = task.action
            if action == "open_app":
                self._state["active_app"] = str(p.get("name", ""))
            elif action == "focus_window":
                self._state["active_window"] = str(p.get("title", ""))
            elif action == "open_path":
                value = str(p.get("path", ""))
                self._state["active_file"] = value
                self._state["current_path"] = value
            elif action == "office_create":
                app = str(p.get("app", ""))
                self._state["active_app"] = app
                self._state["active_document"] = f"{app}:documento-activo"
            elif action in {"type_text", "office_write"}:
                text = str(p.get("text", p.get("content", "")))
                self._state["generated_text"] = text
                if p.get("path"):
                    self._state["active_file"] = str(p["path"])
            elif action == "office_save" and p.get("path"):
                self._state["active_file"] = str(p["path"])
                self._state["current_path"] = str(p["path"])
        self.refresh()

    def refresh(self) -> None:
        if self._state_probe is None:
            return
        try:
            observed = self._state_probe() or {}
        except Exception:
            return
        with self._lock:
            if observed.get("active_window"):
                self._state["active_window"] = observed["active_window"]
            if observed.get("windows"):
                self._state["open_apps"] = [
                    str(w.get("title", "")) for w in observed["windows"] if w.get("title")
                ]

    def set_value(self, key: str, value) -> None:
        with self._lock:
            self._state["values"][key] = value

    def get_value(self, key: str, default=None):
        with self._lock:
            return self._state["values"].get(key, default)

    def result_for(self, task_id: str):
        with self._lock:
            return self._results.get(task_id)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "run_id": self.run_id,
                "instruction": self.instruction,
                "started_at": self.started_at,
                **deepcopy(self._state),
                "completed_signatures": len(self._completed_signatures),
            }

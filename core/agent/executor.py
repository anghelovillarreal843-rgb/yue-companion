"""Ejecutor de una única tarea con locks, confirmación y verificación."""
from __future__ import annotations

import time

from .models import AgentTask, TaskExecutionResult


class TaskExecutor:
    def __init__(
        self,
        *,
        registry,
        locks,
        context,
        verifier,
        action_runner,
        logger,
        confirm=None,
        publish=None,
        cancel_check=None,
    ):
        self.registry = registry
        self.locks = locks
        self.context = context
        self.verifier = verifier
        self.action_runner = action_runner
        self.logger = logger
        self.confirm = confirm or (lambda task: "ok")
        self.publish = publish or (lambda result: None)
        self.cancel_check = cancel_check or (lambda: None)

    def execute_once(self, task: AgentTask, plan_id: str) -> TaskExecutionResult:
        self.cancel_check()
        task.attempts += 1
        self.context.before(task)
        started = time.monotonic()
        before = self.verifier.capture(task)
        result = TaskExecutionResult(task.task_id, task.action, False, attempts=task.attempts)
        try:
            decision = self.confirm(task)
            if decision in {"skip", "deny"}:
                result.skipped = True
                result.error = "acción no confirmada"
                result.detail = "acción cancelada por seguridad o permiso"
                return result
            with self.locks.acquire(task.resources, timeout=max(2.0, task.verification.timeout)):
                self.cancel_check()
                if task.action == "wait_for":
                    outcome = self.verifier.verify(task, True, "espera por condición", before)
                    raw_action = task.action
                else:
                    definition = self.registry.get(task.action)
                    runner = definition.handler or self.action_runner
                    raw = runner(task.as_action())
                    if isinstance(raw, dict):
                        raw_action = str(raw.get("action", task.action))
                        raw_ok = bool(raw.get("ok", True))
                        raw_detail = str(raw.get("detail", raw.get("result", "")))
                    elif isinstance(raw, bool):
                        raw_action, raw_ok, raw_detail = task.action, raw, ""
                    elif isinstance(raw, str):
                        raw_action, raw_ok, raw_detail = task.action, True, raw
                    else:
                        raw_action = str(getattr(raw, "action", task.action))
                        raw_ok = bool(getattr(raw, "ok", False))
                        raw_detail = str(getattr(raw, "detail", ""))
                    outcome = self.verifier.verify(task, raw_ok, raw_detail, before)
                result.action = raw_action
                result.ok = outcome.ok
                result.detail = outcome.detail
                result.verification = outcome.observed
                if not outcome.ok:
                    result.error = outcome.detail
        except Exception as exc:
            result.ok = False
            result.error = str(exc)
            result.detail = f"{task.action} · {exc}"
        finally:
            result.duration = time.monotonic() - started
            self.context.after(task, result)
            self.logger.task_result(self.context.run_id, plan_id, task, result)
            self.publish(result)
        return result

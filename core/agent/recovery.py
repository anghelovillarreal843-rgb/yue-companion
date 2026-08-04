"""Recuperación por reintentos y estrategias alternativas registrables."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .models import AgentTask, TaskExecutionResult


@dataclass(slots=True)
class RecoveryDirective:
    mode: str  # retry | replace | ask_user | abort
    reason: str = ""
    replacements: list[AgentTask] = field(default_factory=list)
    retry_original: bool = False


Strategy = Callable[[AgentTask, TaskExecutionResult, object], RecoveryDirective | None]


class RecoveryEngine:
    def __init__(self):
        self._strategies: dict[str, list[Strategy]] = {}
        self._register_defaults()

    def register(self, action: str, strategy: Strategy) -> None:
        self._strategies.setdefault(action, []).append(strategy)

    def decide(self, task: AgentTask, result: TaskExecutionResult, context) -> RecoveryDirective:
        if result.skipped:
            return RecoveryDirective(
                "abort", result.error or result.detail or "acción omitida por el usuario"
            )
        if task.attempts < task.retry.max_attempts:
            return RecoveryDirective("retry", "reintento automático")
        for strategy in self._strategies.get(task.action, []):
            directive = strategy(task, result, context)
            if directive:
                return directive
        return RecoveryDirective(
            "ask_user",
            f"No pude completar «{task.action}» después de {task.attempts} intentos: "
            f"{result.error or result.detail}",
        )

    def _register_defaults(self) -> None:
        def element_to_text(task, result, context):
            name = str(task.params.get("name", "")).strip()
            if not name:
                return None
            replacement = AgentTask(
                action="click_text",
                params={"text": name, "button": task.params.get("button", "left")},
                depends_on=set(task.depends_on),
                allow_repeat=True,
                metadata={"recovery_for": task.task_id},
            )
            return RecoveryDirective("replace", "alternativa OCR", [replacement], retry_original=False)

        def text_to_element(task, result, context):
            text = str(task.params.get("text", "")).strip()
            if not text:
                return None
            replacement = AgentTask(
                action="click_element",
                params={"name": text, "button": task.params.get("button", "left")},
                depends_on=set(task.depends_on),
                allow_repeat=True,
                metadata={"recovery_for": task.task_id},
            )
            return RecoveryDirective("replace", "alternativa UIA", [replacement], retry_original=False)

        self.register("click_element", element_to_text)
        self.register("click_text", text_to_element)

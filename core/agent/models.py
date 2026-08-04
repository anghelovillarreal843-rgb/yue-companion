"""Modelos inmutables y estados del motor autónomo de Control de PC."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_backoff: float = 0.15
    multiplier: float = 1.8
    max_backoff: float = 1.5

    def delay_for(self, attempt: int) -> float:
        if attempt <= 1:
            return 0.0
        return min(self.max_backoff, self.initial_backoff * (self.multiplier ** (attempt - 2)))


@dataclass(slots=True)
class VerificationSpec:
    kind: str = "auto"
    timeout: float = 10.0
    poll_interval: float = 0.12
    expected: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GoalClause:
    text: str
    relation: str = "sequence"
    connector: str = ""
    index: int = 0


@dataclass(slots=True)
class GoalAnalysis:
    original: str
    objective: str
    clauses: list[GoalClause] = field(default_factory=list)
    has_parallelism: bool = False
    has_dependencies: bool = False
    markers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AgentTask:
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:10]}")
    depends_on: set[str] = field(default_factory=set)
    parallel_group: str = ""
    resources: tuple[str, ...] = field(default_factory=tuple)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    verification: VerificationSpec = field(default_factory=VerificationSpec)
    allow_repeat: bool = False
    idempotent: bool = False
    parallel_safe: bool = False
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_action(self) -> dict[str, Any]:
        return {"action": self.action, **self.params}

    def signature(self, scope: str = "") -> str:
        payload = {
            "action": self.action,
            "params": self._stable_params(self.params),
            "scope": scope,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _stable_params(params: dict[str, Any]) -> dict[str, Any]:
        # Campos internos/capturas no forman parte de la identidad lógica.
        return {
            key: value for key, value in params.items()
            if not str(key).startswith("_") and key not in {"timeout", "retry"}
        }


@dataclass(slots=True)
class ExecutionPlan:
    objective: str
    tasks: list[AgentTask]
    analysis: GoalAnalysis
    source: str = "ai"
    plan_id: str = field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:12]}")
    created_at: float = field(default_factory=time.time)
    already_done: bool = False
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        ids = [task.task_id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("El plan contiene IDs de tarea duplicados.")
        known = set(ids)
        for task in self.tasks:
            missing = task.depends_on - known
            if missing:
                raise ValueError(f"{task.task_id} depende de tareas inexistentes: {sorted(missing)}")
            if task.task_id in task.depends_on:
                raise ValueError(f"{task.task_id} no puede depender de sí misma.")
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        graph = {task.task_id: set(task.depends_on) for task in self.tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                raise ValueError("El plan contiene una dependencia circular.")
            visiting.add(node)
            for dep in graph[node]:
                visit(dep)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)


@dataclass(slots=True)
class VerificationOutcome:
    ok: bool
    detail: str = ""
    observed: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TaskExecutionResult:
    task_id: str
    action: str
    ok: bool
    detail: str = ""
    error: str = ""
    duration: float = 0.0
    attempts: int = 1
    skipped: bool = False
    recovered: bool = False
    verification: dict[str, Any] = field(default_factory=dict)

    def as_legacy(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "ok": self.ok,
            "detail": self.detail,
            "task_id": self.task_id,
            "duration": self.duration,
            "attempts": self.attempts,
            "skipped": self.skipped,
            "recovered": self.recovered,
            "error": self.error,
        }


@dataclass(slots=True)
class QueueReport:
    ok: bool
    results: list[TaskExecutionResult]
    completed_task_ids: set[str] = field(default_factory=set)
    failed_task_id: str = ""
    reason: str = ""
    recovery_count: int = 0
    stopped: bool = False

"""Cola DAG: dependencias, parada estricta, concurrencia segura y recuperación."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .models import QueueReport, TaskExecutionResult, TaskStatus


class TaskQueue:
    def __init__(
        self,
        *,
        registry,
        executor,
        recovery,
        context,
        logger,
        max_workers: int = 3,
        allow_parallel: bool = True,
        progress=None,
        cancel_check=None,
    ):
        self.registry = registry
        self.executor = executor
        self.recovery = recovery
        self.context = context
        self.logger = logger
        self.max_workers = max(1, max_workers)
        self.allow_parallel = allow_parallel
        self.progress = progress or (lambda _msg: None)
        self.cancel_check = cancel_check or (lambda: None)

    def run(self, plan) -> QueueReport:
        plan.validate()
        pending = {task.task_id: task for task in plan.tasks}
        completed: set[str] = set()
        results: list[TaskExecutionResult] = []
        recovery_count = 0
        total = len(plan.tasks)
        while pending:
            self.cancel_check()
            ready = [task for task in pending.values() if task.depends_on <= completed]
            if not ready:
                reason = "La cola quedó bloqueada por dependencias no satisfechas."
                return QueueReport(False, results, completed, reason=reason, stopped=True)
            batch = self._safe_batch(ready)
            done_results = self._run_batch(batch, plan.plan_id, len(completed), total)
            # Todos los futures del lote ya terminaron. Se contabilizan TODOS antes
            # de decidir un retry para no repetir una tarea paralela que sí funcionó.
            failures = []
            for task, result in done_results:
                results.append(result)
                if result.ok:
                    task.status = TaskStatus.SKIPPED if result.skipped else TaskStatus.SUCCEEDED
                    completed.add(task.task_id)
                    pending.pop(task.task_id, None)
                    if task.idempotent and not task.allow_repeat:
                        self.context.mark_executed(task.signature(self.context.scope_for(task)))
                else:
                    task.status = TaskStatus.FAILED
                    failures.append((task, result))

            for task, result in failures:
                directive = self.recovery.decide(task, result, self.context)
                if directive.mode == "retry":
                    recovery_count += 1
                    self._retry_backoff(task.retry.delay_for(task.attempts + 1))
                    task.status = TaskStatus.PENDING
                    continue
                if directive.mode == "replace" and directive.replacements:
                    recovery_count += 1
                    alt_ok, alt_results = self._run_replacements(directive.replacements, plan.plan_id)
                    results.extend(alt_results)
                    if alt_ok and not directive.retry_original:
                        result.recovered = True
                        task.status = TaskStatus.SUCCEEDED
                        completed.add(task.task_id)
                        pending.pop(task.task_id, None)
                        continue
                    if alt_ok and directive.retry_original:
                        task.status = TaskStatus.PENDING
                        continue
                reason = directive.reason or result.error or result.detail
                self.logger.event(
                    "queue_stopped", run_id=self.context.run_id, plan_id=plan.plan_id,
                    task_id=task.task_id, reason=reason,
                )
                return QueueReport(
                    False, results, completed, failed_task_id=task.task_id,
                    reason=reason, recovery_count=recovery_count, stopped=True,
                )
            # Sin fallos o con fallos ya programados para retry/recuperados.
            continue
        return QueueReport(True, results, completed, recovery_count=recovery_count)

    def _retry_backoff(self, delay: float) -> None:
        """Backoff cancelable; no se usa para asumir que una UI terminó de cargar."""
        if delay <= 0:
            return
        deadline = time.monotonic() + delay
        while True:
            self.cancel_check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))

    def _run_batch(self, batch, plan_id: str, completed_count: int, total: int):
        if len(batch) == 1:
            task = batch[0]
            if self._dedupe(task, plan_id):
                return [(task, self._dedupe_result(task))]
            task.status = TaskStatus.RUNNING
            self.progress(f"Ejecutando paso {completed_count + 1}/{total}…")
            return [(task, self.executor.execute_once(task, plan_id))]
        pairs = []
        self.progress(f"Ejecutando {len(batch)} tareas independientes…")
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(batch)), thread_name_prefix="yue-agent") as pool:
            future_map = {}
            for task in batch:
                if self._dedupe(task, plan_id):
                    pairs.append((task, self._dedupe_result(task)))
                    continue
                task.status = TaskStatus.RUNNING
                future_map[pool.submit(self.executor.execute_once, task, plan_id)] = task
            for future in as_completed(future_map):
                pairs.append((future_map[future], future.result()))
        # Orden estable para logs/resultados públicos.
        order = {task.task_id: idx for idx, task in enumerate(batch)}
        return sorted(pairs, key=lambda pair: order[pair[0].task_id])

    def _safe_batch(self, ready):
        ready = sorted(ready, key=lambda task: int(task.metadata.get("index", 0)))
        first = ready[0]
        if not self.allow_parallel or not first.parallel_safe or not first.parallel_group:
            return [first]
        batch = [first]
        used = set(first.resources)
        for task in ready[1:]:
            if len(batch) >= self.max_workers:
                break
            if task.parallel_group != first.parallel_group or not task.parallel_safe:
                continue
            if used & set(task.resources):
                continue
            batch.append(task)
            used.update(task.resources)
        return batch

    def _dedupe(self, task, plan_id: str) -> bool:
        if not task.idempotent or task.allow_repeat:
            return False
        signature = task.signature(self.context.scope_for(task))
        if not self.context.was_executed(signature):
            return False
        self.logger.event(
            "task_deduplicated", run_id=self.context.run_id,
            plan_id=plan_id, task_id=task.task_id, action=task.action,
        )
        return True

    @staticmethod
    def _dedupe_result(task):
        return TaskExecutionResult(
            task_id=task.task_id, action=task.action, ok=True,
            detail="omitida: la acción ya se ejecutó en este contexto",
            attempts=task.attempts, skipped=True,
        )

    def _run_replacements(self, replacements, plan_id):
        alt_results = []
        for replacement in replacements:
            self.registry.configure_task(replacement)
            result = self.executor.execute_once(replacement, plan_id)
            result.recovered = result.ok
            alt_results.append(result)
            if not result.ok:
                return False, alt_results
        return True, alt_results

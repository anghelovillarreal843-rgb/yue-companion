"""Orquestador del agente autónomo y fachada de alto nivel."""
from __future__ import annotations

from .planner import PlanningError


class AgentRuntimeError(RuntimeError):
    pass


class AutonomousAgentRuntime:
    def __init__(
        self,
        *,
        planner,
        queue_factory,
        context_factory,
        logger,
        screen_probe,
        max_cycles: int = 2,
        max_actions: int = 24,
        visual_recheck: bool = True,
    ):
        self.planner = planner
        self.queue_factory = queue_factory
        self.context_factory = context_factory
        self.logger = logger
        self.screen_probe = screen_probe
        self.max_cycles = max(1, max_cycles)
        self.max_actions = max(1, max_actions)
        self.visual_recheck = visual_recheck

    def execute(self, instruction: str, *, engine=None, progress=None, initial_history=None) -> dict:
        progress = progress or (lambda _msg: None)
        context = self.context_factory(instruction)
        history: list[str] = list(initial_history or [])
        all_actions: list[dict] = []
        all_task_ids: list[str] = []
        all_results: list[dict] = []
        used_ai = False
        completed = False
        cycles_used = 0
        unreadable_plans = 0
        invalid_plans = 0
        max_blank_plans = 3
        progress("Comprendiendo el objetivo completo…")
        for cycle in range(1, self.max_cycles + 1):
            if len(all_actions) >= self.max_actions:
                break
            cycles_used = cycle
            screen = self.screen_probe()
            try:
                plan = self.planner.create_plan(
                    instruction,
                    engine=engine,
                    screen_info=screen,
                    history=history,
                    max_actions=self.max_actions - len(all_actions),
                    execution_context=context.snapshot(),
                )
            except PlanningError as exc:
                invalid_plans += 1
                history.append(f"ciclo descartado: {exc}")
                self.logger.event(
                    "plan_invalid", run_id=context.run_id, cycle=cycle, error=str(exc)
                )
                if invalid_plans >= max_blank_plans:
                    raise AgentRuntimeError(
                        f"La IA no produjo ni un paso válido en {invalid_plans} intentos. Último motivo: {exc}"
                    ) from exc
                continue
            except Exception as exc:
                unreadable_plans += 1
                history.append(
                    'ciclo anterior: el plan llegó ilegible. Devuelve SOLO JSON válido con una acción por paso.'
                )
                self.logger.event(
                    "plan_unreadable", run_id=context.run_id, cycle=cycle, error=str(exc)
                )
                if unreadable_plans >= max_blank_plans:
                    raise AgentRuntimeError(
                        f"La IA no consiguió darme un plan legible en {unreadable_plans} intentos. Último problema: {exc}"
                    ) from exc
                continue
            used_ai = used_ai or plan.source == "ai"
            self.logger.event(
                "plan_created", run_id=context.run_id, plan_id=plan.plan_id,
                source=plan.source, objective=plan.objective,
                tasks=len(plan.tasks), cycle=cycle,
            )
            if plan.already_done:
                completed = True
                break
            progress("Plan interno listo. Iniciando ejecución verificada…")
            queue = self.queue_factory(context, progress)
            report = queue.run(plan)
            all_actions.extend(task.as_action() for task in plan.tasks)
            all_task_ids.extend(task.task_id for task in plan.tasks)
            all_results.extend(result.as_legacy() for result in report.results)
            history.extend(
                ("[OK] " if result.ok else "[FALLÓ] ") + result.detail
                for result in report.results
            )
            if not report.ok:
                raise AgentRuntimeError(report.reason or "La cola se detuvo por un error no recuperable.")
            if plan.source != "ai" or not self.visual_recheck:
                completed = True
                break
            # El siguiente ciclo es una comprobación/replanificación adaptativa. El
            # planificador recibe todo lo ya hecho y debe devolver done=true si acabó.
            if cycle >= self.max_cycles:
                completed = False
                break
        return {
            # Una ejecución recuperada puede conservar intentos fallidos en el log
            # histórico. Si llegamos aquí, todas las colas terminaron correctamente.
            "ok": True,
            "used_ai": used_ai,
            "completed": completed,
            "cycles": cycles_used,
            "actions": all_actions,
            "_action_task_ids": all_task_ids,
            "results": all_results,
            "context": context.snapshot(),
            "architecture": "autonomous-agent-v1",
        }

    def execute_actions(self, actions, *, objective: str, progress=None) -> dict:
        progress = progress or (lambda _msg: None)
        context = self.context_factory(objective)
        plan = self.planner.plan_from_actions(actions, objective=objective)
        self.logger.event(
            "plan_created", run_id=context.run_id, plan_id=plan.plan_id,
            source=plan.source, objective=plan.objective, tasks=len(plan.tasks), cycle=1,
        )
        queue = self.queue_factory(context, progress)
        report = queue.run(plan)
        return {
            "ok": report.ok,
            "used_ai": False,
            "completed": report.ok,
            "cycles": 1,
            "actions": [task.as_action() for task in plan.tasks],
            "_action_task_ids": [task.task_id for task in plan.tasks],
            "results": [result.as_legacy() for result in report.results],
            "context": context.snapshot(),
            "architecture": "autonomous-agent-v1",
            "reason": report.reason,
        }

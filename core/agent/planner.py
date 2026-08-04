"""Planificador de tareas atómicas con dependencias y metadatos de ejecución."""
from __future__ import annotations

import inspect
import re
from typing import Callable

from .models import AgentTask, ExecutionPlan, RetryPolicy, VerificationSpec
from .parser import ActionParser


class PlanningError(RuntimeError):
    pass


class TaskPlanner:
    def __init__(
        self,
        registry,
        *,
        direct_planner: Callable[[str], list[dict]] | None = None,
        action_validator: Callable[[dict], dict] | None = None,
        parser: ActionParser | None = None,
        default_retries: int = 3,
        default_timeout: float = 10.0,
    ):
        self.registry = registry
        self.direct_planner = direct_planner
        self.action_validator = action_validator
        self.parser = parser or ActionParser()
        self.default_retries = max(1, default_retries)
        self.default_timeout = max(0.5, default_timeout)

    def create_plan(
        self,
        instruction: str,
        *,
        engine=None,
        screen_info: dict | None = None,
        history: list[str] | None = None,
        max_actions: int = 24,
        execution_context: dict | None = None,
    ) -> ExecutionPlan:
        analysis = self.parser.analyze(instruction)
        direct = self._complete_direct_plan(instruction, analysis)
        source = "direct" if direct else "ai"
        response = None
        raw_actions = direct
        if not raw_actions:
            if engine is None:
                raise PlanningError("No existe un plan directo y no hay motor de IA disponible.")
            planning_context = dict(execution_context or {})
            planning_context["goal_analysis"] = {
                "objective": analysis.objective,
                "clauses": [
                    {
                        "text": clause.text,
                        "relation": clause.relation,
                        "connector": clause.connector,
                        "index": clause.index,
                    }
                    for clause in analysis.clauses
                ],
            }
            kwargs = {
                "instruction": instruction,
                "screen_info": screen_info or {},
                "history": history or [],
                "max_actions": max_actions,
                "execution_context": planning_context,
                "available_actions": self.registry.planner_catalog(),
            }
            method = engine.plan_pc_execution if hasattr(engine, "plan_pc_execution") else engine.plan_pc_task
            response = self._invoke_planner(method, kwargs)
            raw_actions = response.get("actions", []) if isinstance(response, dict) else response
        if isinstance(response, dict) and response.get("done") and not raw_actions:
            return ExecutionPlan(
                objective=str(response.get("objective") or instruction),
                tasks=[], analysis=analysis, source=source, already_done=True,
                summary=str(response.get("summary", "")),
            )
        if not isinstance(raw_actions, list) or not raw_actions:
            raise PlanningError("El planificador no produjo acciones atómicas.")
        raw_actions = self._expand_registered(self._flatten(raw_actions))
        if len(raw_actions) > max(1, max_actions):
            raise PlanningError(
                f"El plan atomizado contiene {len(raw_actions)} pasos y supera el límite {max_actions}."
            )
        tasks = self._build_tasks(raw_actions)
        tasks = self._coalesce_idempotent_duplicates(tasks)
        plan = ExecutionPlan(
            objective=str((response or {}).get("objective") or instruction) if isinstance(response, dict) else instruction,
            tasks=tasks,
            analysis=analysis,
            source=source,
            summary=str((response or {}).get("summary", "")) if isinstance(response, dict) else "",
            metadata={"planner_response": bool(response), "clauses": len(analysis.clauses)},
        )
        try:
            plan.validate()
        except ValueError as exc:
            raise PlanningError(f"Plan inválido: {exc}") from exc
        return plan

    def _complete_direct_plan(self, instruction: str, analysis) -> list[dict]:
        """Usa el planner rápido solo cuando cubre TODA la instrucción.

        En órdenes compuestas se planifica cada cláusula por separado. Si una
        sola cláusula no tiene traducción determinista, se descarta el conjunto
        y el planificador semántico recibe la petición original completa. Esto
        impide que un parámetro codicioso absorba «y luego ...» como texto.
        """
        if self.direct_planner is None:
            return []
        clauses = list(analysis.clauses or [])
        if len(clauses) <= 1:
            return list(self.direct_planner(instruction) or [])
        # El paralelismo necesita una lectura semántica completa; no se infiere
        # a partir de reglas rápidas potencialmente incompletas.
        if analysis.has_parallelism or any(
            clause.relation not in {"sequence", "first"} for clause in clauses
        ):
            return []

        combined: list[dict] = []
        previous_clause_end = ""
        for clause_index, clause in enumerate(clauses, 1):
            clause_actions = self._flatten(self.direct_planner(clause.text) or [])
            if not clause_actions:
                return []
            previous_step = previous_clause_end
            for step_index, raw in enumerate(clause_actions, 1):
                if not isinstance(raw, dict):
                    return []
                step = dict(raw)
                task_id = f"direct_c{clause_index}_s{step_index}"
                step["id"] = task_id
                # La ruta determinista es deliberadamente secuencial. Las
                # dependencias internas del fragmento no pueden apuntar fuera.
                step["depends_on"] = [previous_step] if previous_step else []
                combined.append(step)
                previous_step = task_id
            previous_clause_end = previous_step
        return combined

    def plan_from_actions(self, actions: list[dict], objective: str = "rutina") -> ExecutionPlan:
        analysis = self.parser.analyze(objective)
        expanded = self._expand_registered(self._flatten(actions))
        tasks = self._coalesce_idempotent_duplicates(self._build_tasks(expanded))
        plan = ExecutionPlan(objective=objective, tasks=tasks, analysis=analysis, source="stored")
        try:
            plan.validate()
        except ValueError as exc:
            raise PlanningError(f"Plan inválido: {exc}") from exc
        return plan

    def _expand_registered(self, actions: list[dict]) -> list[dict]:
        """Aplica expanders registrados hasta obtener acciones realmente atómicas."""
        pending = list(actions)
        output: list[dict] = []
        expansions = 0
        while pending:
            item = pending.pop(0)
            expanded = self.registry.expand(item) if isinstance(item, dict) else [item]
            if len(expanded) == 1 and expanded[0] is item:
                output.append(item)
                continue
            if len(expanded) == 1 and expanded[0] == item:
                output.append(expanded[0])
                continue
            expansions += 1
            if expansions > 64:
                raise PlanningError("Demasiadas expansiones de acciones; posible ciclo de plugins.")
            pending = list(expanded) + pending
        return output

    def _build_tasks(self, raw_actions: list[dict]) -> list[AgentTask]:
        tasks: list[AgentTask] = []
        raw_id_map: dict[str, str] = {}
        previous_id = ""
        # Todas las tareas de un mismo grupo paralelo deben partir del mismo
        # conjunto de prerrequisitos. Así se evita que una marca ``parallel``
        # desprenda accidentalmente un paso del flujo lógico anterior.
        parallel_group_deps: dict[str, set[str]] = {}
        discarded: list[str] = []
        for index, raw in enumerate(raw_actions):
            if not isinstance(raw, dict):
                discarded.append(f"paso {index + 1}: debe ser un objeto")
                continue
            try:
                validated = self._validate(raw)
                action = str(validated.pop("action", ""))
                if action == "wait" and previous_id and tasks[-1].action in {
                    "open_app", "open_url", "open_path", "office_create"
                }:
                    validated = self._dynamic_wait_from(tasks[-1], validated)
                    action = "wait_for"
                if not self.registry.has(action):
                    raise PlanningError(f"acción no registrada: {action or 'vacía'}")
            except Exception as exc:
                discarded.append(f"paso {index + 1}: {exc}")
                continue
            requested_id = str(raw.get("id") or raw.get("task_id") or f"step_{index + 1}")
            task_id = self._unique_id(requested_id, {t.task_id for t in tasks})
            raw_id_map[requested_id] = task_id
            explicit_deps = raw.get("depends_on", raw.get("depends", []))
            if isinstance(explicit_deps, str):
                explicit_deps = [explicit_deps]
            has_explicit_deps = bool(explicit_deps)
            depends_on = {str(dep) for dep in explicit_deps or [] if str(dep)}
            parallel_group = str(raw.get("parallel_group", "")).strip()

            if has_explicit_deps:
                # La primera declaración explícita fija la barrera común del
                # grupo. Las demás pueden declarar dependencias más precisas.
                if parallel_group and parallel_group not in parallel_group_deps:
                    parallel_group_deps[parallel_group] = set(depends_on)
            elif parallel_group in parallel_group_deps:
                # Los compañeros del grupo heredan la misma barrera y quedan
                # listos simultáneamente, sin depender unos de otros.
                depends_on = set(parallel_group_deps[parallel_group])
            else:
                # ``parallel: true`` sin grupo no tiene semántica de ejecución
                # concurrente por sí solo: conserva el orden secuencial seguro.
                depends_on = {previous_id} if previous_id else set()
                if parallel_group:
                    parallel_group_deps[parallel_group] = set(depends_on)

            retry_count = int(raw.get("max_attempts") or raw.get("retries") or self.default_retries)
            verify = raw.get("verify") if isinstance(raw.get("verify"), dict) else {}
            action_timeout = float(self.registry.get(action).default_timeout or self.default_timeout)
            verification_timeout = float(verify.get("timeout", raw.get("timeout", action_timeout)))
            task = AgentTask(
                action=action,
                params={k: v for k, v in validated.items() if k not in self._meta_keys()},
                task_id=task_id,
                depends_on=depends_on,
                parallel_group=parallel_group,
                retry=RetryPolicy(max_attempts=max(1, min(retry_count, 5))),
                verification=VerificationSpec(
                    kind=str(verify.get("kind", "auto")),
                    timeout=max(0.05, verification_timeout),
                    expected=dict(verify.get("expected", {})) if isinstance(verify.get("expected", {}), dict) else {},
                ),
                allow_repeat=bool(raw.get("allow_repeat", False)),
                metadata={"index": index, "raw_id": requested_id},
            )
            self.registry.configure_task(task)
            tasks.append(task)
            previous_id = task.task_id
        if not tasks:
            reasons = "; ".join(discarded[:4]) or "plan vacío"
            raise PlanningError(f"La IA no produjo ni un paso válido ({reasons}).")
        # Remapea dependencias que usaban IDs del JSON original.
        for task in tasks:
            task.depends_on = {raw_id_map.get(dep, dep) for dep in task.depends_on}
        # Una tarea que depende explícitamente de un paso descartado tampoco es
        # segura. Se elimina junto con cualquier descendiente que quede huérfano.
        changed = True
        while changed:
            changed = False
            known = {task.task_id for task in tasks}
            safe = []
            for task in tasks:
                missing = task.depends_on - known
                if missing:
                    discarded.append(f"{task.task_id}: dependencia descartada {sorted(missing)}")
                    changed = True
                    continue
                safe.append(task)
            tasks = safe
        if not tasks:
            reasons = "; ".join(discarded[:4]) or "dependencias inválidas"
            raise PlanningError(f"La IA no produjo ni un paso válido ({reasons}).")
        return tasks

    def _validate(self, raw: dict) -> dict:
        candidate = dict(raw)
        for key in self._meta_keys():
            candidate.pop(key, None)
        action_name = str(candidate.get("action", ""))
        # Las capacidades registradas con handler propio son plugins completos:
        # no obligan a modificar el validador heredado ni el switch del controlador.
        if self.registry.has(action_name) and self.registry.get(action_name).handler is not None:
            return candidate
        if self.action_validator:
            return dict(self.action_validator(candidate))
        return candidate

    @staticmethod
    def _invoke_planner(method, kwargs: dict):
        """Pasa solo argumentos compatibles con motores antiguos o fakes de test."""
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return method(**kwargs)
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if accepts_kwargs:
            return method(**kwargs)
        supported = {key: value for key, value in kwargs.items() if key in signature.parameters}
        return method(**supported)

    @staticmethod
    def _flatten(actions):
        flat = []
        for item in actions:
            if isinstance(item, dict) and isinstance(item.get("actions"), list) and not item.get("action"):
                flat.extend(TaskPlanner._flatten(item["actions"]))
            else:
                flat.append(item)
        return flat

    @staticmethod
    def _dynamic_wait_from(previous: AgentTask, wait_params: dict) -> dict:
        timeout = float(wait_params.get("seconds", 10.0))
        timeout = max(3.0, min(30.0, timeout * 4.0))
        if previous.action == "open_app":
            return {"condition": "app_ready", "target": previous.params.get("name", ""), "timeout": timeout}
        if previous.action == "office_create":
            return {"condition": "app_ready", "target": previous.params.get("app", ""), "timeout": timeout}
        if previous.action in {"open_url", "open_path"}:
            return {"condition": "ui_stable", "timeout": timeout}
        return {"condition": "state_change", "timeout": timeout}

    def _coalesce_idempotent_duplicates(self, tasks: list[AgentTask]) -> list[AgentTask]:
        """Fusiona solo duplicados adyacentes inequívocamente redundantes.

        Una repetición no adyacente puede ser intencional después de cambiar de
        ventana o documento. El segundo nivel de deduplicación, en TaskQueue,
        usa el contexto observable de ese momento.
        """
        kept: list[AgentTask] = []
        remap: dict[str, str] = {}
        previous_key = ""
        previous_id = ""
        for task in tasks:
            key = task.signature("") if task.idempotent and not task.allow_repeat else ""
            redundant = bool(
                key and previous_key == key and previous_id
                and (not task.depends_on or task.depends_on <= {previous_id})
            )
            if redundant:
                remap[task.task_id] = previous_id
                continue
            kept.append(task)
            previous_key = key
            previous_id = task.task_id
        for task in kept:
            task.depends_on = {
                remap.get(dep, dep) for dep in task.depends_on
                if remap.get(dep, dep) != task.task_id
            }
        return kept

    @staticmethod
    def _meta_keys() -> set[str]:
        return {
            "id", "task_id", "depends_on", "depends", "parallel", "parallel_group",
            "allow_repeat", "max_attempts", "retries", "verify", "timeout",
        }

    @staticmethod
    def _unique_id(value: str, existing: set[str]) -> str:
        base = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "task"
        candidate = base
        number = 2
        while candidate in existing:
            candidate = f"{base}_{number}"
            number += 1
        return candidate

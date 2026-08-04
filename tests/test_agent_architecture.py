"""Pruebas unitarias del motor autónomo modular (sin controlar el PC real)."""
from __future__ import annotations

import threading
import sys
from pathlib import Path
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent import (
    ActionDefinition,
    ActionParser,
    AgentLogger,
    AgentTask,
    ApplicationManager,
    ContextManager,
    LocksManager,
    RecoveryEngine,
    SmartWaiter,
    TaskExecutor,
    TaskPlanner,
    TaskQueue,
    VerificationEngine,
    WindowManager,
    build_default_registry,
)
from core.agent.models import RetryPolicy, VerificationOutcome


class PassVerifier:
    def capture(self, _task):
        return None

    def verify(self, _task, raw_ok, raw_detail, _before):
        return VerificationOutcome(raw_ok, raw_detail)


def make_queue(registry, runner, context=None, recovery=None):
    context = context or ContextManager("prueba")
    logger = AgentLogger()
    executor = TaskExecutor(
        registry=registry,
        locks=LocksManager(),
        context=context,
        verifier=PassVerifier(),
        action_runner=runner,
        logger=logger,
    )
    return TaskQueue(
        registry=registry,
        executor=executor,
        recovery=recovery or RecoveryEngine(),
        context=context,
        logger=logger,
        max_workers=3,
        allow_parallel=True,
    )


def test_parser_detects_sequence_and_parallelism():
    analysis = ActionParser().analyze(
        "Primero abre Word, luego escribe y al mismo tiempo descarga el archivo; finalmente guárdalo"
    )
    assert len(analysis.clauses) >= 4
    assert analysis.has_dependencies
    assert analysis.has_parallelism
    assert any(c.relation == "parallel" for c in analysis.clauses)


def test_planner_builds_atomic_dag_and_dynamic_wait():
    registry = build_default_registry()
    planner = TaskPlanner(registry)
    plan = planner.plan_from_actions([
        {"id": "open", "action": "open_app", "name": "word"},
        {"id": "pause", "action": "wait", "seconds": 2, "depends_on": ["open"]},
        {"id": "write", "action": "type_text", "text": "Hola", "depends_on": ["pause"]},
    ], objective="crear un documento")
    assert [t.action for t in plan.tasks] == ["open_app", "wait_for", "type_text"]
    assert plan.tasks[1].params["condition"] == "app_ready"
    assert plan.tasks[2].depends_on == {plan.tasks[1].task_id}


def test_planner_coalesces_duplicate_idempotent_actions():
    registry = build_default_registry()
    planner = TaskPlanner(registry)
    plan = planner.plan_from_actions([
        {"action": "open_app", "name": "word"},
        {"action": "open_app", "name": "word"},
        {"action": "office_create", "app": "word"},
    ])
    assert [t.action for t in plan.tasks].count("open_app") == 1
    assert len(plan.tasks) == 2


def test_queue_stops_and_does_not_run_dependents_after_failure():
    registry = build_default_registry()
    calls = []

    def runner(step):
        calls.append(step["action"])
        return SimpleNamespace(action=step["action"], ok=False, detail="falló")

    planner = TaskPlanner(registry, default_retries=1)
    plan = planner.plan_from_actions([
        {"id": "one", "action": "press", "key": "enter", "max_attempts": 1},
        {"id": "two", "action": "open_app", "name": "word", "depends_on": ["one"]},
    ])
    report = make_queue(registry, runner).run(plan)
    assert not report.ok and report.stopped
    assert calls == ["press"]
    assert plan.tasks[1].task_id not in report.completed_task_ids


def test_queue_retries_then_continues():
    registry = build_default_registry()
    state = {"calls": 0}

    def runner(step):
        state["calls"] += 1
        ok = state["calls"] >= 2
        return SimpleNamespace(action=step["action"], ok=ok, detail="ok" if ok else "temporal")

    planner = TaskPlanner(registry, default_retries=2)
    plan = planner.plan_from_actions([
        {"action": "press", "key": "enter", "max_attempts": 2},
    ])
    report = make_queue(registry, runner).run(plan)
    assert report.ok
    assert state["calls"] == 2
    assert report.recovery_count == 1


def test_recovery_uses_registered_alternative():
    registry = build_default_registry()
    calls = []

    def runner(step):
        calls.append(step["action"])
        ok = step["action"] == "click_text"
        return SimpleNamespace(action=step["action"], ok=ok, detail="resultado")

    planner = TaskPlanner(registry, default_retries=1)
    plan = planner.plan_from_actions([
        {"action": "click_element", "name": "Aceptar", "max_attempts": 1},
    ])
    report = make_queue(registry, runner).run(plan)
    assert report.ok
    assert calls == ["click_element", "click_text"]
    assert report.recovery_count == 1


def test_context_prevents_repeating_same_text_in_same_document():
    registry = build_default_registry()
    context = ContextManager("texto")
    context.set_value("dummy", True)
    calls = []

    def runner(step):
        calls.append(step)
        return SimpleNamespace(action=step["action"], ok=True, detail="ok")

    planner = TaskPlanner(registry)
    # allow_repeat hace que el planner no fusione; la deduplicación contextual se
    # prueba ejecutando dos planes dentro del mismo contexto.
    first = planner.plan_from_actions([{"action": "type_text", "text": "Hola"}], "p1")
    second = planner.plan_from_actions([{"action": "type_text", "text": "Hola"}], "p2")
    queue = make_queue(registry, runner, context=context)
    assert queue.run(first).ok
    assert queue.run(second).ok
    assert len(calls) == 1


def test_keyboard_lock_serializes_threads():
    locks = LocksManager()
    active = 0
    peak = 0
    guard = threading.Lock()

    def worker():
        nonlocal active, peak
        with locks.acquire(("keyboard",), timeout=2):
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.04)
            with guard:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak == 1


def test_action_registry_is_extensible_without_core_changes():
    registry = build_default_registry()
    registry.register(ActionDefinition("read_sensor", (), parallel_safe=True, verifier="none"))
    planner = TaskPlanner(registry)
    plan = planner.plan_from_actions([{"action": "read_sensor", "name": "cpu"}])
    assert plan.tasks[0].action == "read_sensor"
    assert plan.tasks[0].parallel_safe


def test_application_manager_reuses_open_window():
    class FakeDesktop:
        @staticmethod
        def list_windows(limit=40):
            return [{"title": "Documento - Microsoft Word", "active": False}]

        @staticmethod
        def active_window_title():
            return "Documento - Microsoft Word"

        @staticmethod
        def similarity(a, b):
            a, b = a.lower(), b.lower()
            return 1.0 if a in b or b in a else 0.0

        @staticmethod
        def focus_window(title):
            return title

    windows = WindowManager(FakeDesktop, SmartWaiter())
    apps = ApplicationManager(
        windows,
        {"word": "word"},
        {"word": ("Microsoft Word", "Word")},
    )
    launched = []
    title, reused, _detail = apps.ensure_open("word", lambda name: launched.append(name))
    assert reused
    assert "Word" in title
    assert launched == []


def test_smart_waiter_uses_condition_not_fixed_sleep():
    state = {"ready": False}

    def flip():
        time.sleep(0.05)
        state["ready"] = True

    threading.Thread(target=flip).start()
    started = time.monotonic()
    value = SmartWaiter().until(lambda: state["ready"], timeout=1, interval=0.01)
    elapsed = time.monotonic() - started
    assert value is True
    assert 0.04 <= elapsed < 0.5


def test_registered_handler_executes_without_legacy_switch():
    registry = build_default_registry()
    called = []

    def read_sensor(step):
        called.append(step["name"])
        return {"ok": True, "detail": "42 %"}

    registry.register_action(
        "read_sensor", read_sensor, parallel_safe=True, verifier="none",
        description="Lee un sensor", parameters="name",
    )
    planner = TaskPlanner(registry)
    plan = planner.plan_from_actions([{"action": "read_sensor", "name": "cpu"}])

    def forbidden_legacy_runner(_step):
        raise AssertionError("No debe entrar al switch heredado")

    report = make_queue(registry, forbidden_legacy_runner).run(plan)
    assert report.ok
    assert called == ["cpu"]
    assert report.results[-1].detail == "42 %"


def test_planner_receives_dynamic_catalog_and_execution_context():
    registry = build_default_registry()
    registry.register_action(
        "read_sensor", lambda _step: True, parallel_safe=True, verifier="none",
        description="Lee un sensor", parameters="name",
    )
    captured = {}

    class Engine:
        def plan_pc_execution(self, **kwargs):
            captured.update(kwargs)
            return {
                "objective": "leer sensor",
                "actions": [{"action": "read_sensor", "name": "cpu"}],
            }

    planner = TaskPlanner(registry)
    plan = planner.create_plan(
        "Primero lee el sensor y luego informa",
        engine=Engine(),
        execution_context={"active_window": "Panel"},
    )
    assert plan.tasks[0].action == "read_sensor"
    assert any(item["action"] == "read_sensor" for item in captured["available_actions"])
    assert captured["execution_context"]["active_window"] == "Panel"
    assert captured["execution_context"]["goal_analysis"]["clauses"]


def test_runtime_reports_success_after_recovered_attempt():
    from core.agent import AutonomousAgentRuntime

    registry = build_default_registry()
    planner = TaskPlanner(registry, default_retries=1)
    plan = planner.plan_from_actions([
        {"action": "click_element", "name": "Aceptar", "max_attempts": 1},
    ], objective="aceptar")

    class FixedPlanner:
        def create_plan(self, *_args, **_kwargs):
            return plan

    calls = []

    def runner(step):
        calls.append(step["action"])
        ok = step["action"] == "click_text"
        return SimpleNamespace(action=step["action"], ok=ok, detail="resultado")

    logger = AgentLogger()

    def queue_factory(context, _progress):
        return make_queue(registry, runner, context=context)

    runtime = AutonomousAgentRuntime(
        planner=FixedPlanner(),
        queue_factory=queue_factory,
        context_factory=lambda instruction: ContextManager(instruction),
        logger=logger,
        screen_probe=lambda: {},
        max_cycles=1,
        visual_recheck=False,
    )
    result = runtime.execute("aceptar")
    assert result["ok"] is True
    assert any(item["ok"] is False for item in result["results"])
    assert calls == ["click_element", "click_text"]


def test_parallel_success_is_not_repeated_when_peer_retries():
    registry = build_default_registry()
    calls = {"alpha": 0, "beta": 0}

    def handler_alpha(_step):
        calls["alpha"] += 1
        return {"ok": True, "detail": "alpha ok"}

    def handler_beta(_step):
        calls["beta"] += 1
        return {"ok": calls["beta"] >= 2, "detail": "beta"}

    registry.register_action(
        "alpha", handler_alpha, resources=("resource:alpha",),
        parallel_safe=True, verifier="none",
    )
    registry.register_action(
        "beta", handler_beta, resources=("resource:beta",),
        parallel_safe=True, verifier="none",
    )
    planner = TaskPlanner(registry, default_retries=2)
    plan = planner.plan_from_actions([
        {"action": "alpha", "parallel": True, "parallel_group": "g", "max_attempts": 2},
        {"action": "beta", "parallel": True, "parallel_group": "g", "max_attempts": 2},
    ])
    report = make_queue(registry, lambda _step: False).run(plan)
    assert report.ok
    assert calls == {"alpha": 1, "beta": 2}


def test_denied_task_stops_without_retries():
    registry = build_default_registry()
    planner = TaskPlanner(registry, default_retries=3)
    plan = planner.plan_from_actions([
        {"action": "press", "key": "enter", "max_attempts": 3},
    ])
    context = ContextManager("permiso")
    logger = AgentLogger()
    executor = TaskExecutor(
        registry=registry,
        locks=LocksManager(),
        context=context,
        verifier=PassVerifier(),
        action_runner=lambda _step: (_ for _ in ()).throw(AssertionError("no debe ejecutar")),
        logger=logger,
        confirm=lambda _task: "deny",
    )
    queue = TaskQueue(
        registry=registry, executor=executor, recovery=RecoveryEngine(),
        context=context, logger=logger,
    )
    report = queue.run(plan)
    assert not report.ok
    assert plan.tasks[0].attempts == 1
    assert "confirm" in report.reason or "permiso" in report.reason


def test_parser_does_not_treat_connectors_as_tasks_or_split_quoted_commas():
    analysis = ActionParser().analyze(
        'Abre Word y escribe "Hola, mundo" y luego abre Chrome'
    )
    assert [clause.text for clause in analysis.clauses] == [
        "Abre Word", 'escribe "Hola, mundo"', "abre Chrome",
    ]


def test_direct_planner_never_swallows_uncovered_complex_clause():
    registry = build_default_registry()

    def unsafe_direct(text):
        # Simula una regla antigua codiciosa sobre el texto completo.
        if text.lower().startswith("abre word"):
            return [{"action": "office_write", "app": "word", "text": text}]
        if text.lower().startswith("abre chrome"):
            return [{"action": "open_app", "name": "chrome"}]
        return []

    captured = {}

    class Engine:
        def plan_pc_execution(self, **kwargs):
            captured["instruction"] = kwargs["instruction"]
            return {"actions": [{"action": "open_app", "name": "word"}]}

    planner = TaskPlanner(registry, direct_planner=unsafe_direct)
    instruction = "Abre Word, redacta una introducción y luego abre Chrome"
    plan = planner.create_plan(instruction, engine=Engine())
    assert plan.source == "ai"
    assert captured["instruction"] == instruction
    assert plan.tasks[0].params["name"] == "word"


def test_compound_office_write_is_atomized_with_dependencies():
    registry = build_default_registry()
    planner = TaskPlanner(registry)
    plan = planner.plan_from_actions([
        {
            "id": "draft",
            "action": "office_write",
            "app": "word",
            "text": "Introducción",
            "new_document": True,
            "path": "informe.docx",
        }
    ], objective="crear, escribir y guardar")
    assert [task.action for task in plan.tasks] == [
        "office_create", "office_write", "office_save",
    ]
    assert plan.tasks[1].depends_on == {plan.tasks[0].task_id}
    assert plan.tasks[2].depends_on == {plan.tasks[1].task_id}
    assert "new_document" not in plan.tasks[1].params
    assert "path" not in plan.tasks[1].params
    assert plan.tasks[2].params["path"] == "informe.docx"


def test_custom_expander_adds_atomic_steps_without_planner_changes():
    registry = build_default_registry()
    registry.register_action(
        "prepare_report",
        lambda _step: True,
        verifier="none",
        expander=lambda raw: [
            {"id": "a", "action": "open_app", "name": raw["app"]},
            {"id": "b", "action": "type_text", "text": raw["text"], "depends_on": ["a"]},
        ],
    )
    planner = TaskPlanner(registry)
    plan = planner.plan_from_actions([
        {"action": "prepare_report", "app": "notepad", "text": "Hola"}
    ])
    assert [task.action for task in plan.tasks] == ["open_app", "type_text"]
    assert plan.tasks[1].depends_on == {plan.tasks[0].task_id}


def test_nonadjacent_open_app_runs_again_after_context_changes():
    registry = build_default_registry()
    state = {"active": "Desktop"}
    context = ContextManager(
        "volver a Word",
        state_probe=lambda: {
            "active_window": state["active"],
            "windows": [{"title": state["active"]}],
        },
    )
    calls = []

    def runner(step):
        calls.append(step["name"])
        state["active"] = step["name"].title()
        return SimpleNamespace(action=step["action"], ok=True, detail="ok")

    planner = TaskPlanner(registry)
    plan = planner.plan_from_actions([
        {"action": "open_app", "name": "word"},
        {"action": "open_app", "name": "chrome"},
        {"action": "open_app", "name": "word"},
    ])
    assert len(plan.tasks) == 3
    report = make_queue(registry, runner, context=context).run(plan)
    assert report.ok
    assert calls == ["word", "chrome", "word"]


def test_parallel_group_inherits_common_dependency_barrier():
    registry = build_default_registry()
    registry.register_action(
        "setup_phase", lambda _step: True, resources=("resource:setup",),
        parallel_safe=True, verifier="none",
    )
    registry.register_action(
        "parallel_alpha", lambda _step: True, resources=("resource:alpha",),
        parallel_safe=True, verifier="none",
    )
    registry.register_action(
        "parallel_beta", lambda _step: True, resources=("resource:beta",),
        parallel_safe=True, verifier="none",
    )
    planner = TaskPlanner(registry)
    plan = planner.plan_from_actions([
        {"id": "setup", "action": "setup_phase"},
        {"id": "alpha", "action": "parallel_alpha", "parallel_group": "g"},
        {"id": "beta", "action": "parallel_beta", "parallel_group": "g"},
    ])
    assert plan.tasks[1].depends_on == {plan.tasks[0].task_id}
    assert plan.tasks[2].depends_on == {plan.tasks[0].task_id}
    assert plan.tasks[1].parallel_group == plan.tasks[2].parallel_group == "g"


def test_parallel_flag_without_group_remains_sequential():
    registry = build_default_registry()
    registry.register_action(
        "first_custom", lambda _step: True, parallel_safe=True, verifier="none",
    )
    registry.register_action(
        "second_custom", lambda _step: True, parallel_safe=True, verifier="none",
    )
    planner = TaskPlanner(registry)
    plan = planner.plan_from_actions([
        {"id": "one", "action": "first_custom"},
        {"id": "two", "action": "second_custom", "parallel": True},
    ])
    assert plan.tasks[1].depends_on == {plan.tasks[0].task_id}
    assert plan.tasks[1].parallel_group == ""


def test_wait_for_resource_declares_corresponding_lock():
    registry = build_default_registry()
    planner = TaskPlanner(registry)
    plan = planner.plan_from_actions([
        {"action": "wait_for", "condition": "cursor_available"},
    ])
    assert plan.tasks[0].resources == ("mouse",)


def test_wait_after_url_becomes_dynamic_ui_stability_condition():
    registry = build_default_registry()
    planner = TaskPlanner(registry)
    plan = planner.plan_from_actions([
        {"action": "open_url", "url": "https://example.com"},
        {"action": "wait", "seconds": 2},
    ])
    assert plan.tasks[1].action == "wait_for"
    assert plan.tasks[1].params["condition"] == "ui_stable"


def test_smart_waiter_detects_stability_instead_of_fixed_delay():
    samples = iter(["loading", "loading-2", "ready", "ready", "ready"])
    last = {"value": "ready"}

    def probe():
        try:
            last["value"] = next(samples)
        except StopIteration:
            pass
        return last["value"]

    value = SmartWaiter().until_stable(
        probe, timeout=1, interval=0.01, stable_samples=3,
    )
    assert value == "ready"


def test_registered_action_default_timeout_is_applied():
    registry = build_default_registry()
    registry.register_action(
        "slow_sensor", lambda _step: True, verifier="none", default_timeout=27,
    )
    planner = TaskPlanner(registry, default_timeout=4)
    plan = planner.plan_from_actions([{"action": "slow_sensor"}])
    assert plan.tasks[0].verification.timeout == 27

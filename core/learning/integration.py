"""Puente entre el aprendizaje y el código que ya existe.

Diseñado para que main.py y core/pc_control.py cambien lo mínimo posible: todo
lo que hace falta son cuatro llamadas (try_replay, enrich_history, after_result,
after_error) y ninguna de ellas rompe nada si el paquete falla.
"""
from __future__ import annotations

import threading
import time

import config
from core.learning.lessons import LessonBook
from core.learning.skills import SkillLibrary

_lock = threading.Lock()
_skills: SkillLibrary | None = None
_lessons: LessonBook | None = None


def get_skills() -> SkillLibrary:
    global _skills
    with _lock:
        if _skills is None:
            _skills = SkillLibrary()
        return _skills


def get_lessons(engine=None) -> LessonBook:
    global _lessons
    with _lock:
        if _lessons is None:
            _lessons = LessonBook(engine=engine)
        elif engine is not None and _lessons.engine is None:
            _lessons.engine = engine
        return _lessons


def bind_engine(engine) -> None:
    """Se llama una vez desde main.py para que las lecciones puedan usar el LLM."""
    get_lessons(engine)


# ------------------------------------------------------------------ repetir
def try_replay(pc_controller, instruction: str, progress=None) -> dict | None:
    """Repite una receta conocida. Devuelve un resultado tipo execute() o None.

    None significa "sigue con el flujo normal": o no había receta, o la
    repetición falló (en cuyo caso ya se registró el fallo).
    """
    if not getattr(config, "LEARNING_ENABLED", True):
        return None
    progress = progress or (lambda _msg: None)
    try:
        receta = get_skills().find(instruction)
    except Exception as exc:
        print("[aprendizaje] no pude consultar las habilidades:", exc)
        return None
    if not receta:
        return None

    plan = receta.get("plan") or []
    if not plan:
        return None

    from core import screen_diff
    from core.pc_control import PCControlError

    print(f"[aprendizaje] ── «{instruction}» -> repito receta conocida "
          f"(similitud {receta.get('_similarity')}, {receta.get('successes')} exitos): "
          f"{[p.get('action') for p in plan]}")
    progress("Ya sé hacer esto: repito lo que funcionó la otra vez…")

    ignorar = pc_controller._ignore_rects()
    antes = screen_diff.signature(ignorar)
    try:
        pc_controller._check_instruction(instruction)
        pc_controller._cancel_event.clear()
        descartados: list[str] = []
        acciones = pc_controller._validate_plan(
            plan, remaining=pc_controller.max_actions, discarded=descartados
        )
        if descartados:
            # Una receta guardada no debería traer pasos inválidos: desconfía.
            print("[aprendizaje] la receta traía pasos inválidos:", descartados)
            get_skills().record_failure(instruction)
            return None
        resultados = pc_controller._execute_batch(acciones, progress, 1, 1)
    except PCControlError as exc:
        if "cancelada" in str(exc).lower():
            raise
        print("[aprendizaje] la receta falló:", exc)
        after_error(instruction, f"receta guardada falló: {exc}")   # ya suma el fallo
        return None
    except Exception as exc:
        print("[aprendizaje] error inesperado repitiendo la receta:", exc)
        get_skills().record_failure(instruction)
        return None

    ok = bool(resultados) and all(r.ok for r in resultados)

    # Verificación final: si la pantalla quedó idéntica, no pasó nada útil.
    time.sleep(float(getattr(config, "PC_VERIFY_DELAY", 0.35)))
    despues = screen_diff.signature(ignorar)
    if ok and antes and despues and not screen_diff.changed(antes, despues):
        ok = False
        print("[aprendizaje] la receta no cambió la pantalla.")

    if not ok:
        detalles = "; ".join(r.detail for r in resultados if not r.ok) or "sin efecto visible"
        after_error(instruction, f"receta guardada sin efecto: {detalles}")   # ya suma el fallo
        return None

    get_skills().record_success(instruction, acciones, int(receta.get("avg_cycles", 1) or 1))
    return {
        "ok": True,
        "used_ai": False,
        "completed": True,
        "cycles": 1,
        "actions": acciones,
        "results": [r.__dict__ for r in resultados],
        "from_skill": True,
    }


# ------------------------------------------------------------------ contexto
def enrich_history(instruction: str) -> list[str]:
    """Lecciones relevantes, ya formateadas para el parámetro history."""
    if not getattr(config, "LEARNING_ENABLED", True):
        return []
    try:
        lecciones = get_lessons().relevant(instruction, k=3)
    except Exception as exc:
        print("[aprendizaje] no pude leer las lecciones:", exc)
        return []
    return [f"Lección aprendida: {texto}" for texto in lecciones]


# ------------------------------------------------------------------ hooks
def _receta_aplicable(instruction: str) -> bool:
    """¿Había una receta que SÍ se iba a usar para esta orden?

    Solo así tiene sentido sumarle un fallo: si find() ya la había descartado,
    el error vino del planificador visual y la receta no tiene la culpa.
    """
    try:
        return get_skills().find(instruction) is not None
    except Exception:
        return False


def after_result(instruction: str, result: dict | None) -> None:
    """Hook para _on_pc_done: aprende de lo que salió bien."""
    if not getattr(config, "LEARNING_ENABLED", True) or not isinstance(result, dict):
        return
    try:
        acciones = result.get("actions") or []
        if not acciones:
            return
        if not result.get("ok", False):
            if result.get("from_skill") or _receta_aplicable(instruction):
                get_skills().record_failure(instruction)
            return
        if not result.get("completed", True):
            return                       # llegó al límite de ciclos sin verificar
        if not result.get("used_ai", False) and not result.get("from_skill", False):
            return                       # el camino directo por regex no necesita receta
        get_skills().record_success(instruction, acciones, int(result.get("cycles", 1) or 1))
    except Exception as exc:
        print("[aprendizaje] no pude registrar el éxito:", exc)


def after_error(
    instruction: str,
    error,
    history: list[str] | None = None,
    penalize: bool | None = None,
) -> None:
    """Hook para _on_pc_failed: aprende de lo que salió mal."""
    if not getattr(config, "LEARNING_ENABLED", True):
        return
    texto = str(error or "")
    if "cancelada" in texto.lower():
        return                           # cancelar no es un fallo del plan
    try:
        if penalize is None:
            penalize = _receta_aplicable(instruction)
        if penalize:
            get_skills().record_failure(instruction)
        get_lessons().record(instruction, history or [], texto)
    except Exception as exc:
        print("[aprendizaje] no pude registrar el fallo:", exc)


def stats() -> dict:
    try:
        return {
            "skills": get_skills().stats(),
            "lessons": len(get_lessons().all()),
        }
    except Exception:
        return {"skills": {}, "lessons": 0}

"""Pruebas del núcleo que no requieren abrir la interfaz gráfica.

Se puede correr de dos formas:
    python -m pytest tests/test_core.py     # con pytest
    python tests/test_core.py               # suelto, sin pytest (usa el runner de abajo)
"""
import sys
from pathlib import Path

# Sin esto, «python tests/test_core.py» a secas falla con «No module named 'core'».
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import commands, emotion
from core.pc_control import PCController, PCControlError, looks_like_pc_command


def test_emotion_text_is_hidden():
    assert emotion.clean_response("[Curiosa] Estoy curiosa. Cuéntame más.") == "Cuéntame más."


def test_sad_user_gets_worried_avatar():
    state = emotion.infer_conversation_state(
        "Estoy muy triste y necesito ayuda",
        "Vamos paso a paso. Estoy aquí contigo.",
    )
    assert state.name == "worried"


def test_stop_command():
    assert commands.match("Yue, detente")[0] == "stop_current"


def test_manual_autonomy_command_removed():
    assert commands.match("crea una idea por tu cuenta")[0] is None


def test_direct_pc_shortcuts():
    controller = PCController()
    assert looks_like_pc_command("abre Word y escribe Hola")
    assert controller._direct_plan("selecciona todo") == [
        {"action": "hotkey", "keys": ["ctrl", "a"]}
    ]


def test_dangerous_pc_instruction_is_blocked():
    controller = PCController()
    try:
        controller._check_instruction("desactiva antivirus")
    except PCControlError:
        return
    raise AssertionError("La orden peligrosa no fue bloqueada")


def test_visual_pc_task_rechecks_screen():
    from core.pc_control import ActionResult

    class FakeEngine:
        def __init__(self):
            self.calls = 0

        def plan_pc_task(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "done": False,
                    "summary": "Abrir la aplicación",
                    "actions": [{"action": "open_app", "name": "notepad"}],
                }
            return {"done": True, "summary": "Tarea completada", "actions": []}

    class FakeController(PCController):
        def _screen_info(self):
            return {"width": 1920, "height": 1080, "image_b64": ""}

        def _execute_action(self, step):
            return ActionResult(step["action"], True, self._describe(step))

    engine = FakeEngine()
    controller = FakeController()
    result = controller.execute("realiza una tarea compleja", engine=engine)
    assert result["used_ai"] is True
    assert result["cycles"] == 2
    assert result["completed"] is True
    assert engine.calls == 2


if __name__ == "__main__":
    # Runner mínimo para correr sin pytest: ejecuta cada test_* y resume.
    fallos = []
    for _nombre, _fn in sorted(globals().items()):
        if _nombre.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("  OK  " + _nombre)
            except Exception as _exc:
                print("  FALLO  " + _nombre + f"  ·  {_exc}")
                fallos.append(_nombre)
    print("\n" + ("TODO OK" if not fallos else f"FALLOS: {fallos}"))
    sys.exit(1 if fallos else 0)

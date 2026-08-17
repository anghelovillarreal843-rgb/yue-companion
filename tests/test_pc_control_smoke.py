"""Smoke de core/pc_control.py::PCController (PR 1, REFACTOR_SPEC §6).

Determinista: sin PyAutoGUI ni hardware real (dobles en sys.modules). Gate
obligatorio antes de tocar el módulo (PR 2/PR 5: inyección de PlatformController).
"""
import sys

from core.pc_control import PCController, looks_like_pc_command


def test_deteccion_de_comandos():
    assert looks_like_pc_command("abre chrome") is True
    assert looks_like_pc_command("abre word") is True
    assert looks_like_pc_command("mueve la ventana a la derecha") is True
    assert looks_like_pc_command("hola") is False
    assert looks_like_pc_command("cierra la calculadora") is False


class _FakeGui:
    FAILSAFE = True
    PAUSE = 0.01

    @staticmethod
    def size():
        return (1920, 1080)

    @staticmethod
    def hotkey(*_a, **_k):
        return None

    @staticmethod
    def write(*_a, **_k):
        return None


class _FakeClip:
    @staticmethod
    def paste():
        return ""

    @staticmethod
    def copy(_t):
        return None


def test_type_text_sin_hardware_devuelve_ok(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyautogui", _FakeGui)
    monkeypatch.setitem(sys.modules, "pyperclip", _FakeClip)
    pc = PCController()
    pc.verify_actions = False  # sin verificación de pantalla (determinista)
    paso = pc._validate_step({"action": "type_text", "text": "hola"})
    res = pc._execute_action(paso)
    assert res.ok is True
    assert res.action == "type_text"


def test_validate_step_descarta_accion_ilegal():
    pc = PCController()
    try:
        pc._validate_step({"action": "format_disk"})
    except Exception:
        pass
    else:
        raise AssertionError("una acción ilegal debería lanzar PCControlError")

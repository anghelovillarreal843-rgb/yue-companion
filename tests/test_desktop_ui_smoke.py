"""Smoke de core/desktop_ui (PR 1, REFACTOR_SPEC §6).

Determinista: NO toca pywinauto ni ventanas reales. Este test es el gate
obligatorio antes de tocar el módulo (PR 2: pasa a platform/windows/).
"""
import sys

from core import desktop_ui


def test_norm_quita_tildes_y_colapsa_espacios():
    assert desktop_ui.norm("  ÁBRE   Chrome ") == "abre chrome"
    assert desktop_ui.norm("&Archivo (Ctrl+Shift+E)") == "archivo"
    assert desktop_ui.norm("Explorer (Ctrl+Shift+E)") == "explorer"


def test_similarity_exacta_y_parcial():
    assert desktop_ui.similarity("Notas", "Notas") == 1.0
    assert desktop_ui.similarity("Guardar", "Guardar como...") > 0.6
    assert desktop_ui.similarity("I", "Siete") == 0.0
    assert desktop_ui.similarity("", "Cualquier") == 0.0


def test_is_windows_responde_al_so(monkeypatch):
    monkeypatch.setattr(desktop_ui.platform, "system", lambda: "Windows")
    assert desktop_ui.is_windows() is True
    monkeypatch.setattr(desktop_ui.platform, "system", lambda: "Linux")
    assert desktop_ui.is_windows() is False


def test_available_falso_sin_pywinauto(monkeypatch):
    monkeypatch.setitem(sys.modules, "pywinauto", None)
    assert desktop_ui.available() is False


def test_list_windows_degrada_sin_hardware(monkeypatch):
    monkeypatch.setitem(sys.modules, "pywinauto", None)
    monkeypatch.setitem(sys.modules, "pygetwindow", None)
    assert desktop_ui.list_windows(limit=5) == []


def test_active_title_win32_con_win32_fake(monkeypatch):
    class FakeWin32:
        @staticmethod
        def GetForegroundWindow():
            return 0x1234

        @staticmethod
        def GetWindowText(hwnd):
            return "Ventana Fake"

    monkeypatch.setitem(sys.modules, "win32gui", FakeWin32)
    monkeypatch.setattr(desktop_ui.platform, "system", lambda: "Windows")
    assert desktop_ui._active_title_win32() == "Ventana Fake"

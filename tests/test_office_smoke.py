"""Smoke de core/office_control (PR 1, REFACTOR_SPEC §6).

Determinista: sin COM, sin Office real. Gate obligatorio antes de tocar el
módulo (PR 2: pasa a platform/windows/office.py).
"""
import sys

import pytest

from core.office_control import OfficeController, OfficeUnavailable, available


def test_available_falso_fuera_de_windows(monkeypatch):
    monkeypatch.setattr("core.office_control.platform.system", lambda: "Linux")
    assert available() is False


def test_operar_sin_windows_lanza_unavailable_y_no_importa_com(monkeypatch):
    monkeypatch.setattr("core.office_control.platform.system", lambda: "Linux")
    sys.modules.pop("win32com", None)
    sys.modules.pop("win32com.client", None)
    with pytest.raises(OfficeUnavailable):
        OfficeController(visible=False).create_document("word")
    assert "win32com" not in sys.modules
    assert "win32com.client" not in sys.modules


def test_constructor_sin_com_no_rompe(monkeypatch):
    monkeypatch.setattr("core.office_control.platform.system", lambda: "Linux")
    ctrl = OfficeController(visible=False)
    assert ctrl is not None

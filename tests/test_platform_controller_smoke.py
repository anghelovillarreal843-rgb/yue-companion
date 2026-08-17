"""Smoke test de la capa platforms (PR 2): en Linux arranca degradado y nunca lanza."""
import pytest

from platforms import DegradedController, get_platform_controller
from platforms.contracts import PlatformController


def test_factory_devuelve_controller() -> None:
    c = get_platform_controller()
    assert isinstance(c, PlatformController)


def test_controller_linux_degradado_sin_lanzar() -> None:
    c = get_platform_controller()
    assert c.is_available() is False
    assert c.list_windows() == []
    assert c.active_window_title() == ""
    assert c.active_window_elements() == []
    assert c.find_element_center("cualquiera") is None
    assert c.element_has_focus("cualquiera") is False
    assert c.set_click_through(True) == 0
    assert c.window_snapshot() is None
    assert c.windows_snapshot() == []
    assert c.foreground_app() == ("", "")
    assert c.target_hwnd() == 0


def test_helpers_puros_funcionan_en_cualquier_plataforma() -> None:
    c = get_platform_controller()
    assert c.norm("  HOLA   Mundo ") == "hola mundo"
    assert c.similarity("hola mundo", "hola mundo") == 1.0
    assert c.similarity("hola", "adios") < 0.5
    assert "no hay ventanas" in c.describe_windows([])


def test_dpi_y_launch_degradados_no_lanzan() -> None:
    c = get_platform_controller()
    assert c.set_dpi_awareness() is False
    assert c.launch_app(["C:\\noexiste.exe"]) == ""


def test_degraded_controller_puro() -> None:
    d = DegradedController()
    assert d.is_available() is False
    assert d.list_windows() == []
    assert d.foreground_app() == ("", "")
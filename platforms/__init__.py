"""Capa de abstraccion de plataforma (REFACTOR_SPEC §3).

Factory unica ``get_platform_controller()``: devuelve la implementacion
según el SO actual, con caché de singleton.  NUNCA importa la carpeta
windows en máquinas no-Windows de forma eager: la factory es lazy.

Decision: el paquete se llama ``platforms`` (plural) para no ensombrecer
el modulo stdlib ``platform``.
"""

from __future__ import annotations

import sys

from platforms.contracts import DegradedController, PlatformController

_CONTROLLER: PlatformController | None = None


def get_platform_controller() -> PlatformController:
    """Devuelve el controller de la plataforma actual (singleton)."""
    global _CONTROLLER
    if _CONTROLLER is None:
        system = sys.platform.lower()
        if system == "win32":
            from platforms.windows import WindowsPlatformController
            _CONTROLLER = WindowsPlatformController()
        elif system.startswith("linux"):
            from platforms.linux import LinuxController
            _CONTROLLER = LinuxController()
        else:
            _CONTROLLER = DegradedController()
    return _CONTROLLER


__all__ = ["PlatformController", "DegradedController", "get_platform_controller"]
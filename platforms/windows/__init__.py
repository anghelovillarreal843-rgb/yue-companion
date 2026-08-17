"""Implementacion Windows de PlatformController.

Delega en los modulos de la subcarpeta ``windows``, que contienen el
codigo real (movido en PR 2).  La logica pura (norm/similarity/
describe_windows) la hereda de DegradedController.
"""

from __future__ import annotations

import os
import subprocess

from platforms.contracts import DegradedController
from platforms.windows import desktop_ui as _ui
from platforms.windows import dpi as _dpi
from platforms.windows import media_source as _media


class WindowsPlatformController(DegradedController):
    """Control real de Windows (ventanas, UI, foreground, dpi, launch)."""

    def is_available(self) -> bool:
        return _ui.available()

    def list_windows(self, limit: int = 25) -> list[dict]:
        return _ui.list_windows(limit=limit)

    def active_window_title(self) -> str:
        return _ui.active_window_title()

    def focus_window(self, title: str) -> str:
        return _ui.focus_window(title)

    def close_window(self, title: str) -> str:
        return _ui.close_window(title)

    def active_window_elements(self, limit: int = 40, use_cache: bool = True) -> list[dict]:
        return _ui.active_window_elements(limit=limit, use_cache=use_cache)

    def find_element_center(self, name: str, control_type: str | None = None, threshold: float = 0.75) -> tuple[int, int] | None:
        return _ui.find_element_center(name, control_type=control_type, threshold=threshold)

    def element_has_focus(self, name: str, threshold: float = 0.75) -> bool:
        return _ui.element_has_focus(name, threshold=threshold)

    def set_click_through(self, activar: bool) -> int:
        return _ui.set_click_through(activar)

    def own_window_rects(self) -> list[list[int]]:
        return _ui.own_window_rects()

    def own_hwnds(self) -> list[int]:
        return _ui.own_hwnds()

    def target_hwnd(self) -> int:
        return _ui.target_hwnd()

    def window_snapshot(self, title: str = "") -> dict | None:
        return _ui.window_snapshot(title=title)

    def windows_snapshot(self, limit: int = 40) -> list[dict]:
        return _ui.windows_snapshot(limit=limit)

    def move_window(self, title: str, x: int, y: int, width: int | None = None, height: int | None = None) -> str:
        return _ui.move_window(title, x, y, width=width, height=height)

    def restore_window_snapshot(self, snapshot: dict) -> str:
        return _ui.restore_window_snapshot(snapshot)

    def reopen_window_snapshot(self, snapshot: dict, timeout: float = 6.0) -> str:
        return _ui.reopen_window_snapshot(snapshot, timeout=timeout)

    def foreground_app(self) -> tuple[str, str]:
        return _media.get_foreground_app()

    def set_dpi_awareness(self) -> bool:
        return _dpi.enable_dpi_awareness()

    def launch_app(self, caminos: list[str], args: tuple[str, ...] = ()) -> str:
        for candidato in caminos:
            if not os.path.isfile(candidato):
                continue
            try:
                if hasattr(os, "startfile"):
                    os.startfile(candidato)  # type: ignore[attr-defined]
                    return f"abierto: {candidato}"
                subprocess.Popen([candidato, *[str(a) for a in args]])
                return f"lanzado: {candidato}"
            except Exception as exc:
                return f"no pude abrir {candidato}: {exc}"
        return "no se encontró la aplicación"
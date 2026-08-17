"""Interfaz de control de plataforma (ventanas, UI, foreground, dpi).

Decisiones (REFACTOR_SPEC §3):
- Nombre del paquete es ``platforms`` (plural) para NO ensombrecer el
  modulo stdlib ``platform``, que se usa en todo el codigo.
- La clase de control de una plataforma que NO soporta una operacion
  devuelve valores degradados seguros (None / [] / "" / False), nunca
  lanza.  Asi main.py arranca en cualquier SO.
- Las helpers puras (norm, similarity, describe_windows) NO dependen del
  SO y viven aqui: el resto del codigo no necesita importarlas de la
  carpeta windows.
"""

from __future__ import annotations

import abc
import re


class PlatformController(abc.ABC):
    """Contrato minimo que toda plataforma debe exponer.

    ``is_available()`` indica si el control real existe (True solo en la
    plataforma con implementacion).  El resto de metodos SIEMPRE devuelve
    un valor degradado seguro y nunca lanza, salvo lo documentado en cada
    metodo (p. ej. ``office`` puede confirmar indisponibilidad).
    """

    # -- Ventanas / UI -------------------------------------------------

    @abc.abstractmethod
    def is_available(self) -> bool:
        """True si hay control real de ventanas en esta plataforma."""

    @abc.abstractmethod
    def list_windows(self, limit: int = 25) -> list[dict]:
        """Lista las ventanas visibles (o [] degradado)."""

    @abc.abstractmethod
    def active_window_title(self) -> str:
        """Titulo de la ventana activa (o '' degradado)."""

    @abc.abstractmethod
    def focus_window(self, title: str) -> str:
        """Fuerza el foco a la ventana con ese titulo (mensaje o '')."""

    @abc.abstractmethod
    def close_window(self, title: str) -> str:
        """Cierra la ventana con ese titulo (mensaje o '')."""

    @abc.abstractmethod
    def active_window_elements(self, limit: int = 40, use_cache: bool = True) -> list[dict]:
        """Elementos accesibles de la ventana activa (o [] degradado)."""

    @abc.abstractmethod
    def find_element_center(self, name: str, control_type: str | None = None, threshold: float = 0.75) -> tuple[int, int] | None:
        """Centro (x, y) de un elemento por nombre (o None degradado)."""

    @abc.abstractmethod
    def element_has_focus(self, name: str, threshold: float = 0.75) -> bool:
        """True si el elemento con ese nombre tiene el foco (o False)."""

    @abc.abstractmethod
    def set_click_through(self, activar: bool) -> int:
        """Activa/desactiva click-through (0 = sin efecto / deshabilitado)."""

    @abc.abstractmethod
    def own_window_rects(self) -> list[list[int]]:
        """Rectangulos [x, y, w, h] de las ventanas propias (o [])."""

    @abc.abstractmethod
    def own_hwnds(self) -> list[int]:
        """Handles de las ventanas propias (o [])."""

    @abc.abstractmethod
    def target_hwnd(self) -> int:
        """Handle de la ventana objetivo (0 = ninguno)."""

    @abc.abstractmethod
    def window_snapshot(self, title: str = "") -> dict | None:
        """Snapshot de geometria de una ventana (o None degradado)."""

    @abc.abstractmethod
    def windows_snapshot(self, limit: int = 40) -> list[dict]:
        """Snapshot de todas las ventanas (o [] degradado)."""

    @abc.abstractmethod
    def move_window(self, title: str, x: int, y: int, width: int | None = None, height: int | None = None) -> str:
        """Mueve/redimensiona una ventana (mensaje o '' degradado)."""

    @abc.abstractmethod
    def restore_window_snapshot(self, snapshot: dict) -> str:
        """Restaura una ventana desde un snapshot (mensaje o '')."""

    @abc.abstractmethod
    def reopen_window_snapshot(self, snapshot: dict, timeout: float = 6.0) -> str:
        """Reabre una ventana desde un snapshot (mensaje o '')."""

    # -- Foreground (fuente de media / contexto) ------------------------

    @abc.abstractmethod
    def foreground_app(self) -> tuple[str, str]:
        """(titulo, proceso) de la ventana activa via nativo. Nunca lanza; por defecto ("", "")."""

    # -- Sistema --------------------------------------------------------

    @abc.abstractmethod
    def set_dpi_awareness(self) -> bool:
        """Activa DPI awareness (False = no aplicable/no soportado)."""

    @abc.abstractmethod
    def launch_app(self, caminos: list[str], args: tuple[str, ...] = ()) -> str:
        """Lanza una aplicacion por ruta(s). Devuelve '' si no hay soporte."""

    # -- Helpers puras (sin dependencia de SO) --------------------------

    @abc.abstractmethod
    def norm(self, text: str) -> str:
        """Normaliza texto para comparacion (espacios, minusculas)."""

    @abc.abstractmethod
    def similarity(self, a: str, b: str) -> float:
        """Similitud de texto 0..1."""

    @abc.abstractmethod
    def describe_windows(self, windows: list[dict]) -> str:
        """Describe una lista de ventanas en texto para el asistente."""


class DegradedController(PlatformController):
    """Implementacion degradada universal: todo devuelve valores seguros.

    El stub Linux (platforms/linux) y cualquier SO sin implementacion usan
    este comportamiento: la app arranca, y las operaciones que requieren
    control real simplemente no producen efecto.
    """

    def is_available(self) -> bool:
        return False

    def list_windows(self, limit: int = 25) -> list[dict]:
        return []

    def active_window_title(self) -> str:
        return ""

    def focus_window(self, title: str) -> str:
        return ""

    def close_window(self, title: str) -> str:
        return ""

    def active_window_elements(self, limit: int = 40, use_cache: bool = True) -> list[dict]:
        return []

    def find_element_center(self, name: str, control_type: str | None = None, threshold: float = 0.75) -> tuple[int, int] | None:
        return None

    def element_has_focus(self, name: str, threshold: float = 0.75) -> bool:
        return False

    def set_click_through(self, activar: bool) -> int:
        return 0

    def own_window_rects(self) -> list[list[int]]:
        return []

    def own_hwnds(self) -> list[int]:
        return []

    def target_hwnd(self) -> int:
        return 0

    def window_snapshot(self, title: str = "") -> dict | None:
        return None

    def windows_snapshot(self, limit: int = 40) -> list[dict]:
        return []

    def move_window(self, title: str, x: int, y: int, width: int | None = None, height: int | None = None) -> str:
        return ""

    def restore_window_snapshot(self, snapshot: dict) -> str:
        return ""

    def reopen_window_snapshot(self, snapshot: dict, timeout: float = 6.0) -> str:
        return ""

    def foreground_app(self) -> tuple[str, str]:
        return ("", "")

    def set_dpi_awareness(self) -> bool:
        return False

    def launch_app(self, caminos: list[str], args: tuple[str, ...] = ()) -> str:
        return ""

    # -- Helpers puras: copia canonica, misma semantica en todo SO -------

    def norm(self, text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip().lower()

    def similarity(self, a: str, b: str) -> float:
        from difflib import SequenceMatcher
        return SequenceMatcher(None, self.norm(a), self.norm(b)).ratio()

    def describe_windows(self, windows: list[dict]) -> str:
        if not windows:
            return "no hay ventanas visibles"
        lineas = []
        for i, w in enumerate(windows, 1):
            titulo = w.get("title") or ""
            hwnd = w.get("hwnd") or w.get("handle") or ""
            cls = w.get("class") or w.get("class_name") or ""
            if cls:
                lineas.append(f"{i}. {titulo} [clase {cls}, handle {hwnd}]")
            else:
                lineas.append(f"{i}. {titulo} [handle {hwnd}]")
        return "\n".join(lineas)
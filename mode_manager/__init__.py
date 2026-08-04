"""Gestor de modos de YUE: arquitectura de modos inteligentes, desacoplada.

Público:
    ModeManager  -> fachada principal
    ModeSpec     -> descripción de un modo (id, gatillos, mensajes, handler…)
    RouteResult  -> resultado de `ModeManager.handle(text)`
    DEFAULT_MODE -> id del modo por defecto ("companion")

La personalidad de YUE NO vive aquí; este paquete solo decide qué motor conversa.
"""
from mode_manager.mode_manager import ModeManager, ModeSpec, RouteResult
from mode_manager.mode_state import DEFAULT_MODE

__all__ = ["ModeManager", "ModeSpec", "RouteResult", "DEFAULT_MODE"]

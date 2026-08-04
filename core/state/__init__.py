"""Núcleo de estado de YUE (FASE 2).

Reúne tres piezas que dan orden a la aplicación:
  - EventBus         : que los módulos se avisen sin acoplarse.
  - ResourceManager  : un solo dueño por recurso físico (cámara, micro...).
  - YueStateManager  : única fuente de verdad + arbitraje del avatar.

Todo es ADITIVO: se puede adoptar módulo a módulo sin romper lo existente.
"""
from .events import EventBus
from .resources import Resource, ResourceManager, Lease, ResourceBusy
from .state_manager import YueStateManager, YueState, Priority

__all__ = [
    "EventBus",
    "Resource", "ResourceManager", "Lease", "ResourceBusy",
    "YueStateManager", "YueState", "Priority",
]

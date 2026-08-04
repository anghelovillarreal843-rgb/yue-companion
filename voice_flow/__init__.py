"""Paquete ADITIVO para el flujo de voz de YUE.

No elimina ni reemplaza nada del `core/listener.py` existente. Aporta una capa
de decisión más permisiva y rápida para la INTERRUPCIÓN por voz (barge-in), de
modo que cuando el usuario le vuelve a hablar mientras YUE habla, YUE se detenga
y arranque la nueva conversación —igual que ya ocurre al escribir por texto.

Se integra opcionalmente: si el módulo no está o falla, el listener mantiene su
comportamiento anterior intacto (degradación elegante).
"""
from __future__ import annotations

from .barge_in import BargeInGate, is_interrupt_command

__all__ = ["BargeInGate", "is_interrupt_command"]

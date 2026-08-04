"""Aprendizaje continuo de órdenes de PC para Yue.

Tres piezas, todas aditivas (no tocan la lógica actual de core/pc_control.py ni
de core/ai_engine.py):

    skills.py       biblioteca de recetas que YA funcionaron
    lessons.py      memoria de errores + reflexión del LLM
    integration.py  funciones puente para main.py y PCController

Uso típico desde fuera:

    from core.learning import integration as learning
    resultado = learning.try_replay(controller, "abre el bloc de notas")
"""
from __future__ import annotations

from core.learning import integration, lessons, skills  # noqa: F401

__all__ = ["skills", "lessons", "integration"]

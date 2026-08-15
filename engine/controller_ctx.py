"""ControllerContext: service-locator compartido de los directores.

Regla del refactor (REFACTOR_SPEC §4.1): ningún director recibe referencia
al Controller (main.py); todo dato que pertenece a otra pieza se publica
y lee aquí. Cada PR de extracción agrega sus dependencias a este contexto.

Paso 1 (PR 5): publica `workers` (WorkerRegistry).
"""

from __future__ import annotations

from engine.worker_registry import WorkerRegistry


class ControllerContext:
    """Service-locator minimalista, crece por paso de PR 5."""

    def __init__(self) -> None:
        self.workers = WorkerRegistry()
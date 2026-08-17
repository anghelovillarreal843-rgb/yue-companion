"""WorkerRegistry: infraestructura base compartida de workers QThread.

Extraído de Controller._track_worker (PR 5, paso 1, REFACTOR_SPEC §4.1):
registra un QThread, lo lanza y lo desregistra al terminar
(worker.finished -> remove). Los directores NO llaman al Controller para
lanzar workers: usan ctx.workers.track(worker).
"""

from __future__ import annotations


class WorkerRegistry:
    """Registro + arranque de workers QThread con baja automática."""

    def __init__(self) -> None:
        self._workers: list = []

    def track(self, worker) -> None:
        """Registra, lanza y desregistra el worker cuando termina."""
        worker.finished.connect(
            lambda w=worker: self._remove(w)
        )
        self._workers.append(worker)
        worker.start()

    def _remove(self, worker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)

    def size(self) -> int:
        """Workers vivos (útiles para asserts de cierre limpio)."""
        return len(self._workers)
"""Planificador de frecuencias (paso 15) + métricas (paso 24).

Cada detector corre en su propio hilo trabajador a su propia frecuencia, tomando
SIEMPRE el último fotograma del FrameHub (nunca se acumulan colas: "cola" de
tamaño 1 conceptual = leer el último frame disponible). Nada corre en el hilo de
la interfaz.

Un fallo en un módulo se aísla (no tumba a los demás). El cierre es ordenado:
`stop()` señaliza a todos los hilos y espera con timeout.

Registra métricas por módulo: FPS real, tiempo de inferencia medio y errores.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("vision.scheduler")


@dataclass
class WorkerMetrics:
    name: str
    target_fps: float
    runs: int = 0
    errors: int = 0
    last_run: float = 0.0
    ema_infer_ms: float = 0.0        # media móvil del tiempo de inferencia
    ema_fps: float = 0.0
    _last_start: float = field(default=0.0, repr=False)

    def record(self, infer_ms: float) -> None:
        now = time.monotonic()
        if self._last_start > 0:
            dt = now - self._last_start
            inst_fps = 1.0 / dt if dt > 0 else 0.0
            self.ema_fps = inst_fps if self.ema_fps == 0 else 0.8 * self.ema_fps + 0.2 * inst_fps
        self._last_start = now
        self.ema_infer_ms = infer_ms if self.ema_infer_ms == 0 else 0.8 * self.ema_infer_ms + 0.2 * infer_ms
        self.runs += 1
        self.last_run = now


class _Worker(threading.Thread):
    def __init__(self, name: str, fps: float, job, stop_event: threading.Event) -> None:
        super().__init__(name=f"Vision-{name}", daemon=True)
        self.metrics = WorkerMetrics(name=name, target_fps=max(0.0, fps))
        self._job = job
        self._stop = stop_event
        self._interval = 1.0 / fps if fps > 0 else 1.0
        self._enabled = fps > 0

    def run(self) -> None:
        if not self._enabled:
            return
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                self._job()
            except Exception as exc:  # aislar el fallo del módulo
                self.metrics.errors += 1
                log.debug("worker %s falló: %s", self.metrics.name, exc)
            infer_ms = (time.perf_counter() - t0) * 1000.0
            self.metrics.record(infer_ms)
            # Duerme lo que falte para respetar el FPS (nunca acumula trabajo).
            remaining = self._interval - (time.perf_counter() - t0)
            self._stop.wait(max(0.0, remaining))


class VisionScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._workers: list[_Worker] = []
        self._started = False

    def add(self, name: str, fps: float, job) -> None:
        """Registra un trabajo `job()` a `fps` cuadros por segundo (0 = desactivado)."""
        if fps <= 0:
            log.info("Módulo %s desactivado (FPS=0).", name)
            return
        self._workers.append(_Worker(name, fps, job, self._stop))

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        for w in self._workers:
            w.start()
        log.info("Planificador de visión iniciado con %d módulos.", len(self._workers))

    def stop(self) -> None:
        self._stop.set()
        for w in self._workers:
            if w.is_alive():
                w.join(timeout=2.0)
        self._started = False

    def metrics(self) -> list[WorkerMetrics]:
        return [w.metrics for w in self._workers]

    def metrics_dict(self) -> dict:
        return {
            m.name: {
                "target_fps": round(m.target_fps, 1),
                "real_fps": round(m.ema_fps, 1),
                "infer_ms": round(m.ema_infer_ms, 1),
                "runs": m.runs,
                "errors": m.errors,
            }
            for m in self.metrics()
        }

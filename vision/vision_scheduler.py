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
    """Hilo trabajador de un módulo de visión.

    CORRECCIÓN IMPORTANTE: el atributo del evento de parada se llama
    `_stop_event`, NO `_stop`. `threading.Thread` ya tiene un método privado
    `_stop()` que la propia biblioteca estándar invoca desde `join()` y desde
    `is_alive()` en cuanto el hilo termina. Guardar ahí un `Event` lo tapaba, y
    al cerrar saltaba:

        TypeError: 'Event' object is not callable

    Era un fallo intermitente (dependía de si el hilo ya había terminado al
    hacer join) que podía reventar el apagado de YUE.
    """

    def __init__(self, name: str, fps: float, job, stop_event: threading.Event) -> None:
        super().__init__(name=f"Vision-{name}", daemon=True)
        self.metrics = WorkerMetrics(name=name, target_fps=max(0.0, fps))
        self._job = job
        self._stop_event = stop_event
        self._interval = 1.0 / fps if fps > 0 else 1.0
        self._enabled = fps > 0

    def run(self) -> None:
        if not self._enabled:
            return
        while not self._stop_event.is_set():
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
            self._stop_event.wait(max(0.0, remaining))


class VisionScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._workers: list[_Worker] = []
        self._started = False

    def add(self, name: str, fps: float, job) -> None:
        """Registra un trabajo `job()` a `fps` cuadros por segundo (0 = desactivado).

        CORRECCIÓN: si el planificador YA está en marcha, el hilo se arranca aquí
        mismo. Antes solo se guardaba en la lista y `start()` no volvía a pasar,
        así que cualquier módulo registrado en caliente —por ejemplo el de
        landmarks del control por cabeza, que solo se añade al activarlo— quedaba
        inerte en silencio: aparecía en las métricas con 0 pasadas y nadie se
        enteraba de que no estaba corriendo.
        """
        if fps <= 0:
            log.info("Módulo %s desactivado (FPS=0).", name)
            return
        worker = _Worker(name, fps, job, self._stop)
        self._workers.append(worker)
        if self._started and not self._stop.is_set():
            worker.start()
            log.info("Módulo %s añadido en caliente (%.0f FPS).", name, fps)

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
            # Cinturón y tirantes: un fallo al cerrar UN hilo no debe impedir
            # que se cierren los demás ni tumbar el apagado de YUE.
            try:
                if w.is_alive():
                    w.join(timeout=2.0)
            except Exception as exc:
                log.warning("No pude cerrar el hilo %s: %s", w.name, exc)
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

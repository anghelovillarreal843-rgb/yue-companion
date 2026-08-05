"""FrameHub (paso 3 del pedido): reparte el ÚLTIMO fotograma a los módulos.

No es una cola: guarda únicamente el fotograma más reciente. Cada detector lo
pide a su propia frecuencia; si un detector va lento, simplemente se pierde los
intermedios (nunca se acumulan colas ni se bloquea la interfaz).

  - `publish(frame_bgr)`  -> lo llama el CameraService en su hilo.
  - `latest_bgr()`        -> BGR sin copia (para módulos que redimensionan).
  - `latest_rgb()`        -> RGB con conversión perezosa y cacheada por frame.
  - `timestamp()`         -> reloj monotónico del último publish.
  - `frame_id()`          -> contador incremental (para saber si hay algo nuevo).

Thread-safe con un lock corto. No importa MediaPipe; OpenCV solo para la
conversión de color y solo si de verdad alguien pide el RGB.
"""
from __future__ import annotations

import threading
import time


class FrameHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bgr = None
        self._rgb = None          # caché perezosa del RGB del frame actual
        self._ts = 0.0
        self._id = 0

    def publish(self, frame_bgr) -> None:
        with self._lock:
            self._bgr = frame_bgr
            self._rgb = None       # invalida la caché RGB del frame anterior
            self._ts = time.monotonic()
            self._id += 1

    def latest_bgr(self):
        with self._lock:
            return self._bgr

    def latest_rgb(self):
        with self._lock:
            if self._rgb is not None:
                return self._rgb
            bgr = self._bgr
            if bgr is None:
                return None
        # Conversión fuera del lock (puede tardar un poco).
        try:
            import cv2
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        except Exception:
            # Sin OpenCV: intento con numpy invirtiendo el último eje.
            try:
                rgb = bgr[:, :, ::-1]
            except Exception:
                return None
        with self._lock:
            # Solo cachea si el frame no cambió mientras convertíamos.
            if self._bgr is bgr:
                self._rgb = rgb
        return rgb

    def timestamp(self) -> float:
        with self._lock:
            return self._ts

    def frame_id(self) -> int:
        with self._lock:
            return self._id

    def has_frame(self) -> bool:
        with self._lock:
            return self._bgr is not None

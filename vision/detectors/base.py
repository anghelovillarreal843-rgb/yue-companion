"""Base común de los detectores MediaPipe Tasks.

Todos usan el modo IMAGE (una llamada por fotograma). Es el más simple y seguro
para nuestro planificador: cada detector lo usa un único hilo trabajador, sin
las restricciones de marca de tiempo monotónica del modo VIDEO/LIVE_STREAM.

`mediapipe` y `numpy` se importan de forma perezosa: si no están, el detector
queda `available=False` y sus métodos devuelven None sin romper nada.
"""
from __future__ import annotations

import logging

log = logging.getLogger("vision.detector")


def mp_tasks():
    """Devuelve (mp, vision_module) o (None, None) si MediaPipe no está."""
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as _  # noqa: F401  (valida que exista)
        from mediapipe.tasks.python import vision as mp_vision
        return mp, mp_vision
    except Exception as exc:  # pragma: no cover
        log.debug("MediaPipe Tasks no disponible: %s", exc)
        return None, None


def make_mp_image(rgb_frame):
    """Envuelve un ndarray RGB en un mediapipe.Image. None si no se puede."""
    try:
        import mediapipe as mp
        import numpy as np
        arr = np.ascontiguousarray(rgb_frame)
        return mp.Image(image_format=mp.ImageFormat.SRGB, data=arr)
    except Exception as exc:  # pragma: no cover
        log.debug("No pude crear mp.Image: %s", exc)
        return None


class BaseDetector:
    """Contrato mínimo: `available`, `ensure_loaded`, `close`."""

    name = "base"

    def __init__(self) -> None:
        self._loaded = False
        self._failed = False
        self._impl = None  # el objeto de MediaPipe (landmarker/detector/…)

    @property
    def available(self) -> bool:
        """True si se puede intentar cargar (no ha fallado antes)."""
        return not self._failed

    def ensure_loaded(self) -> bool:
        """Carga perezosa. Devuelve True si el modelo está listo."""
        if self._loaded:
            return True
        if self._failed:
            return False
        try:
            self._impl = self._build()
        except Exception as exc:
            self._failed = True
            log.warning("No pude cargar %s: %s", self.name, exc)
            return False
        if self._impl is None:
            self._failed = True
            return False
        self._loaded = True
        log.info("Modelo cargado: %s", self.name)
        return True

    def _build(self):  # pragma: no cover - lo implementa cada subclase
        raise NotImplementedError

    def close(self) -> None:
        impl = self._impl
        self._impl = None
        self._loaded = False
        if impl is not None:
            try:
                impl.close()
            except Exception:
                pass

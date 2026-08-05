"""Interactive Segmenter bajo demanda (paso 11).

Herramienta opcional: el usuario marca un punto/región sobre una vista de la
cámara y el segmentador devuelve la máscara del objeto seleccionado. NO se carga
ni se ejecuta de forma permanente.
"""
from __future__ import annotations

import logging

from vision.detectors.base import make_mp_image, mp_tasks

log = logging.getLogger("vision.interactive_segmenter")


class InteractiveSegmenter:
    def __init__(self, model_path) -> None:
        self.model_path = str(model_path) if model_path else None
        self._impl = None
        self._failed = False

    def _ensure(self) -> bool:
        if self._impl is not None:
            return True
        if self._failed or not self.model_path:
            return False
        try:
            mp, mp_vision = mp_tasks()
            if mp is None:
                self._failed = True
                return False
            base_options = mp.tasks.BaseOptions(model_asset_path=self.model_path)
            options = mp_vision.InteractiveSegmenterOptions(
                base_options=base_options,
                output_category_mask=True,
            )
            self._impl = mp_vision.InteractiveSegmenter.create_from_options(options)
            return True
        except Exception as exc:
            self._failed = True
            log.warning("No pude cargar el segmentador interactivo: %s", exc)
            return False

    def segment_point(self, rgb_frame, x_norm: float, y_norm: float):
        """Segmenta el objeto bajo el punto (x, y) normalizado 0..1. Máscara bool o None."""
        if rgb_frame is None or not self._ensure():
            return None
        image = make_mp_image(rgb_frame)
        if image is None:
            return None
        try:
            from mediapipe.tasks.python.components import containers
            roi = containers.keypoint.NormalizedKeypoint(x=float(x_norm), y=float(y_norm))
            RegionOfInterest = containers.keypoint.RegionOfInterest  # type: ignore[attr-defined]
            region = RegionOfInterest(format=RegionOfInterest.Format.KEYPOINT, keypoint=roi)
            result = self._impl.segment(image, region)
            cat = getattr(result, "category_mask", None)
            if cat is None:
                return None
            return cat.numpy_view() > 0
        except Exception as exc:
            log.debug("fallo en segment(): %s", exc)
            return None

    def close(self) -> None:
        if self._impl is not None:
            try:
                self._impl.close()
            except Exception:
                pass
            self._impl = None

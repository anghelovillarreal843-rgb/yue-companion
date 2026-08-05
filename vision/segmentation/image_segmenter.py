"""Image Segmenter bajo demanda (paso 10).

Separa persona/fondo y permite: máscara, fondo transparente, desenfoque de
fondo, sustitución de fondo o recorte de la persona. NO se ejecuta de forma
permanente: solo mientras la función está activa.

Todo el trabajo pesado (OpenCV/MediaPipe) es perezoso; sin ellos el módulo queda
inactivo sin romper nada.
"""
from __future__ import annotations

import logging

from vision.detectors.base import make_mp_image, mp_tasks

log = logging.getLogger("vision.segmenter")


class ImageSegmenter:
    def __init__(self, model_path) -> None:
        self.model_path = str(model_path) if model_path else None
        self._impl = None
        self._failed = False
        self.active = False  # solo procesa cuando está activo

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
            options = mp_vision.ImageSegmenterOptions(
                base_options=base_options,
                running_mode=mp_vision.RunningMode.IMAGE,
                output_category_mask=True,
            )
            self._impl = mp_vision.ImageSegmenter.create_from_options(options)
            return True
        except Exception as exc:
            self._failed = True
            log.warning("No pude cargar el segmentador: %s", exc)
            return False

    def activate(self) -> bool:
        self.active = self._ensure()
        return self.active

    def deactivate(self) -> None:
        self.active = False

    def mask(self, rgb_frame):
        """Devuelve una máscara booleana (True=persona) o None."""
        if not self.active or rgb_frame is None or not self._ensure():
            return None
        image = make_mp_image(rgb_frame)
        if image is None:
            return None
        try:
            result = self._impl.segment(image)
            cat = getattr(result, "category_mask", None)
            if cat is None:
                return None
            import numpy as np
            arr = cat.numpy_view()
            return arr > 0
        except Exception as exc:
            log.debug("fallo en segment(): %s", exc)
            return None

    def apply(self, rgb_frame, effect: str = "blur", background=None):
        """Aplica un efecto de fondo. `effect`: blur | transparent | replace | cutout."""
        mask = self.mask(rgb_frame)
        if mask is None:
            return rgb_frame
        try:
            import numpy as np
            import cv2
            person = np.stack([mask] * 3, axis=-1)
            if effect == "blur":
                bg = cv2.GaussianBlur(rgb_frame, (0, 0), 15)
                return np.where(person, rgb_frame, bg)
            if effect == "transparent":
                rgba = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2RGBA)
                rgba[:, :, 3] = (mask * 255).astype("uint8")
                return rgba
            if effect == "replace" and background is not None:
                bg = cv2.resize(background, (rgb_frame.shape[1], rgb_frame.shape[0]))
                return np.where(person, rgb_frame, bg)
            if effect == "cutout":
                out = rgb_frame.copy()
                out[~mask] = 0
                return out
        except Exception as exc:
            log.debug("fallo aplicando efecto: %s", exc)
        return rgb_frame

    def close(self) -> None:
        self.active = False
        if self._impl is not None:
            try:
                self._impl.close()
            except Exception:
                pass
            self._impl = None

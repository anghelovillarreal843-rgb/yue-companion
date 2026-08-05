"""Image Classifier (paso 12): clasificación de escena/imagen bajo petición.

Módulo lento: no corre en cada fotograma. Se dispara cuando el usuario pregunta
qué ve YUE, al capturar una imagen, ante un cambio importante de escena, o cada
varios segundos si el modo de comprensión de escena está activo.

Solo reporta clases que el modelo conoce; nunca inventa categorías.
"""
from __future__ import annotations

import logging

from vision.detectors.base import BaseDetector, make_mp_image, mp_tasks
from vision.observations import SceneObservation

log = logging.getLogger("vision.classifier")


class ImageClassifierModule(BaseDetector):
    name = "image_classifier"

    def __init__(self, model_path, max_results: int = 3) -> None:
        super().__init__()
        self.model_path = str(model_path) if model_path else None
        self.max_results = int(max_results)

    def _build(self):
        if not self.model_path:
            return None
        mp, mp_vision = mp_tasks()
        if mp is None:
            return None
        base_options = mp.tasks.BaseOptions(model_asset_path=self.model_path)
        options = mp_vision.ImageClassifierOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.IMAGE,
            max_results=self.max_results,
        )
        return mp_vision.ImageClassifier.create_from_options(options)

    def classify(self, rgb_frame) -> SceneObservation:
        if rgb_frame is None or not self.ensure_loaded():
            return SceneObservation()
        image = make_mp_image(rgb_frame)
        if image is None:
            return SceneObservation()
        try:
            result = self._impl.classify(image)
            classifications = getattr(result, "classifications", None) or []
            if classifications and classifications[0].categories:
                top = classifications[0].categories[0]
                return SceneObservation(label=top.category_name or "unknown",
                                        confidence=round(float(top.score), 3))
        except Exception as exc:
            log.debug("fallo en classify(): %s", exc)
        return SceneObservation()

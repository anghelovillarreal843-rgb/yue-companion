"""Face Detector rápido (paso 4): presencia, conteo y posición de rostros.

Sirve de filtro barato para decidir si vale la pena correr el Face Landmarker
(mucho más caro). Devuelve una lista de `FaceBox` normalizadas 0..1.
"""
from __future__ import annotations

import logging

from vision.detectors.base import BaseDetector, make_mp_image, mp_tasks
from vision.observations import FaceBox

log = logging.getLogger("vision.face_detector")


class FaceDetectorModule(BaseDetector):
    name = "face_detector"

    def __init__(self, model_path, min_confidence: float = 0.5, max_faces: int = 3) -> None:
        super().__init__()
        self.model_path = str(model_path) if model_path else None
        self.min_confidence = float(min_confidence)
        self.max_faces = int(max_faces)

    def _build(self):
        if not self.model_path:
            return None
        mp, mp_vision = mp_tasks()
        if mp is None:
            return None
        base_options = mp.tasks.BaseOptions(model_asset_path=self.model_path)
        options = mp_vision.FaceDetectorOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.IMAGE,
            min_detection_confidence=self.min_confidence,
        )
        return mp_vision.FaceDetector.create_from_options(options)

    def detect(self, rgb_frame) -> list[FaceBox]:
        if rgb_frame is None or not self.ensure_loaded():
            return []
        image = make_mp_image(rgb_frame)
        if image is None:
            return []
        try:
            result = self._impl.detect(image)
        except Exception as exc:
            log.debug("fallo en detect(): %s", exc)
            return []

        h, w = rgb_frame.shape[0], rgb_frame.shape[1]
        boxes: list[FaceBox] = []
        for det in (result.detections or [])[: self.max_faces]:
            bb = det.bounding_box
            score = float(det.categories[0].score) if det.categories else 0.0
            if score < self.min_confidence:
                continue
            nx = bb.origin_x / max(1, w)
            ny = bb.origin_y / max(1, h)
            nw = bb.width / max(1, w)
            nh = bb.height / max(1, h)
            cx = nx + nw / 2.0
            position = "left" if cx < 0.4 else "right" if cx > 0.6 else "center"
            boxes.append(FaceBox(
                bounding_box={"x": round(nx, 4), "y": round(ny, 4),
                              "w": round(nw, 4), "h": round(nh, 4)},
                score=round(score, 4),
                position=position,
                relative_size=round(nw * nh, 5),
            ))
        return boxes

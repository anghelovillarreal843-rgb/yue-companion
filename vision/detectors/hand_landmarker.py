"""Hand Landmarker: los 21 puntos de referencia por mano (punto 7 del pedido).

El `GestureRecognizerModule` ya trae landmarks, pero solo si su modelo está
instalado y solo con su catálogo cerrado de gestos. Este módulo es la vía
directa a `hand_landmarker.task`, que es más ligero y da los 21 puntos con su
componente Z (profundidad aproximada) para el `HandTracker`.

Devuelve una lista de tuplas `(lado, landmarks)` lista para `HandTracker.update`.
Si falta MediaPipe o el modelo, `available` es False y devuelve [] sin romper.
"""
from __future__ import annotations

import logging

from vision.detectors.base import BaseDetector, make_mp_image, mp_tasks

log = logging.getLogger("vision.hands")


class HandLandmarkerModule(BaseDetector):
    name = "hand_landmarker"

    def __init__(self, model_path, max_hands: int = 2,
                 min_confidence: float = 0.5) -> None:
        super().__init__()
        self.model_path = str(model_path) if model_path else None
        self.max_hands = int(max_hands)
        self.min_confidence = float(min_confidence)

    def _build(self):
        if not self.model_path:
            return None
        mp, mp_vision = mp_tasks()
        if mp is None:
            return None
        base_options = mp.tasks.BaseOptions(model_asset_path=self.model_path)
        options = mp_vision.HandLandmarkerOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.IMAGE,
            num_hands=self.max_hands,
            min_hand_detection_confidence=self.min_confidence,
            min_hand_presence_confidence=self.min_confidence,
            min_tracking_confidence=self.min_confidence,
        )
        return mp_vision.HandLandmarker.create_from_options(options)

    # ------------------------------------------------------------------
    def detect(self, rgb_frame) -> list[tuple[str, list]]:
        """[(lado, [21 landmarks]), ...]. Lista vacía si no hay manos o modelo."""
        if rgb_frame is None or not self.ensure_loaded():
            return []
        image = make_mp_image(rgb_frame)
        if image is None:
            return []
        try:
            result = self._impl.detect(image)
        except Exception as exc:
            log.debug("fallo en hand detect(): %s", exc)
            return []

        landmarks_all = getattr(result, "hand_landmarks", None) or []
        world_all = getattr(result, "hand_world_landmarks", None) or []
        handed_all = getattr(result, "handedness", None) or []

        out: list[tuple[str, list]] = []
        for i, points in enumerate(landmarks_all):
            side = "unknown"
            if i < len(handed_all) and handed_all[i]:
                raw = (handed_all[i][0].category_name or "").strip().lower()
                # MediaPipe etiqueta desde el punto de vista de la cámara; con la
                # imagen en espejo (webcam), "Left" es la mano derecha real.
                side = {"left": "right", "right": "left"}.get(raw, "unknown")
            # Si hay landmarks 3D del mundo real, se usan para la profundidad.
            if i < len(world_all) and world_all[i]:
                merged = []
                for p2d, p3d in zip(points, world_all[i]):
                    merged.append((float(p2d.x), float(p2d.y), float(getattr(p3d, "z", 0.0))))
                out.append((side, merged))
            else:
                out.append((side, [(float(p.x), float(p.y), float(getattr(p, "z", 0.0)))
                                   for p in points]))
        return out[: self.max_hands]

"""Face Landmarker + Blendshapes (paso 5).

Activa `output_face_blendshapes=True` y
`output_facial_transformation_matrixes=True`. NO crea un modelo aparte para los
blendshapes: son salida del propio Face Landmarker.

Produce `FaceObservation` normalizadas, con expresión/emoción ya suavizadas por
`ExpressionSmoother` (uno por rostro).
"""
from __future__ import annotations

import logging
import math

from vision.detectors.base import BaseDetector, make_mp_image, mp_tasks
from vision.expression_engine import (
    ExpressionSmoother,
    classify_expression,
    expression_to_emotion,
)
from vision.observations import FaceObservation

log = logging.getLogger("vision.face_landmarker")


def _head_pose_from_matrix(matrix) -> dict:
    """Extrae yaw/pitch/roll (grados) de una matriz de transformación 4x4."""
    try:
        m = list(matrix.data) if hasattr(matrix, "data") else list(matrix)
        # MediaPipe entrega 16 floats en orden fila-mayor.
        r00, r01, r02 = m[0], m[1], m[2]
        r10, r11, r12 = m[4], m[5], m[6]
        r20, r21, r22 = m[8], m[9], m[10]
        pitch = math.degrees(math.atan2(-r12, math.sqrt(r02 * r02 + r22 * r22)))
        yaw = math.degrees(math.atan2(r02, r22))
        roll = math.degrees(math.atan2(r10, r11))
        return {"yaw": yaw, "pitch": pitch, "roll": roll}
    except Exception:
        return {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}


class FaceLandmarkerModule(BaseDetector):
    name = "face_landmarker"

    def __init__(self, model_path, max_faces: int = 3, keep_landmarks: bool = False) -> None:
        super().__init__()
        self.model_path = str(model_path) if model_path else None
        self.max_faces = int(max_faces)
        self.keep_landmarks = bool(keep_landmarks)  # los landmarks pesan; opcional
        self._smoothers: dict[int, ExpressionSmoother] = {}

    def _build(self):
        if not self.model_path:
            return None
        mp, mp_vision = mp_tasks()
        if mp is None:
            return None
        base_options = mp.tasks.BaseOptions(model_asset_path=self.model_path)
        options = mp_vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.IMAGE,
            num_faces=self.max_faces,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        return mp_vision.FaceLandmarker.create_from_options(options)

    def _smoother(self, idx: int) -> ExpressionSmoother:
        s = self._smoothers.get(idx)
        if s is None:
            s = ExpressionSmoother()
            self._smoothers[idx] = s
        return s

    def detect(self, rgb_frame, now: float | None = None) -> list[FaceObservation]:
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

        faces: list[FaceObservation] = []
        blendshapes_all = getattr(result, "face_blendshapes", None) or []
        matrices_all = getattr(result, "facial_transformation_matrixes", None) or []
        landmarks_all = getattr(result, "face_landmarks", None) or []

        for idx, _lm in enumerate(landmarks_all[: self.max_faces]):
            bs = {}
            if idx < len(blendshapes_all):
                for cat in blendshapes_all[idx]:
                    bs[cat.category_name] = float(cat.score)

            raw_expr, raw_conf = classify_expression(bs)
            expr, conf = self._smoother(idx).update(raw_expr, raw_conf, now)
            emotion = expression_to_emotion(expr)

            head_pose = {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}
            if idx < len(matrices_all):
                head_pose = _head_pose_from_matrix(matrices_all[idx])
            looking = abs(head_pose["yaw"]) < 20 and abs(head_pose["pitch"]) < 20

            landmarks = []
            if self.keep_landmarks:
                landmarks = [(round(p.x, 4), round(p.y, 4), round(p.z, 4)) for p in _lm]

            faces.append(FaceObservation(
                face_id=idx,
                landmarks=landmarks,
                blendshapes={k: round(v, 3) for k, v in bs.items() if v > 0.05},
                head_pose=head_pose,
                looking_at_camera=bool(looking),
                expression=expr,
                emotion_estimate=emotion,
                confidence=conf,
            ))
        return faces

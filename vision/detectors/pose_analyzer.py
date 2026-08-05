"""Pose Landmarker + análisis de postura (paso 7).

Interpreta los landmarks del cuerpo para estimar estados legibles: de pie,
sentado, agachado, inclinado, brazos levantados, y movimiento.

El MOVIMIENTO/caminar NO se decide con un solo fotograma: se compara la posición
de referencia (cadera/hombros) contra una ventana temporal reciente.

Índices de landmarks de MediaPipe Pose (BlazePose, 33 puntos):
  0 nariz · 11/12 hombros · 13/14 codos · 15/16 muñecas · 23/24 cadera ·
  25/26 rodillas · 27/28 tobillos.
"""
from __future__ import annotations

import logging
from collections import deque

from vision.detectors.base import BaseDetector, make_mp_image, mp_tasks
from vision.observations import PoseObservation

log = logging.getLogger("vision.pose")

L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28


class PoseAnalyzerModule(BaseDetector):
    name = "pose_landmarker"

    def __init__(self, model_path, min_confidence: float = 0.5, motion_window: int = 8) -> None:
        super().__init__()
        self.model_path = str(model_path) if model_path else None
        self.min_confidence = float(min_confidence)
        self._centroids: deque = deque(maxlen=max(3, int(motion_window)))

    def _build(self):
        if not self.model_path:
            return None
        mp, mp_vision = mp_tasks()
        if mp is None:
            return None
        base_options = mp.tasks.BaseOptions(model_asset_path=self.model_path)
        options = mp_vision.PoseLandmarkerOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.IMAGE,
            num_poses=1,
            min_pose_detection_confidence=self.min_confidence,
        )
        return mp_vision.PoseLandmarker.create_from_options(options)

    def detect(self, rgb_frame) -> PoseObservation:
        if rgb_frame is None or not self.ensure_loaded():
            return PoseObservation(visible=False)
        image = make_mp_image(rgb_frame)
        if image is None:
            return PoseObservation(visible=False)
        try:
            result = self._impl.detect(image)
        except Exception as exc:
            log.debug("fallo en detect(): %s", exc)
            return PoseObservation(visible=False)

        all_poses = getattr(result, "pose_landmarks", None) or []
        if not all_poses:
            self._centroids.clear()
            return PoseObservation(visible=False)

        lm = all_poses[0]
        return self._interpret(lm)

    # -- interpretación pura (probable sin cámara con landmarks simulados) --
    def _interpret(self, lm) -> PoseObservation:
        def y(i):
            return lm[i].y

        def x(i):
            return lm[i].x

        def vis(i):
            return getattr(lm[i], "visibility", 1.0)

        try:
            shoulder_y = (y(L_SHOULDER) + y(R_SHOULDER)) / 2
            hip_y = (y(L_HIP) + y(R_HIP)) / 2
            knee_y = (y(L_KNEE) + y(R_KNEE)) / 2
            ankle_y = (y(L_ANKLE) + y(R_ANKLE)) / 2
        except Exception:
            return PoseObservation(visible=False)

        # Brazos levantados: muñeca por encima del hombro (y menor = más arriba).
        left_arm = y(L_WRIST) < shoulder_y - 0.05
        right_arm = y(R_WRIST) < shoulder_y - 0.05

        # Postura por proporciones verticales. Todo normalizado 0..1.
        torso = abs(hip_y - shoulder_y)
        legs = abs(ankle_y - hip_y)
        legs_visible = vis(L_ANKLE) > 0.5 or vis(R_ANKLE) > 0.5

        if not legs_visible or legs < torso * 0.6:
            state = "sitting"
        elif knee_y > hip_y + 0.02 and legs > torso * 1.1:
            state = "standing"
        elif knee_y < hip_y + 0.02:
            state = "crouching"
        else:
            state = "standing"

        # Inclinación lateral: desalineación hombro/cadera en X.
        shoulder_cx = (x(L_SHOULDER) + x(R_SHOULDER)) / 2
        hip_cx = (x(L_HIP) + x(R_HIP)) / 2
        dx = shoulder_cx - hip_cx
        if dx < -0.06:
            state = "leaning_left"
        elif dx > 0.06:
            state = "leaning_right"

        # Movimiento por ventana temporal (centroide de cadera).
        self._centroids.append((hip_cx, hip_y))
        movement = "still"
        if len(self._centroids) >= 3:
            xs = [c[0] for c in self._centroids]
            ys = [c[1] for c in self._centroids]
            spread = (max(xs) - min(xs)) + (max(ys) - min(ys))
            movement = "moving" if spread > 0.06 else "still"

        return PoseObservation(
            visible=True,
            state=state,
            left_arm_raised=bool(left_arm),
            right_arm_raised=bool(right_arm),
            both_arms_raised=bool(left_arm and right_arm),
            movement=movement,
            confidence=round(float(vis(L_HIP)), 3),
        )

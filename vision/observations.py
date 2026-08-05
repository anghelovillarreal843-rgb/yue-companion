"""Estructuras de datos normalizadas que producen los módulos de visión.

Son `dataclasses` puras (sin OpenCV ni MediaPipe): fáciles de crear en pruebas y
de serializar. NUNCA contienen imágenes ni vídeo, solo datos derivados.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FaceObservation:
    """Un rostro observado (paso 5 del pedido)."""

    face_id: Optional[int] = None
    bounding_box: Optional[dict] = None          # {x, y, w, h} normalizado 0..1
    landmarks: list = field(default_factory=list)
    blendshapes: dict = field(default_factory=dict)
    head_pose: dict = field(default_factory=dict)  # {yaw, pitch, roll}
    looking_at_camera: bool = False
    expression: str = "neutral"
    emotion_estimate: str = "neutral"
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def as_state(self) -> dict:
        return {
            "expression": self.expression,
            "emotion_estimate": self.emotion_estimate,
            "emotion_confidence": round(float(self.confidence), 3),
            "looking_at_camera": bool(self.looking_at_camera),
            "head_pose": {
                "yaw": round(float(self.head_pose.get("yaw", 0.0)), 3),
                "pitch": round(float(self.head_pose.get("pitch", 0.0)), 3),
                "roll": round(float(self.head_pose.get("roll", 0.0)), 3),
            },
        }


@dataclass
class FaceBox:
    """Detección rápida (paso 4): solo caja + puntuación, sin landmarks."""

    bounding_box: dict                            # {x, y, w, h} normalizado 0..1
    score: float = 0.0
    position: str = "center"                       # left | center | right
    relative_size: float = 0.0                     # área de la caja / área del frame
    timestamp: float = field(default_factory=time.time)


@dataclass
class PoseObservation:
    """Resultado del analizador de postura (paso 7)."""

    visible: bool = False
    state: str = "unknown"                         # standing | sitting | ...
    left_arm_raised: bool = False
    right_arm_raised: bool = False
    both_arms_raised: bool = False
    movement: str = "still"                        # still | moving
    landmarks: list = field(default_factory=list)
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def as_state(self) -> dict:
        return {
            "visible": bool(self.visible),
            "state": self.state,
            "left_arm_raised": bool(self.left_arm_raised),
            "right_arm_raised": bool(self.right_arm_raised),
            "movement": self.movement,
        }


@dataclass
class GestureEvent:
    """Un gesto reconocido, con antirrebote (paso 8)."""

    gesture: str
    hand: str = "unknown"                          # left | right | unknown
    confidence: float = 0.0
    started_at: float = 0.0
    duration: float = 0.0
    is_new: bool = True

    def as_state(self) -> dict:
        return {
            "side": self.hand,
            "gesture": self.gesture,
            "confidence": round(float(self.confidence), 3),
        }


@dataclass
class ObjectObservation:
    """Un objeto detectado (paso 9)."""

    label: str
    confidence: float = 0.0
    bounding_box: dict = field(default_factory=dict)
    tracking_id: Optional[int] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class SceneObservation:
    """Clasificación de escena/imagen (paso 12)."""

    label: str = "unknown"
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)

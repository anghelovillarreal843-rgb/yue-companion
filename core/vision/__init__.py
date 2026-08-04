"""YUE V3 PERCEPTION SYSTEM.

Sistema de percepción visual local, modular y ADITIVO: le da a YUE la capacidad
de "ver" a la persona (presencia, rostro, emoción, atención) usando modelos ya
existentes (OpenCV, MediaPipe, DeepFace), sin entrenar nada desde cero y sin
tocar el núcleo de YUE.

Uso típico (desde main.py, opt-in):

    from core.vision import VisionController

    vc = VisionController(
        on_avatar_emotion=self.pet.set_emotion,   # marshalado a Qt por el host
        on_speak=self._yue_say,
    )
    vc.start()   # no hace nada si VISION_V3_ENABLED=false

Privacidad: análisis 100% local; jamás se guardan ni se envían imágenes.
"""
from core.vision.attention_detector import AttentionDetector, AttentionUpdate
from core.vision.avatar_emotion_controller import (
    empathic_avatar_emotion,
    state as avatar_state,
)
from core.vision.camera import CameraEngine
from core.vision.emotion_ai import EmotionAI, EmotionReading, VALID_EMOTIONS
from core.vision.emotion_manager import EmotionManager, Reaction
from core.vision.events import EventBus, VisionEvent
from core.vision.face_detector import FaceDetector, FaceResult
from core.vision.perf_profile import PerfProfile, resolve_profile
from core.vision.vision_controller import VisionController

__all__ = [
    "VisionController",
    "CameraEngine",
    "FaceDetector",
    "FaceResult",
    "EmotionAI",
    "EmotionReading",
    "VALID_EMOTIONS",
    "AttentionDetector",
    "AttentionUpdate",
    "EmotionManager",
    "Reaction",
    "EventBus",
    "VisionEvent",
    "PerfProfile",
    "resolve_profile",
    "empathic_avatar_emotion",
    "avatar_state",
]

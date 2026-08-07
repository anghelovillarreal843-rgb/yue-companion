"""Sistema de visión artificial de YUE (MediaPipe Tasks, una sola cámara).

Arquitectura (aditiva, OPT-IN, apagada por defecto con VISION_MP_ENABLED=false):

    Webcam
      -> CameraService (una sola cv2.VideoCapture)
      -> FrameHub (último fotograma)
      -> Detectores independientes (rostro, landmarks, pose, gestos, objetos)
      -> VisionStateFusion
      -> VisionState -> motor de diálogo y avatar de YUE

Uso típico desde main.py:

    from vision import integration as vision_mp
    self.vision_mp = vision_mp.attach(self)   # None si está apagado

Privacidad: procesamiento 100% local; nunca guarda ni envía imágenes.
"""
from vision.controller import VisionSystem  # noqa: E402

# --- ADITIVO (visión avanzada) -----------------------------------------
# Se exportan de forma PEREZOSA para que importar `vision` siga siendo barato
# y no arrastre OpenCV, MediaPipe ni los motores de OCR.
__all__ = [
    "VisionSystem",
    "PerceptionEngine", "CameraManager", "PrivacyManager", "EventManager",
    "VisionContextBuilder", "ModelRegistry", "LegacyCameraObserverAdapter",
    "capabilities", "voice_intents",
]

_LAZY = {
    "PerceptionEngine": ("vision.perception_engine", "PerceptionEngine"),
    "CameraManager": ("vision.camera_manager", "CameraManager"),
    "PrivacyManager": ("vision.privacy_manager", "PrivacyManager"),
    "EventManager": ("vision.event_manager", "EventManager"),
    "VisionContextBuilder": ("vision.context_builder", "VisionContextBuilder"),
    "ModelRegistry": ("vision.models.model_registry", "ModelRegistry"),
    "LegacyCameraObserverAdapter": ("vision.legacy_adapter", "LegacyCameraObserverAdapter"),
}


def __getattr__(name):
    """Importación perezosa de los módulos nuevos (PEP 562)."""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module 'vision' has no attribute {name!r}")
    import importlib
    return getattr(importlib.import_module(target[0]), target[1])

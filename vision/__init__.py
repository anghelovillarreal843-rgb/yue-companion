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

__all__ = ["VisionSystem"]

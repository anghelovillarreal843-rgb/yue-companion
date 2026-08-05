"""Detectores independientes de visión (rostro, landmarks, pose, gestos, objetos).

Cada detector:
  - carga su modelo de forma perezosa (solo al primer uso),
  - degrada a inactivo si falta MediaPipe o el archivo del modelo,
  - trabaja sobre el fotograma RGB del FrameHub (nunca abre la cámara),
  - devuelve observaciones normalizadas de `vision.observations`.
"""

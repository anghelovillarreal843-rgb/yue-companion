"""Administrador de modelos de visión (paso 20 del pedido).

Responsabilidades:
  - resolver la ruta de cada modelo (`models/vision/*.task|*.tflite`) tanto en
    desarrollo como dentro del ejecutable de PyInstaller,
  - comprobar si el archivo existe y avisar con un mensaje claro si falta,
  - NO descargar nada automáticamente (privacidad y peso del bundle),
  - carga perezosa: solo se resuelve la ruta cuando un módulo la pide.

No importa MediaPipe: solo trabaja con rutas del sistema de archivos, así que
se prueba sin modelos y sin cámara.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("vision.models")


# Nombres canónicos de cada modelo dentro de models/vision/.
MODEL_FILES = {
    "face_detector": "face_detector.task",
    "face_landmarker": "face_landmarker.task",
    "pose_landmarker": "pose_landmarker.task",
    "gesture_recognizer": "gesture_recognizer.task",
    "object_detector": "object_detector.tflite",
    "image_segmenter": "image_segmenter.tflite",
    "interactive_segmenter": "interactive_segmenter.tflite",
    "image_classifier": "image_classifier.tflite",
}


def resource_path(relative_path: str) -> Path:
    """Ruta absoluta a un recurso, en desarrollo y empaquetado con PyInstaller.

    Con PyInstaller los datos viven en `sys._MEIPASS`. En desarrollo, se toma la
    raíz del proyecto (dos niveles arriba de este archivo: vision/ -> raíz).
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    return (base / relative_path).resolve()


class ModelManager:
    def __init__(self, base_dir: str | os.PathLike | None = None) -> None:
        # Permite configurar la carpeta de modelos; por defecto models/vision.
        if base_dir is not None:
            self.base = Path(base_dir)
        else:
            self.base = resource_path(os.path.join("models", "vision"))
        self._logged_missing: set[str] = set()

    def path_for(self, key: str) -> Path:
        """Ruta esperada del modelo `key` (exista o no)."""
        name = MODEL_FILES.get(key)
        if name is None:
            raise KeyError(f"Modelo desconocido: {key!r}")
        # Permite sobrescribir una ruta puntual con VISION_MODEL_<KEY>_PATH.
        override = os.environ.get(f"VISION_MODEL_{key.upper()}_PATH")
        if override:
            return Path(override)
        return self.base / name

    def exists(self, key: str) -> bool:
        try:
            return self.path_for(key).is_file()
        except Exception:
            return False

    def require(self, key: str) -> Path | None:
        """Devuelve la ruta si el modelo existe; si no, avisa una vez y None."""
        p = self.path_for(key)
        if p.is_file():
            return p
        if key not in self._logged_missing:
            self._logged_missing.add(key)
            log.warning(
                "Falta el modelo '%s' (%s). Ese módulo quedará inactivo. "
                "Descárgalo de MediaPipe Model Maker/Model Zoo y colócalo ahí. "
                "Ver docs/VISION_SYSTEM.md.",
                key, p,
            )
        return None

    def status(self) -> dict[str, bool]:
        """Mapa {clave: existe?} para diagnósticos."""
        return {key: self.exists(key) for key in MODEL_FILES}

    def describe(self) -> str:
        rows = []
        for key, present in self.status().items():
            marca = "OK" if present else "FALTA"
            rows.append(f"  [{marca}] {key} -> {self.path_for(key)}")
        return "Modelos de visión:\n" + "\n".join(rows)

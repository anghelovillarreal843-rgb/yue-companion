"""Registro de modelos de visión (punto 17 del pedido).

Amplía el `ModelManager` clásico (que sigue funcionando igual) con lo que pide
el documento:

  - saber qué modelos están instalados y cuáles faltan,
  - NO descargar nada salvo autorización explícita (`VISION_ALLOW_DOWNLOAD`),
  - verificar integridad por tamaño y hash SHA-256 cuando se conoce,
  - evitar cargar dos veces el mismo modelo (caché por ruta),
  - elegir variante ligera o precisa (`VISION_MODEL_VARIANT=lite|full`),
  - rutas configurables por .env,
  - mensajes en español que se entiendan.

Nunca se asume que un archivo existe: si falta, ese módulo queda inactivo y el
resto de YUE sigue funcionando con capacidades reducidas.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from vision.model_manager import MODEL_FILES, ModelManager, resource_path

log = logging.getLogger("vision.registry")


@dataclass
class ModelSpec:
    """Descripción de un modelo: dónde vive, cuánto pesa y de dónde sale."""

    key: str
    filename: str
    description: str = ""
    url: str = ""
    sha256: str = ""
    min_bytes: int = 1024
    optional: bool = True
    lite_filename: str = ""

    def filename_for(self, variant: str) -> str:
        if variant == "lite" and self.lite_filename:
            return self.lite_filename
        return self.filename


# Catálogo. Las URL son las oficiales de MediaPipe; NO se descargan solas.
SPECS: dict[str, ModelSpec] = {
    "face_detector": ModelSpec(
        "face_detector", "face_detector.task", "Detección rápida de rostros",
        "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
        min_bytes=100_000),
    "face_landmarker": ModelSpec(
        "face_landmarker", "face_landmarker.task", "478 puntos faciales y blendshapes",
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
        min_bytes=1_000_000),
    "pose_landmarker": ModelSpec(
        "pose_landmarker", "pose_landmarker.task", "33 puntos de postura corporal",
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task",
        min_bytes=1_000_000,
        lite_filename="pose_landmarker_lite.task"),
    "hand_landmarker": ModelSpec(
        "hand_landmarker", "hand_landmarker.task", "21 puntos por mano",
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
        min_bytes=1_000_000),
    "gesture_recognizer": ModelSpec(
        "gesture_recognizer", "gesture_recognizer.task", "Catálogo de gestos de mano",
        "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task",
        min_bytes=1_000_000),
    "object_detector": ModelSpec(
        "object_detector", "object_detector.tflite", "Objetos comunes (EfficientDet)",
        "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float16/1/efficientdet_lite0.tflite",
        min_bytes=1_000_000,
        lite_filename="object_detector_lite.tflite"),
    "image_classifier": ModelSpec(
        "image_classifier", "image_classifier.tflite", "Clasificador de escena",
        "https://storage.googleapis.com/mediapipe-models/image_classifier/efficientnet_lite0/float32/1/efficientnet_lite0.tflite",
        min_bytes=1_000_000),
    "image_segmenter": ModelSpec(
        "image_segmenter", "image_segmenter.tflite", "Segmentación de la imagen"),
    "interactive_segmenter": ModelSpec(
        "interactive_segmenter", "interactive_segmenter.tflite", "Segmentación interactiva"),
}


@dataclass
class ModelStatus:
    key: str
    present: bool
    path: str
    size: int = 0
    valid: bool = False
    reason: str = ""
    description: str = ""

    def as_dict(self) -> dict:
        return {
            "modelo": self.key,
            "instalado": self.present,
            "valido": self.valid,
            "ruta": self.path,
            "tamano": self.size,
            "motivo": self.reason,
            "descripcion": self.description,
        }


class ModelRegistry:
    """Fuente única de verdad sobre los modelos de visión."""

    def __init__(
        self,
        base_dir: str | os.PathLike | None = None,
        *,
        variant: str = "full",
        allow_download: bool = False,
        downloader: Callable[[str, Path], bool] | None = None,
    ) -> None:
        self.manager = ModelManager(base_dir=base_dir)
        self.base = self.manager.base
        self.variant = "lite" if str(variant).lower().startswith("lite") else "full"
        self.allow_download = bool(allow_download)
        self._downloader = downloader
        self._lock = threading.RLock()
        self._loaded: dict[str, Any] = {}     # ruta -> objeto ya cargado
        self._warned: set[str] = set()

    # ------------------------------------------------------------------
    def path_for(self, key: str) -> Path:
        """Ruta esperada, respetando VISION_MODEL_<KEY>_PATH y la variante."""
        override = os.environ.get(f"VISION_MODEL_{key.upper()}_PATH")
        if override:
            return Path(override)
        spec = SPECS.get(key)
        if spec is None:
            return self.manager.path_for(key)
        candidate = self.base / spec.filename_for(self.variant)
        if candidate.is_file():
            return candidate
        # Si la variante ligera no está, se cae a la normal sin quejarse.
        return self.base / spec.filename

    def exists(self, key: str) -> bool:
        try:
            return self.path_for(key).is_file()
        except Exception:
            return False

    # ------------------------------------------------------------------
    def verify(self, key: str) -> ModelStatus:
        """Comprueba existencia, tamaño mínimo y hash (si se conoce)."""
        spec = SPECS.get(key)
        path = self.path_for(key)
        desc = spec.description if spec else ""
        if not path.is_file():
            return ModelStatus(key, False, str(path), 0, False, "no encontrado", desc)
        size = path.stat().st_size
        min_bytes = spec.min_bytes if spec else 1024
        if size < min_bytes:
            return ModelStatus(key, True, str(path), size, False,
                               f"archivo demasiado pequeño ({size} bytes); parece corrupto", desc)
        if spec and spec.sha256:
            digest = self.sha256(path)
            if digest.lower() != spec.sha256.lower():
                return ModelStatus(key, True, str(path), size, False,
                                   "el hash no coincide; el archivo podría estar dañado", desc)
        return ModelStatus(key, True, str(path), size, True, "", desc)

    @staticmethod
    def sha256(path: Path, chunk: int = 1 << 20) -> str:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as fh:
                while True:
                    block = fh.read(chunk)
                    if not block:
                        break
                    h.update(block)
        except Exception:
            return ""
        return h.hexdigest()

    # ------------------------------------------------------------------
    def require(self, key: str) -> Path | None:
        """Ruta del modelo si es utilizable; None si falta o está dañado."""
        status = self.verify(key)
        if status.valid:
            return Path(status.path)
        if key not in self._warned:
            self._warned.add(key)
            spec = SPECS.get(key)
            extra = f" Puedes descargarlo de: {spec.url}" if spec and spec.url else ""
            log.warning(
                "Falta el modelo '%s' (%s): %s. Ese módulo quedará desactivado y "
                "YUE seguirá funcionando con capacidades reducidas.%s",
                key, status.path, status.reason or "no disponible", extra,
            )
        return None

    # ------------------------------------------------------------------
    def get_or_load(self, key: str, builder: Callable[[Path], Any]) -> Any:
        """Carga el modelo UNA sola vez por ruta; después devuelve la caché."""
        path = self.require(key)
        if path is None:
            return None
        cache_key = str(path)
        with self._lock:
            if cache_key in self._loaded:
                return self._loaded[cache_key]
        try:
            obj = builder(path)
        except Exception as exc:
            log.warning("No pude cargar el modelo '%s': %s", key, exc)
            return None
        if obj is None:
            return None
        with self._lock:
            self._loaded[cache_key] = obj
        log.info("Modelo cargado una sola vez: %s", key)
        return obj

    def release(self, key: str | None = None) -> None:
        with self._lock:
            targets = list(self._loaded) if key is None else [str(self.path_for(key))]
            for k in targets:
                obj = self._loaded.pop(k, None)
                if obj is not None:
                    try:
                        obj.close()
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    def download(self, key: str, force: bool = False) -> bool:
        """Descarga un modelo SOLO si hay autorización explícita."""
        if not self.allow_download and not force:
            log.info("Descarga de '%s' no autorizada (VISION_ALLOW_DOWNLOAD=false).", key)
            return False
        spec = SPECS.get(key)
        if spec is None or not spec.url:
            log.info("No tengo una URL oficial para '%s'.", key)
            return False
        destination = self.base / spec.filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self._downloader is not None:
            try:
                return bool(self._downloader(spec.url, destination))
            except Exception as exc:
                log.warning("Fallo descargando '%s': %s", key, exc)
                return False
        try:
            import urllib.request
            log.info("Descargando modelo '%s' (autorizado)…", key)
            urllib.request.urlretrieve(spec.url, destination)
        except Exception as exc:
            log.warning("No pude descargar '%s': %s", key, exc)
            return False
        return self.verify(key).valid

    # ------------------------------------------------------------------
    def status(self) -> dict[str, bool]:
        """Mapa {clave: utilizable?}; compatible con `ModelManager.status()`."""
        return {key: self.verify(key).valid for key in SPECS}

    def report(self) -> list[dict]:
        return [self.verify(key).as_dict() for key in SPECS]

    def missing(self) -> list[str]:
        return [key for key in SPECS if not self.verify(key).valid]

    def describe(self) -> str:
        rows = ["Modelos de visión (carpeta: %s):" % self.base]
        for key in SPECS:
            st = self.verify(key)
            marca = "OK   " if st.valid else "FALTA"
            extra = f"  <- {st.reason}" if st.reason else ""
            rows.append(f"  [{marca}] {key:22s} {SPECS[key].description}{extra}")
        faltan = self.missing()
        if faltan:
            rows.append("")
            rows.append("Los módulos que dependen de los modelos que faltan quedarán "
                        "desactivados; el resto de YUE funciona igual.")
        return "\n".join(rows)


# Compatibilidad: quien importe MODEL_FILES desde aquí sigue funcionando.
__all__ = ["ModelRegistry", "ModelSpec", "ModelStatus", "SPECS", "MODEL_FILES", "resource_path"]

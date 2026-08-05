"""Lectura de configuración del sistema de visión (MediaPipe Tasks).

Este módulo es la ÚNICA puerta de acceso a la configuración para todo el paquete
`vision/`. Lee cada valor con esta prioridad:

    1) el módulo `config` de YUE (que ya carga el .env),
    2) la variable de entorno directamente,
    3) un valor por defecto seguro.

Así el paquete funciona igual dentro de YUE, en pruebas sueltas (sin `config`)
y con un .env mínimo. No importa PyQt, ni OpenCV, ni MediaPipe: es Python puro
y se puede probar sin cámara.

REGLA ADITIVA: el interruptor maestro es `VISION_MP_ENABLED` (NO reutiliza el
`VISION_ENABLED` clásico, que en YUE controla la visión de PANTALLA). Por
defecto está APAGADO, de modo que este sistema nunca pelea por la webcam con el
`CameraObserver` clásico ni con el `core.vision` (V3/DeepFace) salvo que se
active a propósito.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:  # pragma: no cover - config puede no existir en pruebas sueltas
    import config as _yue_config  # type: ignore
except Exception:  # pragma: no cover
    _yue_config = None  # type: ignore


_TRUE = {"1", "true", "yes", "si", "sí", "on", "y", "t"}


def _raw(name: str):
    """Valor crudo: primero `config`, luego el entorno, si no None."""
    if _yue_config is not None and hasattr(_yue_config, name):
        return getattr(_yue_config, name)
    if name in os.environ:
        return os.environ[name]
    return None


def get_bool(name: str, default: bool) -> bool:
    v = _raw(name)
    if v is None:
        return bool(default)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in _TRUE


def get_int(name: str, default: int) -> int:
    v = _raw(name)
    if v is None or v == "":
        return int(default)
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return int(default)


def get_float(name: str, default: float) -> float:
    v = _raw(name)
    if v is None or v == "":
        return float(default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def get_str(name: str, default: str) -> str:
    v = _raw(name)
    if v is None:
        return default
    return str(v).strip()


# ---------------------------------------------------------------------------
# Perfiles de rendimiento (paso 24 del pedido): low / balanced / high.
# Ajustan resolución y FPS por defecto. Cada FPS concreto puede sobrescribirse
# con su propia variable de entorno.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PerformancePreset:
    name: str
    width: int
    height: int
    capture_fps: float
    face_fps: float
    landmark_fps: float
    pose_fps: float
    gesture_fps: float
    object_fps: float
    classifier_fps: float
    classifier_default: bool  # ¿el clasificador arranca activo en este perfil?


_PRESETS = {
    "low": PerformancePreset("low", 320, 240, 12, 5, 6, 5, 10, 1.0, 0.0, False),
    "balanced": PerformancePreset("balanced", 640, 480, 15, 8, 10, 8, 15, 2.0, 0.2, False),
    "high": PerformancePreset("high", 960, 720, 24, 10, 15, 10, 20, 3.0, 0.5, True),
}


def performance_preset() -> PerformancePreset:
    choice = get_str("VISION_PERFORMANCE_MODE", "balanced").lower()
    return _PRESETS.get(choice, _PRESETS["balanced"])


@dataclass(frozen=True)
class VisionSettings:
    """Foto instantánea y coherente de toda la configuración de visión."""

    # Interruptor maestro y cámara
    enabled: bool
    camera_enabled: bool
    camera_index: int
    width: int
    height: int
    target_fps: float
    privacy_mode: bool

    # Módulos (on/off)
    face_detector: bool
    face_landmarker: bool
    pose: bool
    gesture: bool
    objects: bool
    image_classifier: bool
    image_segmenter: bool
    interactive_segmenter: bool
    holistic: bool

    # Frecuencias por módulo (FPS)
    face_fps: float
    landmark_fps: float
    pose_fps: float
    gesture_fps: float
    object_fps: float
    classifier_fps: float

    # Límites
    max_faces: int
    max_hands: int

    # Confianzas mínimas
    face_min_conf: float
    pose_min_conf: float
    gesture_min_conf: float
    object_min_conf: float

    # Privacidad / depuración
    save_frames: bool
    external_upload: bool
    debug_overlay: bool

    performance_mode: str


def load() -> VisionSettings:
    """Construye un `VisionSettings` desde config/entorno, aplicando el perfil."""
    preset = performance_preset()

    holistic = get_bool("VISION_HOLISTIC_ENABLED", False)
    # Si Holistic se activa, los módulos redundantes se apagan solos (paso 13).
    face_landmarker = get_bool("VISION_FACE_LANDMARKER_ENABLED", True) and not holistic
    pose = get_bool("VISION_POSE_ENABLED", True) and not holistic
    gesture = get_bool("VISION_GESTURE_ENABLED", True) and not holistic

    return VisionSettings(
        enabled=get_bool("VISION_MP_ENABLED", False),
        camera_enabled=get_bool("CAMERA_ENABLED", True),
        camera_index=get_int("CAMERA_INDEX", 0),
        width=get_int("CAMERA_WIDTH", preset.width),
        height=get_int("CAMERA_HEIGHT", preset.height),
        target_fps=get_float("CAMERA_TARGET_FPS", preset.capture_fps),
        privacy_mode=get_bool("CAMERA_PRIVACY_MODE", True),
        face_detector=get_bool("VISION_FACE_DETECTOR_ENABLED", True),
        face_landmarker=face_landmarker,
        pose=pose,
        gesture=gesture,
        objects=get_bool("VISION_OBJECTS_ENABLED", True),
        image_classifier=get_bool("VISION_IMAGE_CLASSIFIER_ENABLED", preset.classifier_default),
        image_segmenter=get_bool("VISION_IMAGE_SEGMENTER_ENABLED", False),
        interactive_segmenter=get_bool("VISION_INTERACTIVE_SEGMENTER_ENABLED", False),
        holistic=holistic,
        face_fps=get_float("VISION_FACE_DETECTOR_FPS", preset.face_fps),
        landmark_fps=get_float("VISION_FACE_LANDMARKER_FPS", preset.landmark_fps),
        pose_fps=get_float("VISION_POSE_FPS", preset.pose_fps),
        gesture_fps=get_float("VISION_GESTURE_FPS", preset.gesture_fps),
        object_fps=get_float("VISION_OBJECT_FPS", preset.object_fps),
        classifier_fps=get_float("VISION_CLASSIFIER_FPS", preset.classifier_fps or 0.2),
        max_faces=get_int("VISION_MAX_FACES", 3),
        max_hands=get_int("VISION_MAX_HANDS", 2),
        face_min_conf=get_float("VISION_FACE_MIN_CONFIDENCE", 0.5),
        pose_min_conf=get_float("VISION_POSE_MIN_CONFIDENCE", 0.5),
        gesture_min_conf=get_float("VISION_GESTURE_MIN_CONFIDENCE", 0.6),
        object_min_conf=get_float("VISION_OBJECT_MIN_CONFIDENCE", 0.5),
        save_frames=get_bool("VISION_SAVE_FRAMES", False),
        external_upload=get_bool("VISION_EXTERNAL_UPLOAD", False),
        debug_overlay=get_bool("VISION_DEBUG_OVERLAY", False),
        performance_mode=preset.name,
    )

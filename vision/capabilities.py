"""Sistema de capacidades y degradación (punto 18 del pedido).

YUE debe poder decir en cualquier momento QUÉ puede hacer con la cámara y qué
no, y por qué. Si un modelo falta o una librería no está instalada, se desactiva
SOLO ese módulo: nunca se cierra YUE entera.

Devuelve exactamente el mapa que pide el documento:

    {"camera": True, "face_detection": True, "hand_tracking": True,
     "pose_tracking": True, "object_detection": False, "ocr": True,
     "scene_description": False, "action_recognition": True,
     "emotion_estimation": True}

y, además, un informe legible con el motivo de cada "False".
"""
from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field

log = logging.getLogger("vision.capabilities")

CAPABILITY_KEYS = (
    "camera",
    "face_detection",
    "face_landmarks",
    "hand_tracking",
    "pose_tracking",
    "object_detection",
    "ocr",
    "scene_description",
    "action_recognition",
    "emotion_estimation",
)

CAPABILITY_ES = {
    "camera": "cámara",
    "face_detection": "detección de rostros",
    "face_landmarks": "puntos faciales",
    "hand_tracking": "seguimiento de manos",
    "pose_tracking": "seguimiento de postura",
    "object_detection": "reconocimiento de objetos",
    "ocr": "lectura de textos",
    "scene_description": "descripción de la escena",
    "action_recognition": "reconocimiento de acciones",
    "emotion_estimation": "estimación de emociones",
}


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


@dataclass
class Capabilities:
    """Capacidades activas + motivo de las que no lo están."""

    values: dict[str, bool] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)

    def __getitem__(self, key: str) -> bool:
        return bool(self.values.get(key, False))

    def get(self, key: str, default: bool = False) -> bool:
        return bool(self.values.get(key, default))

    def as_dict(self) -> dict:
        """Mapa exacto del documento (solo las claves pedidas, en orden)."""
        return {k: bool(self.values.get(k, False)) for k in (
            "camera", "face_detection", "hand_tracking", "pose_tracking",
            "object_detection", "ocr", "scene_description",
            "action_recognition", "emotion_estimation",
        )}

    def full_dict(self) -> dict:
        return {k: bool(self.values.get(k, False)) for k in CAPABILITY_KEYS}

    def missing(self) -> list[str]:
        return [k for k in CAPABILITY_KEYS if not self.values.get(k, False)]

    def describe_es(self) -> str:
        activas = [CAPABILITY_ES[k] for k in CAPABILITY_KEYS if self.values.get(k)]
        lineas = ["Capacidades de visión activas: " + (", ".join(activas) if activas else "ninguna")]
        faltan = [(k, self.reasons.get(k, "no disponible")) for k in self.missing()]
        if faltan:
            lineas.append("No disponibles:")
            for key, reason in faltan:
                lineas.append(f"  - {CAPABILITY_ES.get(key, key)}: {reason}")
        return "\n".join(lineas)


def detect(
    *,
    settings=None,
    registry=None,
    privacy=None,
    camera_available: bool | None = None,
    ocr_chain=None,
) -> Capabilities:
    """Calcula las capacidades reales combinando .env, modelos y librerías."""
    values: dict[str, bool] = {}
    reasons: dict[str, str] = {}

    def deny(key: str, reason: str) -> None:
        values[key] = False
        reasons[key] = reason

    has_cv2 = _installed("cv2")
    has_mp = _installed("mediapipe")
    has_numpy = _installed("numpy")

    def cfg(name: str, default: bool = True) -> bool:
        if settings is None:
            return default
        return bool(getattr(settings, name, default))

    def model_ok(key: str) -> bool:
        if registry is None:
            return False
        try:
            return bool(registry.verify(key).valid)
        except Exception:
            return False

    def allowed(feature: str) -> bool:
        if privacy is None:
            return True
        try:
            state = privacy.state()
            if state.privacy_mode:
                return False
            return bool(state.features.get(feature, True))
        except Exception:
            return True

    # --- cámara -------------------------------------------------------
    if not cfg("enabled", False):
        deny("camera", "el sistema de visión está apagado (VISION_MP_ENABLED=false)")
    elif not cfg("camera_enabled", True):
        deny("camera", "la cámara está desactivada en la configuración")
    elif not has_cv2:
        deny("camera", "falta opencv-python (pip install opencv-python)")
    elif camera_available is False:
        deny("camera", "no encontré ninguna webcam disponible")
    else:
        values["camera"] = True

    base_ok = values.get("camera", False)

    # --- MediaPipe ----------------------------------------------------
    def mp_capability(key: str, model: str, feature: str, setting: str) -> None:
        if not base_ok:
            deny(key, reasons.get("camera", "la cámara no está disponible"))
        elif not has_mp:
            deny(key, "falta mediapipe (pip install mediapipe)")
        elif not cfg(setting, True):
            deny(key, "desactivado en la configuración")
        elif not allowed(feature):
            deny(key, "desactivado por privacidad")
        elif not model_ok(model):
            deny(key, f"falta el modelo {model}")
        else:
            values[key] = True

    mp_capability("face_detection", "face_detector", "faces", "face_detector")
    mp_capability("face_landmarks", "face_landmarker", "faces", "face_landmarker")
    mp_capability("pose_tracking", "pose_landmarker", "pose", "pose")
    mp_capability("object_detection", "object_detector", "objects", "objects")

    # Manos: vale el hand_landmarker O el gesture_recognizer.
    if not base_ok:
        deny("hand_tracking", reasons.get("camera", "la cámara no está disponible"))
    elif not has_mp:
        deny("hand_tracking", "falta mediapipe (pip install mediapipe)")
    elif not allowed("hands"):
        deny("hand_tracking", "desactivado por privacidad")
    elif model_ok("hand_landmarker") or model_ok("gesture_recognizer"):
        values["hand_tracking"] = True
    else:
        deny("hand_tracking", "falta el modelo hand_landmarker o gesture_recognizer")

    # --- OCR ----------------------------------------------------------
    if not base_ok:
        deny("ocr", reasons.get("camera", "la cámara no está disponible"))
    elif not cfg("ocr_enabled", True):
        deny("ocr", "desactivado en la configuración")
    elif not allowed("ocr"):
        deny("ocr", "desactivado por privacidad")
    elif not has_cv2:
        deny("ocr", "falta opencv-python para preparar la imagen")
    else:
        engine_ok = None
        if ocr_chain is not None:
            try:
                engine_ok = bool(ocr_chain.available)
            except Exception:
                engine_ok = None
        if engine_ok is None:
            engine_ok = _installed("paddleocr") or _installed("easyocr") or _installed("pytesseract")
        if engine_ok:
            values["ocr"] = True
        else:
            deny("ocr", "no hay ningún motor OCR instalado (paddleocr, easyocr o Tesseract)")

    # --- escena -------------------------------------------------------
    if not base_ok:
        deny("scene_description", reasons.get("camera", "la cámara no está disponible"))
    elif not cfg("scene_description_enabled", True):
        deny("scene_description", "desactivado en la configuración")
    elif not allowed("scene"):
        deny("scene_description", "desactivado por privacidad")
    elif not has_numpy:
        deny("scene_description", "falta numpy")
    else:
        values["scene_description"] = True

    # --- acciones -----------------------------------------------------
    if not base_ok:
        deny("action_recognition", reasons.get("camera", "la cámara no está disponible"))
    elif not cfg("actions_enabled", True):
        deny("action_recognition", "desactivado en la configuración")
    elif not allowed("actions"):
        deny("action_recognition", "desactivado por privacidad")
    elif not (values.get("pose_tracking") or values.get("hand_tracking")):
        deny("action_recognition", "necesita postura o manos para tener evidencias")
    else:
        values["action_recognition"] = True

    # --- emociones ----------------------------------------------------
    if not base_ok:
        deny("emotion_estimation", reasons.get("camera", "la cámara no está disponible"))
    elif not cfg("emotions_enabled", True):
        deny("emotion_estimation", "desactivado en la configuración")
    elif not allowed("emotions"):
        deny("emotion_estimation", "desactivado por privacidad")
    elif not (values.get("face_landmarks") or values.get("face_detection")):
        deny("emotion_estimation", "necesita ver el rostro")
    else:
        values["emotion_estimation"] = True

    for key in CAPABILITY_KEYS:
        values.setdefault(key, False)
        if not values[key]:
            reasons.setdefault(key, "no disponible")

    return Capabilities(values=values, reasons=reasons)

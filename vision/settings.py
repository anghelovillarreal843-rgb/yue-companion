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
    """Booleano de config/entorno.

    CORRECCIÓN (fallo real, y era el que dejaba a YUE ciega): `config.py`
    declara a propósito algunas banderas como CADENA VACÍA para decir «no está
    definida, usa la lógica de respaldo». El caso concreto:

        VISION_CAMERA_ENABLED = os.getenv("VISION_CAMERA_ENABLED", "").strip()

    `get_bool` no distinguía "" de "false": `"" not in _TRUE` -> False. Así que
    `camera_enabled` salía False aunque `VISION_REPLACE_LEGACY=true`, y
    `VisionSystem.start()` se cortaba en seco en:

        if not self.cfg.camera_enabled: return

    Es decir: TODO el sistema de visión se construía y no arrancaba nunca. Ni
    cámara, ni detectores, ni eventos, ni reacciones. Ahora la cadena vacía (y
    los espacios en blanco) se tratan como «sin definir» y cae al `default`,
    que es justo lo que la lógica de respaldo espera.
    """
    v = _raw(name)
    if v is None:
        return bool(default)
    if isinstance(v, bool):
        return v
    texto = str(v).strip().lower()
    if texto == "":
        return bool(default)
    return texto in _TRUE


def get_confidence(name: str, default: float) -> float:
    """Confianza 0..1, tolerando que venga en PORCENTAJE.

    CORRECCIÓN (segunda contradicción real): `VISION_EMOTION_MIN_CONFIDENCE` la
    comparten dos sistemas con escalas distintas. El de percepción V3
    (`core/vision`) la lee en porcentaje (60 = 60%), y el `.env` del proyecto
    tiene `VISION_EMOTION_MIN_CONFIDENCE=60`. Este paquete la compara contra
    confianzas 0..1, así que el umbral quedaba en 60.0: IMPOSIBLE de superar y
    las emociones no se emitían jamás.

    Se normaliza: cualquier valor > 1 se entiende como porcentaje.
    """
    valor = get_float(name, default)
    if valor > 1.0:
        valor = valor / 100.0
    return max(0.0, min(1.0, valor))


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


def _languages(raw: str) -> tuple:
    """Convierte 'es,en' en ('es', 'en'). Español si viene vacío."""
    if isinstance(raw, (list, tuple)):
        items = [str(x).strip().lower() for x in raw]
    else:
        items = [p.strip().lower() for p in str(raw or "").replace(";", ",").split(",")]
    items = [i for i in items if i]
    return tuple(items) or ("es",)


def get_fps(name: str, default: float) -> float:
    """FPS con convención de YUE: 0 (o negativo) significa «usa el perfil».

    CORRECCIÓN: `config.py` declara los FPS con valor 0 documentado como «usa el
    valor del perfil de rendimiento», pero `get_float` los devolvía tal cual.
    El resultado era que la captura corría a 1 FPS (por el `max(1.0, ...)` del
    CameraService) y que TODOS los módulos quedaban desactivados, porque el
    planificador entiende `fps <= 0` como «módulo apagado». Aquí se respeta la
    convención documentada.
    """
    value = get_float(name, default)
    return float(default) if value <= 0 else value


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

    # ------------------------------------------------------------------
    # ADITIVO (visión avanzada): todo lo de abajo tiene valor por defecto, así
    # que cualquier código que construya `VisionSettings` a mano sigue igual.
    # ------------------------------------------------------------------
    # Percepción avanzada
    perception_enabled: bool = False
    hands_enabled: bool = True
    hand_fps: float = 12.0
    max_people: int = 4
    device: str = "auto"
    debug: bool = False

    # OCR
    ocr_enabled: bool = True
    ocr_only_on_request: bool = True
    ocr_engine: str = "auto"
    ocr_languages: tuple = ("es",)
    ocr_min_agreements: int = 3
    ocr_min_confidence: float = 0.35

    # Escena, acciones y emociones
    scene_description_enabled: bool = True
    scene_fps: float = 0.3
    actions_enabled: bool = True
    action_fps: float = 6.0
    emotions_enabled: bool = True
    emotion_min_confidence: float = 0.45
    emotion_use_voice: bool = False
    text_watch_fps: float = 0.5

    # Privacidad avanzada
    process_local: bool = True
    save_events: bool = False
    allow_cloud: bool = False
    only_on_request: bool = False

    # Eventos
    event_min_duration: float = 0.45
    event_cooldown: float = 4.0

    # Modelos
    model_variant: str = "full"
    allow_download: bool = False

    # Accesibilidad (control del cursor por cabeza)
    head_control_fps: float = 18.0

    # Migración
    replace_legacy: bool = False
    legacy_camera_enabled: bool = True
    auto_search_camera: bool = True
    max_camera_index: int = 4

    # ------------------------------------------------------------------
    # ADITIVO (capa reactiva): conecta la percepción con el avatar y la voz.
    # Todo tiene valor por defecto, así que quien construya `VisionSettings` a
    # mano (pruebas incluidas) sigue funcionando igual.
    # ------------------------------------------------------------------
    reactive_enabled: bool = True
    reactive_poll_hz: float = 4.0
    reactive_avatar: bool = True          # ¿puede mover el avatar?
    reactive_speech: bool = True          # ¿puede decir frases por su cuenta?
    reactive_greet_cooldown: float = 45.0
    reactive_emotion_cooldown: float = 25.0
    reactive_comment_cooldown: float = 90.0
    reactive_object_cooldown: float = 240.0
    reactive_gaze_cooldown: float = 180.0
    reactive_gaze_seconds: float = 3.5
    reactive_gaze_opens_chat: bool = True
    reactive_max_phrases_per_minute: float = 2.0
    reactive_log_gap: float = 1.5
    # OCR automático cuando aparece un documento estable frente a la cámara.
    ocr_auto_on_document: bool = True
    ocr_document_min_score: float = 0.55


def load() -> VisionSettings:
    """Construye un `VisionSettings` desde config/entorno, aplicando el perfil."""
    preset = performance_preset()

    holistic = get_bool("VISION_HOLISTIC_ENABLED", False)
    # Si Holistic se activa, los módulos redundantes se apagan solos (paso 13).
    face_landmarker = get_bool("VISION_FACE_LANDMARKER_ENABLED", True) and not holistic
    pose = get_bool("VISION_POSE_ENABLED", True) and not holistic
    gesture = get_bool("VISION_GESTURE_ENABLED", True) and not holistic

    # ------------------------------------------------------------------
    # CORRECCIÓN (contradicción real detectada en uso): `CAMERA_ENABLED` es el
    # interruptor del observador CLÁSICO. Al migrar hay que apagarlo para que no
    # pelee por la webcam... pero gobernaba TAMBIÉN al sistema nuevo, así que
    # apagarlo dejaba a YUE sin visión de ninguna clase.
    #
    # Ahora el sistema nuevo tiene su propio interruptor:
    #   - `VISION_CAMERA_ENABLED` explícito manda siempre,
    #   - si no está y `VISION_REPLACE_LEGACY=true`, el motor nuevo ES el sistema
    #     de cámara: se enciende aunque `CAMERA_ENABLED=false` (que en ese caso
    #     solo significa "el observador clásico no abre la webcam"),
    #   - si no está y no hay migración, se hereda `CAMERA_ENABLED` como antes,
    #     de modo que quien tenía la cámara apagada la sigue teniendo apagada.
    # ------------------------------------------------------------------
    legacy_camera = get_bool("CAMERA_ENABLED", True)
    replace_legacy = get_bool("VISION_REPLACE_LEGACY", False)
    camera_enabled = get_bool("VISION_CAMERA_ENABLED",
                              True if replace_legacy else legacy_camera)

    return VisionSettings(
        enabled=get_bool("VISION_MP_ENABLED", False),
        camera_enabled=camera_enabled,
        camera_index=get_int("CAMERA_INDEX", 0),
        width=get_int("CAMERA_WIDTH", preset.width),
        height=get_int("CAMERA_HEIGHT", preset.height),
        target_fps=get_fps("CAMERA_TARGET_FPS", preset.capture_fps),
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
        face_fps=get_fps("VISION_FACE_DETECTOR_FPS", preset.face_fps),
        landmark_fps=get_fps("VISION_FACE_LANDMARKER_FPS", preset.landmark_fps),
        pose_fps=get_fps("VISION_POSE_FPS", preset.pose_fps),
        gesture_fps=get_fps("VISION_GESTURE_FPS", preset.gesture_fps),
        object_fps=get_fps("VISION_OBJECT_FPS", preset.object_fps),
        classifier_fps=get_fps("VISION_CLASSIFIER_FPS", preset.classifier_fps or 0.2),
        max_faces=get_int("VISION_MAX_FACES", 3),
        max_hands=get_int("VISION_MAX_HANDS", 2),
        face_min_conf=get_confidence("VISION_FACE_MIN_CONFIDENCE", 0.5),
        pose_min_conf=get_confidence("VISION_POSE_MIN_CONFIDENCE", 0.5),
        gesture_min_conf=get_confidence("VISION_GESTURE_MIN_CONFIDENCE", 0.6),
        object_min_conf=get_confidence("VISION_OBJECT_MIN_CONFIDENCE", 0.5),
        save_frames=get_bool("VISION_SAVE_FRAMES", False),
        external_upload=get_bool("VISION_EXTERNAL_UPLOAD", False),
        debug_overlay=get_bool("VISION_DEBUG_OVERLAY", False),
        performance_mode=preset.name,

        # --- ADITIVO: visión avanzada ---------------------------------
        # Motor de percepción (OCR, escena, acciones, emociones). Va dentro del
        # mismo interruptor maestro VISION_MP_ENABLED, con su propio apagado.
        perception_enabled=get_bool("VISION_PERCEPTION_ENABLED", True),
        hands_enabled=get_bool("VISION_HANDS_ENABLED", True) and not holistic,
        hand_fps=get_fps("VISION_HAND_FPS", preset.gesture_fps),
        max_people=get_int("VISION_MAX_PEOPLE", 4),
        device=get_str("VISION_DEVICE", "auto"),
        debug=get_bool("VISION_DEBUG", False),

        # OCR por cámara
        ocr_enabled=get_bool("VISION_OCR_ENABLED", True),
        ocr_only_on_request=get_bool("VISION_OCR_ONLY_ON_REQUEST", True),
        ocr_engine=get_str("VISION_OCR_ENGINE", "auto"),
        ocr_languages=_languages(get_str("VISION_OCR_LANGUAGES", "es")),
        ocr_min_agreements=get_int("VISION_OCR_MIN_AGREEMENTS", 3),
        ocr_min_confidence=get_confidence("VISION_OCR_MIN_CONFIDENCE", 0.35),

        # Escena, acciones y emociones
        scene_description_enabled=get_bool("VISION_SCENE_DESCRIPTION_ENABLED", True),
        scene_fps=get_fps("VISION_SCENE_FPS", 0.3),
        actions_enabled=get_bool("VISION_ACTIONS_ENABLED", True),
        action_fps=get_fps("VISION_ACTION_FPS", 6.0),
        emotions_enabled=get_bool("VISION_EMOTIONS_ENABLED", True),
        emotion_min_confidence=get_confidence("VISION_EMOTION_MIN_CONFIDENCE", 0.45),
        # La voz SOLO se usa como señal afectiva con permiso explícito.
        emotion_use_voice=get_bool("VISION_EMOTION_USE_VOICE", False),
        text_watch_fps=get_fps("VISION_TEXT_WATCH_FPS", 0.5),

        # Privacidad avanzada
        process_local=get_bool("VISION_PROCESS_LOCAL", True),
        save_events=get_bool("VISION_SAVE_EVENTS", False),
        allow_cloud=get_bool("VISION_ALLOW_CLOUD", False),
        only_on_request=get_bool("VISION_ONLY_ON_REQUEST", False),

        # Antirrebote de eventos
        event_min_duration=get_float("VISION_EVENT_MIN_DURATION", 0.45),
        event_cooldown=get_float("VISION_EVENT_COOLDOWN", 4.0),

        # Modelos
        model_variant=get_str("VISION_MODEL_VARIANT", "full"),
        allow_download=get_bool("VISION_ALLOW_DOWNLOAD", False),

        # Control del cursor por cabeza: los landmarks van a más FPS que el
        # resto, porque el cursor se nota si va lento.
        head_control_fps=get_fps("VISION_HEAD_CONTROL_FPS", 18.0),

        # Migración desde el observador clásico
        replace_legacy=replace_legacy,
        legacy_camera_enabled=legacy_camera,
        auto_search_camera=get_bool("VISION_AUTO_SEARCH_CAMERA", True),
        max_camera_index=get_int("VISION_MAX_CAMERA_INDEX", 4),

        # --- ADITIVO: capa reactiva (percepción -> avatar y voz) -------
        reactive_enabled=get_bool("VISION_REACTIVE_ENABLED", True),
        reactive_poll_hz=get_fps("VISION_REACTIVE_POLL_HZ", 4.0),
        reactive_avatar=get_bool("VISION_REACTIVE_AVATAR", True),
        reactive_speech=get_bool("VISION_REACTIVE_SPEECH", True),
        reactive_greet_cooldown=get_float("VISION_REACTIVE_GREET_COOLDOWN", 45.0),
        reactive_emotion_cooldown=get_float("VISION_REACTIVE_EMOTION_COOLDOWN", 25.0),
        reactive_comment_cooldown=get_float("VISION_REACTIVE_COMMENT_COOLDOWN", 90.0),
        reactive_object_cooldown=get_float("VISION_REACTIVE_OBJECT_COOLDOWN", 240.0),
        reactive_gaze_cooldown=get_float("VISION_REACTIVE_GAZE_COOLDOWN", 180.0),
        reactive_gaze_seconds=get_float("VISION_REACTIVE_GAZE_SECONDS", 3.5),
        reactive_gaze_opens_chat=get_bool("VISION_REACTIVE_GAZE_OPENS_CHAT", True),
        reactive_max_phrases_per_minute=get_float("VISION_REACTIVE_MAX_PHRASES_PER_MINUTE", 2.0),
        reactive_log_gap=get_float("VISION_REACTIVE_LOG_GAP", 1.5),
        ocr_auto_on_document=get_bool("VISION_OCR_AUTO_ON_DOCUMENT", True),
        ocr_document_min_score=get_confidence("VISION_OCR_DOCUMENT_MIN_SCORE", 0.55),
    )

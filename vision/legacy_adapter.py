"""Adaptador de compatibilidad con el `CameraObserver` clásico (punto 16).

La migración se hace SIN romper nada: este adaptador ofrece exactamente la misma
interfaz que `core.camera_observer.CameraObserver` espera `main.py`, pero por
dentro habla con el nuevo `PerceptionEngine`.

Interfaz reproducida (comprobada contra los usos reales de main.py):

    .enabled            .active (propiedad)
    .start()            .stop()
    .latest()           .describe()
    .context_for_ai()   .risk_signal()
    .set_landmark_consumer(fn)     .set_fast_mode(bool)

Modo de uso durante la transición (en `main.py`):

    if config.VISION_MP_ENABLED and config.VISION_REPLACE_LEGACY:
        self.camera = LegacyCameraObserverAdapter(self.vision_mp)
    else:
        self.camera = CameraObserver(...)      # como siempre

Así se puede probar el sistema nuevo sin borrar el viejo. Cuando el nuevo esté
validado, `core/camera_observer.py` se marca como obsoleto y se elimina solo
cuando ya nadie lo importe.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("vision.legacy")


@dataclass
class LegacyObservation:
    """Doble de `CameraObservation` con los campos que main.py consulta."""

    timestamp: float = field(default_factory=time.time)
    faces: int = 0
    person_emotions: tuple = ()
    expressions: tuple = ()
    postures: tuple = ()
    summary: str = ""

    @property
    def people(self) -> int:
        return self.faces

    def summary_es(self) -> str:
        return self.summary

    def rich_summary_es(self) -> str:
        return self.summary


@dataclass
class LegacyEmotion:
    """Doble de la emoción que `_camera_empathy` espera (key/confidence)."""

    key: str = "neutral"
    confidence: float = 0.0
    label_es: str = "neutral"


class LegacyCameraObserverAdapter:
    """Fachada antigua sobre el motor de percepción nuevo."""

    def __init__(
        self,
        engine,
        *,
        observation_callback: Callable | None = None,
        status_callback: Callable | None = None,
        context_max_age: float = 8.0,
    ) -> None:
        self.engine = engine
        self.observation_callback = observation_callback or (lambda _obs: None)
        self.status_callback = status_callback or (lambda _t, _a: None)
        self.context_max_age = float(context_max_age)
        self.enabled = True
        self._landmark_consumer = None
        self._fast_mode = False
        self._last_obs: LegacyObservation | None = None

    # -- ciclo de vida ---------------------------------------------------
    def start(self) -> None:
        if self.engine is None:
            return
        try:
            self.engine.start()
        except Exception as exc:
            log.warning("El adaptador no pudo arrancar la percepción: %s", exc)

    def stop(self) -> None:
        if self.engine is None:
            return
        try:
            self.engine.stop()
        except Exception:
            pass

    @property
    def active(self) -> bool:
        return bool(self.engine is not None and self.engine.running)

    # -- accesibilidad (control por cabeza) -------------------------------
    def set_landmark_consumer(self, consumer) -> None:
        """Compatibilidad con `HeadCursorController`.

        El motor nuevo no entrega landmarks crudos por fotograma; se guarda el
        consumidor para no romper la llamada y se avisa una sola vez.
        """
        self._landmark_consumer = consumer
        if consumer is not None:
            log.info("El control por cabeza usa el observador clásico; con el motor "
                     "nuevo mantén CameraObserver activo para esa función.")

    def set_fast_mode(self, on: bool) -> None:
        self._fast_mode = bool(on)

    # -- estado -----------------------------------------------------------
    def latest(self) -> LegacyObservation | None:
        if self.engine is None or not self.engine.running:
            return None
        try:
            snap = self.engine.snapshot()
        except Exception:
            return None
        presence = snap.get("presence", {}) or {}
        afecto = snap.get("affective", {}) or {}
        emotions: tuple = ()
        estado = afecto.get("affective_state", "undetermined")
        if estado != "undetermined":
            from vision.analyzers.emotion_analyzer import CATEGORIES
            emotions = (LegacyEmotion(
                key=_map_emotion_key(estado),
                confidence=float(afecto.get("confidence", 0.0)),
                label_es=CATEGORIES.get(estado, estado),
            ),)
        obs = LegacyObservation(
            timestamp=time.time(),
            faces=int(presence.get("count", 0)),
            person_emotions=emotions,
            summary=self.describe(),
        )
        self._last_obs = obs
        return obs

    def describe(self) -> str:
        if self.engine is None:
            return "El sistema de visión no está disponible."
        if not self.engine.running:
            return "La cámara no está activa ahora mismo."
        try:
            return self.engine.describe_room()
        except Exception:
            return "La cámara está activa, pero aún estoy preparando el análisis."

    def context_for_ai(self, max_age: float | None = None) -> str:
        if self.engine is None or not self.engine.running:
            return ""
        try:
            from vision.context_builder import VisionContextBuilder
            builder = VisionContextBuilder(max_age=float(max_age or self.context_max_age))
            ctx = builder.build(self.engine.snapshot(), events=self.engine.events)
            return ctx.text
        except Exception:
            return ""

    def risk_signal(self) -> bool:
        """Señal de riesgo VISUAL sostenida (equivalente a la del observador clásico).

        Solo se enciende con una posible caída de confianza alta, o con un estado
        afectivo negativo mantenido y con confianza alta. Nunca con un fotograma.
        """
        if self.engine is None or not self.engine.running:
            return False
        try:
            snap = self.engine.snapshot()
        except Exception:
            return False
        for a in snap.get("actions", []) or []:
            if a.get("action") == "possible_fall" and float(a.get("confidence", 0)) >= 0.85:
                return True
        afecto = snap.get("affective", {}) or {}
        if afecto.get("affective_state") in {"sad", "angry"} \
                and float(afecto.get("confidence", 0)) >= 0.8 \
                and afecto.get("certainty") == "high":
            return True
        return False


def _map_emotion_key(state: str) -> str:
    """Traduce las categorías nuevas a las claves que usa el YUE clásico."""
    return {
        "happy": "feliz", "sad": "triste", "angry": "molesta",
        "surprised": "sorprendida", "tired": "cansada", "tense": "tensa",
        "focused": "concentrada", "confused": "confundida", "neutral": "neutral",
    }.get(state, "neutral")

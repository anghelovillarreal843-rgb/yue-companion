"""Orquestador del YUE V3 PERCEPTION SYSTEM (punto 10 del pedido).

Une todas las piezas y expone una fachada simple:

    cámara -> rostro/presencia -> emoción (DeepFace) -> atención
           -> avatar reactivo + posible frase + EVENTOS al cerebro de YUE

NO modifica el núcleo de YUE. Se comunica hacia afuera de dos formas, ambas
opcionales, para no acoplarse a Qt ni a main.py:

  1) EVENTOS: emite `VisionEvent` por un `EventBus` (quien quiera, se suscribe).
  2) CALLBACKS: `on_avatar_emotion`, `on_speak`, `on_status` para que main.py
     los conecte a `pet.set_emotion`, `_yue_say`, etc. (marshalando a Qt).

OPT-IN: está APAGADO por defecto (`VISION_V3_ENABLED=false`). Así convive con el
observador clásico `core/camera_observer.py` sin pelear por la webcam. Al
encenderlo, usa su propio motor de cámara; si el observador clásico también está
activo con el mismo índice, se avisa (no se rompe nada).

Todo degradable: sin DeepFace hay presencia/atención pero no emociones; sin
cámara, el controlador queda inactivo y lo informa.
"""
from __future__ import annotations

import threading
import time

try:
    import config  # type: ignore
except Exception:  # pragma: no cover
    config = None  # type: ignore

from core.vision import avatar_emotion_controller as avatar_ctl
from core.vision.attention_detector import AttentionDetector
from core.vision.camera import CameraEngine
from core.vision.emotion_ai import EmotionAI
from core.vision.emotion_manager import EmotionManager
from core.vision.events import EventBus, VisionEvent
from core.vision.face_detector import FaceDetector
from core.vision.perf_profile import PerfProfile, describe as describe_profile, resolve_profile


def _cfg(name: str, default):
    return getattr(config, name, default) if config is not None else default


class VisionController:
    def __init__(
        self,
        event_bus: EventBus | None = None,
        on_avatar_emotion=None,       # (name, intensity, duration_ms) -> None
        on_speak=None,                # (text) -> None
        on_status=None,               # (text, active) -> None
        profile: PerfProfile | None = None,
        # --- inyectables (pruebas / usos avanzados) ---
        camera_factory=None,          # (profile) -> (cap, index)
        analyzer=None,                # (roi) -> {"emotion","confidence"} | None
        face_provider=None,           # (frame) -> [(x,y,w,h,score), ...]
        persona: str | None = None,
    ) -> None:
        self.enabled = bool(_cfg("VISION_V3_ENABLED", False))
        self.bus = event_bus or EventBus()
        self._on_avatar_emotion = on_avatar_emotion or (lambda *_a: None)
        self._on_speak = on_speak or (lambda *_a: None)
        self._on_status = on_status or (lambda *_a: None)

        self.profile = profile or resolve_profile()
        self.face = FaceDetector(box_provider=face_provider)
        self.emotion_ai = EmotionAI(
            emotion_interval=self.profile.emotion_interval, analyzer=analyzer
        )
        self.attention = AttentionDetector()
        self.manager = EmotionManager(persona=persona)

        self.camera = CameraEngine(
            frame_callback=self._on_frame,
            status_callback=self._handle_status,
            profile=self.profile,
            capture_factory=camera_factory,
        )

        self._lock = threading.Lock()
        self._last_face = None            # último FaceResult
        self._last_emotion = None         # última EmotionReading
        self._last_emo_emit_at = 0.0      # marca del último emotion event emitido
        self._last_avatar_at = 0.0        # anti-spam del gesto empático
        self._avatar_gap = float(_cfg("VISION_AVATAR_MIN_GAP", 6.0))

    # ---------------------------------------------------------------
    def start(self) -> None:
        if not self.enabled:
            print("[vision.v3] desactivado (VISION_V3_ENABLED=false). No abro la cámara.")
            return
        self._warn_camera_conflict()
        print("[vision.v3] iniciando percepción.", describe_profile(self.profile),
              "· DeepFace:", "sí" if self.emotion_ai.available else "no disponible")
        self.camera.start()

    def stop(self) -> None:
        try:
            self.camera.stop()
        finally:
            self.face.close()

    @property
    def active(self) -> bool:
        return self.camera.active

    def latest(self):
        with self._lock:
            return self._last_face, self._last_emotion

    def snapshot(self) -> dict:
        """Estado actual como dict (sin imágenes)."""
        face, emo = self.latest()
        data = {
            "enabled": self.enabled,
            "active": self.active,
            "profile": self.profile.name,
            "deepface": self.emotion_ai.available,
            "attention": self.attention.state,
            "user_present": bool(getattr(face, "user_present", False)),
        }
        if getattr(face, "face_position", None):
            data["face_position"] = face.face_position
        if emo is not None:
            data["emotion"] = emo.as_dict()
        return data

    def describe(self) -> str:
        if not self.enabled:
            return "El sistema de percepción V3 está desactivado."
        if not self.active:
            return "La cámara de percepción aún no está activa."
        face, emo = self.latest()
        if not getattr(face, "user_present", False):
            return "No detecto a nadie frente a la cámara ahora mismo."
        estado = {"atento": "mirando de frente", "presente": "presente",
                  "ausente": "ausente"}.get(self.attention.state, self.attention.state)
        if emo is not None:
            return f"Detecto a una persona ({estado}); expresión aparente: {emo.emotion} ({emo.confidence}%)."
        return f"Detecto a una persona ({estado})."

    def context_for_ai(self, max_age: float = 8.0) -> str:
        _face, emo = self.latest()
        if emo is None:
            return ""
        if time.time() - emo.at > max_age:
            return ""
        return f"Por la cámara, la expresión de la persona parece '{emo.emotion}' (~{emo.confidence}%)."

    # ---------------------------------------------------------------
    def _on_frame(self, frame_bgr, index: int) -> None:
        """Pipeline por fotograma (corre en el hilo de la cámara)."""
        now = time.time()
        face = self.face.detect(frame_bgr)
        with self._lock:
            self._last_face = face

        # -- atención / presencia --
        upd = self.attention.update(face.user_present, face.face_position, now)
        self._emit_attention_events(face, upd, now)

        # -- emoción (throttle por perfil) --
        if face.user_present and self.profile.run_emotion:
            reading = self.emotion_ai.analyze(face.roi, now)
            if reading is not None:
                with self._lock:
                    self._last_emotion = reading
                self._handle_emotion(reading, now)

    def _emit_attention_events(self, face, upd, now: float) -> None:
        if upd.presence_changed:
            self.bus.emit(VisionEvent.presence(face.user_present, face.face_position))
        if upd.face_lost:
            self.bus.emit(VisionEvent.face_lost())
        if upd.attention_changed:
            self.bus.emit(VisionEvent.attention(upd.state))
            gesto = avatar_ctl.attention_avatar_emotion(upd.state)
            if gesto:
                self._apply_avatar(*gesto, now=now, force=True)
        if upd.long_absence:
            self.bus.emit(VisionEvent.absence(upd.absent_seconds))
            try:
                self._on_speak(self.manager.absence_line())
            except Exception:
                pass

    def _handle_emotion(self, reading, now: float) -> None:
        # Solo actuamos cuando es un análisis FRESCO (no la caché repetida).
        if reading.at == self._last_emo_emit_at:
            return
        self._last_emo_emit_at = reading.at

        # 1) Evento al cerebro de YUE (forma EXACTA del documento).
        self.bus.emit(VisionEvent.emotion(reading.emotion, reading.confidence))

        # 2) Avatar empático (con su propio anti-spam).
        gesto = avatar_ctl.empathic_avatar_emotion(reading.emotion)
        if gesto:
            self._apply_avatar(*gesto, now=now)

        # 3) Posible frase (con límites por hora/enfriamiento).
        reaccion = self.manager.maybe_react(reading.emotion, reading.confidence, now)
        if reaccion is not None:
            try:
                self._on_speak(reaccion.text)
            except Exception:
                pass

    def _apply_avatar(self, name, intensity, duration_ms, now: float, force: bool = False) -> None:
        if not force and (now - self._last_avatar_at) < self._avatar_gap:
            return
        self._last_avatar_at = now
        try:
            self._on_avatar_emotion(name, intensity, duration_ms)
        except Exception as exc:
            print("[vision.v3] no pude aplicar gesto del avatar:", exc)

    def _handle_status(self, text: str, active: bool) -> None:
        self.bus.emit(VisionEvent.status(text, active))
        try:
            self._on_status(text, active)
        except Exception:
            pass

    def _warn_camera_conflict(self) -> None:
        try:
            legacy_on = bool(_cfg("CAMERA_ENABLED", True))
            legacy_idx = int(_cfg("CAMERA_MAX_INDEX", 3))  # el clásico barre 0..idx
            v3_idx = int(_cfg("CAMERA_INDEX", _cfg("VISION_CAMERA_INDEX", 0)))
            if legacy_on and 0 <= v3_idx <= legacy_idx:
                print(
                    "[vision.v3] AVISO: el observador clásico (CAMERA_ENABLED=true) puede usar "
                    f"la misma webcam (índice {v3_idx}). Para evitar conflictos, pon "
                    "CAMERA_ENABLED=false o dale a V3 otra cámara con CAMERA_INDEX."
                )
        except Exception:
            pass

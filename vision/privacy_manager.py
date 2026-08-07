"""Gestor central de privacidad de la visión (punto 13 del pedido).

TODO módulo que quiera analizar algo debe preguntar antes aquí. Es la única
puerta que decide si se puede hacer OCR, estimar emociones, detectar objetos,
describir la escena, guardar eventos o enviar algo fuera del equipo.

Reglas duras (no configurables por accidente):
  - procesamiento LOCAL por defecto (`VISION_PROCESS_LOCAL=true`),
  - NUNCA se guardan fotogramas ni vídeo por defecto (`VISION_SAVE_FRAMES=false`),
  - NUNCA se envía imagen a un servicio externo sin `VISION_ALLOW_CLOUD=true`,
  - `modo privacidad` apaga TODO el análisis en una sola llamada,
  - `VISION_ONLY_ON_REQUEST=true` deja la percepción dormida hasta que la
    persona pide explícitamente "mira", "lee esto", "¿qué ves?".

Es Python puro y sin estado global: se instancia una vez en el `VisionSystem` y
se comparte. Thread-safe con un lock corto porque lo consultan varios hilos.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

# Capacidades que se pueden apagar de forma independiente.
FEATURES: tuple[str, ...] = (
    "ocr",
    "emotions",
    "objects",
    "actions",
    "scene",
    "faces",
    "hands",
    "pose",
)

_FEATURE_ES = {
    "ocr": "lectura de textos",
    "emotions": "estimación de emociones",
    "objects": "reconocimiento de objetos",
    "actions": "reconocimiento de acciones",
    "scene": "descripción de la escena",
    "faces": "detección de rostros",
    "hands": "seguimiento de manos",
    "pose": "seguimiento de postura",
}


@dataclass
class PrivacyState:
    """Foto serializable del estado de privacidad (para la interfaz y /vision)."""

    camera_on: bool = False
    privacy_mode: bool = False
    only_on_request: bool = False
    process_local: bool = True
    save_frames: bool = False
    save_events: bool = False
    allow_cloud: bool = False
    features: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "camara_encendida": self.camera_on,
            "modo_privacidad": self.privacy_mode,
            "solo_bajo_peticion": self.only_on_request,
            "procesamiento_local": self.process_local,
            "guardar_fotogramas": self.save_frames,
            "guardar_eventos": self.save_events,
            "permitir_nube": self.allow_cloud,
            "modulos": dict(self.features),
        }


class PrivacyManager:
    """Interruptores de privacidad + ventana de "análisis bajo petición"."""

    def __init__(
        self,
        *,
        process_local: bool = True,
        save_frames: bool = False,
        save_events: bool = False,
        allow_cloud: bool = False,
        only_on_request: bool = False,
        features: dict[str, bool] | None = None,
        request_window: float = 12.0,
        on_change: Callable[[PrivacyState], None] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._process_local = bool(process_local)
        # Nunca se enciende solo: hay que pedirlo a propósito en el .env.
        self._save_frames = bool(save_frames)
        self._save_events = bool(save_events)
        self._allow_cloud = bool(allow_cloud)
        self._only_on_request = bool(only_on_request)
        self._privacy_mode = False
        self._camera_on = False
        self._request_window = max(1.0, float(request_window))
        self._request_until = 0.0
        self._on_change = on_change or (lambda _s: None)

        base = {name: True for name in FEATURES}
        if features:
            for key, value in features.items():
                if key in base:
                    base[key] = bool(value)
        self._features = base

    # ------------------------------------------------------------------
    # Consultas: ¿puedo hacer esto AHORA?
    # ------------------------------------------------------------------
    def allows(self, feature: str) -> bool:
        """True si `feature` puede ejecutarse en este instante."""
        with self._lock:
            if self._privacy_mode:
                return False
            if not self._camera_on:
                return False
            if not self._features.get(feature, False):
                return False
            if self._only_on_request and not self._request_open_locked():
                return False
            return True

    def allows_any(self, features: Iterable[str]) -> bool:
        return any(self.allows(f) for f in features)

    def can_save_frames(self) -> bool:
        with self._lock:
            return self._save_frames and not self._privacy_mode

    def can_save_events(self) -> bool:
        with self._lock:
            return self._save_events and not self._privacy_mode

    def can_use_cloud(self) -> bool:
        """Solo True si la persona lo autorizó de forma explícita."""
        with self._lock:
            return self._allow_cloud and not self._privacy_mode and not self._process_local

    def is_private(self) -> bool:
        with self._lock:
            return self._privacy_mode

    def only_on_request(self) -> bool:
        with self._lock:
            return self._only_on_request

    def camera_on(self) -> bool:
        with self._lock:
            return self._camera_on

    # ------------------------------------------------------------------
    # Ventana de petición explícita ("mira esto", "lee esto")
    # ------------------------------------------------------------------
    def _request_open_locked(self) -> bool:
        return time.monotonic() < self._request_until

    def request_analysis(self, seconds: float | None = None) -> None:
        """Abre una ventana temporal de análisis pedida por la persona."""
        with self._lock:
            window = self._request_window if seconds is None else max(1.0, float(seconds))
            self._request_until = time.monotonic() + window

    def close_request(self) -> None:
        with self._lock:
            self._request_until = 0.0

    def request_open(self) -> bool:
        with self._lock:
            return self._request_open_locked()

    # ------------------------------------------------------------------
    # Interruptores
    # ------------------------------------------------------------------
    def set_camera(self, on: bool) -> None:
        with self._lock:
            self._camera_on = bool(on)
        self._notify()

    def set_privacy_mode(self, on: bool) -> None:
        """Modo privacidad: apaga TODO el análisis de golpe."""
        with self._lock:
            self._privacy_mode = bool(on)
            if self._privacy_mode:
                self._request_until = 0.0
        self._notify()

    def set_only_on_request(self, on: bool) -> None:
        with self._lock:
            self._only_on_request = bool(on)
            if not self._only_on_request:
                self._request_until = 0.0
        self._notify()

    def set_feature(self, feature: str, on: bool) -> bool:
        """Activa/desactiva un módulo concreto. False si el nombre no existe."""
        with self._lock:
            if feature not in self._features:
                return False
            self._features[feature] = bool(on)
        self._notify()
        return True

    def set_save_events(self, on: bool) -> None:
        with self._lock:
            self._save_events = bool(on)
        self._notify()

    def set_save_frames(self, on: bool) -> None:
        with self._lock:
            self._save_frames = bool(on)
        self._notify()

    def set_allow_cloud(self, on: bool) -> None:
        with self._lock:
            self._allow_cloud = bool(on)
        self._notify()

    # ------------------------------------------------------------------
    def state(self) -> PrivacyState:
        with self._lock:
            return PrivacyState(
                camera_on=self._camera_on,
                privacy_mode=self._privacy_mode,
                only_on_request=self._only_on_request,
                process_local=self._process_local,
                save_frames=self._save_frames,
                save_events=self._save_events,
                allow_cloud=self._allow_cloud,
                features=dict(self._features),
            )

    def describe_es(self) -> str:
        """Resumen legible para que YUE lo diga en voz alta."""
        s = self.state()
        if s.privacy_mode:
            return ("Modo privacidad activo: la cámara no analiza nada. "
                    "No leo textos, no estimo emociones y no guardo observaciones.")
        partes = ["Cámara " + ("encendida" if s.camera_on else "apagada")]
        apagados = [_FEATURE_ES[k] for k, v in s.features.items() if not v and k in _FEATURE_ES]
        if apagados:
            partes.append("desactivado: " + ", ".join(apagados))
        if s.only_on_request:
            partes.append("solo analizo cuando me lo pides")
        partes.append("procesamiento local" if s.process_local else "procesamiento externo permitido")
        partes.append("no guardo imágenes" if not s.save_frames else "guardo fotogramas (activado a propósito)")
        partes.append("no guardo eventos" if not s.save_events else "guardo eventos visuales")
        return "; ".join(partes) + "."

    def _notify(self) -> None:
        try:
            self._on_change(self.state())
        except Exception:
            pass


def from_settings(cfg, on_change=None) -> PrivacyManager:
    """Construye un `PrivacyManager` desde un `VisionSettings`."""
    return PrivacyManager(
        process_local=getattr(cfg, "process_local", True),
        save_frames=getattr(cfg, "save_frames", False),
        save_events=getattr(cfg, "save_events", False),
        allow_cloud=getattr(cfg, "allow_cloud", False),
        only_on_request=getattr(cfg, "only_on_request", False),
        features={
            "ocr": getattr(cfg, "ocr_enabled", True),
            "emotions": getattr(cfg, "emotions_enabled", True),
            "objects": getattr(cfg, "objects", True),
            "actions": getattr(cfg, "actions_enabled", True),
            "scene": getattr(cfg, "scene_description_enabled", True),
            "faces": getattr(cfg, "face_detector", True),
            "hands": getattr(cfg, "hands_enabled", True),
            "pose": getattr(cfg, "pose", True),
        },
        on_change=on_change,
    )

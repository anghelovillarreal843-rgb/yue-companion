"""Eventos de percepción y un bus sencillo para entregarlos al cerebro de YUE.

Este es el ÚNICO puente entre el sistema de visión V3 y el resto de YUE: la
visión NUNCA llama directamente a la UI ni al motor de IA. Solo EMITE eventos
(presencia, emoción, atención, rostro perdido…) y quien quiera reaccionar se
suscribe. Así se cumple la regla del pedido: "No modificar el núcleo de YUE".

Es puro Python (sin Qt, sin cámara, sin IA): fácil de probar y de reutilizar.
Todo es degradable: si un suscriptor lanza una excepción, se ignora y el resto
sigue recibiendo el evento.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


# ---- Tipos de evento que emite la visión (constantes para evitar typos) ----
EVENT_PRESENCE = "presence_changed"      # el usuario apareció / desapareció
EVENT_FACE_LOST = "face_lost"            # había rostro y se perdió
EVENT_EMOTION = "emotion_detected"       # se estimó una emoción con confianza
EVENT_ATTENTION = "attention_changed"    # cambió el estado de atención
EVENT_ABSENCE = "long_absence"           # ausencia prolongada (p. ej. 10 min)
EVENT_STATUS = "status"                  # cambios de estado de la cámara/sistema


@dataclass(frozen=True)
class VisionEvent:
    """Un hecho observado. `data` lleva el detalle; nunca imágenes ni vídeo.

    Ejemplo (emoción):
        VisionEvent(type="emotion_detected",
                    data={"emotion": "happy", "confidence": 90})
    """
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    # -- Constructores cómodos (con la forma exacta que pide el documento) --
    @staticmethod
    def emotion(emotion: str, confidence: float) -> "VisionEvent":
        return VisionEvent(EVENT_EMOTION, {"emotion": emotion, "confidence": int(round(confidence))})

    @staticmethod
    def presence(present: bool, position: dict | None = None) -> "VisionEvent":
        data: dict[str, Any] = {"user_present": bool(present)}
        if position:
            data["face_position"] = position
        return VisionEvent(EVENT_PRESENCE, data)

    @staticmethod
    def face_lost() -> "VisionEvent":
        return VisionEvent(EVENT_FACE_LOST, {})

    @staticmethod
    def attention(state: str) -> "VisionEvent":
        return VisionEvent(EVENT_ATTENTION, {"attention": state})

    @staticmethod
    def absence(seconds: float) -> "VisionEvent":
        return VisionEvent(EVENT_ABSENCE, {"seconds": float(seconds)})

    @staticmethod
    def status(text: str, active: bool) -> "VisionEvent":
        return VisionEvent(EVENT_STATUS, {"text": text, "active": bool(active)})


class EventBus:
    """Bus publicar/suscribir mínimo y seguro entre hilos.

    - `subscribe(fn)` / `subscribe(fn, tipo)`: recibe todos los eventos o solo
      los de un tipo.
    - `emit(evento)`: entrega el evento a los suscriptores. Un fallo de un
      suscriptor NO frena a los demás ni rompe el hilo de la cámara.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[tuple[str | None, Callable[[VisionEvent], None]]] = []

    def subscribe(self, callback: Callable[[VisionEvent], None], event_type: str | None = None):
        with self._lock:
            self._subs.append((event_type, callback))
        return callback  # cómodo para desuscribir luego si se guarda la referencia

    def unsubscribe(self, callback: Callable[[VisionEvent], None]) -> None:
        with self._lock:
            self._subs = [(t, cb) for (t, cb) in self._subs if cb is not callback]

    def emit(self, event: VisionEvent) -> None:
        with self._lock:
            targets = list(self._subs)
        for wanted, cb in targets:
            if wanted is not None and wanted != event.type:
                continue
            try:
                cb(event)
            except Exception as exc:  # pragma: no cover - defensivo
                print(f"[vision.bus] suscriptor falló con {event.type}: {exc}")

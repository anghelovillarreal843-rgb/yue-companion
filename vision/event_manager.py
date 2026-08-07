"""Gestor de eventos visuales (puntos 7, 8 y 11 del pedido).

Es el ÚNICO sitio donde nacen los eventos que ve el resto de YUE. Su trabajo es
que un gesto sostenido o una acción continua NO generen un evento por fotograma.

Aplica, en este orden:

  1. duración mínima   -> el candidato debe mantenerse N segundos antes de contar,
  2. confianza mínima  -> por debajo del umbral no se emite nada,
  3. deduplicación     -> el mismo evento sostenido no se repite,
  4. enfriamiento      -> tras emitir, ese evento calla N segundos,
  5. histéresis        -> para dejar de estar activo hace falta un tiempo sin verlo.

Además guarda un historial corto en memoria (nunca en disco salvo que la
privacidad lo permita) para que el constructor de contexto sepa qué es nuevo.

Python puro y thread-safe: lo llaman varios hilos de detección a la vez.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Iterable


@dataclass
class VisualEvent:
    """Evento visual puntual ya filtrado y listo para consumirse."""

    event: str                                  # gesture_detected, action_detected, ...
    key: str                                    # identidad estable (gesto, acción, etiqueta)
    confidence: float = 0.0
    duration: float = 0.0
    source: str = ""                            # módulo que lo generó
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        out = {
            "event": self.event,
            "key": self.key,
            "confidence": round(float(self.confidence), 3),
            "duration": round(float(self.duration), 2),
            "source": self.source,
            "timestamp": round(float(self.timestamp), 3),
        }
        out.update(self.data)
        return out


@dataclass
class _Candidate:
    key: str
    first_seen: float
    last_seen: float
    confidence: float
    emitted: bool = False
    data: dict = field(default_factory=dict)


class EventManager:
    """Filtro temporal + bus de eventos visuales."""

    def __init__(
        self,
        *,
        min_duration: float = 0.45,
        cooldown: float = 3.0,
        release_after: float = 0.7,
        min_confidence: float = 0.5,
        history: int = 60,
    ) -> None:
        self._lock = threading.RLock()
        self.min_duration = float(min_duration)
        self.cooldown = float(cooldown)
        self.release_after = float(release_after)
        self.min_confidence = float(min_confidence)

        self._candidates: dict[str, _Candidate] = {}
        self._last_emit: dict[str, float] = {}
        self._history: Deque[VisualEvent] = deque(maxlen=int(history))
        self._subs: list[Callable[[VisualEvent], None]] = []
        self._muted = False

    # ------------------------------------------------------------------
    def subscribe(self, callback: Callable[[VisualEvent], None]) -> Callable:
        with self._lock:
            self._subs.append(callback)
        return callback

    def unsubscribe(self, callback: Callable) -> None:
        with self._lock:
            if callback in self._subs:
                self._subs.remove(callback)

    def set_muted(self, muted: bool) -> None:
        """Silencia la emisión (modo privacidad) sin perder el filtro temporal."""
        with self._lock:
            self._muted = bool(muted)
            if self._muted:
                self._candidates.clear()

    # ------------------------------------------------------------------
    def observe(
        self,
        event: str,
        key: str,
        confidence: float = 1.0,
        *,
        source: str = "",
        data: dict | None = None,
        min_duration: float | None = None,
        cooldown: float | None = None,
        now: float | None = None,
    ) -> VisualEvent | None:
        """Registra que `key` sigue presente. Devuelve el evento SOLO si toca emitir.

        Llamar en cada pasada del detector: el filtro decide cuándo hay novedad.
        """
        now = time.time() if now is None else float(now)
        min_dur = self.min_duration if min_duration is None else float(min_duration)
        cool = self.cooldown if cooldown is None else float(cooldown)
        conf = float(confidence)

        with self._lock:
            if self._muted:
                return None
            slot = f"{event}:{key}"
            cand = self._candidates.get(slot)
            if cand is None or (now - cand.last_seen) > self.release_after:
                # Reaparición tras una pausa: empieza de cero (histéresis).
                cand = _Candidate(key=key, first_seen=now, last_seen=now, confidence=conf)
                self._candidates[slot] = cand
            else:
                cand.last_seen = now
                # Media móvil suave de confianza: evita picos de un solo frame.
                cand.confidence = 0.7 * cand.confidence + 0.3 * conf
            if data:
                cand.data.update(data)

            if cand.emitted:
                return None
            if cand.confidence < self.min_confidence:
                return None
            duration = now - cand.first_seen
            if duration < min_dur:
                return None
            last = self._last_emit.get(slot, 0.0)
            if last and (now - last) < cool:
                return None

            cand.emitted = True
            self._last_emit[slot] = now
            evt = VisualEvent(
                event=event,
                key=key,
                confidence=round(cand.confidence, 3),
                duration=round(duration, 2),
                source=source,
                data=dict(cand.data),
                timestamp=now,
            )
            self._history.append(evt)
            targets = list(self._subs)

        for cb in targets:
            try:
                cb(evt)
            except Exception:
                pass
        return evt

    def expire(self, now: float | None = None) -> list[str]:
        """Cierra candidatos que ya no se ven. Devuelve las claves cerradas."""
        now = time.time() if now is None else float(now)
        closed: list[str] = []
        with self._lock:
            for slot, cand in list(self._candidates.items()):
                if (now - cand.last_seen) > self.release_after:
                    self._candidates.pop(slot, None)
                    closed.append(cand.key)
        return closed

    def active_keys(self, event: str | None = None) -> list[str]:
        with self._lock:
            out = []
            for slot, cand in self._candidates.items():
                name, _, key = slot.partition(":")
                if event is None or name == event:
                    out.append(key)
            return out

    # ------------------------------------------------------------------
    def recent(self, seconds: float = 20.0, kinds: Iterable[str] | None = None) -> list[VisualEvent]:
        """Eventos emitidos en los últimos `seconds` segundos."""
        cutoff = time.time() - float(seconds)
        wanted = set(kinds) if kinds else None
        with self._lock:
            return [
                e for e in self._history
                if e.timestamp >= cutoff and (wanted is None or e.event in wanted)
            ]

    def history(self) -> list[VisualEvent]:
        with self._lock:
            return list(self._history)

    def forget(self) -> int:
        """Borra el historial de observaciones ("olvida lo que viste")."""
        with self._lock:
            n = len(self._history)
            self._history.clear()
            self._candidates.clear()
            self._last_emit.clear()
            return n

    def snapshot(self) -> list[dict]:
        return [e.as_dict() for e in self.history()]

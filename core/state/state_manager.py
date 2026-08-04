"""Gestor central de estado de YUE — la ÚNICA fuente de verdad (FASE 2).

Hoy varios módulos (personalidad, cámara, música, voz, modo profesora) pueden
mandarle órdenes contradictorias al avatar al mismo tiempo. Este gestor pone
orden: todo cambio de emoción o de animación pasa por aquí con una PRIORIDAD, y
gana la de mayor prioridad. Así el avatar deja de recibir señales que se pelean.

Prioridades (de mayor a menor), según el diseño acordado:
    EMERGENCIA (100) > USUARIO (90) > PROFESORA (80) > CONVERSACIÓN (70) >
    EMOCIÓN (60) > MULTIMEDIA (40) > AMBIENTAL (20) > IDLE (10)

Regla del avatar: una petición de animación solo se aplica si su prioridad es
MAYOR O IGUAL que la de la animación vigente. Cuando esa animación caduca (ttl),
el avatar vuelve al estado base según la emoción actual.

ADITIVO: nada obliga a los módulos a usarlo de golpe. El avatar y cada servicio
pueden migrar a este gestor de forma gradual; mientras tanto, todo lo demás
sigue igual.
"""
from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable

from .events import EventBus


class Priority(enum.IntEnum):
    IDLE = 10
    AMBIENT = 20        # animación ambiental, ruido de fondo
    MEDIA = 40          # reacción a música/vídeo
    EMOTION = 60        # reacción emocional
    CONVERSATION = 70   # conversación activa
    TEACHER = 80        # modo profesora
    USER = 90           # intervención directa del usuario
    EMERGENCY = 100     # seguridad / emergencia


@dataclass(frozen=True)
class YueState:
    """Foto completa del estado de YUE en un instante."""
    emotion_primary: str = "neutral"
    emotion_secondary: str = ""
    intensity: float = 0.4            # 0..1
    avatar_state: str = "idle"        # idle, hablar, escuchar, pensar, bailar...
    voice_state: str = "silencio"     # silencio, hablando
    mic_state: str = "apagado"        # apagado, escuchando, procesando
    camera_state: str = "apagada"     # apagada, activa
    teacher_mode: bool = False
    autonomy_state: str = "inactiva"  # inactiva, proponiendo, ejecutando
    media_state: str = "detenido"     # detenido, reproduciendo
    bond_level: int = 1
    attention: str = "presente"       # presente, ausente
    activity: str = "libre"           # libre, conversando, ensenando, control_pc...


@dataclass
class _AvatarHold:
    state: str
    priority: int
    source: str
    expires_at: float | None  # None = sin caducidad


class YueStateManager:
    def __init__(self, bus: EventBus | None = None):
        self._lock = threading.RLock()
        self._state = YueState()
        self._avatar_hold: _AvatarHold | None = None
        self._subs: list[Callable[[YueState], None]] = []
        self.bus = bus or EventBus()

    # ---- Suscripción a cambios de estado ---------------------------------
    def subscribe(self, callback: Callable[[YueState], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(callback)

        def _off():
            with self._lock:
                try:
                    self._subs.remove(callback)
                except ValueError:
                    pass

        return _off

    def _emit(self, old: YueState, new: YueState):
        for cb in list(self._subs):
            try:
                cb(new)
            except Exception:
                pass
        self.bus.publish("estado_cambiado", old=old, new=new)

    def get_state(self) -> YueState:
        with self._lock:
            return self._state

    # ---- Actualización genérica de campos --------------------------------
    def update(self, **fields) -> YueState:
        """Cambia campos sueltos del estado (voz, micro, cámara, modo...)."""
        valid = {k: v for k, v in fields.items() if hasattr(self._state, k)}
        with self._lock:
            old = self._state
            new = replace(old, **valid)
            if new == old:
                return old
            self._state = new
        self._emit(old, new)
        return new

    # ---- Emoción ----------------------------------------------------------
    def set_emotion(self, primary: str, intensity: float | None = None,
                    secondary: str = "", *, source: str = "sistema",
                    priority: int = Priority.EMOTION) -> YueState:
        """Fija la emoción. Si además no hay una animación de mayor prioridad
        reteniendo el avatar, sincroniza el avatar con la emoción."""
        with self._lock:
            old = self._state
            fields = {"emotion_primary": primary, "emotion_secondary": secondary}
            if intensity is not None:
                fields["intensity"] = max(0.0, min(1.0, intensity))
            new = replace(old, **fields)
            self._state = new
            # Si nadie con más prioridad manda en el avatar, refleja la emoción.
            self._maybe_relax_avatar_locked()
        self._emit(old, new)
        self.bus.publish("emocion_cambiada", emocion=primary,
                         intensidad=new.intensity, origen=source)
        return new

    # ---- Arbitraje del avatar (el corazón del asunto) --------------------
    def request_avatar(self, state: str, priority: int, *, source: str,
                       ttl: float | None = None) -> bool:
        """Pide una animación para el avatar con cierta prioridad.

        Se aplica solo si su prioridad es >= la de la animación vigente (o si la
        vigente ya caducó). Devuelve True si se aplicó, False si se ignoró por
        prioridad insuficiente. `ttl` = segundos que dura antes de relajarse.
        """
        now = time.time()
        with self._lock:
            hold = self._avatar_hold
            vigente_activa = hold is not None and (
                hold.expires_at is None or hold.expires_at > now
            )
            if vigente_activa and priority < hold.priority:
                self.bus.publish("avatar_orden_ignorada", solicitado=state,
                                 origen=source, prioridad=priority,
                                 gana=hold.source, prioridad_actual=hold.priority)
                return False

            self._avatar_hold = _AvatarHold(
                state=state, priority=priority, source=source,
                expires_at=(now + ttl) if ttl else None,
            )
            old = self._state
            new = replace(old, avatar_state=state)
            self._state = new
        if new != old:
            self._emit(old, new)
        self.bus.publish("avatar_actualizado", estado=state,
                         origen=source, prioridad=priority)
        return True

    def _maybe_relax_avatar_locked(self):
        """Sincroniza el avatar con la emoción cuando NADIE lo está reteniendo.

        - Si hay una animación retenida y ya caducó, se suelta.
        - Si no hay animación activa reteniendo el avatar, el avatar refleja la
          emoción actual (su pose base).
        """
        hold = self._avatar_hold
        now = time.time()
        if hold is not None and hold.expires_at is not None and hold.expires_at <= now:
            self._avatar_hold = None
            hold = None
        if hold is None:
            base = _emotion_to_avatar(self._state.emotion_primary)
            if self._state.avatar_state != base:
                self._state = replace(self._state, avatar_state=base)

    def tick(self):
        """Llamar periódicamente (p. ej. cada 0.5 s) para relajar animaciones
        caducadas y devolver el avatar a su estado emocional base."""
        with self._lock:
            old = self._state
            self._maybe_relax_avatar_locked()
            new = self._state
        if new != old:
            self._emit(old, new)

    def release_avatar(self, source: str) -> bool:
        """El dueño actual suelta el avatar (vuelve a la emoción base)."""
        with self._lock:
            hold = self._avatar_hold
            if hold is None or hold.source != source:
                return False
            self._avatar_hold = None
            old = self._state
            base = _emotion_to_avatar(self._state.emotion_primary)
            new = replace(old, avatar_state=base)
            self._state = new
        self._emit(old, new)
        return True


# Mapa simple emoción -> animación base del avatar.
_EMOTION_AVATAR = {
    "feliz": "sonreir", "alegre": "sonreir", "enamorado": "sonrojo",
    "triste": "cabizbaja", "enojado": "molesta", "sorprendido": "sorpresa",
    "tranquilo": "idle", "neutral": "idle", "pensativo": "pensar",
}


def _emotion_to_avatar(emotion: str) -> str:
    return _EMOTION_AVATAR.get((emotion or "").lower(), "idle")

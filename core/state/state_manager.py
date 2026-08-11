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
import logging
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable

from .arbiter import ProposalArbiter
from .events import EventBus
from .fusion import SensorFusion
from .models import (
    IDLE_PROPOSAL, SystemState, UserObservation, UserState, YueBehaviorState,
    YueGlobalState, YueProposal,
)

log = logging.getLogger("yue.state")


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


@dataclass
class _EmotionHold:
    """Quién manda AHORA sobre la emoción del avatar y hasta cuándo.

    Antes solo se arbitraba la ANIMACIÓN (`avatar_state`), no la EMOCIÓN. Por
    eso la música alegre podía pisar la cara de YUE justo mientras el usuario
    contaba algo doloroso: eran dos llamadas independientes a
    `pet.set_emotion(...)` y ganaba la última. Con este arbitraje, gana la de
    mayor prioridad.
    """
    emotion: str
    intensity: float
    priority: int
    source: str
    expires_at: float | None


class YueStateManager:
    def __init__(self, bus: EventBus | None = None):
        self._lock = threading.RLock()
        self._state = YueState()
        self._avatar_hold: _AvatarHold | None = None
        self._emotion_hold: _EmotionHold | None = None
        self._subs: list[Callable[[YueState], None]] = []
        self.bus = bus or EventBus()

        # ---- FASE 3: los tres estados separados --------------------------
        # `YueState` (arriba) mezclaba usuario, YUE y máquina en una sola
        # estructura. Se conserva intacto y sincronizado para que nada de lo
        # que ya funcionaba tenga que cambiar, pero la verdad vive aquí.
        self._user = UserState()
        self._yue = IDLE_PROPOSAL.to_behavior()
        self._system = SystemState()
        self._fusion = SensorFusion()
        self._arbiter = ProposalArbiter()
        self._global_subs: list[Callable[[YueGlobalState], None]] = []
        self._last_winner_key: tuple = ()
        self._log_decisions = True

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

    # ---- Arbitraje de EMOCIÓN (NUEVO) ------------------------------------
    def request_emotion(self, emotion: str, intensity: float = 0.6,
                        duration_ms: int = 4500, *, priority: int = Priority.EMOTION,
                        source: str = "sistema", secondary: str = "") -> bool:
        """Pide una emoción para el avatar CON prioridad. Devuelve si se aplicó.

        Misma regla que `request_avatar`: solo se aplica si su prioridad es
        mayor o igual que la de la emoción vigente, o si aquella ya caducó.

        Esto es lo que impide el escenario que había que arreglar: mientras el
        usuario cuenta algo doloroso (prioridad USER/CONVERSATION), una canción
        alegre (prioridad MEDIA) ya NO puede poner al avatar eufórico. Cuando la
        retención caduca, el avatar vuelve solo a su estado base.

        Es ADITIVO: quien prefiera seguir llamando a `set_emotion()` puede
        hacerlo y todo funciona como antes.
        """
        from .mapping import avatar_pose

        propuesta = YueProposal(
            emotion=emotion,
            intensity=max(0.0, min(1.0, float(intensity))),
            behavior=_BEHAVIOR_POR_ORIGEN.get(source, "attentive"),
            voice_style="warm",
            initiative="low",
            avatar_state=avatar_pose(emotion,
                                     _BEHAVIOR_POR_ORIGEN.get(source, "")),
            source=source,
            priority=int(priority),
            ttl=max(0.5, float(duration_ms) / 1000.0),
            reason="request_emotion (compatibilidad)",
        )
        gano = self.propose(propuesta, secondary=secondary)
        if not gano:
            ganadora = self._arbiter.winner()
            self.bus.publish("emocion_orden_ignorada", solicitada=emotion,
                             origen=source, prioridad=priority,
                             gana=ganadora.source,
                             prioridad_actual=ganadora.priority)
        return gano

    def emotion_owner(self) -> tuple[str, int] | None:
        """Quién retiene la emoción ahora mismo (origen, prioridad), o None."""
        ganadora = self._arbiter.winner()
        if ganadora.source == IDLE_PROPOSAL.source:
            return None
        return (ganadora.source, int(ganadora.priority))

    def release_emotion(self, source: str) -> bool:
        """El dueño actual suelta la emoción (deja paso a prioridades menores).

        No deja a YUE en blanco: al retirar la propuesta se RECALCULA, así que
        el mando pasa a la siguiente propuesta viva (la conversación que seguía
        abierta debajo del modo profesora, por ejemplo).
        """
        retirada = self._arbiter.withdraw(source)
        with self._lock:
            if self._emotion_hold is not None and self._emotion_hold.source == source:
                self._emotion_hold = None
        if retirada:
            self._recompute_yue()
        return retirada

    # ======================================================================
    # FASE 3 — EL CEREBRO CENTRAL
    #
    #   LOS SENSORES OBSERVAN   →  observe()
    #   LOS MÓDULOS PROPONEN    →  propose()
    #   EL STATE MANAGER DECIDE →  _recompute_yue()
    #   EL RENDERER EJECUTA     →  subscribe_global()
    # ======================================================================

    # ---- 1) OBSERVAR: los sensores reportan, no mandan -------------------
    def observe(self, observation: UserObservation) -> UserState:
        """Una fuente reporta lo que cree ver en el usuario.

        Esto NO cambia la cara de YUE. Solo actualiza lo que YUE entiende del
        usuario. Que la cámara vea tristeza no pone triste al avatar: pone en
        `UserState` que quizá la persona esté triste, con su confianza y su
        fuente, y el sistema decide después qué hacer con esa información.
        """
        if observation is None:
            return self.user_state()
        self._fusion.observe(observation)
        with self._lock:
            base = self._user
        fusionado = self._fusion.fuse(base=base)
        return self._commit_user(fusionado)

    def observe_emotion(self, source: str, emotion: str, confidence: float,
                        *, valence: float = 0.0, arousal: float = 0.3,
                        explicit: bool = False, detail: str = "") -> UserState:
        """Atajo cómodo para los sensores: `observe()` sin construir el objeto."""
        return self.observe(UserObservation(
            source=source, emotion=emotion, confidence=confidence,
            valence=valence, arousal=arousal, explicit=explicit, detail=detail))

    def update_user(self, **campos) -> UserState:
        """Actualiza campos del estado del usuario (necesidad, tema, riesgo...).

        Lo usa el cerebro afectivo para aportar lo que ningún sensor sabe: qué
        necesita la persona y hacia dónde va su ánimo.
        """
        with self._lock:
            validos = {k: v for k, v in campos.items() if hasattr(self._user, k)}
            if not validos:
                return self._user
            nuevo = replace(self._user, updated_at=time.time(), **validos)
        return self._commit_user(nuevo)

    def set_user_state(self, user: UserState) -> UserState:
        """Reemplaza el estado del usuario entero (viene de CompanionBrain)."""
        if user is None:
            return self.user_state()
        return self._commit_user(user)

    def user_state(self) -> UserState:
        with self._lock:
            return self._user

    def _commit_user(self, nuevo: UserState) -> UserState:
        with self._lock:
            anterior = self._user
            if _mismo_user(anterior, nuevo):
                self._user = nuevo   # se refresca la hora, no se avisa a nadie
                return nuevo
            self._user = nuevo
        if self._log_decisions:
            log.info("[USER_STATE] emotion=%s confidence=%.2f source=%s "
                     "need=%s trend=%s",
                     nuevo.emotion, nuevo.confidence, nuevo.dominant_source,
                     nuevo.need, nuevo.trend)
        self.bus.publish("estado_usuario_cambiado", anterior=anterior, nuevo=nuevo)
        self._emit_global()
        return nuevo

    # ---- 2) PROPONER: los módulos piden, no imponen ----------------------
    def propose(self, proposal: YueProposal, *, secondary: str = "") -> bool:
        """Un módulo propone un comportamiento completo para YUE.

        Devuelve si esa propuesta es la que gobierna AHORA. Devolver False no
        es un rechazo definitivo: la propuesta queda viva y tomará el mando en
        cuanto caduque la que manda. Es lo que hace que al terminar el modo
        profesora la conversación recupere el control sola, sin que nadie tenga
        que acordarse de reactivarla.
        """
        if proposal is None:
            return False
        gano = self._arbiter.submit(proposal)
        if self._log_decisions:
            log.info("[YUE_PROPOSAL] source=%s priority=%d emotion=%s behavior=%s",
                     proposal.source, proposal.priority, proposal.emotion,
                     proposal.behavior)
        self.bus.publish("propuesta_recibida", propuesta=proposal, gano=gano)
        self._recompute_yue(secondary=secondary)
        return gano

    def withdraw(self, source: str) -> bool:
        """Una fuente retira su propuesta. Se recalcula el ganador."""
        retirada = self._arbiter.withdraw(source)
        if retirada:
            self._recompute_yue()
        return retirada

    def would_win(self, priority: int, source: str = "") -> bool:
        """¿Merece la pena que un módulo calcule una reacción, o perdería igual?"""
        return self._arbiter.would_win(priority, source)

    def yue_state(self) -> YueBehaviorState:
        with self._lock:
            return self._yue

    def active_proposals(self) -> list[YueProposal]:
        return self._arbiter.active()

    def _recompute_yue(self, *, secondary: str = "") -> YueBehaviorState:
        """Elige la propuesta ganadora y publica el estado resultante.

        Es el único sitio donde se decide qué hace YUE. Se llama al proponer,
        al retirar y en cada `tick()` (cuando algo caduca), y siempre recalcula
        desde cero en vez de «volver a neutral»: si había algo debajo, gana eso.
        """
        ganadora = self._arbiter.winner()
        nuevo = ganadora.to_behavior()

        with self._lock:
            anterior = self._yue
            clave = (nuevo.emotion, round(nuevo.intensity, 2), nuevo.behavior,
                     nuevo.voice_style, nuevo.initiative, nuevo.avatar_state,
                     nuevo.source, nuevo.priority)
            cambio = clave != self._last_winner_key
            self._last_winner_key = clave
            self._yue = nuevo

            # --- sincronía con el YueState clásico (compatibilidad) --------
            # Todo lo que ya leía `get_state()` sigue funcionando igual.
            viejo_estado = self._state
            self._emotion_hold = _EmotionHold(
                emotion=nuevo.emotion, intensity=nuevo.intensity,
                priority=int(nuevo.priority), source=nuevo.source,
                expires_at=nuevo.expires_at,
            )
            self._state = replace(
                viejo_estado, emotion_primary=nuevo.emotion,
                emotion_secondary=secondary, intensity=nuevo.intensity)
            self._maybe_relax_avatar_locked()
            estado_nuevo = self._state

        if estado_nuevo != viejo_estado:
            self._emit(viejo_estado, estado_nuevo)
        if cambio:
            if self._log_decisions:
                log.info("[YUE_STATE] winner=%s priority=%d emotion=%s "
                         "behavior=%s voice=%s initiative=%s",
                         nuevo.source, nuevo.priority, nuevo.emotion,
                         nuevo.behavior, nuevo.voice_style, nuevo.initiative)
            self.bus.publish("estado_yue_cambiado", anterior=anterior, nuevo=nuevo)
            self._emit_global()
        return nuevo

    # ---- 3) ESTADO DEL SISTEMA: que sea real, no decorativo --------------
    def update_system(self, **campos) -> SystemState:
        """Actualiza el estado técnico (micro, cámara, voz, modo, ocupación).

        Estos campos existían antes pero casi nadie los escribía, así que
        mentían. Ahora `main.py` los conecta a los interruptores de verdad.
        """
        with self._lock:
            validos = {k: v for k, v in campos.items() if hasattr(self._system, k)}
            if not validos:
                return self._system
            anterior = self._system
            nuevo = replace(anterior, updated_at=time.time(), **validos)
            if _mismo_system(anterior, nuevo):
                self._system = nuevo
                return nuevo
            self._system = nuevo
            # El YueState clásico refleja los mismos datos con sus nombres.
            viejo_estado = self._state
            self._state = replace(
                viejo_estado,
                mic_state=_MIC_ES.get(nuevo.mic, viejo_estado.mic_state),
                camera_state=_CAM_ES.get(nuevo.camera, viejo_estado.camera_state),
                voice_state=_VOZ_ES.get(nuevo.voice, viejo_estado.voice_state),
                teacher_mode=bool(nuevo.teacher_active),
                media_state="reproduciendo" if nuevo.media_playing else "detenido",
                activity=_ACT_ES.get(nuevo.activity, viejo_estado.activity),
                bond_level=int(nuevo.bond_level),
            )
            estado_nuevo = self._state
        if estado_nuevo != viejo_estado:
            self._emit(viejo_estado, estado_nuevo)
        self.bus.publish("estado_sistema_cambiado", anterior=anterior, nuevo=nuevo)
        self._emit_global()
        return nuevo

    def system_state(self) -> SystemState:
        with self._lock:
            return self._system

    # ---- 4) ESTADO GLOBAL: lo que consulta cualquier módulo --------------
    def global_state(self) -> YueGlobalState:
        """Foto coherente de los tres estados. Segura desde cualquier hilo."""
        with self._lock:
            return YueGlobalState(user=self._user, yue=self._yue,
                                  system=self._system)

    def subscribe_global(self, callback: Callable[[YueGlobalState], None]):
        """Se suscribe al estado global. Devuelve la función para cancelar.

        El renderer del avatar se engancha aquí: es su ÚNICA entrada. Ningún
        módulo vuelve a tocar el avatar directamente.
        """
        with self._lock:
            self._global_subs.append(callback)

        def _off():
            with self._lock:
                try:
                    self._global_subs.remove(callback)
                except ValueError:
                    pass

        return _off

    def _emit_global(self):
        estado = self.global_state()
        for cb in list(self._global_subs):
            try:
                cb(estado)
            except Exception as exc:
                log.debug("suscriptor de estado global falló: %s", exc)

    # ---- 5) DEPURACIÓN ---------------------------------------------------
    def debug_snapshot(self) -> dict:
        """Todo lo necesario para entender por qué YUE hizo lo que hizo."""
        estado = self.global_state()
        return {
            "user": estado.user.to_dict(),
            "yue": estado.yue.to_dict(),
            "system": estado.system.to_dict(),
            "winner": estado.yue.source,
            "proposals": self._arbiter.snapshot(),
            "observations": self._fusion.snapshot(),
            "summary": estado.summary(),
        }

    def set_decision_logging(self, activo: bool) -> None:
        """Enciende o apaga el log de decisiones (por si molesta en consola)."""
        self._log_decisions = bool(activo)

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
        # La retención de EMOCIÓN también caduca sola, para que una prioridad
        # alta no bloquee el avatar para siempre si nadie la suelta.
        emo = self._emotion_hold
        if emo is not None and emo.expires_at is not None and emo.expires_at <= now:
            self._emotion_hold = None
        if hold is None:
            base = _emotion_to_avatar(self._state.emotion_primary)
            if self._state.avatar_state != base:
                self._state = replace(self._state, avatar_state=base)

    def tick(self):
        """Latido del cerebro. Llamar cada ~250 ms desde el hilo de la interfaz.

        Hace DOS cosas, y la segunda es la que faltaba:

        1. Relaja las animaciones caducadas (comportamiento de siempre).
        2. Caduca las propuestas vencidas y RECALCULA el ganador. Al terminar
           el modo profesora no se cae a neutral: se vuelve a elegir entre lo
           que siguiera vivo, y la conversación recupera el mando sola.

        Sin este latido conectado a un `QTimer`, los TTL solo caducaban de
        rebote cuando alguien más pedía algo. Estaba definido y nunca se
        llamaba: era una expiración que en la práctica no existía.
        """
        caducadas = self._arbiter.prune()
        if caducadas:
            if self._log_decisions:
                log.info("[YUE_STATE] caducan %s → recalculando",
                         ", ".join(caducadas))
            self._recompute_yue()

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


# --------------------------------------------------------------------------
# Puentes entre el vocabulario nuevo (inglés, técnico) y el YueState clásico
# (español). Existen para que NADA de lo que ya leía `get_state()` se entere
# del cambio. Cuando toda la app use `global_state()`, esto podrá irse.
# --------------------------------------------------------------------------
_MIC_ES = {"off": "apagado", "listening": "escuchando",
           "processing": "procesando", "muted": "apagado"}
_CAM_ES = {"off": "apagada", "active": "activa", "error": "apagada"}
_VOZ_ES = {"silent": "silencio", "speaking": "hablando"}
_ACT_ES = {"idle": "libre", "conversing": "conversando", "teaching": "ensenando",
           "controlling": "control_pc", "watching": "observando"}

#: Origen de una llamada antigua a `request_emotion` → qué está haciendo YUE.
#: Permite que el código que aún no manda propuestas completas produzca, aun
#: así, un estado coherente en vez de una cara suelta sin comportamiento.
_BEHAVIOR_POR_ORIGEN = {
    "apoyo_usuario": "comforting",
    "conversacion": "conversing",
    "conversation": "conversing",
    "multimedia": "enjoying_music",
    "media": "enjoying_music",
    "camara": "attentive",
    "camera": "attentive",
    "control_pc": "solving",
    "tarea": "solving",
    "vision": "attentive",
    "profesora": "teaching",
    "teacher": "teaching",
    "seguridad": "grounding",
    "safety": "grounding",
}


def _mismo_user(a: UserState, b: UserState) -> bool:
    """¿Dos estados de usuario son el mismo a efectos prácticos?

    Se ignora `updated_at` y se redondea la confianza: si no, un cambio de
    0.6100 a 0.6103 dispararía un aviso a toda la aplicación varias veces por
    segundo sin que nada haya cambiado de verdad.
    """
    return (a.emotion == b.emotion
            and a.secondary_emotion == b.secondary_emotion
            and round(a.confidence, 2) == round(b.confidence, 2)
            and a.need == b.need
            and a.trend == b.trend
            and a.sustained == b.sustained
            and a.dominant_source == b.dominant_source
            and a.safety_level == b.safety_level)


def _mismo_system(a: SystemState, b: SystemState) -> bool:
    return (a.mic == b.mic and a.camera == b.camera and a.voice == b.voice
            and a.mode == b.mode and a.media_playing == b.media_playing
            and a.pc_busy == b.pc_busy and a.vision_busy == b.vision_busy
            and a.autonomy_busy == b.autonomy_busy
            and a.teacher_active == b.teacher_active
            and a.activity == b.activity and a.bond_level == b.bond_level)

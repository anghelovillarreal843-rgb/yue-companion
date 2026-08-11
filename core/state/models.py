"""Los tres estados de YUE, separados de una vez: USUARIO, YUE y SISTEMA.

Hasta ahora `YueState` mezclaba tres cosas distintas en una sola estructura: lo
que le pasa al usuario, cómo reacciona YUE y cómo está la máquina. Esa mezcla es
la raíz del problema que se quiere arreglar, porque hace que «tristeza» pueda
significar dos cosas opuestas según quién lea el campo:

    el usuario está triste          (observación)
    YUE está triste                 (comportamiento)

Y no son lo mismo. Si el usuario está triste, YUE NO debe ponerse triste: debe
ponerse preocupada, suave y poco invasiva. Esa es la REGLA DE ORO del módulo:

    USER EMOTION  ≠  YUE EMOTION

Estructura:

    YueGlobalState
        ├── UserState        lo que YUE entiende del usuario  (observado)
        ├── YueBehaviorState cómo decidió reaccionar YUE       (decidido)
        └── SystemState      cómo está la máquina              (técnico)

Todas son `frozen`: nadie puede mutarlas por accidente desde otro hilo. Para
cambiar algo se crea una copia nueva, y de eso se encarga `YueStateManager`.

ADITIVO: el `YueState` clásico de `state_manager.py` sigue existiendo intacto y
se mantiene sincronizado. Ningún módulo antiguo tiene que cambiar de golpe.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace


def _clamp(valor: float, minimo: float = 0.0, maximo: float = 1.0) -> float:
    try:
        return max(minimo, min(maximo, float(valor)))
    except (TypeError, ValueError):
        return minimo


# ==========================================================================
# 1) ESTADO DEL USUARIO — observación, nunca decisión
# ==========================================================================
@dataclass(frozen=True)
class UserState:
    """Lo que YUE CREE que le pasa al usuario ahora mismo.

    Es una estimación con incertidumbre declarada, no un veredicto. Sale de
    fusionar varias fuentes (texto, voz, cámara, histórico) y por eso lleva la
    confianza de cada una por separado: cuando algo salga mal, se podrá ver de
    dónde vino la lectura equivocada.

    NUNCA describe la emoción de YUE. Para eso está `YueBehaviorState`.
    """

    #: Emoción principal detectada en el usuario (vocabulario de core.affect).
    emotion: str = "neutral"
    secondary_emotion: str = ""

    #: Confianza global de la lectura fusionada (0..1).
    confidence: float = 0.0

    #: -1 muy negativo · 0 neutro · +1 muy positivo.
    valence: float = 0.0
    #: 0 muy calmado · 1 muy activado.
    arousal: float = 0.3
    #: Cuánto malestar aparente hay (0..1).
    distress: float = 0.0
    #: Ambigüedad que quedó sin resolver (0..1).
    uncertainty: float = 1.0

    #: Qué parece necesitar (SupportNeed: LISTEN, COMFORT, SOLVE...).
    need: str = "none"
    #: Modo de apoyo secundario, si lo hubiera.
    secondary_need: str = ""

    #: De qué se está hablando y qué parece haberlo provocado.
    topic: str = ""
    trigger: str = ""

    #: Tendencia del contexto afectivo: stable, getting_better, getting_worse...
    trend: str = "stable"
    #: ¿Lleva rato igual? (señal de que no es un pico puntual)
    sustained: bool = False
    #: Segundos que lleva en esta emoción.
    duration_s: float = 0.0
    #: Estabilidad del contexto (0..1).
    stability: float = 0.0

    #: Confianza APORTADA POR CADA FUENTE, ya ponderada. Sirve para depurar.
    text_confidence: float = 0.0
    voice_confidence: float = 0.0
    camera_confidence: float = 0.0

    #: Qué fuente acabó mandando en esta lectura.
    dominant_source: str = "none"
    #: ¿El usuario lo dijo con palabras explícitas? Si sí, nadie lo contradice.
    explicit: bool = False

    #: Nivel de riesgo detectado (NONE, LOW, MODERATE, HIGH, CRITICAL).
    safety_level: str = "NONE"

    updated_at: float = field(default_factory=time.time)

    def __post_init__(self):
        object.__setattr__(self, "confidence", _clamp(self.confidence))
        object.__setattr__(self, "valence", _clamp(self.valence, -1.0, 1.0))
        object.__setattr__(self, "arousal", _clamp(self.arousal))
        object.__setattr__(self, "distress", _clamp(self.distress))
        object.__setattr__(self, "uncertainty", _clamp(self.uncertainty))
        object.__setattr__(self, "text_confidence", _clamp(self.text_confidence))
        object.__setattr__(self, "voice_confidence", _clamp(self.voice_confidence))
        object.__setattr__(self, "camera_confidence", _clamp(self.camera_confidence))

    @property
    def is_negative(self) -> bool:
        return self.valence <= -0.25

    @property
    def in_distress(self) -> bool:
        return self.distress >= 0.5

    def to_dict(self) -> dict:
        return {
            "emotion": self.emotion,
            "secondary_emotion": self.secondary_emotion,
            "confidence": round(self.confidence, 3),
            "valence": round(self.valence, 3),
            "arousal": round(self.arousal, 3),
            "distress": round(self.distress, 3),
            "uncertainty": round(self.uncertainty, 3),
            "need": self.need,
            "trend": self.trend,
            "sustained": self.sustained,
            "text_confidence": round(self.text_confidence, 3),
            "voice_confidence": round(self.voice_confidence, 3),
            "camera_confidence": round(self.camera_confidence, 3),
            "dominant_source": self.dominant_source,
            "explicit": self.explicit,
            "safety_level": self.safety_level,
        }


# ==========================================================================
# 2) ESTADO DE YUE — decisión, no observación
# ==========================================================================
#: Cuánta iniciativa se permite YUE. Controla si pregunta, propone, habla
#: primero o simplemente acompaña en silencio.
INITIATIVE_LEVELS = ("none", "low", "medium", "high")


@dataclass(frozen=True)
class YueBehaviorState:
    """Cómo decidió REACCIONAR YUE. Esto es lo que se renderiza.

    Sale del arbitraje entre todas las propuestas activas. Es un estado
    COHERENTE: cara, voz, comportamiento y animación salen siempre del mismo
    ganador, para que no pueda ocurrir «cara triste + voz alegre + gesto de
    baile», que era exactamente lo que pasaba antes.
    """

    #: Cara del avatar (vocabulario de core.support.expression.AVATAR_EMOTIONS).
    emotion: str = "neutral"
    intensity: float = 0.4

    #: Qué está HACIENDO YUE: attentive, listening, comforting, teaching...
    behavior: str = "attentive"

    #: Tono de voz sugerido: warm, gentle, soft, steady, playful, quiet, urgent.
    voice_style: str = "warm"

    #: Cuánto se permite intervenir: none, low, medium, high.
    initiative: str = "low"

    #: Animación/pose del avatar: idle, explaining, listening, dancing...
    avatar_state: str = "idle"

    #: Gesto puntual opcional (el avatar lo reproduce una vez).
    gesture: str = ""

    #: Quién ganó el arbitraje: conversation, teacher, media, camera, safety...
    source: str = "idle"
    #: Con qué prioridad ganó.
    priority: int = 10

    #: Cuánto debería durar esta expresión, en milisegundos.
    duration_ms: int = 4200
    #: Momento en que caduca (epoch). None = sin caducidad.
    expires_at: float | None = None

    #: Por qué YUE reaccionó así (depuración y tests).
    reason: str = ""

    updated_at: float = field(default_factory=time.time)

    def __post_init__(self):
        object.__setattr__(self, "intensity", _clamp(self.intensity))
        object.__setattr__(self, "duration_ms", max(600, int(self.duration_ms)))
        if self.initiative not in INITIATIVE_LEVELS:
            object.__setattr__(self, "initiative", "low")

    @property
    def ttl_remaining(self) -> float:
        """Segundos que le quedan de vida. `inf` si no caduca."""
        if self.expires_at is None:
            return float("inf")
        return max(0.0, self.expires_at - time.time())

    @property
    def may_speak_first(self) -> bool:
        """¿Puede YUE arrancar a hablar sin que le pregunten?"""
        return self.initiative in ("medium", "high")

    def to_dict(self) -> dict:
        return {
            "emotion": self.emotion,
            "intensity": round(self.intensity, 3),
            "behavior": self.behavior,
            "voice_style": self.voice_style,
            "initiative": self.initiative,
            "avatar_state": self.avatar_state,
            "gesture": self.gesture,
            "source": self.source,
            "priority": int(self.priority),
            "duration_ms": int(self.duration_ms),
            "ttl_remaining": (None if self.expires_at is None
                              else round(self.ttl_remaining, 2)),
            "reason": self.reason,
        }


# ==========================================================================
# 3) ESTADO DEL SISTEMA — la máquina, no las emociones
# ==========================================================================
@dataclass(frozen=True)
class SystemState:
    """Cómo está la aplicación por dentro. Nada de esto es emocional.

    Importa que sea REAL: un campo que nunca cambia es peor que no tenerlo,
    porque invita a tomar decisiones con información falsa.
    """

    #: off, listening, processing, muted
    mic: str = "off"
    #: off, active, error
    camera: str = "off"
    #: silent, speaking
    voice: str = "silent"

    #: companion, teacher, control, autonomy
    mode: str = "companion"

    media_playing: bool = False
    #: Qué suena, si es que suena algo (para el acompañamiento musical).
    media_title: str = ""

    pc_busy: bool = False
    vision_busy: bool = False
    autonomy_busy: bool = False
    teacher_active: bool = False

    #: idle, conversing, teaching, controlling, watching
    activity: str = "idle"

    #: Nivel de vínculo con el usuario (lo usa el prompt y la UI).
    bond_level: int = 1

    updated_at: float = field(default_factory=time.time)

    @property
    def busy(self) -> bool:
        """¿Hay algo en marcha que desaconseje interrumpir al usuario?"""
        return bool(self.pc_busy or self.vision_busy or self.autonomy_busy)

    @property
    def is_speaking(self) -> bool:
        return self.voice == "speaking"

    def to_dict(self) -> dict:
        return {
            "mic": self.mic,
            "camera": self.camera,
            "voice": self.voice,
            "mode": self.mode,
            "media_playing": self.media_playing,
            "pc_busy": self.pc_busy,
            "vision_busy": self.vision_busy,
            "autonomy_busy": self.autonomy_busy,
            "teacher_active": self.teacher_active,
            "activity": self.activity,
            "bond_level": self.bond_level,
        }


# ==========================================================================
# 4) ESTADO GLOBAL — lo que cualquier módulo puede consultar
# ==========================================================================
@dataclass(frozen=True)
class YueGlobalState:
    """Foto completa y coherente de YUE en un instante.

    Se obtiene con `state_manager.global_state()` desde CUALQUIER hilo: al ser
    inmutable, quien la recibe puede leerla con calma sin que se le cambie
    debajo. Es la «única fuente de verdad» del sistema.
    """

    user: UserState = field(default_factory=UserState)
    yue: YueBehaviorState = field(default_factory=YueBehaviorState)
    system: SystemState = field(default_factory=SystemState)
    updated_at: float = field(default_factory=time.time)

    def with_user(self, user: UserState) -> "YueGlobalState":
        return replace(self, user=user, updated_at=time.time())

    def with_yue(self, yue: YueBehaviorState) -> "YueGlobalState":
        return replace(self, yue=yue, updated_at=time.time())

    def with_system(self, system: SystemState) -> "YueGlobalState":
        return replace(self, system=system, updated_at=time.time())

    def to_dict(self) -> dict:
        return {
            "user": self.user.to_dict(),
            "yue": self.yue.to_dict(),
            "system": self.system.to_dict(),
        }

    def summary(self) -> str:
        """Una línea legible, para logs y para el panel de depuración."""
        return (f"usuario={self.user.emotion}({self.user.confidence:.2f}"
                f"/{self.user.dominant_source}) → "
                f"yue={self.yue.emotion}({self.yue.intensity:.2f})"
                f"/{self.yue.behavior}/init={self.yue.initiative} "
                f"[{self.yue.source} p{self.yue.priority}] "
                f"sys={self.system.activity}")


# ==========================================================================
# 5) OBSERVACIÓN — lo que un sensor CREE haber visto
# ==========================================================================
#: Cuánto se fía YUE de cada fuente. La cámara ve la cara, pero la cara miente
#: mucho: se sonríe por educación, se pone cara neutra por cansancio y se llora
#: de alegría. El texto explícito («estoy fatal») es otra cosa: es la persona
#: diciendo lo que le pasa. Por eso pesa el doble que la cámara.
SOURCE_RELIABILITY: dict[str, float] = {
    "text": 1.00,       # el usuario lo escribió o lo dijo con palabras
    "voice": 0.80,      # prosodia (aún no implementada: ver core/voice_affect.py)
    "history": 0.65,    # contexto afectivo acumulado
    "context": 0.65,    # alias de history
    "camera": 0.55,     # expresión facial: EVIDENCIA, no verdad
    "screen": 0.45,     # lo que hay en pantalla
    "system": 0.40,     # heurísticas de actividad
}

#: Cuánto tiempo sigue siendo válida una observación de cada fuente. Una cara
#: triste de hace un minuto ya no dice nada; una frase explícita, sí.
SOURCE_TTL: dict[str, float] = {
    "text": 90.0,
    "voice": 45.0,
    "history": 300.0,
    "context": 300.0,
    "camera": 15.0,
    "screen": 60.0,
    "system": 30.0,
}


@dataclass(frozen=True)
class UserObservation:
    """Una fuente reportando lo que cree ver en el usuario.

    Un sensor NUNCA decide nada. Solo dice:

        «Creo que el usuario parece triste, con confianza 0.63,
         y lo vi hace 2 segundos por la cámara.»

    Quien decide qué hacer con eso es `YueStateManager`.
    """

    source: str = "text"
    emotion: str = "neutral"
    confidence: float = 0.0

    valence: float = 0.0
    arousal: float = 0.3
    #: ¿El usuario lo dijo con palabras? El texto explícito no se contradice.
    explicit: bool = False
    #: Detalle libre por si el origen quiere dejar constancia.
    detail: str = ""
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        object.__setattr__(self, "source", (self.source or "text").strip().lower())
        object.__setattr__(self, "emotion",
                           (self.emotion or "neutral").strip().lower())
        object.__setattr__(self, "confidence", _clamp(self.confidence))
        object.__setattr__(self, "valence", _clamp(self.valence, -1.0, 1.0))
        object.__setattr__(self, "arousal", _clamp(self.arousal))

    @property
    def reliability(self) -> float:
        return SOURCE_RELIABILITY.get(self.source, 0.5)

    @property
    def effective_confidence(self) -> float:
        """Confianza ya ponderada por lo fiable que es la fuente.

        Es la cifra con la que compiten las observaciones entre sí. Una cámara
        segurísima (0.95) pesa 0.52; un texto medio seguro (0.70) pesa 0.70. Así
        una cara neutra no puede tumbar un «estoy fatal».
        """
        return _clamp(self.confidence * self.reliability)

    def is_expired(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return (now - self.timestamp) > SOURCE_TTL.get(self.source, 30.0)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "emotion": self.emotion,
            "confidence": round(self.confidence, 3),
            "effective": round(self.effective_confidence, 3),
            "explicit": self.explicit,
            "age_s": round(time.time() - self.timestamp, 1),
        }


# ==========================================================================
# 6) PROPUESTA — lo que un módulo PIDE que haga YUE
# ==========================================================================
@dataclass(frozen=True)
class YueProposal:
    """Un módulo proponiendo un comportamiento completo para YUE.

    Ojo con la distinción que más confusión causaba:

        CONFIDENCE  = qué tan seguro estoy de una OBSERVACIÓN   (0.63)
        PRIORITY    = qué comportamiento tiene AUTORIDAD ahora  (80)

    No son lo mismo y no se mezclan. Una observación de cámara puede tener
    confianza 0.95 y aun así no mandar nada, porque las observaciones no mandan.
    Solo las propuestas compiten, y compiten por PRIORIDAD.

    Se propone un comportamiento ENTERO (cara, voz, iniciativa, animación) para
    que el ganador sea coherente consigo mismo.
    """

    emotion: str = "neutral"
    intensity: float = 0.4
    behavior: str = "attentive"
    voice_style: str = "warm"
    initiative: str = "low"
    avatar_state: str = "idle"
    gesture: str = ""

    #: Quién propone. Es también su IDENTIDAD: una fuente solo tiene una
    #: propuesta viva a la vez (la nueva reemplaza a la anterior).
    source: str = "idle"
    priority: int = 10

    #: Segundos que la propuesta sigue viva. None = hasta que la retiren.
    ttl: float | None = 6.0
    reason: str = ""
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        object.__setattr__(self, "intensity", _clamp(self.intensity))
        object.__setattr__(self, "source", (self.source or "idle").strip().lower())
        if self.initiative not in INITIATIVE_LEVELS:
            object.__setattr__(self, "initiative", "low")

    @property
    def expires_at(self) -> float | None:
        if self.ttl is None:
            return None
        return self.created_at + max(0.2, float(self.ttl))

    def is_expired(self, now: float | None = None) -> bool:
        limite = self.expires_at
        if limite is None:
            return False
        return (time.time() if now is None else now) >= limite

    @property
    def duration_ms(self) -> int:
        if self.ttl is None:
            return 6000
        return int(max(0.2, float(self.ttl)) * 1000)

    def to_behavior(self) -> YueBehaviorState:
        """Convierte la propuesta ganadora en el estado real de YUE."""
        return YueBehaviorState(
            emotion=self.emotion,
            intensity=self.intensity,
            behavior=self.behavior,
            voice_style=self.voice_style,
            initiative=self.initiative,
            avatar_state=self.avatar_state,
            gesture=self.gesture,
            source=self.source,
            priority=int(self.priority),
            duration_ms=self.duration_ms,
            expires_at=self.expires_at,
            reason=self.reason,
        )

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "priority": int(self.priority),
            "emotion": self.emotion,
            "intensity": round(self.intensity, 3),
            "behavior": self.behavior,
            "initiative": self.initiative,
            "ttl_remaining": (None if self.expires_at is None
                              else round(max(0.0, self.expires_at - time.time()), 2)),
        }


#: Estado por defecto cuando no hay ninguna propuesta viva: YUE en reposo,
#: atenta y sin iniciativa. Nunca se cae a «neutral a secas y ya veremos».
IDLE_PROPOSAL = YueProposal(
    emotion="neutral", intensity=0.35, behavior="attentive",
    voice_style="warm", initiative="low", avatar_state="idle",
    source="idle", priority=10, ttl=None, reason="reposo",
)

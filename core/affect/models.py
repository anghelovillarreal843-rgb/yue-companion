"""Modelos de datos del sistema afectivo de YUE.

Aquí vive el VOCABULARIO común de toda la arquitectura nueva: qué emociones
reconoce YUE, cómo se representa un estado afectivo y qué límites explícitos
puede poner el usuario.

Regla de diseño: estos objetos son DATOS PUROS (dataclasses inmutables, sin
dependencias). Cualquier módulo puede importarlos sin arrastrar el resto del
sistema, y por eso son fáciles de testear.

Importante: nada de esto es un diagnóstico. Son ESTIMACIONES con su propia
incertidumbre; YUE nunca afirma con certeza lo que siente una persona.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, replace


class Emotion(str, enum.Enum):
    """Taxonomía emocional de YUE.

    Es una lista deliberadamente MEDIA: más rica que happy/sad/angry (que
    aplanaba todo), pero sin llegar a un catálogo clínico que invitaría a
    sobrediagnosticar. Hereda de `str` para que se serialice sola en JSON y se
    pueda comparar con cadenas ("sadness" == Emotion.SADNESS).
    """

    NEUTRAL = "neutral"
    JOY = "joy"                    # alegría
    EXCITEMENT = "excitement"      # entusiasmo / euforia
    PRIDE = "pride"                # orgullo por un logro
    RELIEF = "relief"              # alivio
    AFFECTION = "affection"        # cariño / ternura
    SADNESS = "sadness"            # tristeza
    DISAPPOINTMENT = "disappointment"  # decepción (expectativa rota)
    LONELINESS = "loneliness"      # soledad
    ANGER = "anger"                # enojo
    FRUSTRATION = "frustration"    # frustración (obstáculo, no persona)
    FEAR = "fear"                  # miedo concreto
    ANXIETY = "anxiety"            # ansiedad / preocupación difusa
    GUILT = "guilt"                # culpa
    EMBARRASSMENT = "embarrassment"  # vergüenza
    CONFUSION = "confusion"        # desconcierto
    TIREDNESS = "tiredness"        # cansancio / agotamiento

    def __str__(self) -> str:  # pragma: no cover - comodidad al imprimir
        return self.value


#: Valencia y activación TÍPICAS de cada emoción (valence -1..+1, arousal 0..1).
#: Sirven de punto de partida; las reglas y el intérprete semántico las ajustan
#: según intensificadores, puntuación y contexto.
EMOTION_VAD: dict[Emotion, tuple[float, float]] = {
    Emotion.NEUTRAL: (0.0, 0.30),
    Emotion.JOY: (0.70, 0.55),
    Emotion.EXCITEMENT: (0.75, 0.90),
    Emotion.PRIDE: (0.70, 0.60),
    Emotion.RELIEF: (0.45, 0.25),
    Emotion.AFFECTION: (0.65, 0.40),
    Emotion.SADNESS: (-0.65, 0.25),
    Emotion.DISAPPOINTMENT: (-0.55, 0.30),
    Emotion.LONELINESS: (-0.60, 0.30),
    Emotion.ANGER: (-0.70, 0.80),
    Emotion.FRUSTRATION: (-0.55, 0.65),
    Emotion.FEAR: (-0.65, 0.75),
    Emotion.ANXIETY: (-0.50, 0.70),
    Emotion.GUILT: (-0.55, 0.45),
    Emotion.EMBARRASSMENT: (-0.30, 0.55),
    Emotion.CONFUSION: (-0.15, 0.45),
    Emotion.TIREDNESS: (-0.30, 0.15),
}

#: Emociones que indican MALESTAR (guían a SupportPolicy hacia escuchar/consolar).
NEGATIVE_EMOTIONS = frozenset({
    Emotion.SADNESS, Emotion.DISAPPOINTMENT, Emotion.LONELINESS,
    Emotion.ANGER, Emotion.FRUSTRATION, Emotion.FEAR, Emotion.ANXIETY,
    Emotion.GUILT, Emotion.EMBARRASSMENT, Emotion.TIREDNESS,
})

#: Emociones agradables (guían hacia celebrar / acompañar la alegría).
POSITIVE_EMOTIONS = frozenset({
    Emotion.JOY, Emotion.EXCITEMENT, Emotion.PRIDE,
    Emotion.RELIEF, Emotion.AFFECTION,
})


class Boundary(str, enum.Enum):
    """Límites EXPLÍCITOS que el usuario puede pedir.

    Solo se activan cuando la persona los dice con palabras. YUE no los
    "intuye": un límite inventado sería tan invasivo como ignorar uno real.
    """

    WANTS_SPACE = "wants_space"                    # «déjame solo un rato»
    NO_ADVICE = "no_advice"                        # «no quiero consejos»
    DOES_NOT_WANT_TO_TALK = "does_not_want_to_talk"  # «no quiero hablar de eso»
    NO_QUESTIONS = "no_questions"                  # «deja de preguntarme»
    NO_PITY = "no_pity"                            # «no me tengas lástima»

    def __str__(self) -> str:  # pragma: no cover
        return self.value


def clamp(value: float, low: float, high: float) -> float:
    """Recorta un número al rango [low, high] sin reventar con basura."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return low
    if value != value:  # NaN
        return low
    return max(low, min(high, value))


@dataclass(frozen=True)
class AffectiveState:
    """Lo que YUE cree que le pasa al usuario AHORA MISMO.

    Es una estimación con incertidumbre declarada, no un veredicto.

    Campos
    ------
    primary_emotion / secondary_emotion
        Emoción principal y (si tiene sentido) la segunda más probable.
    valence
        -1 muy negativo · 0 neutro · +1 muy positivo.
    arousal
        0 muy calmado · 1 muy activado.
    confidence
        Cuánto se fía YUE de esta lectura (0..1).
    uncertainty
        Cuánta ambigüedad quedó sin resolver (0..1). No es exactamente
        1-confidence: un mensaje puede leerse con confianza media y AÚN así
        tener contradicciones internas que conviene reconocer.
    possible_trigger
        Frase corta con lo que parece haberlo causado, SOLO si se deduce con
        razonabilidad. Si no, cadena vacía.
    sarcasm_probability
        Probabilidad de ironía/sarcasmo (0..1).
    negated_emotions
        Emociones que el usuario NEGÓ explícitamente ("no estoy triste").
    explicit_boundary
        Límites que pidió con palabras.
    evidence
        Pistas legibles de por qué se llegó aquí (para depurar y para tests).
    source
        "rules" | "semantic" | "hybrid" — de dónde salió la lectura.
    """

    primary_emotion: Emotion = Emotion.NEUTRAL
    secondary_emotion: Emotion | None = None
    valence: float = 0.0
    arousal: float = 0.3
    confidence: float = 0.3
    uncertainty: float = 0.5
    possible_trigger: str = ""
    sarcasm_probability: float = 0.0
    negated_emotions: tuple[Emotion, ...] = ()
    explicit_boundary: tuple[Boundary, ...] = ()
    evidence: tuple[str, ...] = ()
    source: str = "rules"

    def __post_init__(self):
        # Los rangos se normalizan aquí para que NINGÚN consumidor tenga que
        # defenderse de un -3.7 o un NaN venido de un LLM.
        object.__setattr__(self, "valence", clamp(self.valence, -1.0, 1.0))
        object.__setattr__(self, "arousal", clamp(self.arousal, 0.0, 1.0))
        object.__setattr__(self, "confidence", clamp(self.confidence, 0.0, 1.0))
        object.__setattr__(self, "uncertainty", clamp(self.uncertainty, 0.0, 1.0))
        object.__setattr__(self, "sarcasm_probability",
                           clamp(self.sarcasm_probability, 0.0, 1.0))

    # ---- consultas cómodas -------------------------------------------------
    @property
    def is_negative(self) -> bool:
        return self.primary_emotion in NEGATIVE_EMOTIONS or self.valence <= -0.25

    @property
    def is_positive(self) -> bool:
        return self.primary_emotion in POSITIVE_EMOTIONS and self.valence >= 0.25

    @property
    def distress(self) -> float:
        """Malestar aproximado 0..1: cuánto duele, combinando valencia y activación.

        Una tristeza apagada (valencia baja, activación baja) y una angustia
        agitada (valencia baja, activación alta) duelen ambas, pero la segunda
        pesa algo más. Es la señal que mira SupportPolicy para decidir si toca
        contener antes que cualquier otra cosa.
        """
        if self.valence >= 0:
            return 0.0
        base = -self.valence
        return clamp(base * (0.75 + 0.25 * self.arousal), 0.0, 1.0)

    def has_boundary(self, boundary: Boundary) -> bool:
        return boundary in self.explicit_boundary

    def negated(self, emotion: Emotion) -> bool:
        return emotion in self.negated_emotions

    def with_(self, **cambios) -> "AffectiveState":
        """Copia con cambios (los dataclasses son inmutables a propósito)."""
        return replace(self, **cambios)

    def to_dict(self) -> dict:
        """Serialización plana, apta para JSON, logs y memoria."""
        return {
            "primary_emotion": str(self.primary_emotion),
            "secondary_emotion": str(self.secondary_emotion) if self.secondary_emotion else "",
            "valence": round(self.valence, 3),
            "arousal": round(self.arousal, 3),
            "confidence": round(self.confidence, 3),
            "uncertainty": round(self.uncertainty, 3),
            "possible_trigger": self.possible_trigger,
            "sarcasm_probability": round(self.sarcasm_probability, 3),
            "negated_emotions": [str(e) for e in self.negated_emotions],
            "explicit_boundary": [str(b) for b in self.explicit_boundary],
            "evidence": list(self.evidence),
            "source": self.source,
        }


def emotion_from_name(name) -> Emotion | None:
    """Convierte un texto suelto (o un Emotion) en Emotion. None si no encaja.

    Tolera lo que devuelva un LLM: mayúsculas, espacios, sinónimos en español y
    las etiquetas VIEJAS de core/emotion.py (happy, worried, sleepy…), que es
    justo lo que permite la retrocompatibilidad.
    """
    if isinstance(name, Emotion):
        return name
    clave = str(name or "").strip().lower()
    if not clave:
        return None
    try:
        return Emotion(clave)
    except ValueError:
        pass
    return _ALIAS.get(clave)


#: Sinónimos en español y etiquetas heredadas del sistema antiguo.
_ALIAS: dict[str, Emotion] = {
    # español
    "alegria": Emotion.JOY, "alegría": Emotion.JOY, "feliz": Emotion.JOY,
    "felicidad": Emotion.JOY, "contento": Emotion.JOY, "contenta": Emotion.JOY,
    "entusiasmo": Emotion.EXCITEMENT, "emocionado": Emotion.EXCITEMENT,
    "emocionada": Emotion.EXCITEMENT, "euforia": Emotion.EXCITEMENT,
    "orgullo": Emotion.PRIDE, "orgulloso": Emotion.PRIDE, "orgullosa": Emotion.PRIDE,
    "alivio": Emotion.RELIEF, "aliviado": Emotion.RELIEF, "aliviada": Emotion.RELIEF,
    "carino": Emotion.AFFECTION, "cariño": Emotion.AFFECTION, "ternura": Emotion.AFFECTION,
    "amor": Emotion.AFFECTION,
    "tristeza": Emotion.SADNESS, "triste": Emotion.SADNESS, "pena": Emotion.SADNESS,
    "decepcion": Emotion.DISAPPOINTMENT, "decepción": Emotion.DISAPPOINTMENT,
    "decepcionado": Emotion.DISAPPOINTMENT, "decepcionada": Emotion.DISAPPOINTMENT,
    "desilusion": Emotion.DISAPPOINTMENT, "desilusión": Emotion.DISAPPOINTMENT,
    "soledad": Emotion.LONELINESS, "solo": Emotion.LONELINESS, "sola": Emotion.LONELINESS,
    "enojo": Emotion.ANGER, "enfado": Emotion.ANGER, "ira": Emotion.ANGER,
    "rabia": Emotion.ANGER, "enojado": Emotion.ANGER, "enojada": Emotion.ANGER,
    "frustracion": Emotion.FRUSTRATION, "frustración": Emotion.FRUSTRATION,
    "frustrado": Emotion.FRUSTRATION, "frustrada": Emotion.FRUSTRATION,
    "miedo": Emotion.FEAR, "temor": Emotion.FEAR, "asustado": Emotion.FEAR,
    "ansiedad": Emotion.ANXIETY, "angustia": Emotion.ANXIETY,
    "preocupacion": Emotion.ANXIETY, "preocupación": Emotion.ANXIETY,
    "nervios": Emotion.ANXIETY, "nervioso": Emotion.ANXIETY, "nerviosa": Emotion.ANXIETY,
    "culpa": Emotion.GUILT, "culpable": Emotion.GUILT, "remordimiento": Emotion.GUILT,
    "verguenza": Emotion.EMBARRASSMENT, "vergüenza": Emotion.EMBARRASSMENT,
    "avergonzado": Emotion.EMBARRASSMENT, "avergonzada": Emotion.EMBARRASSMENT,
    "confusion": Emotion.CONFUSION, "confusión": Emotion.CONFUSION,
    "confundido": Emotion.CONFUSION, "confundida": Emotion.CONFUSION,
    "cansancio": Emotion.TIREDNESS, "cansado": Emotion.TIREDNESS,
    "cansada": Emotion.TIREDNESS, "agotamiento": Emotion.TIREDNESS,
    "agotado": Emotion.TIREDNESS, "agotada": Emotion.TIREDNESS,
    "neutro": Emotion.NEUTRAL, "neutra": Emotion.NEUTRAL, "ninguna": Emotion.NEUTRAL,
    # etiquetas HEREDADAS de core/emotion.py (retrocompatibilidad)
    "happy": Emotion.JOY, "excited": Emotion.EXCITEMENT, "love": Emotion.AFFECTION,
    "shy": Emotion.EMBARRASSMENT, "proud": Emotion.PRIDE,
    "playful": Emotion.JOY, "curious": Emotion.CONFUSION,
    "confused": Emotion.CONFUSION, "worried": Emotion.ANXIETY,
    "sad": Emotion.SADNESS, "angry": Emotion.ANGER,
    "surprised": Emotion.EXCITEMENT, "relaxed": Emotion.RELIEF,
    "focused": Emotion.NEUTRAL, "bored": Emotion.TIREDNESS,
    "sleepy": Emotion.TIREDNESS, "tired": Emotion.TIREDNESS,
    "anger": Emotion.ANGER, "sadness": Emotion.SADNESS, "joy": Emotion.JOY,
    "fear": Emotion.FEAR, "anxiety": Emotion.ANXIETY,
}


def boundary_from_name(name) -> Boundary | None:
    """Igual que emotion_from_name pero para límites explícitos."""
    if isinstance(name, Boundary):
        return name
    clave = str(name or "").strip().lower()
    if not clave:
        return None
    try:
        return Boundary(clave)
    except ValueError:
        return None


def vad_for(emotion: Emotion) -> tuple[float, float]:
    """Valencia y activación típicas de una emoción."""
    return EMOTION_VAD.get(emotion, (0.0, 0.3))

"""CompanionExpressionPolicy — la cara de YUE NO es la cara del usuario.

Este módulo existe para arreglar una confusión que estaba en el corazón del
sistema viejo: se usaba UNA sola etiqueta de emoción para dos cosas distintas.

    interpretar al usuario   ─┐
                              ├─ misma etiqueta → el avatar IMITABA al usuario
    animar el avatar         ─┘

Con eso, un usuario furioso ponía a YUE furiosa, y un usuario contando algo
doloroso mientras sonaba música alegre acababa viendo un avatar eufórico. Ni
una cosa ni la otra es acompañar.

La cadena correcta, y la que implementa este módulo:

    UserAffect  →  SupportPolicy  →  CompanionExpressionPolicy  →  YueExpression

Regla central: YUE **responde** a la emoción del usuario, no la copia.

    usuario: anger, arousal 0.9      →  YUE: worried, intensidad 0.6
    usuario: sadness profunda        →  YUE: worried/soft, presente y calmada
    usuario: pride por un logro      →  YUE: proud/excited  (aquí sí se contagia)

Las emociones positivas SÍ se comparten: alegrarse con alguien es acompañarlo.
Las negativas se responden con presencia, no con espejo.

Los nombres de salida son los que YA entiende el avatar VRM
(`ui/desktop_pet.set_emotion`), para no romper nada del renderizado existente.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..affect.models import AffectiveState, Emotion, clamp
from .needs import SupportNeed
from .policy import SafetyAssessment, SafetyLevel, SupportDecision

#: Vocabulario EXACTO que acepta el avatar VRM. No inventar nombres nuevos aquí:
#: cualquier etiqueta fuera de esta lista se renderiza como neutral.
AVATAR_EMOTIONS = frozenset({
    "neutral", "happy", "excited", "love", "shy", "proud", "playful",
    "curious", "confused", "worried", "sad", "angry", "surprised",
    "relaxed", "focused", "bored", "sleepy",
})


@dataclass(frozen=True)
class YueExpression:
    """Lo que YUE muestra en su cara y su cuerpo.

    Es una PROPUESTA: quien la recibe (el gestor de estado) puede descartarla
    si algo de mayor prioridad manda en el avatar en ese momento.
    """

    name: str = "neutral"
    intensity: float = 0.5
    duration_ms: int = 4200
    #: Prioridad sugerida (se traduce a `core.state.Priority` en la integración).
    priority: str = "USER_SUPPORT"
    #: Por qué esta cara y no otra.
    reason: str = ""

    def __post_init__(self):
        nombre = (self.name or "neutral").strip().lower()
        if nombre not in AVATAR_EMOTIONS:
            nombre = "neutral"
        object.__setattr__(self, "name", nombre)
        object.__setattr__(self, "intensity", clamp(self.intensity, 0.25, 1.0))
        object.__setattr__(self, "duration_ms", max(1200, int(self.duration_ms)))

    def as_tuple(self) -> tuple[str, float, int]:
        """Formato directo para `pet.set_emotion(nombre, intensidad, ms)`."""
        return (self.name, round(self.intensity, 3), int(self.duration_ms))


#: Cómo RESPONDE YUE a cada emoción del usuario.
#: (cara_de_yue, factor_de_intensidad, se_contagia)
#:
#: El factor amortigua: ante un usuario a 0.9 de activación, YUE no se pone a
#: 0.9 también. Estar presente y sereno es más útil que desbordarse con alguien.
_RESPUESTA: dict[Emotion, tuple[str, float, bool]] = {
    # --- malestar: YUE responde con presencia, NO con espejo ---------------
    Emotion.SADNESS:        ("worried", 0.70, False),
    Emotion.DISAPPOINTMENT: ("worried", 0.60, False),
    Emotion.LONELINESS:     ("worried", 0.72, False),
    Emotion.ANGER:          ("worried", 0.62, False),   # NO angry: sería sumar fuego
    Emotion.FRUSTRATION:    ("focused", 0.62, False),   # acompaña el problema, no la rabia
    Emotion.FEAR:           ("worried", 0.80, False),
    Emotion.ANXIETY:        ("worried", 0.72, False),
    Emotion.GUILT:          ("worried", 0.60, False),
    Emotion.EMBARRASSMENT:  ("shy", 0.55, False),       # complicidad, no juicio
    Emotion.CONFUSION:      ("curious", 0.60, False),
    Emotion.TIREDNESS:      ("relaxed", 0.50, False),   # baja el ritmo con la persona
    # --- lo bueno SÍ se comparte -------------------------------------------
    Emotion.JOY:            ("happy", 0.85, True),
    Emotion.EXCITEMENT:     ("excited", 0.90, True),
    Emotion.PRIDE:          ("proud", 0.95, True),
    Emotion.RELIEF:         ("relaxed", 0.75, True),
    Emotion.AFFECTION:      ("shy", 0.80, True),        # tsundere: se pone tímida
    Emotion.NEUTRAL:        ("curious", 0.50, False),
}

#: La decisión de apoyo puede imponer una cara concreta. Manda sobre la tabla
#: de arriba porque describe lo que YUE está HACIENDO, no lo que percibe.
_POR_MODO: dict[SupportNeed, tuple[str, float]] = {
    SupportNeed.SAFETY:     ("worried", 0.90),
    SupportNeed.GIVE_SPACE: ("relaxed", 0.40),   # calma, no drama ni reproche
    SupportNeed.CELEBRATE:  ("excited", 0.90),
    SupportNeed.DISTRACT:   ("playful", 0.75),
    SupportNeed.SOLVE:      ("focused", 0.78),
    SupportNeed.ASK:        ("curious", 0.60),
}

#: Pistas de `SupportDecision.avatar_hint` → cara del avatar.
_POR_PISTA: dict[str, str] = {
    "concerned": "worried", "soft": "relaxed", "attentive": "curious",
    "focused": "focused", "playful": "playful", "excited": "excited",
    "happy": "happy", "curious": "curious", "quiet": "relaxed",
}


@dataclass
class CompanionExpressionPolicy:
    """Traduce estado del usuario + decisión de apoyo en la cara de YUE."""

    #: Techo de intensidad ante malestar ajeno. YUE acompaña; no se desborda.
    max_intensidad_empatica: float = 0.75

    def express(self, affect: AffectiveState, decision: SupportDecision,
                safety: SafetyAssessment | None = None) -> YueExpression:
        safety = safety or SafetyAssessment()

        # ---- 1) Seguridad: cara de presencia total, sin alarmismo -----------
        if decision.mode == SupportNeed.SAFETY or safety.level >= SafetyLevel.HIGH:
            return YueExpression(
                "worried",
                0.85 if safety.level >= SafetyLevel.CRITICAL else 0.75,
                9000, "SAFETY",
                "seguridad: presencia serena y atención plena",
            )

        # ---- 2) El modo de apoyo tiene preferencia --------------------------
        if decision.mode in _POR_MODO:
            nombre, base = _POR_MODO[decision.mode]
            # Celebrar con alguien que está siendo irónico sería un desastre.
            if decision.mode == SupportNeed.CELEBRATE and affect.sarcasm_probability >= 0.45:
                return YueExpression("curious", 0.55, 4200, "USER_SUPPORT",
                                     "posible ironía: no se celebra a ciegas")
            return YueExpression(
                nombre,
                self._ajustar(base, affect),
                self._duracion(decision, affect),
                "USER_SUPPORT",
                f"modo de apoyo {decision.mode}",
            )

        # ---- 3) Sarcasmo alto: la cara sigue al fondo, no al tono -----------
        if affect.sarcasm_probability >= 0.55:
            return YueExpression("worried", 0.60, 5200, "USER_SUPPORT",
                                 "sarcasmo: se responde al malestar de debajo")

        # ---- 4) Respuesta a la emoción del usuario --------------------------
        nombre, factor, contagia = _RESPUESTA.get(
            affect.primary_emotion, ("curious", 0.55, False))

        intensidad = factor * (0.55 + 0.45 * abs(affect.valence))
        if contagia:
            # Con lo bueno YUE sí sube: alegrarse a medias no alegra a nadie.
            intensidad = min(1.0, intensidad + 0.15 * affect.arousal)
        else:
            intensidad = min(self.max_intensidad_empatica, intensidad)

        # Con poca confianza, la cara tampoco se compromete demasiado: una
        # expresión intensísima basada en una corazonada delata el error.
        if affect.confidence < 0.5:
            intensidad *= 0.75
            if affect.primary_emotion == Emotion.NEUTRAL:
                nombre = "curious"

        # La pista de la decisión puede matizar el resultado final.
        pista = _POR_PISTA.get((decision.avatar_hint or "").lower())
        if pista and affect.primary_emotion == Emotion.NEUTRAL:
            nombre = pista

        return YueExpression(
            nombre, intensidad, self._duracion(decision, affect),
            "USER_SUPPORT",
            f"respuesta a {affect.primary_emotion} (no imitación)",
        )

    # ------------------------------------------------------------------ interno
    def _ajustar(self, base: float, affect: AffectiveState) -> float:
        """Modula la intensidad base con la fuerza y la fiabilidad de la lectura."""
        valor = base * (0.7 + 0.3 * max(abs(affect.valence), affect.arousal))
        if affect.confidence < 0.5:
            valor *= 0.8
        return clamp(valor, 0.25, 1.0)

    def _duracion(self, decision: SupportDecision, affect: AffectiveState) -> int:
        """Cuánto aguanta la expresión.

        Los momentos delicados duran más: cambiar de cara a los tres segundos
        mientras alguien cuenta algo doloroso se lee como desinterés.
        """
        if decision.mode in (SupportNeed.SAFETY, SupportNeed.COMFORT):
            return 8000
        if decision.mode == SupportNeed.GIVE_SPACE:
            return 6000
        if affect.distress >= 0.5:
            return 6500
        return 4600


DEFAULT_EXPRESSION_POLICY = CompanionExpressionPolicy()


def express(affect: AffectiveState, decision: SupportDecision,
            safety: SafetyAssessment | None = None) -> YueExpression:
    """Atajo funcional."""
    return DEFAULT_EXPRESSION_POLICY.express(affect, decision, safety)

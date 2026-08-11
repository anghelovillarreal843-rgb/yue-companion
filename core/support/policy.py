"""SupportPolicy — la capa que decide CÓMO acompaña YUE.

Recibe tres lecturas independientes y produce una decisión:

    AffectiveState      qué parece sentir el usuario
    SupportIntent       qué parece querer de YUE
    SafetyAssessment    qué nivel de riesgo existe
            ↓
    SupportDecision     qué debería hacer YUE

Esta separación es lo que evita el viejo atajo «emoción → respuesta
predefinida». La emoción es solo UNA de las tres entradas, y ni siquiera la
que más manda.

Prioridades, de mayor a menor. Se aplican en este orden exacto:

    1. SEGURIDAD          si hay riesgo serio, todo lo demás pasa a segundo plano
    2. LÍMITES EXPLÍCITOS «no quiero consejos», «déjame solo» → se obedecen
    3. PETICIONES EXPLÍCITAS  «¿qué hago?» → ADVISE · «ayúdame» → SOLVE
    4. CELEBRACIÓN        una buena noticia no se convierte en terapia
    5. MALESTAR sin petición  → escuchar y/o consolar, NO resolver
    6. INCERTIDUMBRE alta → preguntar suavemente, no afirmar
    7. Conversación normal

Y una regla transversal: YUE nunca habla con más seguridad de la que tiene.
Con confianza baja no dice «sé cómo te sientes», dice «no sé si te leí bien».
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ..affect.models import AffectiveState, Boundary, Emotion
from .needs import SupportIntent, SupportNeed


class SafetyLevel(enum.IntEnum):
    """Espejo neutro de `core.safety_ext.RiskLevel`.

    Se replica aquí para que esta capa no dependa del módulo de seguridad y
    siga siendo testeable en aislamiento. Los valores COINCIDEN a propósito, de
    modo que la conversión es directa.
    """
    NONE = 0
    LOW = 1
    MODERATE = 2
    HIGH = 3
    CRITICAL = 4


@dataclass(frozen=True)
class SafetyAssessment:
    """Resultado de seguridad, en el formato que entiende esta capa."""
    level: SafetyLevel = SafetyLevel.NONE
    score: float = 0.0
    directive: str = ""
    reasons: tuple[str, ...] = ()
    protective: bool = False
    negated: bool = False

    @property
    def is_serious(self) -> bool:
        """A partir de MODERADO, la seguridad manda sobre cualquier otra cosa."""
        return self.level >= SafetyLevel.MODERATE

    @classmethod
    def from_safety_ext(cls, informe: dict | None) -> "SafetyAssessment":
        """Convierte el dict de `core.safety_ext.assess()` sin perder matices.

        Se conserva el NIVEL graduado, las razones, los factores protectores y
        la negación. Reducirlo a un booleano aquí tiraría por la borda todo el
        trabajo del detector — que es justo lo que había que evitar.
        """
        if not informe:
            return cls()
        try:
            nivel = SafetyLevel(int(informe.get("level", 0)))
        except (ValueError, TypeError):
            nivel = SafetyLevel.NONE
        return cls(
            level=nivel,
            score=float(informe.get("score", 0.0) or 0.0),
            directive=str(informe.get("directive", "") or ""),
            reasons=tuple(informe.get("reasons", ()) or ()),
            protective=bool(informe.get("protective", False)),
            negated=bool(informe.get("negated", False)),
        )


@dataclass(frozen=True)
class SupportDecision:
    """Qué debería hacer YUE en este turno.

    No es un texto ni una plantilla: son PARÁMETROS DE COMPORTAMIENTO que luego
    guían al modelo conversacional. YUE sigue escribiendo con su propia voz.
    """

    mode: SupportNeed = SupportNeed.LISTEN
    secondary_mode: SupportNeed | None = None
    #: Validar lo que siente ANTES de cualquier otra cosa.
    validate_first: bool = True
    #: ¿Puede hacer una pregunta? (como mucho una, y suave)
    ask_question: bool = False
    #: ¿Puede ofrecer consejo o soluciones?
    offer_advice: bool = False
    #: ¿Debe retirarse y no insistir?
    give_space: bool = False
    #: ¿Conviene retomar el tema más adelante?
    should_follow_up: bool = False
    #: Tono sugerido: warm, gentle, steady, playful, celebratory, quiet, urgent.
    tone: str = "warm"
    #: Pista (NO orden) para la expresión del avatar.
    avatar_hint: str = "attentive"
    #: Longitud sugerida de la respuesta: short, medium.
    length: str = "short"
    #: ¿Debe reconocer explícitamente que no está segura de haber leído bien?
    acknowledge_uncertainty: bool = False
    #: Por qué se decidió esto (depuración y tests).
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "mode": str(self.mode),
            "secondary_mode": str(self.secondary_mode) if self.secondary_mode else "",
            "validate_first": self.validate_first,
            "ask_question": self.ask_question,
            "offer_advice": self.offer_advice,
            "give_space": self.give_space,
            "should_follow_up": self.should_follow_up,
            "tone": self.tone,
            "avatar_hint": self.avatar_hint,
            "length": self.length,
            "acknowledge_uncertainty": self.acknowledge_uncertainty,
            "reasons": list(self.reasons),
        }


@dataclass
class SupportPolicy:
    """Decide el comportamiento de YUE. Sin estado y sin efectos secundarios."""

    #: Por debajo de esta confianza, YUE reconoce que no está segura.
    umbral_certeza: float = 0.55

    def decide(self, affect: AffectiveState, intent: SupportIntent,
               safety: SafetyAssessment | None = None,
               *, context_summary: dict | None = None) -> SupportDecision:
        safety = safety or SafetyAssessment()
        razones: list[str] = []
        ctx = context_summary or {}

        # ================================================================== 1
        # SEGURIDAD. Prioridad máxima y sin excepciones: aquí no hay bromas, ni
        # consejos, ni "dejarle espacio" aunque lo pida.
        if safety.is_serious:
            razones.append(f"seguridad nivel {safety.level.name}")
            urgente = safety.level >= SafetyLevel.HIGH
            return SupportDecision(
                mode=SupportNeed.SAFETY,
                secondary_mode=SupportNeed.COMFORT,
                validate_first=True,
                # Se pregunta solo en niveles no críticos: en CRÍTICO toca
                # acompañar y acercar ayuda, no interrogar.
                ask_question=safety.level < SafetyLevel.CRITICAL,
                offer_advice=False,
                give_space=False,   # nunca se deja sola a una persona en riesgo
                should_follow_up=True,
                tone="urgent" if urgente else "gentle",
                avatar_hint="concerned",
                length="short",
                acknowledge_uncertainty=False,
                reasons=tuple(razones + list(safety.reasons)),
            )

        # Riesgo LEVE: no cambia el modo, pero sí baja el sarcasmo y sube el cuidado.
        cuidado_extra = safety.level == SafetyLevel.LOW
        if cuidado_extra:
            razones.append("señal leve de cuidado")

        # ================================================================== 2
        # LÍMITES EXPLÍCITOS. Lo que la persona pidió que YUE NO haga.
        prohibido_aconsejar = (
            intent.wants_advice is False
            or affect.has_boundary(Boundary.NO_ADVICE)
        )
        prohibido_preguntar = affect.has_boundary(Boundary.NO_QUESTIONS)

        if (affect.has_boundary(Boundary.WANTS_SPACE)
                or intent.primary_need == SupportNeed.GIVE_SPACE):
            razones.append("pidió espacio: se respeta sin insistir")
            return SupportDecision(
                mode=SupportNeed.GIVE_SPACE,
                secondary_mode=None,
                validate_first=True,
                ask_question=False,       # nada de «¿seguro que estás bien?»
                offer_advice=False,
                give_space=True,
                # No se persigue a nadie que acaba de pedir espacio. Si dijo
                # «luego hablamos», el siguiente paso lo da la persona.
                should_follow_up=False,
                tone="quiet",
                avatar_hint="soft",
                length="short",
                acknowledge_uncertainty=False,
                reasons=tuple(razones),
            )

        if affect.has_boundary(Boundary.DOES_NOT_WANT_TO_TALK):
            razones.append("no quiere hablar del tema")
            return SupportDecision(
                mode=SupportNeed.LISTEN,
                secondary_mode=SupportNeed.DISTRACT,
                validate_first=True,
                ask_question=False,
                offer_advice=False,
                give_space=False,
                should_follow_up=False,
                tone="gentle",
                avatar_hint="soft",
                reasons=tuple(razones),
            )

        # ================================================================== 3
        # PETICIONES EXPLÍCITAS. Si lo pidió, se le da.
        if intent.explicit and intent.primary_need == SupportNeed.SOLVE:
            razones.append("pidió resolver algo paso a paso")
            return SupportDecision(
                mode=SupportNeed.SOLVE,
                secondary_mode=intent.secondary_need,
                validate_first=affect.distress >= 0.45,
                ask_question=affect.uncertainty >= 0.6,
                offer_advice=True,
                give_space=False,
                should_follow_up=True,
                tone="steady",
                avatar_hint="focused",
                length="medium",
                reasons=tuple(razones),
            )

        if intent.explicit and intent.primary_need == SupportNeed.ADVISE and not prohibido_aconsejar:
            razones.append("pidió consejo")
            return SupportDecision(
                mode=SupportNeed.ADVISE,
                secondary_mode=intent.secondary_need,
                validate_first=affect.distress >= 0.45,
                ask_question=affect.uncertainty >= 0.6,
                offer_advice=True,
                give_space=False,
                should_follow_up=True,
                tone="warm",
                avatar_hint="attentive",
                length="medium",
                reasons=tuple(razones),
            )

        if intent.primary_need == SupportNeed.DISTRACT:
            razones.append("pidió distracción")
            return SupportDecision(
                mode=SupportNeed.DISTRACT,
                secondary_mode=SupportNeed.COMFORT if affect.is_negative else None,
                validate_first=affect.is_negative,
                ask_question=False,
                offer_advice=False,
                give_space=False,
                should_follow_up=affect.is_negative,
                tone="playful" if not cuidado_extra else "warm",
                avatar_hint="playful",
                reasons=tuple(razones),
            )

        if intent.primary_need == SupportNeed.LISTEN and intent.explicit:
            razones.append("pidió solo ser escuchado")
            return SupportDecision(
                mode=SupportNeed.LISTEN,
                secondary_mode=SupportNeed.COMFORT if affect.is_negative else None,
                validate_first=True,
                # Nada de interrogatorio: pidió contar, no que le preguntaran.
                ask_question=False,
                offer_advice=False,
                give_space=False,
                should_follow_up=True,
                tone="gentle",
                avatar_hint="soft",
                reasons=tuple(razones),
            )

        # ================================================================== 4
        # CELEBRACIÓN. Una alegría no se analiza, se acompaña.
        if intent.primary_need == SupportNeed.CELEBRATE and affect.sarcasm_probability < 0.45:
            razones.append("hay algo que celebrar")
            return SupportDecision(
                mode=SupportNeed.CELEBRATE,
                secondary_mode=None,
                validate_first=False,   # aquí no toca validar dolor: toca alegrarse
                ask_question=True,      # una pregunta curiosa SUMA a la alegría
                offer_advice=False,
                give_space=False,
                should_follow_up=False,
                tone="celebratory",
                avatar_hint="excited",
                reasons=tuple(razones),
            )

        # ================================================================== 5
        # SARCASMO ALTO. La persona no está bien aunque suene alegre.
        if affect.sarcasm_probability >= 0.55:
            razones.append("sarcasmo alto: hay malestar debajo del tono")
            return SupportDecision(
                mode=SupportNeed.LISTEN,
                secondary_mode=SupportNeed.COMFORT,
                validate_first=True,
                ask_question=not prohibido_preguntar,
                offer_advice=False,     # nunca se resuelve encima de una ironía
                give_space=False,
                should_follow_up=True,
                tone="gentle",
                avatar_hint="soft",
                acknowledge_uncertainty=False,
                reasons=tuple(razones),
            )

        # ================================================================== 6
        # INCERTIDUMBRE ALTA. Preguntar es mejor que acertar de milagro.
        poca_certeza = (affect.confidence < self.umbral_certeza
                        or affect.uncertainty >= 0.65)
        if poca_certeza and (affect.is_negative or affect.negated_emotions
                             or intent.primary_need == SupportNeed.ASK):
            # Pero preguntar DOS turnos seguidos a alguien que lleva un rato
            # mal deja de ser interés y se convierte en interrogatorio. Si el
            # malestar viene sostenido y el turno anterior YUE ya preguntó,
            # toca callar y acompañar: la información llegará sola o no llegará.
            ya_pregunto = str(ctx.get("last_support_mode", "")) == str(SupportNeed.ASK)
            if bool(ctx.get("sustained")) and ya_pregunto:
                razones.append("ya preguntó antes y el malestar sigue: acompañar en silencio")
                return SupportDecision(
                    mode=SupportNeed.COMFORT,
                    secondary_mode=SupportNeed.LISTEN,
                    validate_first=True,
                    ask_question=False,
                    offer_advice=False,
                    give_space=False,
                    should_follow_up=True,
                    tone="gentle",
                    avatar_hint="concerned",
                    acknowledge_uncertainty=False,
                    reasons=tuple(razones),
                )
            razones.append("lectura poco segura: preguntar en vez de afirmar")
            return SupportDecision(
                mode=SupportNeed.ASK,
                secondary_mode=SupportNeed.LISTEN,
                validate_first=True,
                ask_question=not prohibido_preguntar,
                offer_advice=False,
                give_space=False,
                should_follow_up=True,
                tone="gentle",
                avatar_hint="attentive",
                acknowledge_uncertainty=True,
                reasons=tuple(razones),
            )

        # ================================================================== 7
        # MALESTAR sin petición de soluciones → contener y escuchar.
        if affect.distress >= 0.5 or intent.primary_need == SupportNeed.COMFORT:
            sostenido = bool(ctx.get("sustained"))
            if sostenido:
                razones.append("malestar sostenido varios turnos")
            razones.append("hay malestar y no pidió soluciones")
            return SupportDecision(
                mode=SupportNeed.COMFORT,
                secondary_mode=SupportNeed.LISTEN,
                validate_first=True,
                # Una sola pregunta abierta, y solo si no lleva ya un rato mal:
                # a quien lleva tres turnos hundido no se le interroga más.
                ask_question=(not prohibido_preguntar) and not sostenido,
                offer_advice=False,
                give_space=False,
                should_follow_up=True,
                tone="gentle",
                avatar_hint="concerned",
                acknowledge_uncertainty=affect.confidence < self.umbral_certeza,
                reasons=tuple(razones),
            )

        if intent.primary_need == SupportNeed.ASK:
            razones.append("conviene entender mejor antes de actuar")
            return SupportDecision(
                mode=SupportNeed.ASK,
                secondary_mode=intent.secondary_need or SupportNeed.LISTEN,
                validate_first=affect.is_negative,
                ask_question=not prohibido_preguntar,
                offer_advice=False,
                give_space=False,
                should_follow_up=False,
                tone="warm",
                avatar_hint="curious",
                acknowledge_uncertainty=affect.confidence < self.umbral_certeza,
                reasons=tuple(razones),
            )

        # ================================================================== 8
        # Conversación normal. YUE puede ser ella misma sin protocolo encima.
        razones.append("conversación normal")
        positivo = affect.is_positive
        return SupportDecision(
            mode=intent.primary_need if intent.primary_need != SupportNeed.SAFETY
            else SupportNeed.LISTEN,
            secondary_mode=intent.secondary_need,
            validate_first=affect.is_negative,
            ask_question=not prohibido_preguntar,
            offer_advice=bool(intent.wants_advice) and not prohibido_aconsejar,
            give_space=False,
            should_follow_up=False,
            tone="playful" if (positivo and not cuidado_extra) else "warm",
            avatar_hint="happy" if positivo else "attentive",
            acknowledge_uncertainty=False,
            reasons=tuple(razones),
        )


DEFAULT_POLICY = SupportPolicy()


def decide(affect: AffectiveState, intent: SupportIntent,
           safety: SafetyAssessment | None = None,
           *, context_summary: dict | None = None) -> SupportDecision:
    """Atajo funcional sobre la política por defecto."""
    return DEFAULT_POLICY.decide(affect, intent, safety, context_summary=context_summary)

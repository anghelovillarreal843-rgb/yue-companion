"""CompanionBrain — el orquestador de la comprensión emocional de YUE.

Es la ÚNICA pieza que `main.py` necesita conocer. Por dentro encadena todo lo
demás en el orden que pide el diseño:

    MENSAJE DEL USUARIO
        ↓
    COMPRENDER ESTADO AFECTIVO      core.affect.AffectiveInterpreter
        ↓
    COMPRENDER QUÉ NECESITA         core.support.NeedDetector
        ↓
    EVALUAR SEGURIDAD               core.safety_ext.assess  (graduado)
        ↓
    DECIDIR CÓMO ACOMPAÑAR          core.support.SupportPolicy
        ↓
    GENERAR RESPUESTA               bloque de prompt → el LLM escribe
        ↓
    DECIDIR EXPRESIÓN DEL AVATAR    core.support.CompanionExpressionPolicy

Todo es ADITIVO y degradable. Si falta un módulo, si no hay red o si algo
revienta, `process()` devuelve un resultado neutro válido y la aplicación sigue
funcionando exactamente como antes. Ninguna excepción de aquí puede tumbar una
conversación.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .affect import AffectiveInterpreter, AffectiveState, Emotion
from .affect.negation import risk_phrase_is_negated
from .support import (
    NeedDetector, SafetyAssessment, SafetyLevel, SupportDecision, SupportIntent,
    SupportNeed, SupportPolicy, YueExpression,
)
from .support.expression import CompanionExpressionPolicy
from .support.prompting import build_compact_summary, build_state_block

try:  # El detector graduado es el bueno; si no está, se degrada con elegancia.
    from core import safety_ext as _safety_ext
except Exception:  # pragma: no cover
    _safety_ext = None

try:  # Detector clásico: se conserva como red de seguridad mínima.
    from core import safety as _safety_base
except Exception:  # pragma: no cover
    _safety_base = None


@dataclass(frozen=True)
class CompanionResult:
    """Todo lo que YUE entendió de un mensaje, en un solo objeto."""

    affect: AffectiveState
    intent: SupportIntent
    safety: SafetyAssessment
    decision: SupportDecision
    expression: YueExpression
    #: Bloque listo para inyectar en el prompt del sistema.
    prompt_block: str = ""
    #: Directiva de seguridad (vacía si no hay riesgo).
    safety_directive: str = ""
    #: Resumen de una línea, para logs.
    summary: str = ""
    #: Resumen del contexto afectivo (tendencia, duración, estabilidad).
    context: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0

    # ---- atajos cómodos para main.py --------------------------------------
    @property
    def is_risk(self) -> bool:
        """¿Hay riesgo suficiente para que la seguridad tome el mando?"""
        return self.safety.is_serious

    @property
    def should_log_risk_event(self) -> bool:
        """¿Conviene dejar constancia (solo la hora) del evento de riesgo?"""
        return self.safety.level >= SafetyLevel.HIGH

    def mood_label(self) -> str:
        """Etiqueta para el histórico de ánimo, compatible con `mood_log`."""
        return str(self.affect.primary_emotion)

    def to_dict(self) -> dict:
        return {
            "affect": self.affect.to_dict(),
            "intent": self.intent.to_dict(),
            "safety": {
                "level": self.safety.level.name,
                "score": round(self.safety.score, 3),
                "reasons": list(self.safety.reasons),
            },
            "decision": self.decision.to_dict(),
            "expression": {
                "name": self.expression.name,
                "intensity": round(self.expression.intensity, 3),
                "duration_ms": self.expression.duration_ms,
                "priority": self.expression.priority,
            },
            "context": dict(self.context),
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


@dataclass
class CompanionBrain:
    """Comprensión emocional completa de un mensaje del usuario.

    Parámetros
    ----------
    engine
        Motor conversacional (`AIEngine`). Opcional: sin él, todo el sistema
        funciona con reglas locales, offline y sin coste.
    use_semantic
        Permite apagar por completo la consulta al LLM (útil en tests y en
        equipos sin conexión).
    """

    engine: object | None = None
    use_semantic: bool = True

    interpreter: AffectiveInterpreter = field(default=None)  # type: ignore[assignment]
    needs: NeedDetector = field(default_factory=NeedDetector)
    policy: SupportPolicy = field(default_factory=SupportPolicy)
    expression_policy: CompanionExpressionPolicy = field(
        default_factory=CompanionExpressionPolicy)

    _last_result: CompanionResult | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if self.interpreter is None:
            umbral = 0.62
            turnos = 3
            try:
                import config as _cfg
                umbral = float(getattr(_cfg, "AFFECT_CONFIDENCE_THRESHOLD", 0.62))
                turnos = int(getattr(_cfg, "AFFECT_CONTEXT_TURNS", 3))
            except Exception:
                pass
            self.interpreter = AffectiveInterpreter(
                engine=self.engine, use_semantic=self.use_semantic,
                confidence_threshold=umbral, context_turns=turnos)
            # Presupuesto y timeout de la consulta semántica, si hay config.
            try:
                import config as _cfg
                self.interpreter.semantic.max_calls = int(
                    getattr(_cfg, "AFFECT_SEMANTIC_MAX_CALLS", 40))
                self.interpreter.semantic.timeout = int(
                    getattr(_cfg, "AFFECT_SEMANTIC_TIMEOUT", 12))
            except Exception:
                pass

    # ------------------------------------------------------------------ API
    def process(self, text: str, *, recent_messages=None,
                external_signal: bool = False,
                allow_semantic: bool | None = None) -> CompanionResult:
        """Analiza un mensaje del usuario de principio a fin.

        Parámetros
        ----------
        text
            El mensaje.
        recent_messages
            Últimos intercambios (formato de `memory.recent_messages()`). Se usa
            un trocito para dar contexto; no se manda la conversación entera.
        external_signal
            Señal SOSTENIDA no textual (cámara / histórico de ánimo). Solo puede
            SUBIR el nivel de cuidado, nunca bajarlo.
        """
        inicio = time.perf_counter()

        # ---- 1) Estado afectivo ------------------------------------------
        try:
            affect = self.interpreter.interpret(
                text, recent_context=recent_messages, allow_semantic=allow_semantic)
        except Exception as exc:  # pragma: no cover
            print("[affect] fallo interpretando, sigo en neutro:", exc)
            affect = AffectiveState(confidence=0.0, uncertainty=1.0)

        # ---- 2) Qué necesita ----------------------------------------------
        try:
            intent = self.needs.detect(text, affect)
        except Exception as exc:  # pragma: no cover
            print("[support] fallo detectando la necesidad:", exc)
            intent = SupportIntent()

        # ---- 3) Seguridad (graduada, nunca booleana) -----------------------
        safety = self._assess_safety(text, affect=affect, external_signal=external_signal)

        # ---- 4) Decisión de acompañamiento --------------------------------
        contexto = {}
        try:
            contexto = self.interpreter.context.summary()
        except Exception:
            contexto = {}
        try:
            decision = self.policy.decide(affect, intent, safety, context_summary=contexto)
        except Exception as exc:  # pragma: no cover
            print("[support] fallo decidiendo, uso escucha:", exc)
            decision = SupportDecision()

        try:
            self.interpreter.context.note_support_mode(str(decision.mode))
        except Exception:
            pass

        # ---- 5) Expresión del avatar (respuesta, NO imitación) -------------
        try:
            expression = self.expression_policy.express(affect, decision, safety)
        except Exception as exc:  # pragma: no cover
            print("[support] fallo eligiendo la expresión:", exc)
            expression = YueExpression()

        # ---- 6) Bloque para el prompt --------------------------------------
        try:
            bloque = build_state_block(affect, intent, safety, decision, contexto)
        except Exception:  # pragma: no cover
            bloque = ""

        resultado = CompanionResult(
            affect=affect,
            intent=intent,
            safety=safety,
            decision=decision,
            expression=expression,
            prompt_block=bloque,
            safety_directive=safety.directive,
            summary=build_compact_summary(affect, intent, safety, decision),
            context=contexto,
            elapsed_ms=(time.perf_counter() - inicio) * 1000.0,
        )
        self._last_result = resultado
        return resultado

    # ---------------------------------------------------------------- interno
    def _assess_safety(self, text: str, *, affect: AffectiveState | None = None,
                       external_signal: bool = False) -> SafetyAssessment:
        """Evalúa el riesgo apoyándose en `safety_ext` (el detector completo).

        Se usa `safety_ext` y NO se crea un tercer detector: ya conserva niveles
        graduados, negación, factores protectores, contexto de ficción y señal
        externa. Aquí solo se traduce su informe al formato de la política y se
        añade un matiz que el sistema afectivo puede aportar: si una frase de
        riesgo aparece EXPLÍCITAMENTE negada («ya no quiero desaparecer»), se
        baja un escalón — la persona sigue hablando del tema, así que el cuidado
        no desaparece, pero no se le lanza una alerta crítica encima de una
        frase que expresa justo lo contrario.
        """
        if _safety_ext is None:
            # Degradación: al menos el detector clásico, si existe.
            try:
                if _safety_base is not None and _safety_base.detect_risk(text):
                    return SafetyAssessment(
                        level=SafetyLevel.HIGH, score=0.75,
                        directive=_safety_base.safety_directive(_recursos()),
                        reasons=("detector clásico",))
            except Exception:
                pass
            return SafetyAssessment()

        try:
            informe = _safety_ext.assess(text, external_signal=bool(external_signal))
            evaluacion = SafetyAssessment.from_safety_ext(informe)
        except Exception as exc:  # pragma: no cover
            print("[safety] fallo evaluando el riesgo:", exc)
            return SafetyAssessment()

        # Matiz de negación explícita sobre una frase de riesgo.
        try:
            if (evaluacion.level >= SafetyLevel.MODERATE
                    and risk_phrase_is_negated(text)):
                nuevo = SafetyLevel(max(SafetyLevel.LOW, evaluacion.level - 1))
                evaluacion = SafetyAssessment(
                    level=nuevo,
                    score=evaluacion.score * 0.7,
                    directive=_safety_ext.directive_for(nuevo, _recursos()),
                    reasons=evaluacion.reasons + ("la frase de riesgo aparece negada",),
                    protective=evaluacion.protective,
                    negated=True,
                )
        except Exception:
            pass

        # Nivel LEVE apoyado en una emoción que el usuario NEGÓ: se retira.
        # `safety_ext` casa «estoy triste» aunque venga precedido de un «no»;
        # el análisis de alcance de negación sí lo ve, y aquí se corrige. Solo
        # se aplica al nivel más bajo: por encima, la duda siempre juega a favor
        # de cuidar.
        try:
            if (evaluacion.level == SafetyLevel.LOW and affect is not None
                    and affect.negated_emotions and not affect.is_negative
                    and not external_signal):
                evaluacion = SafetyAssessment(
                    level=SafetyLevel.NONE, score=0.0, directive="",
                    reasons=evaluacion.reasons + ("la emoción aparece negada",),
                    protective=evaluacion.protective, negated=True)
        except Exception:
            pass

        # La directiva se regenera con los recursos de crisis configurados.
        if evaluacion.level > SafetyLevel.NONE and not evaluacion.directive:
            try:
                evaluacion = SafetyAssessment(
                    level=evaluacion.level, score=evaluacion.score,
                    directive=_safety_ext.directive_for(evaluacion.level, _recursos()),
                    reasons=evaluacion.reasons, protective=evaluacion.protective,
                    negated=evaluacion.negated)
            except Exception:
                pass
        return evaluacion

    # ------------------------------------------------------------ utilidades
    @property
    def last_result(self) -> CompanionResult | None:
        return self._last_result

    def reset(self) -> None:
        """Olvida el contexto afectivo (nueva sesión)."""
        try:
            self.interpreter.reset()
        except Exception:
            pass
        self._last_result = None

    def stats(self) -> dict:
        try:
            return self.interpreter.stats()
        except Exception:  # pragma: no cover
            return {}


def _recursos() -> str:
    """Recursos de crisis configurados. Vacío si no hay config disponible."""
    try:
        import config
        return str(getattr(config, "CRISIS_RESOURCES", "") or "")
    except Exception:
        return ""

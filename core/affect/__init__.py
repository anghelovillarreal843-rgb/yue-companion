"""core.affect — comprensión del estado afectivo del usuario.

Responde a UNA sola pregunta: *¿qué parece estar sintiendo la persona?*

Deliberadamente NO decide qué hace YUE con esa información (de eso se encarga
`core.support`) ni qué cara pone el avatar (`core.support.expression`). Esa
separación es el corazón del rediseño: antes, una misma etiqueta servía para
interpretar al usuario y para animar el avatar, y por eso YUE acababa imitando
al usuario en vez de acompañarlo.

Uso típico:

    from core.affect import AffectiveInterpreter

    interpreter = AffectiveInterpreter(engine=ai_engine)
    estado = interpreter.interpret("Bueno, al final no vino. Da igual.")
    estado.primary_emotion    # Emotion.DISAPPOINTMENT
    estado.confidence         # 0.64  -> conviene preguntar, no afirmar

Todo funciona OFFLINE: sin `engine` se usan solo las reglas locales.
"""
from .context import AffectiveContext
from .interpreter import AffectiveInterpreter
from .models import (
    AffectiveState, Boundary, Emotion, NEGATIVE_EMOTIONS, POSITIVE_EMOTIONS,
    boundary_from_name, emotion_from_name, vad_for,
)
from .negation import find_scopes, normalize, risk_phrase_is_negated
from .rules import FastAffectiveRules, analyze
from .sarcasm import SarcasmSignal
from .sarcasm import detect as detect_sarcasm
from .semantic import SemanticInterpreter, merge

__all__ = [
    "AffectiveInterpreter", "AffectiveState", "AffectiveContext",
    "Emotion", "Boundary", "NEGATIVE_EMOTIONS", "POSITIVE_EMOTIONS",
    "FastAffectiveRules", "analyze",
    "SemanticInterpreter", "merge",
    "SarcasmSignal", "detect_sarcasm",
    "find_scopes", "normalize", "risk_phrase_is_negated",
    "emotion_from_name", "boundary_from_name", "vad_for",
]

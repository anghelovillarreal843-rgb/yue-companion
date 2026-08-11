"""core.support — qué hace YUE con lo que entendió.

Mientras `core.affect` responde a *«¿qué siente la persona?»*, este paquete
responde a las otras dos preguntas:

    ¿qué quiere de mí?     →  needs.py       (SupportIntent)
    ¿cómo la acompaño?     →  policy.py      (SupportDecision)
    ¿qué cara pongo?       →  expression.py  (YueExpression)
    ¿qué le digo al modelo? → prompting.py   (bloque de prompt)

Cada capa es independiente y testeable por separado. La emoción del usuario
entra por un lado y sale una decisión de comportamiento por el otro, sin que
en ningún punto se confunda «lo que siente él» con «lo que hace ella».
"""
from .expression import (
    AVATAR_EMOTIONS, CompanionExpressionPolicy, YueExpression, express,
)
from .needs import NeedDetector, SupportIntent, SupportNeed, detect
from .policy import (
    SafetyAssessment, SafetyLevel, SupportDecision, SupportPolicy, decide,
)
from .prompting import build_compact_summary, build_state_block

__all__ = [
    "SupportNeed", "SupportIntent", "NeedDetector", "detect",
    "SupportPolicy", "SupportDecision", "SafetyAssessment", "SafetyLevel", "decide",
    "CompanionExpressionPolicy", "YueExpression", "AVATAR_EMOTIONS", "express",
    "build_state_block", "build_compact_summary",
]

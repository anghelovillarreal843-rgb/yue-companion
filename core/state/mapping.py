"""Traducción entre lo que YA existe en YUE y los tres estados nuevos.

Este módulo es deliberadamente FINO. No decide nada por su cuenta: solo mueve
información desde las piezas que ya funcionaban hacia la estructura nueva.

    result.affect + result.context + result.intent   →   USER STATE
    result.expression + result.decision              →   YUE PROPOSAL

En concreto NO se crea un segundo detector emocional ni una segunda política de
expresión. `CompanionExpressionPolicy` ya hace la transformación buena
(usuario triste → YUE preocupada, no triste) y es la que se sigue usando: aquí
solo se le pone el envoltorio de propuesta con prioridad y TTL.

Lo único que se AÑADE es lo que faltaba: `behavior`, `voice_style` e
`initiative`, derivados de la necesidad detectada. Son los campos que permiten
que YUE sepa cuándo callarse, y esa decisión no estaba en ninguna parte.
"""
from __future__ import annotations

import time

from .models import UserObservation, UserState, YueProposal

# --------------------------------------------------------------------------
# Necesidad → cómo se comporta YUE
# --------------------------------------------------------------------------
#: (behavior, voice_style, initiative)
#:
#: `initiative` es la pieza nueva y la más importante para no resultar pesada:
#:
#:     none    no habla si no le hablan          (la persona pidió espacio)
#:     low     acompaña, responde, no propone
#:     medium  puede preguntar una cosa suave
#:     high    puede proponer y llevar la iniciativa
_POR_NECESIDAD: dict[str, tuple[str, str, str]] = {
    "LISTEN":     ("listening",    "gentle",      "low"),
    "COMFORT":    ("comforting",   "soft",        "low"),
    "ASK":        ("asking",       "warm",        "medium"),
    "ADVISE":     ("advising",     "steady",      "medium"),
    "SOLVE":      ("solving",      "steady",      "high"),
    "DISTRACT":   ("entertaining", "playful",     "high"),
    "CELEBRATE":  ("celebrating",  "celebratory", "high"),
    # Pidió espacio: iniciativa CERO. Insistir aquí es el error clásico.
    "GIVE_SPACE": ("waiting",      "quiet",       "none"),
    # Riesgo: presencia total, pero calmada. Ni alarmismo ni desaparecer.
    "SAFETY":     ("grounding",    "steady",      "medium"),
}

#: Emoción de YUE → pose base del avatar, cuando nadie pide una animación
#: concreta. Vocabulario del avatar VRM (ui/desktop_pet).
_POSE_POR_EMOCION: dict[str, str] = {
    "happy": "smile", "excited": "cheer", "proud": "proud", "love": "shy",
    "shy": "shy", "playful": "playful", "curious": "curious",
    "confused": "think", "worried": "concerned", "sad": "down",
    "angry": "tense", "surprised": "surprise", "relaxed": "idle",
    "focused": "focus", "bored": "idle", "sleepy": "idle", "neutral": "idle",
}

#: Comportamiento → pose del avatar. Manda sobre la emoción, porque describe lo
#: que YUE está HACIENDO y eso se ve más que la cara.
_POSE_POR_COMPORTAMIENTO: dict[str, str] = {
    "teaching": "explaining", "explaining": "explaining",
    "listening": "listening", "comforting": "listening",
    "waiting": "idle", "solving": "focus", "celebrating": "cheer",
    "entertaining": "playful", "enjoying_music": "dancing",
}


def behavior_for_need(need: str) -> tuple[str, str, str]:
    """(behavior, voice_style, initiative) para una necesidad de apoyo."""
    clave = str(need or "").strip().upper()
    return _POR_NECESIDAD.get(clave, ("attentive", "warm", "low"))


def avatar_pose(emotion: str, behavior: str = "") -> str:
    """Pose coherente del avatar para una emoción y un comportamiento."""
    porcomp = _POSE_POR_COMPORTAMIENTO.get(str(behavior or "").strip().lower())
    if porcomp:
        return porcomp
    return _POSE_POR_EMOCION.get(str(emotion or "").strip().lower(), "idle")


# --------------------------------------------------------------------------
# CompanionResult → USER STATE
# --------------------------------------------------------------------------
def user_state_from_companion(result, *, base: UserState | None = None,
                              topic: str = "") -> UserState:
    """Extrae de `CompanionResult` TODO lo que describe al usuario.

    Se reutilizan las estructuras existentes tal cual: `affect` (emoción,
    valencia, activación, incertidumbre), `context` (tendencia, duración,
    estabilidad — ya calculadas por `AffectiveContext`, no se recalcula nada) e
    `intent` (la necesidad detectada por `NeedDetector`).
    """
    base = base or UserState()
    if result is None:
        return base

    afecto = getattr(result, "affect", None)
    intencion = getattr(result, "intent", None)
    contexto = getattr(result, "context", None) or {}
    seguridad = getattr(result, "safety", None)

    if afecto is None:
        return base

    secundaria = getattr(afecto, "secondary_emotion", None)
    necesidad = ""
    secundaria_necesidad = ""
    if intencion is not None:
        necesidad = str(getattr(intencion, "primary_need", "") or "")
        sec = getattr(intencion, "secondary_need", None)
        secundaria_necesidad = str(sec) if sec else ""

    nivel = "NONE"
    if seguridad is not None:
        try:
            nivel = seguridad.level.name
        except Exception:
            nivel = str(getattr(seguridad, "level", "NONE"))

    return UserState(
        emotion=str(getattr(afecto, "primary_emotion", "neutral")),
        secondary_emotion=str(secundaria) if secundaria else "",
        confidence=float(getattr(afecto, "confidence", 0.0)),
        valence=float(getattr(afecto, "valence", 0.0)),
        arousal=float(getattr(afecto, "arousal", 0.3)),
        distress=float(getattr(afecto, "distress", 0.0)),
        uncertainty=float(getattr(afecto, "uncertainty", 1.0)),
        need=necesidad or "none",
        secondary_need=secundaria_necesidad,
        topic=topic or base.topic,
        trigger=str(getattr(afecto, "possible_trigger", "") or ""),
        # Tendencia, duración y estabilidad YA las calcula AffectiveContext.
        # Aquí solo se copian: duplicar ese cálculo sería crear un segundo
        # sistema que acabaría discrepando del primero.
        trend=str(contexto.get("trend", base.trend) or "stable"),
        sustained=bool(contexto.get("sustained", base.sustained)),
        duration_s=float(contexto.get("duration_s", 0.0) or 0.0),
        stability=float(contexto.get("stability", 0.0) or 0.0),
        # El texto es la fuente: peso 1.00, sin ponderar.
        text_confidence=float(getattr(afecto, "confidence", 0.0)),
        voice_confidence=base.voice_confidence,
        camera_confidence=base.camera_confidence,
        dominant_source="text",
        explicit=bool(getattr(intencion, "explicit", False)),
        safety_level=nivel,
        updated_at=time.time(),
    )


def observation_from_companion(result) -> UserObservation | None:
    """Convierte el análisis del texto en una observación para la fusión.

    Marca `explicit=True` cuando la persona lo dijo con palabras claras. Es lo
    que le da autoridad frente a la cámara: una frase explícita no la tumba una
    cara neutra.
    """
    if result is None:
        return None
    afecto = getattr(result, "affect", None)
    if afecto is None:
        return None
    intencion = getattr(result, "intent", None)

    confianza = float(getattr(afecto, "confidence", 0.0))
    explicito = bool(getattr(intencion, "explicit", False))
    # Una lectura muy segura del texto vale como declaración explícita aunque
    # no haya una petición literal: «estoy fatal» no pide nada, pero lo dice.
    if confianza >= 0.75 and str(getattr(afecto, "primary_emotion", "")) != "neutral":
        explicito = True

    return UserObservation(
        source="text",
        emotion=str(getattr(afecto, "primary_emotion", "neutral")),
        confidence=confianza,
        valence=float(getattr(afecto, "valence", 0.0)),
        arousal=float(getattr(afecto, "arousal", 0.3)),
        explicit=explicito,
        detail=str(getattr(afecto, "possible_trigger", "") or ""),
    )


# --------------------------------------------------------------------------
# CompanionResult → YUE PROPOSAL
# --------------------------------------------------------------------------
def proposal_from_companion(result, *, priority: int | None = None,
                            source: str = "conversation") -> YueProposal | None:
    """Convierte la decisión de acompañamiento en una propuesta completa.

    La cara sale de `CompanionExpressionPolicy` SIN tocarla: ya implementa la
    regla de que YUE responde al usuario en vez de imitarlo. Lo que se añade
    aquí es el resto del comportamiento (qué hace, cómo suena, cuánta iniciativa
    se permite), para que el estado final sea coherente y no una cara suelta.
    """
    if result is None:
        return None
    expresion = getattr(result, "expression", None)
    decision = getattr(result, "decision", None)
    if expresion is None:
        return None

    from .state_manager import Priority

    modo = str(getattr(decision, "mode", "")) if decision is not None else ""
    comportamiento, voz, iniciativa = behavior_for_need(modo)

    # El tono decidido por SupportPolicy manda sobre el genérico de la tabla:
    # es más específico y ya tuvo en cuenta el contexto.
    tono = str(getattr(decision, "tone", "") or "") if decision is not None else ""
    if tono:
        voz = tono

    # Si la persona pidió espacio explícitamente, la iniciativa se apaga pase lo
    # que pase. Es un límite que puso ella; no se negocia con una tabla.
    if decision is not None and bool(getattr(decision, "give_space", False)):
        iniciativa = "none"
    elif decision is not None and bool(getattr(decision, "ask_question", False)):
        if iniciativa in ("none", "low"):
            iniciativa = "medium"

    es_riesgo = bool(getattr(result, "is_risk", False))
    if priority is None:
        priority = int(Priority.EMERGENCY if es_riesgo else Priority.USER)

    duracion = int(getattr(expresion, "duration_ms", 4200))
    return YueProposal(
        emotion=str(getattr(expresion, "name", "neutral")),
        intensity=float(getattr(expresion, "intensity", 0.5)),
        behavior=comportamiento,
        voice_style=voz,
        initiative=iniciativa,
        avatar_state=avatar_pose(getattr(expresion, "name", "neutral"), comportamiento),
        source="safety" if es_riesgo else source,
        priority=int(priority),
        ttl=max(1.0, duracion / 1000.0),
        reason=str(getattr(expresion, "reason", "") or ""),
    )

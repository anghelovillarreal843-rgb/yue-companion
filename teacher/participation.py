"""Participación Activa durante la clase (§8).

Cuando un profesor humano imparte la clase, YUE no solo escucha: puede intervenir
cuando es APROPIADO (complementar, dar un ejemplo, resolver una duda, relacionar
con algo visto antes), pero NUNCA interrumpe constantemente. Este módulo decide el
«¿debo hablar ahora?» con reglas prudentes; el «qué digo» lo genera el modelo con
el prompt que arma `build_intervention_messages`.

Señales para intervenir:
    - El profesor hace una pregunta abierta al aire ("¿alguien sabe…?").
    - Se detecta una posible confusión o algo que conviene ampliar.
    - Hay una pausa larga (silencio) tras una explicación.
    - El profesor cede la palabra a YUE explícitamente.

Frenos (para no ser inoportuna):
    - Enfriamiento mínimo entre intervenciones.
    - No hablar si el profesor está a media frase (mensaje muy corto/entrecortado).
    - Límite de intervenciones seguidas.

Puro: sin Qt, sin IA. Solo decide y arma el prompt.
"""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


# El profesor cede la palabra a YUE.
_CESION = (
    "yue que opinas", "yue complementa", "yue algo que agregar", "yue tu que dices",
    "verdad yue", "cierto yue", "yue ayudame", "yue explica", "yue un ejemplo",
    "adelante yue", "yue puedes",
)
# Pregunta abierta lanzada al aire.
_PREGUNTA_ABIERTA = (
    "alguien sabe", "quien me dice", "alguna idea", "que creen", "que opinan",
    "alguien puede", "quien sabe", "a ver quien",
)
# Señales de posible confusión / algo que conviene ampliar.
_CONFUSION = (
    "no se si me explico", "esto es un poco confuso", "es complicado",
    "no entiendo", "no me queda claro", "es dificil de", "se complica",
)


@dataclass(frozen=True)
class Decision:
    should: bool
    reason: str = ""       # cesion | pregunta | confusion | silencio | ""
    kind: str = ""         # complementar | ejemplo | duda | relacionar


class ParticipationPolicy:
    def __init__(self, cooldown_s: float = 45.0, max_consecutivas: int = 2,
                 silencio_s: float = 12.0):
        self._cooldown = cooldown_s
        self._max = max_consecutivas
        self._silencio = silencio_s
        self._last_spoke = 0.0
        self._consecutivas = 0
        self._last_prof_ts = 0.0
        self._active = False

    # ---- ciclo ----
    def set_active(self, on: bool) -> None:
        self._active = bool(on)
        if on:
            self._consecutivas = 0

    def is_active(self) -> bool:
        return self._active

    def note_teacher_turn(self) -> None:
        """Marca que el profesor acaba de hablar (reinicia el conteo de seguidas)."""
        self._last_prof_ts = time.time()
        self._consecutivas = 0

    def note_intervened(self) -> None:
        self._last_spoke = time.time()
        self._consecutivas += 1

    # ---- decisión ----
    def decide(self, teacher_text: str) -> Decision:
        """¿Debe YUE intervenir tras esta intervención del profesor?"""
        if not self._active:
            return Decision(False)
        self._last_prof_ts = time.time()
        n = _norm(teacher_text)
        palabras = len(n.split())

        # Cesión explícita: prioridad máxima, salta el enfriamiento.
        if any(c in n for c in _CESION):
            return Decision(True, "cesion", "complementar")

        # Frenos: enfriamiento y límite de intervenciones seguidas.
        ahora = time.time()
        if ahora - self._last_spoke < self._cooldown:
            return Decision(False)
        if self._consecutivas >= self._max:
            return Decision(False)
        # Frase demasiado corta/entrecortada: probablemente sigue hablando.
        if palabras < 4:
            return Decision(False)

        if any(q in n for q in _PREGUNTA_ABIERTA) or n.strip().endswith("?"):
            return Decision(True, "pregunta", "duda")
        if any(c in n for c in _CONFUSION):
            return Decision(True, "confusion", "ejemplo")
        return Decision(False)

    def silence_check(self) -> Decision:
        """Llamar periódicamente: si hay silencio largo tras el profesor, YUE puede
        retomar con un aporte breve (una sola vez por silencio)."""
        if not self._active:
            return Decision(False)
        ahora = time.time()
        if self._last_prof_ts <= 0:
            return Decision(False)
        if ahora - self._last_spoke < self._cooldown:
            return Decision(False)
        if self._consecutivas >= self._max:
            return Decision(False)
        if ahora - self._last_prof_ts >= self._silencio:
            return Decision(True, "silencio", "relacionar")
        return Decision(False)


# --------------------------------------------------------------------------
# Prompt de intervención
# --------------------------------------------------------------------------
_IDENTIDAD = (
    "Sigues siendo YUE (español, primera persona, tu carácter). Estás en una clase "
    "que imparte un PROFESOR HUMANO y participas como asistente inteligente. No "
    "reveles que eres un modelo; no escribas etiquetas de emoción."
)

_POR_MOTIVO = {
    "cesion": "El profesor te cedió la palabra: aporta lo que te pide de forma breve y útil.",
    "pregunta": "El profesor lanzó una pregunta al aire: respóndela con precisión y brevedad.",
    "confusion": "Se detectó posible confusión: aclara con un ejemplo sencillo, sin corregir al profesor de forma brusca.",
    "silencio": "Hubo una pausa: retoma con un aporte corto (un ejemplo, un dato o una relación con lo ya visto).",
}


def build_intervention_messages(teacher_text: str, decision: Decision,
                                recent_context: str = "",
                                style_hint: str = "") -> list:
    """Arma el prompt para una intervención breve y oportuna de YUE en la clase."""
    guia = _POR_MOTIVO.get(decision.reason, _POR_MOTIVO["cesion"])
    reglas = (
        "Interviene en 1–3 frases como MÁXIMO. Sé complementaria, respetuosa con el "
        "profesor y natural. No repitas lo que él ya dijo; añade valor (ejemplo, "
        "matiz, relación con un tema previo o respuesta a la duda). Si no tienes algo "
        "que realmente sume, dilo en una sola frase muy corta."
    )
    partes = [_IDENTIDAD, "", guia, reglas]
    if style_hint:
        partes.append("\n" + style_hint)
    if recent_context:
        partes.append("\nContexto reciente de la clase: " + recent_context[:800])
    system = {"role": "system", "content": "\n".join(partes)}
    user = "El profesor acaba de decir:\n«" + (teacher_text or "").strip() + "»\n\nInterviene ahora."
    return [system, {"role": "user", "content": user}]

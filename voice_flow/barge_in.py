"""Decisión de INTERRUPCIÓN por voz (barge-in), aditiva y permisiva.

Problema que resuelve
---------------------
Al escribir por texto, cualquier mensaje corta a YUE al instante (pasa por
`_interrupt_response`). Por voz, en cambio, el mensaje solo corta si el listener
emite la señal `barge_in`, y esa señal estaba muy filtrada mientras YUE hablaba:

- exigía el DOBLE de longitud mínima (fragmentos cortos se descartaban), y
- descartaba como "eco parcial" en cuanto compartía ~34% de tokens con el TTS.

El resultado es que muchas interrupciones reales del usuario se tiraban a la
basura y YUE seguía hablando. Este módulo añade una decisión más generosa:

1. Palabras de interrupción ("para", "espera", "detente", "cállate", "oye",
   "yue", "un momento", "stop"…): son órdenes que el usuario da PARA callar a
   YUE y que YUE prácticamente nunca se dice a sí misma, así que disparan la
   interrupción de inmediato aunque sean cortas.
2. Para el resto, se sigue filtrando el eco, pero con un umbral configurable y
   una longitud mínima menor, para que la voz real del usuario pase.

Todo es a prueba de fallos: si algo va mal, se devuelve "no interrumpir" y el
listener aplica su lógica de siempre.
"""
from __future__ import annotations

import re
import unicodedata

try:
    import config
except Exception:  # pragma: no cover - YUE siempre trae config, pero por si acaso
    config = None


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


# Léxico de interrupción. Son imperativos que el usuario dirige a YUE para que
# se detenga o le preste atención. YUE, hablando desde su carácter, casi nunca
# los pronuncia hacia sí misma, así que son señales de barge-in muy fiables.
_DEFAULT_INTERRUPT_WORDS = (
    "para", "parate", "pare", "espera", "esperate", "espérate", "detente",
    "deten", "detén", "callate", "cállate", "calla", "silencio", "shh",
    "oye", "oyeme", "óyeme", "escucha", "escuchame", "escúchame",
    "un momento", "momento", "perdona", "perdon", "perdón", "disculpa",
    "stop", "alto", "basta", "ya", "no no", "cancela", "cancelalo",
    "yue", "oye yue",
)


def _interrupt_words() -> tuple[str, ...]:
    extra = ()
    if config is not None:
        raw = getattr(config, "MIC_BARGE_IN_WORDS", ())
        if isinstance(raw, str):
            extra = tuple(w.strip() for w in raw.split(",") if w.strip())
        elif raw:
            try:
                extra = tuple(str(w).strip() for w in raw if str(w).strip())
            except Exception:
                extra = ()
    words = tuple(dict.fromkeys(_norm(w) for w in (*_DEFAULT_INTERRUPT_WORDS, *extra) if _norm(w)))
    return words


def is_interrupt_command(text: str) -> bool:
    """¿El texto oído es una orden clara de interrupción?

    Se considera interrupción si empieza por una de las palabras del léxico o si
    una de ellas aparece como palabra suelta y el texto es corto (típico de un
    "para, espera" dicho encima de YUE). Se mira por límites de palabra para no
    saltar con subcadenas ("separa" no es "para").
    """
    heard = _norm(text)
    if not heard:
        return False
    tokens = heard.split()
    if not tokens:
        return False
    words = _interrupt_words()
    # 1) Arranca con una palabra/expresión de interrupción.
    for w in words:
        if heard == w or heard.startswith(w + " "):
            return True
    # 2) Aparece como palabra suelta y la frase es corta (una intervención breve
    #    encima de la voz de YUE, no una frase larga que la contenga por azar).
    if len(tokens) <= 4:
        token_set = set(tokens)
        for w in words:
            if " " in w:
                if w in heard:
                    return True
            elif w in token_set:
                return True
    return False


class BargeInGate:
    """Decide si una frase oída MIENTRAS YUE habla debe interrumpirla.

    Uso desde el listener (aditivo)::

        gate = BargeInGate()
        decision = gate.should_interrupt(heard_text, overlap)
        if decision.interrupt:
            emitir barge_in + heard

    donde `overlap` es el máximo solape de tokens contra el TTS reciente (lo que
    el listener ya calcula con `_tts_token_overlap`). El eco fuerte ya viene
    descartado antes por `_looks_like_echo`, así que aquí solo afinamos.
    """

    def __init__(self):
        cfg = config
        # Longitud mínima (en caracteres normalizados) para una interrupción que
        # NO sea una palabra de mando. Antes el listener exigía el doble del
        # mínimo general; aquí bajamos ese listón para que la voz real pase.
        self.min_chars = int(getattr(cfg, "MIC_BARGE_IN_GATE_MIN_CHARS", 4)) if cfg else 4
        # Umbral de solape para tratar algo como eco parcial DURANTE el habla.
        # Más alto que el 0.34 original = más permisivo con la voz del usuario.
        self.echo_overlap = float(getattr(cfg, "MIC_BARGE_IN_ECHO_OVERLAP", 0.62)) if cfg else 0.62

    class Decision:
        __slots__ = ("interrupt", "reason")

        def __init__(self, interrupt: bool, reason: str = ""):
            self.interrupt = bool(interrupt)
            self.reason = reason

    def should_interrupt(self, heard_text: str, tts_overlap: float) -> "BargeInGate.Decision":
        heard = _norm(heard_text)
        if not heard:
            return BargeInGate.Decision(False, "vacio")
        # 1) Orden de interrupción explícita: corta ya, sin más filtros.
        if is_interrupt_command(heard_text):
            return BargeInGate.Decision(True, "orden_interrupcion")
        # 2) Frase con contenido suficiente y que no es un calco del TTS.
        if len(heard) < self.min_chars:
            return BargeInGate.Decision(False, "muy_corto")
        try:
            overlap = float(tts_overlap)
        except Exception:
            overlap = 0.0
        if overlap >= self.echo_overlap:
            return BargeInGate.Decision(False, "eco_parcial")
        return BargeInGate.Decision(True, "voz_usuario")

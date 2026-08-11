"""Detección de sarcasmo e ironía por CONTRADICCIÓN, no por emojis.

La idea central: el sarcasmo casi nunca vive en una palabra, vive en el CHOQUE
entre dos capas del mensaje.

    «Qué maravilla, se borraron seis horas de trabajo.»
     └─ vocabulario positivo ─┘  └─ evento claramente malo ─┘

Ese choque (sentimiento positivo + evento negativo en la misma frase) es la
señal más fuerte y funciona SIN un solo emoji. A partir de ahí se suman pistas
menores: marcadores de concesión irónica («sí sí», «claro claro»), superlativos
impostados («súper feliz»), puntuación exagerada, emojis de ironía y la
contradicción con el contexto reciente (venías mal y de golpe estás «genial»).

Devuelve una probabilidad 0..1 con sus razones. Nunca decide sola: quien la usa
combina esta probabilidad con el resto de la lectura afectiva.

Solo librería estándar. Determinista y offline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .negation import normalize


@dataclass(frozen=True)
class SarcasmSignal:
    probability: float
    reasons: tuple[str, ...] = ()
    #: Emoción de FONDO que sugiere el sarcasmo cuando es alto. El sarcasmo casi
    #: siempre tapa frustración o decepción, no alegría.
    implied: str = "frustration"


#: Vocabulario POSITIVO fuerte: lo que el sarcasmo usa como disfraz.
_POSITIVO = re.compile(
    r"\b(?:que\s+)?(?:maravill\w*|fantastic\w*|genial|estupend\w*|perfect\w*|"
    r"exelente|excelente|increible|magnific\w*|de\s+lujo|de\s+maravilla|"
    r"encantad\w*|feliz|felices|content\w*|alegr\w*|divin\w*|precios\w*|"
    r"me\s+encanta|adoro|justo\s+lo\s+que\s+(?:queria|necesitaba)|"
    r"no\s+puede\s+ir\s+mejor|todo\s+va\s+genial|un\s+exito)\b"
)

#: Eventos claramente NEGATIVOS: pérdidas, fallos, rupturas, castigos.
#: Son hechos del mundo, no emociones — por eso chocan tan bien con lo anterior.
_EVENTO_MALO = re.compile(
    r"\b(?:perd[ií]\w*|perder|se\s+(?:borr|perdi|cay|rompi|jodi)\w*|borr[oó]\s|"
    r"se\s+me\s+borr\w*|desaparecio|reprob\w*|suspend[ií]\w*|me\s+despidieron|"
    r"me\s+echaron|despido|me\s+despiden|me\s+acaban\s+de\s+despedir|despedir|"
    r"me\s+dejo|me\s+dejaron|termin[oó]\s+conmigo|"
    r"me\s+rechazaron|rechazaron\s+mi|no\s+me\s+aceptaron|fracas\w*|"
    r"se\s+arruin\w*|arruin\w*|se\s+daño|se\s+dano|se\s+rompio|no\s+funciona|"
    r"fall[oó]|error|crash\w*|se\s+cayo\s+el|me\s+robaron|me\s+estafaron|"
    r"choque|accidente|hospital|multa|deuda|sin\s+trabajo|sin\s+dinero|"
    r"se\s+cancel\w*|cancel(?:aron|aran|ara|o|e)\w*|me\s+cancel\w*|"
    r"no\s+vino|me\s+dejo\s+plantad\w*|me\s+qued[eé]\s+fuera|"
    r"horas?\s+de\s+trabajo|todo\s+mi\s+trabajo|todo\s+el\s+trabajo|"
    r"desde\s+cero|de\s+nuevo\s+desde\s+cero)\b"
)

#: Concesión irónica: repetir el asentimiento es una forma clásica de burla.
_CONCESION = re.compile(
    r"\b(?:si\s*,?\s*si|sisi|claro\s*,?\s*claro|ya\s*,?\s*ya|obvio\s*,?\s*obvio|"
    r"pues\s+claro|como\s+no|por\s+supuesto\s*,|faltaba\s+mas|"
    r"que\s+novedad|lo\s+que\s+faltaba|justo\s+lo\s+que\s+necesitaba|"
    r"encantad\w*\s+de\s+la\s+vida)\b"
)

#: Superlativos impostados: intensifican el disfraz positivo.
_SUPERLATIVO = re.compile(
    r"\b(?:super|hiper|mega|re|tan|tremendamente|absolutamente|totalmente|"
    r"completamente|de\s+lo\s+mas)\s+(?:feliz|content\w*|bien|genial|"
    r"emocionad\w*|alegr\w*|maravillos\w*|fantastic\w*)\b"
)

#: Marcas explícitas de ironía.
_MARCA_IRONIA = re.compile(
    r"(?:/s\b|#sarcasmo|\bnotese\s+el\s+sarcasmo\b|\bironia\b|\bironic\w*\b|"
    r"\bes\s+sarcasmo\b|\bsarcasticamente\b)"
)

#: Emojis que suelen acompañar la ironía (pista MENOR, nunca decisiva).
_EMOJI_IRONIA = ("🙄", "😒", "😏", "🤡", "💀", "😑", "🫠", "🙃")

#: Emojis claramente sinceros: si aparecen, restan probabilidad de sarcasmo.
_EMOJI_SINCERO = ("🥰", "😍", "🥹", "🤗", "💕", "❤️", "🎉", "🥳", "😭")

#: Cierres que rebajan lo dicho («da igual», «en fin»): no son sarcasmo por sí
#: mismos, pero acompañados de un disfraz positivo lo refuerzan.
_RESIGNACION = re.compile(
    r"\b(?:da\s+igual|en\s+fin|que\s+se\s+le\s+va\s+a\s+hacer|asi\s+es\s+la\s+vida|"
    r"como\s+siempre|otra\s+vez\s+igual|era\s+de\s+esperar|ya\s+me\s+lo\s+esperaba|"
    r"tipico|lo\s+de\s+siempre)\b"
)


def _frases(text_norm: str) -> list[str]:
    """Parte por puntuación fuerte y por comas: el choque suele ser INTRAfrase."""
    crudas = re.split(r"[.;!?\n]", text_norm)
    return [f.strip() for f in crudas if f.strip()]


def detect(text: str, *, previous_valence: float | None = None) -> SarcasmSignal:
    """Estima la probabilidad de sarcasmo/ironía de un mensaje.

    Parámetros
    ----------
    text
        Mensaje original (con emojis y mayúsculas: aquí sí importan).
    previous_valence
        Valencia del estado afectivo anterior, si se conoce. Un salto brusco de
        muy negativo a «estoy de maravilla» es sospechoso.
    """
    original = text or ""
    tn = normalize(original)
    if not tn:
        return SarcasmSignal(0.0)

    score = 0.0
    razones: list[str] = []

    # ---- 1) LA señal fuerte: positivo + evento malo en la MISMA frase -------
    choque_intrafrase = False
    for frase in _frases(tn):
        if _POSITIVO.search(frase) and _EVENTO_MALO.search(frase):
            choque_intrafrase = True
            break
    if choque_intrafrase:
        score += 0.60
        razones.append("vocabulario positivo describiendo un hecho negativo")
    elif _POSITIVO.search(tn) and _EVENTO_MALO.search(tn):
        # El choque existe pero repartido en frases distintas: pesa menos,
        # porque puede ser un contraste legítimo («fue horrible, pero me alegro»).
        score += 0.32
        razones.append("mezcla de tono positivo y hecho negativo en el mensaje")

    # ---- 2) Concesión irónica ---------------------------------------------
    if _CONCESION.search(tn):
        score += 0.22
        razones.append("marcador de concesión irónica")

    # ---- 3) Superlativo impostado -----------------------------------------
    if _SUPERLATIVO.search(tn):
        score += 0.15
        razones.append("superlativo exagerado")
        if _EVENTO_MALO.search(tn):
            score += 0.15
            razones.append("superlativo exagerado sobre algo que salió mal")

    # ---- 4) Marca explícita: casi certeza ---------------------------------
    if _MARCA_IRONIA.search(tn):
        score += 0.55
        razones.append("marca explícita de ironía")

    # ---- 5) Emojis (pista menor, en ambos sentidos) ------------------------
    if any(e in original for e in _EMOJI_IRONIA):
        score += 0.20
        razones.append("emoji de ironía")
    if any(e in original for e in _EMOJI_SINCERO) and not choque_intrafrase:
        score -= 0.18
        razones.append("emoji sincero (resta sospecha)")

    # ---- 6) Resignación junto a disfraz positivo --------------------------
    if _RESIGNACION.search(tn) and _POSITIVO.search(tn):
        score += 0.15
        razones.append("resignación acompañando tono positivo")

    # ---- 7) Puntuación / mayúsculas exageradas -----------------------------
    if _POSITIVO.search(tn):
        if re.search(r"[!]{2,}|[.]{3,}", original):
            score += 0.08
            razones.append("puntuación exagerada")
        letras = [c for c in original if c.isalpha()]
        if len(letras) >= 8 and sum(1 for c in letras if c.isupper()) / len(letras) > 0.6:
            score += 0.10
            razones.append("mayúsculas sostenidas sobre tono positivo")

    # ---- 8) Contradicción con el contexto reciente -------------------------
    if previous_valence is not None and previous_valence <= -0.4 and _POSITIVO.search(tn):
        score += 0.18
        razones.append("giro brusco desde un estado negativo reciente")

    # Un mensaje muy corto y sin choque casi nunca es sarcasmo detectable.
    if len(tn) < 12 and not _MARCA_IRONIA.search(tn):
        score *= 0.6

    prob = max(0.0, min(1.0, score))

    # La emoción de fondo: si hubo pérdida de algo trabajado, decepción;
    # si el evento es un obstáculo o un fallo, frustración.
    implied = "frustration"
    if re.search(r"\b(?:no\s+vino|me\s+dejo|cancel|rechaz|no\s+me\s+aceptaron|"
                 r"me\s+dejaron|plantad)\w*\b", tn):
        implied = "disappointment"

    return SarcasmSignal(prob, tuple(razones), implied)

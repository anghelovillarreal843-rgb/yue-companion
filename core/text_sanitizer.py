"""Limpieza del texto del modelo ANTES de que YUE lo diga.  [ADITIVO]

Por qué existe
--------------
Los modelos "de razonamiento" (Qwen3, gpt-oss, DeepSeek-R1, Magistral...)
devuelven su monólogo interno DENTRO del contenido y casi siempre EN INGLÉS:

    <think>The user is greeting me in Spanish. I should reply in a tsundere
    tone, keep it short...</think>
    Ah... eres tú otra vez. ¿Qué quieres?

Como nadie recortaba eso, YUE leía en voz alta todo el monólogo: eso es
exactamente el "me habla en inglés cosas raras". Este módulo corta el
razonamiento y deja SOLO la frase final.

También cubre el formato "harmony" de los modelos openai/gpt-oss-* servidos por
Groq, que separan canales:

    <|channel|>analysis<|message|>...<|end|><|channel|>final<|message|>Hola

No borra nada del resto del proyecto: es una capa de limpieza que se llama
justo después de recibir la respuesta.
"""
from __future__ import annotations

import re

# Etiquetas de "pensamiento" que usan los distintos modelos.
_ETIQUETAS = (
    "think", "thinking", "thought", "thoughts", "reason", "reasoning",
    "analysis", "reflection", "scratchpad", "internal", "plan",
)

# 1) Bloques bien cerrados: <think>...</think>
_BLOQUES = [
    re.compile(rf"<\s*{t}\s*>.*?<\s*/\s*{t}\s*>", re.I | re.S) for t in _ETIQUETAS
]
# 2) Bloque ABIERTO y sin cerrar (respuesta cortada a mitad del razonamiento).
_ABIERTO = re.compile(rf"<\s*(?:{'|'.join(_ETIQUETAS)})\s*>.*\Z", re.I | re.S)
# 3) Etiqueta de cierre huérfana: todo lo anterior era razonamiento.
_CIERRE_HUERFANO = re.compile(rf"\A.*?<\s*/\s*(?:{'|'.join(_ETIQUETAS)})\s*>", re.I | re.S)
# 4) Canales estilo "harmony" de gpt-oss.
_CANAL_FINAL = re.compile(r"<\|channel\|>\s*final\s*<\|message\|>(.*?)(?:<\|end\|>|\Z)", re.I | re.S)
_CANAL_OTRO = re.compile(r"<\|channel\|>\s*\w+\s*<\|message\|>.*?(?:<\|end\|>|\Z)", re.I | re.S)
_MARCAS_SUELTAS = re.compile(r"<\|[^|>]*\|>", re.I)
# 5) Encabezados en texto plano que algunos modelos escriben igual.
_ENCABEZADOS = re.compile(
    r"^\s*(?:thought|thinking|reasoning|analysis|internal monologue|"
    r"razonamiento|pensamiento)\s*:\s*.*?(?:\n\s*\n|\Z)",
    re.I | re.S,
)


def quitar_razonamiento(texto: str) -> str:
    """Devuelve solo la respuesta final, sin el monólogo interno del modelo.

    Si TODO el texto era razonamiento (el modelo se quedó sin espacio antes de
    contestar), devuelve cadena vacía para que quien llama pueda reintentar o
    usar su respaldo, en vez de que YUE lea el monólogo en inglés.
    """
    valor = (texto or "").strip()
    if not valor:
        return ""

    # --- Canales de gpt-oss: si hay canal "final", ese es el mensaje real ---
    if "<|channel|>" in valor.lower():
        finales = _CANAL_FINAL.findall(valor)
        if finales:
            valor = finales[-1]
        else:
            valor = _CANAL_OTRO.sub(" ", valor)
    valor = _MARCAS_SUELTAS.sub(" ", valor)

    # --- Bloques <think>...</think> bien cerrados ---
    for patron in _BLOQUES:
        valor = patron.sub(" ", valor)

    # --- Cierre huérfano: "...razonamiento</think> respuesta" ---
    if re.search(rf"<\s*/\s*(?:{'|'.join(_ETIQUETAS)})\s*>", valor, re.I):
        valor = _CIERRE_HUERFANO.sub("", valor)

    # --- Apertura sin cierre: el resto es razonamiento truncado ---
    valor = _ABIERTO.sub("", valor)

    # --- Encabezados en texto plano ("Thinking: ...") ---
    valor = _ENCABEZADOS.sub("", valor)

    # Restos de formato y espacios.
    valor = re.sub(r"\n{3,}", "\n\n", valor)
    return valor.strip()


# --- Detección (conservadora) de respuesta en inglés --------------------------
_ES_PISTAS = re.compile(
    r"[áéíóúñü¿¡]|\b(?:que|qué|de|la|el|los|las|un|una|con|para|por|pero|"
    r"como|cómo|estás|estoy|eres|soy|tú|te|me|no|sí|ya|más|bien|hola|gracias|"
    r"quieres|hacer|ahora|eso|esto|nada|algo|siempre|claro)\b",
    re.I,
)
_EN_PISTAS = re.compile(
    r"\b(?:the|you|your|i'm|i am|it's|that|this|with|for|and|but|what|"
    r"should|would|could|okay|let's|here|there|about|because|need|want|"
    r"user|assistant|response|sorry|sure|thanks)\b",
    re.I,
)


def parece_ingles(texto: str) -> bool:
    """¿La respuesta salió en inglés? Heurística prudente (evita falsos positivos).

    Solo dice True cuando hay MUCHAS marcas de inglés y prácticamente ninguna
    de español. Palabras sueltas en inglés dentro de una frase española
    ("hazme un backup") NO cuentan.
    """
    valor = (texto or "").strip()
    if len(valor) < 12:
        return False
    es = len(_ES_PISTAS.findall(valor))
    en = len(_EN_PISTAS.findall(valor))
    if es >= 2:
        return False
    return en >= 3


def limpiar(texto: str) -> str:
    """Atajo: quita el razonamiento y normaliza espacios."""
    valor = quitar_razonamiento(texto)
    return re.sub(r"[ \t]{2,}", " ", valor).strip()

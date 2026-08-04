"""Lectura de EMOCIONES de la persona por la cámara (petición: saber qué siente).

`camera_observer.py` ya obtiene de MediaPipe los *blendshapes* faciales (sonrisa,
ceño, mandíbula abierta, cejas, ojos muy abiertos…). Hasta ahora solo se convertían
en señales descriptivas ("sonrisa visible") sin nombrar la emoción. Este módulo da
el siguiente paso: a partir de esos mismos valores estima cómo se siente la persona
(contenta, triste, sorprendida, molesta, pensativa, tranquila…) con un nivel de
confianza, y ofrece una lectura en español.

Es puro y determinista (solo un diccionario de puntuaciones), no importa MediaPipe
ni OpenCV, así que se prueba sin cámara. Es ADITIVO: si algo falla, la cámara sigue
funcionando igual que antes con sus señales descriptivas.

Nota honesta: son estimaciones a partir de la expresión, no una certeza. YUE lo dice
como una impresión ("diría que se le ve…"), nunca como un diagnóstico.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PersonEmotion:
    key: str            # "feliz" | "triste" | "sorprendida" | "molesta" | ...
    confidence: float   # 0..1
    label_es: str       # etiqueta corta para mostrar ("contenta", "sorprendida"…)


def _avg(scores: dict, *names: str) -> float:
    vals = [float(scores.get(n, 0.0) or 0.0) for n in names]
    return sum(vals) / max(1, len(vals))


# Etiqueta en español (femenino, encaja con "la persona") para cada clave.
_ETIQUETA = {
    "feliz": "contenta",
    "triste": "algo triste",
    "sorprendida": "sorprendida",
    "molesta": "molesta o tensa",
    "pensativa": "pensativa",
    "tranquila": "tranquila",
    "neutral": "con gesto neutro",
}


def infer_person_emotion(scores: dict) -> PersonEmotion | None:
    """Estima la emoción de UNA cara a partir de sus blendshapes (ARKit-like).

    Devuelve None si no hay señales suficientes para arriesgar una lectura.
    Los nombres de blendshape son los de MediaPipe FaceLandmarker:
      mouthSmileLeft/Right, mouthFrownLeft/Right, browInnerUp,
      browDownLeft/Right, browOuterUpLeft/Right, eyeWideLeft/Right,
      eyeSquintLeft/Right, jawOpen, noseSneerLeft/Right, mouthPressLeft/Right,
      cheekSquintLeft/Right, eyeBlinkLeft/Right, mouthPucker.
    """
    if not scores:
        return None

    smile = _avg(scores, "mouthSmileLeft", "mouthSmileRight")
    frown = _avg(scores, "mouthFrownLeft", "mouthFrownRight")
    brow_inner = float(scores.get("browInnerUp", 0.0) or 0.0)
    brow_down = _avg(scores, "browDownLeft", "browDownRight")
    brow_outer = _avg(scores, "browOuterUpLeft", "browOuterUpRight")
    eye_wide = _avg(scores, "eyeWideLeft", "eyeWideRight")
    eye_squint = _avg(scores, "eyeSquintLeft", "eyeSquintRight")
    jaw = float(scores.get("jawOpen", 0.0) or 0.0)
    sneer = _avg(scores, "noseSneerLeft", "noseSneerRight")
    press = _avg(scores, "mouthPressLeft", "mouthPressRight")
    cheek_squint = _avg(scores, "cheekSquintLeft", "cheekSquintRight")

    # Puntuación por emoción (combinaciones típicas de la cara).
    puntos: dict[str, float] = {}

    # Feliz: sonrisa (y a menudo mejillas apretadas al sonreír de verdad).
    puntos["feliz"] = smile * 1.15 + cheek_squint * 0.35

    # Sorprendida: mandíbula abierta + ojos muy abiertos + cejas altas.
    puntos["sorprendida"] = jaw * 0.9 + eye_wide * 0.8 + brow_outer * 0.5 + brow_inner * 0.3

    # Triste: comisuras hacia abajo + parte interna de las cejas hacia arriba.
    puntos["triste"] = frown * 1.0 + brow_inner * 0.6

    # Molesta/tensa: cejas hacia abajo, entrecerrar ojos, arrugar la nariz, apretar labios.
    puntos["molesta"] = brow_down * 0.9 + eye_squint * 0.5 + sneer * 0.6 + press * 0.4

    # Pensativa: cejas juntas leves sin boca marcada (mirada concentrada).
    puntos["pensativa"] = brow_down * 0.5 + eye_squint * 0.35 - smile * 0.4 - jaw * 0.3

    # Nos quedamos con la mejor por encima de un umbral prudente.
    mejor = max(puntos, key=lambda k: puntos[k])
    score = puntos[mejor]

    # Umbrales: pedimos evidencia clara para no inventar emociones.
    umbral = {
        "feliz": 0.34,
        "sorprendida": 0.5,
        "triste": 0.42,
        "molesta": 0.42,
        "pensativa": 0.28,
    }.get(mejor, 0.4)

    if score < umbral:
        # Sin señal fuerte: cara neutra (baja confianza, pero informativa).
        return PersonEmotion("neutral", 0.3, _ETIQUETA["neutral"])

    confidence = round(min(1.0, 0.45 + score * 0.55), 3)
    return PersonEmotion(mejor, confidence, _ETIQUETA.get(mejor, mejor))


def reading_es(emotions) -> str:
    """Frase de impresión a partir de una o varias emociones detectadas.

    `emotions` es una secuencia de PersonEmotion (una por rostro). Devuelve algo
    como "diría que se le ve contenta" o "" si no hay nada que decir.
    """
    ems = [e for e in (emotions or ()) if isinstance(e, PersonEmotion)]
    if not ems:
        return ""

    # Ignoramos las lecturas neutras salvo que sea lo único que hay.
    fuertes = [e for e in ems if e.key != "neutral"]
    usar = fuertes or ems

    if len(usar) == 1:
        e = usar[0]
        if e.key == "neutral":
            return "diría que tiene un gesto neutro, ni muy animada ni apagada"
        return f"diría que se le ve {e.label_es}"

    etiquetas = []
    for e in usar[:3]:
        if e.label_es not in etiquetas:
            etiquetas.append(e.label_es)
    if len(etiquetas) == 1:
        return f"diría que se les ve {etiquetas[0]}"
    return "diría que veo a personas " + ", ".join(etiquetas[:-1]) + f" y {etiquetas[-1]}"


# ---------------------------------------------------------------------------
# Espejo empático: qué CARA pone YUE al ver cómo se siente la persona.
# No imita sin más (no se enfada si tú te enfadas): acompaña con cariño.
# Devuelve (emocion_avatar, intensidad, duracion_ms) o None si no acompaña.
# ---------------------------------------------------------------------------
_ESPEJO = {
    "feliz":       ("happy", 0.8, 4200),
    "triste":      ("worried", 0.72, 5200),   # se preocupa, la acompaña
    "sorprendida": ("surprised", 0.7, 3600),
    "molesta":     ("worried", 0.6, 4200),    # atenta, no responde con enfado
    "pensativa":   ("curious", 0.55, 4000),
    "tranquila":   ("relaxed", 0.5, 4000),
}


def empathic_avatar_emotion(key: str):
    """Emoción con la que YUE acompaña a la persona. None para 'neutral'."""
    return _ESPEJO.get(key)


# ---------------------------------------------------------------------------
# NUEVO (YUE habla al ver tu emoción): material para que YUE PREGUNTE por qué
# estás así al detectar una emoción fuerte o peligro por la cámara.
#
# - `talk_prompt_hint(key)`  -> instrucción breve para el LLM, así responde en su
#   propio tono (tsundere, español mexicano) y suena natural, no un guion fijo.
# - `talk_fallback_line(key)` -> frase lista para decir si el LLM no está
#   disponible (offline), para que NUNCA se quede muda cuando decide preguntar.
# ---------------------------------------------------------------------------
_PROMPT_HINT = {
    "feliz": "se le ve muy contenta/animada",
    "triste": "se le ve triste o decaída",
    "sorprendida": "se le ve muy sorprendida o desconcertada",
    "molesta": "se le ve molesta, tensa o de mal humor",
    "pensativa": "se le ve pensativa o preocupada, algo ida",
    "neutral": "tiene un gesto raro, difícil de leer",
}

# Frases de respaldo (tono tsundere, mexicano). Que pregunte el porqué sin
# ponerse melosa; le importa aunque lo disimule.
_FALLBACK = {
    "feliz": [
        "Oye… te veo demasiado contento. ¿Qué te traes? Cuéntame, ¿no?",
        "Traes una sonrisota de nada. ¿Pasó algo bueno o qué?",
    ],
    "triste": [
        "Hey… te veo algo triste. No es que me preocupe mucho, pero… ¿qué tienes?",
        "Oye, se te ve decaído. ¿Qué pasó? Puedes contarme, ¿sabes?",
    ],
    "sorprendida": [
        "¿Y esa cara? Te veo súper sorprendido. ¿Qué pasó?",
        "Te quedaste con cara de '¿qué?'. Ándale, ¿qué viste?",
    ],
    "molesta": [
        "Oye… te noto de mal humor. ¿Quién te hizo enojar? No fui yo, ¿verdad?",
        "Traes cara de pocos amigos. ¿Qué te molestó?",
    ],
    "pensativa": [
        "Te veo muy metido en tus pensamientos. ¿En qué piensas tanto?",
        "Oye, andas ido y con el ceño fruncido. ¿Qué te da vueltas en la cabeza?",
    ],
    "neutral": [
        "Te noto raro… ¿estás bien? Dime, no me hagas insistir.",
    ],
}

# Cuando hay DISTRÉS sostenido (lo tratamos como "peligro"/algo va mal), YUE baja
# el tono tsundere y pregunta con más cuidado, sin dramatizar.
_FALLBACK_RIESGO = [
    "Oye… llevo un rato viéndote así y no me quedo tranquila. ¿Qué está pasando? "
    "Cuéntame, en serio.",
    "Hey, te veo apagado desde hace rato. ¿Estás bien? Aquí estoy si quieres soltarlo.",
]


def talk_prompt_hint(key: str) -> str:
    """Descripción breve de la emoción para inyectar al LLM."""
    return _PROMPT_HINT.get(key, _PROMPT_HINT["neutral"])


def talk_fallback_line(key: str, riesgo: bool = False) -> str:
    """Frase lista para hablar si el LLM no está disponible.

    `riesgo=True` (distrés sostenido) usa un tono más cuidadoso.
    """
    import random
    if riesgo:
        return random.choice(_FALLBACK_RIESGO)
    opciones = _FALLBACK.get(key) or _FALLBACK["neutral"]
    return random.choice(opciones)

"""core/voice_affect — hueco preparado para la emoción por prosodia. NO inventa.

Estado actual del proyecto, comprobado archivo por archivo:

    core/voice.py       → síntesis (TTS). No analiza nada del usuario.
    core/listener.py    → reconocimiento (STT) y energía del micro.
    voice_flow/         → interrupción por voz (barge-in), no emoción.

Es decir: hay AUDIO, pero NO hay ningún clasificador emocional de voz. Ni tono,
ni ritmo, ni temblor, ni pausas. Nada.

Por eso este módulo devuelve `confidence = 0.0` y no aporta ninguna observación.
La tentación era rellenar `voice_confidence` con algo derivado de la energía del
micrófono, y es exactamente lo que NO se debe hacer: hablar fuerte no es estar
enfadado, y un dato inventado con etiqueta de "voz" contaminaría la fusión y
haría que YUE respondiera a un ruido como si fuera un sentimiento.

Cuando exista un clasificador de verdad, el enganche ya está hecho: basta con
implementar `analyze()` y devolver una `UserObservation` con confianza real.
Todo lo demás (ponderación 0.80, caducidad, fusión con el texto) ya funciona.

    from core.state import UserObservation
    return UserObservation(source="voice", emotion="anxiety",
                           confidence=0.71, arousal=0.8, valence=-0.4)
"""
from __future__ import annotations

#: Se pondrá a True el día que haya un clasificador real detrás.
AVAILABLE = False

#: Peso que tendrá la voz en la fusión cuando exista (ver SOURCE_RELIABILITY).
#: Alto, pero por debajo del texto: el tono sugiere, las palabras afirman.
PLANNED_RELIABILITY = 0.80


def analyze(audio=None, *, sample_rate: int = 16000, text: str = ""):
    """Analiza la prosodia. Hoy devuelve None SIEMPRE, a propósito.

    Devolver None (y no una observación neutra con confianza baja) es
    deliberado: una observación "neutral" de la voz sí entraría en la fusión y
    podría restar peso a lo que el usuario dijo con palabras. Ausencia de dato
    no es dato.
    """
    return None


def confidence() -> float:
    """Confianza actual de la vía de voz: cero mientras no haya clasificador."""
    return 0.0


def describe() -> str:
    """Explicación corta para el panel de depuración."""
    if AVAILABLE:
        return "prosodia activa"
    return ("prosodia no implementada: hay STT/TTS pero ningún clasificador "
            "emocional de voz. voice_confidence se mantiene en 0.0 a propósito.")

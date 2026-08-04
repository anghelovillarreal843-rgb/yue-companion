"""Avatar reactivo (punto 6 del pedido): traduce lo percibido a un gesto del avatar.

El avatar VRM ya entiende `pet.set_emotion(nombre, intensidad, duracion_ms)` con
un vocabulario propio (neutral, happy, sad, surprised, worried, focused, shy…).
Este controlador NO dibuja nada: solo decide qué (nombre, intensidad, duración)
mandarle a partir de la emoción que la cámara estimó en la persona.

Filosofía = ESPEJO EMPÁTICO, no imitación: si te ve triste o enojado, YUE se
PREOCUPA (no se enoja contigo). Si te ve contenta, se contagia. Es lo mismo que
ya hace `core/face_emotion.empathic_avatar_emotion`, aquí adaptado a las claves
de DeepFace y con los estados con nombre que pide el documento.

Es puro y determinista: se prueba sin avatar ni cámara.
"""
from __future__ import annotations

# Estados con nombre del documento -> gesto del avatar (nombre, intensidad, ms).
NAMED_STATES = {
    "NORMAL": ("neutral", 0.5, 3500),
    "FELIZ": ("happy", 0.85, 5200),        # más brillo, movimiento suave
    "TRISTE": ("worried", 0.8, 6500),      # movimiento lento, suave (empático)
    "SORPRENDIDO": ("surprised", 0.85, 4200),  # reacción rápida
}

# Emoción de DeepFace en la persona -> gesto EMPÁTICO del avatar.
# Nota: ante tristeza/miedo/enojo, YUE muestra preocupación, no imita.
_EMPATHIC = {
    "happy": ("happy", 0.85, 5200),
    "sad": ("worried", 0.82, 6500),
    "fear": ("worried", 0.85, 6000),
    "angry": ("worried", 0.7, 5200),
    "disgust": ("confused", 0.6, 4200),
    "surprise": ("surprised", 0.85, 4200),
    "neutral": ("neutral", 0.5, 3200),
}


def state(name: str) -> tuple[str, float, int]:
    """Devuelve el gesto de un estado con nombre (NORMAL/FELIZ/TRISTE/SORPRENDIDO)."""
    return NAMED_STATES.get((name or "").strip().upper(), NAMED_STATES["NORMAL"])


def empathic_avatar_emotion(emotion_key: str) -> tuple[str, float, int] | None:
    """Gesto con el que YUE acompaña la emoción `emotion_key` de DeepFace.

    Devuelve None para 'neutral' (no hace falta cambiar la cara por algo neutro),
    de modo que el llamador solo aplique un gesto cuando aporte algo.
    """
    key = (emotion_key or "").strip().lower()
    if key in ("", "neutral"):
        return None
    return _EMPATHIC.get(key)


def attention_avatar_emotion(attention_state: str) -> tuple[str, float, int] | None:
    """Gesto opcional según la atención (ausente -> gesto de espera tranquilo)."""
    st = (attention_state or "").strip().lower()
    if st == "ausente":
        return ("relaxed", 0.4, 4000)
    return None

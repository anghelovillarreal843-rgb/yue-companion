"""Gestor de emociones (puntos 4 y 5 del pedido): emoción -> comportamiento.

Traduce lo que la cámara ve en la persona a una posible frase de YUE (o KAI),
SIN cambiar nunca su personalidad: la emoción detectada solo influye en el TONO
y en si conviene o no decir algo. La personalidad (tsundere en YUE, seca y
calmada en KAI) manda siempre.

REGLAS DE ORO (para que no hable de más):
  - Límite por hora: MAX_EMOTION_RESPONSES_PER_HOUR.
  - Enfriamiento por emoción: no repite la misma emoción en poco tiempo.
  - Confianza mínima: ignora lecturas dudosas.
  - "neutral" nunca dispara frase.

Es PURO y determinista (se le pasa el reloj), así que se prueba sin cámara.
Devuelve una `Reaction` (texto + personaje) o None si toca callar.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

try:
    import config  # type: ignore
except Exception:  # pragma: no cover
    config = None  # type: ignore


def _cfg(name: str, default):
    return getattr(config, name, default) if config is not None else default


@dataclass(frozen=True)
class Reaction:
    text: str
    persona: str            # "YUE" | "KAI"
    emotion: str            # emoción que la provocó
    avatar_state: str       # estado con nombre para el avatar (FELIZ/TRISTE/…)


# Frases por emoción y personaje. YUE = tsundere (reticente por fuera, tierna por
# dentro); KAI = tranquilo, calculador, humor seco. Sin markdown, 1 frase.
_LINES = {
    "YUE": {
        "sad": "No pongas esa cara... ¿todo bien? No es que me preocupe, pero cuéntame.",
        "happy": "Vaya, se te ve de buen humor hoy. Me alegro... un poquito.",
        "angry": "Oye, respira. ¿Quién te hizo enojar? Más te vale que no sea yo.",
        "fear": "Te noto inquieto. Estoy aquí, ¿sí? No hace falta que lo digas.",
        "surprise": "¿Y esa cara? Algo te sorprendió, cuéntame antes de que reviente de curiosidad.",
        "disgust": "Uf, ¿qué viste? Traes una cara que ni yo cuando pierdo.",
        "cansado": "Llevas un buen rato en esto. Descansa tantito, no te vayas a fundir.",
    },
    "KAI": {
        "sad": "Te veo decaído. Si quieres hablar, te escucho; si no, aquí estaré igual.",
        "happy": "Buen ánimo hoy. Aprovéchalo.",
        "angry": "Estás tenso. Respira; ningún problema se arregla apretando la mandíbula.",
        "fear": "Algo te preocupa. Vamos por partes, sin prisa.",
        "surprise": "Algo te tomó por sorpresa. Cuéntame qué fue.",
        "disgust": "Esa reacción dice bastante. ¿Qué acabas de ver?",
        "cansado": "Llevas mucho tiempo trabajando. Podrías descansar un momento.",
    },
}

# Emoción DeepFace -> estado con nombre del avatar (para el controlador de avatar).
_AVATAR_STATE = {
    "happy": "FELIZ",
    "sad": "TRISTE",
    "fear": "TRISTE",
    "angry": "TRISTE",     # empático: preocupación, no enojo
    "disgust": "NORMAL",
    "surprise": "SORPRENDIDO",
    "neutral": "NORMAL",
    "cansado": "TRISTE",
}


class EmotionManager:
    def __init__(self, persona: str | None = None) -> None:
        self.persona = (persona or str(_cfg("VISION_EMOTION_PERSONA", "YUE"))).strip().upper()
        if self.persona not in _LINES:
            self.persona = "YUE"
        self.max_per_hour = int(_cfg("MAX_EMOTION_RESPONSES_PER_HOUR", 4))
        self.min_confidence = int(_cfg("VISION_EMOTION_MIN_CONFIDENCE", 60))
        self.cooldown = float(_cfg("VISION_EMOTION_COOLDOWN", 120.0))

        self._spoken: deque[float] = deque()   # timestamps de reacciones habladas
        self._last_emotion = ""
        self._last_emotion_at = 0.0

    # -------------------------------------------------------------------
    def maybe_react(self, emotion: str, confidence: float, now: float) -> Reaction | None:
        """Decide si YUE/KAI comenta la emoción observada. None = callar."""
        emo = (emotion or "").strip().lower()
        if emo == "neutral" or emo not in _LINES[self.persona]:
            return None
        if float(confidence) < self.min_confidence:
            return None

        # Enfriamiento por emoción: no repetir la misma en poco tiempo.
        if emo == self._last_emotion and (now - self._last_emotion_at) < self.cooldown:
            return None

        # Límite por hora (ventana móvil de 3600 s).
        self._prune(now)
        if len(self._spoken) >= self.max_per_hour:
            return None

        text = _LINES[self.persona][emo]
        self._spoken.append(now)
        self._last_emotion = emo
        self._last_emotion_at = now
        return Reaction(
            text=text,
            persona=self.persona,
            emotion=emo,
            avatar_state=_AVATAR_STATE.get(emo, "NORMAL"),
        )

    def absence_line(self) -> str:
        """Frase para una ausencia prolongada (punto 7 del documento)."""
        if self.persona == "KAI":
            return "No detecto tu presencia, seguiré esperando."
        return "Te fuiste un rato largo... aquí sigo, no es que te extrañara."

    def line_for(self, emotion: str) -> str:
        """Frase cruda para una emoción (sin límites); útil para pruebas/depuración."""
        emo = (emotion or "").strip().lower()
        return _LINES.get(self.persona, {}).get(emo, "")

    def remaining_this_hour(self, now: float) -> int:
        self._prune(now)
        return max(0, self.max_per_hour - len(self._spoken))

    # -------------------------------------------------------------------
    def _prune(self, now: float) -> None:
        corte = now - 3600.0
        while self._spoken and self._spoken[0] < corte:
            self._spoken.popleft()

"""Contrato VisionHost: la app lo implementa, vision/ lo consume.

Es exactamente lo que vision/integration.py usaba con duck-typing sobre la
app (REFACTOR_SPEC §2.2): request_emotion, emotion_would_win, media_playing
e is_speaking, más la constante de prioridad que reemplaza el import de
core.state.Priority dentro de vision/.

EMOTION_PRIORITY == Priority.EMOTION (60) congelado aquí para que vision
no necesite importar core.state (ajuste de decisión: se refleja el valor
numérico actual y se documenta la equivalencia).
"""

from __future__ import annotations

import abc


class VisionHost(abc.ABC):
    """Contrato que la aplicación (Controller) provee a la visión."""

    EMOTION_PRIORITY: int = 60  # equivalente a core.state.Priority.EMOTION

    @abc.abstractmethod
    def request_emotion(self, name: str, intensity: float, duration_ms: int,
                        priority: int, source: str) -> None:
        """Propone una emoción al arbitraje de estado (no la impone)."""

    @abc.abstractmethod
    def emotion_would_win(self, priority: int) -> bool:
        """¿La prioridad dada ganaría el arbitraje ahora mismo?"""

    @property
    @abc.abstractmethod
    def media_playing(self) -> bool:
        """True si hay TTS/audio/música ocupando el canal del avatar."""

    @property
    @abc.abstractmethod
    def is_speaking(self) -> bool:
        """True si el avatar/locutor está hablando."""
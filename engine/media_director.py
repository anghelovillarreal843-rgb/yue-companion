"""MediaCompanionDirector (PR 5, paso 4).

Extrae de Controller: _on_media_heard y _recent_lyrics (fila 4 de §4.1).
El buffer rolling (timestamp, texto) vive aquí; Controller solo conecta la
señal listener.media_heard y consulta lyrics para _describe_audio.
"""
from __future__ import annotations

import time

_CORTE_S = 180.0      # ventana máxima del rolling
_MAX_FRAGMENTOS = 25


class MediaCompanionDirector:
    def __init__(self) -> None:
        self._buffer: list[tuple[float, str]] = []

    def remember_heard(self, text: str) -> None:
        """Guarda la letra/diálogo que YUE oye mientras suena media (rolling)."""
        text = (text or "").strip()
        if not text:
            return
        ahora = time.time()
        self._buffer.append((ahora, text))
        corte = ahora - _CORTE_S
        self._buffer = [(t, s) for (t, s) in self._buffer if t >= corte][-_MAX_FRAGMENTOS:]

    def recent_lyrics(self, max_age: float = 150.0, max_chars: int = 600) -> str:
        """Texto reciente oído del audio (letra/diálogo), para dar contexto a YUE."""
        ahora = time.time()
        trozos = [s for (t, s) in self._buffer if ahora - t <= max_age]
        if not trozos:
            return ""
        salida = []
        total = 0
        for s in reversed(trozos):
            if total + len(s) > max_chars:
                break
            salida.append(s)
            total += len(s)
        return " … ".join(reversed(salida)).strip()
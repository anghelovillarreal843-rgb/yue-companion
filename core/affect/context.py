"""AffectiveContext — el estado afectivo NO se reemplaza en cada mensaje.

Sin esto, YUE trataría igual un bajón de un solo mensaje que un malestar que
lleva media hora arrastrándose. Con esto puede notar la diferencia:

    «llevas un rato bajoneado»   ≠   «se te escapó una frase triste»

Guarda una ventana corta (por defecto 8 lecturas) y de ahí saca:

    current      la lectura de ahora
    previous     la anterior
    trend        "mejorando" | "empeorando" | "estable"
    duration     segundos que lleva el malestar sin cortarse
    stability    0..1, cuánto se parecen las últimas lecturas entre sí
    last_trigger la última causa que se pudo identificar
    last_support_mode  cómo acompañó YUE la última vez (para no repetirse)

IMPORTANTE: esto NO es un diagnóstico ni un historial clínico. Es memoria
conversacional de trabajo — se pierde al cerrar y no sale de la sesión salvo
que la memoria estructurada decida guardar el resumen.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from .models import AffectiveState, Emotion


@dataclass
class _Marca:
    state: AffectiveState
    ts: float


@dataclass
class AffectiveContext:
    """Ventana corta de estados afectivos con su lectura de tendencia."""

    max_len: int = 8
    _marcas: deque = field(default_factory=lambda: deque(maxlen=8), repr=False)
    last_support_mode: str = ""

    def __post_init__(self):
        if self.max_len != self._marcas.maxlen:
            self._marcas = deque(self._marcas, maxlen=max(2, int(self.max_len)))

    # ---- escritura --------------------------------------------------------
    def push(self, state: AffectiveState, *, ts: float | None = None) -> None:
        """Registra una nueva lectura."""
        self._marcas.append(_Marca(state, float(ts if ts is not None else time.time())))

    def note_support_mode(self, mode: str) -> None:
        """Recuerda cómo acompañó YUE la última vez (evita repetir la misma jugada)."""
        self.last_support_mode = str(mode or "")

    def clear(self) -> None:
        self._marcas.clear()
        self.last_support_mode = ""

    # ---- lectura ----------------------------------------------------------
    @property
    def current(self) -> AffectiveState | None:
        return self._marcas[-1].state if self._marcas else None

    @property
    def previous(self) -> AffectiveState | None:
        return self._marcas[-2].state if len(self._marcas) >= 2 else None

    @property
    def previous_valence(self) -> float | None:
        prev = self.previous
        return prev.valence if prev is not None else None

    def __len__(self) -> int:
        return len(self._marcas)

    @property
    def trend(self) -> str:
        """"mejorando" | "empeorando" | "estable" | "" (sin datos suficientes).

        Compara la media de valencia de la mitad reciente contra la anterior;
        una sola oscilación no cambia la tendencia.
        """
        if len(self._marcas) < 3:
            return ""
        vals = [m.state.valence for m in self._marcas]
        corte = len(vals) // 2
        antes = sum(vals[:corte]) / max(1, corte)
        ahora = sum(vals[corte:]) / max(1, len(vals) - corte)
        delta = ahora - antes
        if delta > 0.18:
            return "mejorando"
        if delta < -0.18:
            return "empeorando"
        return "estable"

    @property
    def duration(self) -> float:
        """Segundos que lleva el malestar SIN interrumpirse.

        Se cuenta hacia atrás mientras las lecturas sigan siendo negativas. En
        cuanto aparece una neutra o positiva, la racha se corta: así un mal rato
        que ya pasó no sigue pesando.
        """
        if not self._marcas:
            return 0.0
        actual = self._marcas[-1].state
        if not actual.is_negative:
            return 0.0
        fin = self._marcas[-1].ts
        inicio = fin
        for m in reversed(self._marcas):
            if not m.state.is_negative:
                break
            inicio = m.ts
        return max(0.0, fin - inicio)

    @property
    def sustained(self) -> bool:
        """¿El malestar viene sostenido en varios turnos, no en uno suelto?"""
        negativos = sum(1 for m in self._marcas if m.state.is_negative)
        return negativos >= 3 and (self.current is not None and self.current.is_negative)

    @property
    def stability(self) -> float:
        """0..1 — cuánto se parecen entre sí las últimas lecturas.

        1.0 = la persona lleva un rato en el mismo sitio emocional.
        0.0 = va dando bandazos (o hay muy poca información).
        """
        if len(self._marcas) < 2:
            return 0.0
        vals = [m.state.valence for m in self._marcas]
        media = sum(vals) / len(vals)
        varianza = sum((v - media) ** 2 for v in vals) / len(vals)
        # La valencia va de -1 a 1, así que la varianza máxima práctica es ~1.
        return max(0.0, min(1.0, 1.0 - varianza))

    @property
    def last_trigger(self) -> str:
        """La última causa identificada (la más reciente que no venga vacía)."""
        for m in reversed(self._marcas):
            if m.state.possible_trigger:
                return m.state.possible_trigger
        return ""

    @property
    def dominant_emotion(self) -> Emotion:
        """Emoción más repetida en la ventana (ignorando neutrales)."""
        conteo: dict[Emotion, int] = {}
        for m in self._marcas:
            e = m.state.primary_emotion
            if e == Emotion.NEUTRAL:
                continue
            conteo[e] = conteo.get(e, 0) + 1
        if not conteo:
            return Emotion.NEUTRAL
        return max(conteo.items(), key=lambda kv: kv[1])[0]

    def summary(self) -> dict:
        """Resumen compacto, apto para el prompt del modelo y para logs."""
        actual = self.current
        return {
            "current": str(actual.primary_emotion) if actual else "",
            "previous": str(self.previous.primary_emotion) if self.previous else "",
            "trend": self.trend,
            "duration_s": round(self.duration, 1),
            "sustained": self.sustained,
            "stability": round(self.stability, 2),
            "last_trigger": self.last_trigger,
            "last_support_mode": self.last_support_mode,
            "dominant": str(self.dominant_emotion),
        }

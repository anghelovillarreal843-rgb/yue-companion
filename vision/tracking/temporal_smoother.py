"""Suavizado temporal reutilizable (puntos 7, 8 y 9 del pedido).

Tres herramientas pequeñas que usan todos los analizadores para no dar tumbos
entre fotogramas:

  - `EMA`               : media móvil exponencial para números (confianzas, ángulos).
  - `Hysteresis`        : un booleano que necesita evidencia para encenderse y
                          evidencia para apagarse (no parpadea en el umbral).
  - `MajorityVote`      : etiqueta ganadora de una ventana temporal, con
                          confianza = proporción de votos.
  - `MotionTracker`     : desplazamiento suavizado de un punto (para "se acerca",
                          "se aleja", "movimiento ascendente").

Python puro, sin dependencias. Se prueban con listas de números.
"""
from __future__ import annotations

import time
from collections import Counter, deque
from typing import Deque


class EMA:
    """Media móvil exponencial con arranque inmediato (sin sesgo inicial)."""

    def __init__(self, alpha: float = 0.3, initial: float | None = None) -> None:
        self.alpha = min(1.0, max(0.01, float(alpha)))
        self.value: float | None = None if initial is None else float(initial)

    def update(self, sample: float) -> float:
        x = float(sample)
        if self.value is None:
            self.value = x
        else:
            self.value = (1.0 - self.alpha) * self.value + self.alpha * x
        return self.value

    def get(self, default: float = 0.0) -> float:
        return default if self.value is None else self.value

    def reset(self) -> None:
        self.value = None


class Hysteresis:
    """Booleano con dos umbrales y tiempos mínimos: enciende tarde, apaga tarde.

    `on_threshold` > `off_threshold`. Para pasar a True hace falta superar el
    umbral alto durante `min_on`; para volver a False, caer por debajo del bajo
    durante `min_off`. Así un valor que baila alrededor del umbral no parpadea.
    """

    def __init__(
        self,
        on_threshold: float = 0.6,
        off_threshold: float = 0.4,
        min_on: float = 0.3,
        min_off: float = 0.5,
    ) -> None:
        self.on_threshold = float(on_threshold)
        self.off_threshold = float(off_threshold)
        self.min_on = float(min_on)
        self.min_off = float(min_off)
        self.state = False
        self._since = 0.0
        self._pending: bool | None = None

    def update(self, value: float, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else float(now)
        v = float(value)
        target: bool | None = None
        if not self.state and v >= self.on_threshold:
            target = True
        elif self.state and v <= self.off_threshold:
            target = False

        if target is None:
            self._pending = None
            self._since = 0.0
            return self.state

        if self._pending != target:
            self._pending = target
            self._since = now
            return self.state

        needed = self.min_on if target else self.min_off
        if (now - self._since) >= needed:
            self.state = target
            self._pending = None
            self._since = 0.0
        return self.state

    def reset(self) -> None:
        self.state = False
        self._pending = None
        self._since = 0.0


class MajorityVote:
    """Etiqueta más votada dentro de una ventana temporal deslizante."""

    def __init__(self, window: float = 2.0, max_samples: int = 60) -> None:
        self.window = float(window)
        self._samples: Deque[tuple[float, str, float]] = deque(maxlen=int(max_samples))

    def add(self, label: str, confidence: float = 1.0, now: float | None = None) -> None:
        now = time.monotonic() if now is None else float(now)
        self._samples.append((now, str(label), float(confidence)))

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def winner(self, now: float | None = None) -> tuple[str, float]:
        """Devuelve (etiqueta, confianza 0..1). ('', 0.0) si no hay muestras."""
        now = time.monotonic() if now is None else float(now)
        self._prune(now)
        if not self._samples:
            return "", 0.0
        counts = Counter(label for _t, label, _c in self._samples)
        label, votes = counts.most_common(1)[0]
        share = votes / len(self._samples)
        confs = [c for _t, l, c in self._samples if l == label]
        mean_conf = sum(confs) / len(confs) if confs else 0.0
        return label, round(share * mean_conf, 4)

    def size(self) -> int:
        return len(self._samples)

    def clear(self) -> None:
        self._samples.clear()


class MotionTracker:
    """Historial corto de una coordenada para describir su movimiento."""

    def __init__(self, window: float = 1.2, max_samples: int = 40) -> None:
        self.window = float(window)
        self._pts: Deque[tuple[float, float, float]] = deque(maxlen=int(max_samples))

    def add(self, x: float, y: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else float(now)
        self._pts.append((now, float(x), float(y)))
        cutoff = now - self.window
        while self._pts and self._pts[0][0] < cutoff:
            self._pts.popleft()

    def delta(self) -> tuple[float, float]:
        """Desplazamiento neto (dx, dy) dentro de la ventana."""
        if len(self._pts) < 2:
            return 0.0, 0.0
        _t0, x0, y0 = self._pts[0]
        _t1, x1, y1 = self._pts[-1]
        return x1 - x0, y1 - y0

    def speed(self) -> float:
        """Velocidad media en unidades normalizadas por segundo."""
        if len(self._pts) < 2:
            return 0.0
        dt = self._pts[-1][0] - self._pts[0][0]
        if dt <= 0:
            return 0.0
        dx, dy = self.delta()
        return ((dx * dx + dy * dy) ** 0.5) / dt

    def direction(self, min_delta: float = 0.05) -> str:
        """'up' | 'down' | 'left' | 'right' | 'still'."""
        dx, dy = self.delta()
        if abs(dx) < min_delta and abs(dy) < min_delta:
            return "still"
        if abs(dy) >= abs(dx):
            return "up" if dy < 0 else "down"      # y crece hacia abajo en imagen
        return "right" if dx > 0 else "left"

    def oscillations(self, axis: str = "x") -> int:
        """Cambios de dirección (para saludar / aplaudir)."""
        idx = 1 if axis == "x" else 2
        vals = [p[idx] for p in self._pts]
        changes = 0
        for a, b, c in zip(vals, vals[1:], vals[2:]):
            if (b - a) * (c - b) < 0:
                changes += 1
        return changes

    def span(self, axis: str = "x") -> float:
        idx = 1 if axis == "x" else 2
        vals = [p[idx] for p in self._pts]
        return (max(vals) - min(vals)) if vals else 0.0

    def clear(self) -> None:
        self._pts.clear()

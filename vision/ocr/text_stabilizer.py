"""Estabilizador de texto OCR (punto 5 del pedido).

El OCR de una webcam cambia en cada fotograma: una letra baila, una palabra se
parte. Este módulo espera a que varios fotogramas COINCIDAN antes de dar el
texto por bueno, y recuerda lo ya leído para no repetirlo cada segundo.

Funcionamiento:
  - `feed(texto, confianza)` en cada pasada del OCR,
  - se normaliza el texto (espacios, mayúsculas, tildes) para comparar,
  - se agrupan lecturas parecidas (similitud >= `similarity`),
  - cuando un grupo acumula `min_agreements` lecturas, se marca `stable=True`,
  - la mejor variante (la más frecuente y de mayor confianza) es la que sale,
  - lo ya entregado entra en una memoria corta: si vuelve a verse, `is_new=False`.

Python puro (usa `difflib` de la biblioteca estándar). Se prueba sin cámara.
"""
from __future__ import annotations

import re
import time
import unicodedata
from collections import Counter, deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Deque


@dataclass
class StableText:
    """Resultado con la forma exacta que pide el documento."""

    text: str
    confidence: float = 0.0
    language: str = "es"
    stable: bool = False
    region: list = field(default_factory=list)
    timestamp: float = 0.0
    is_new: bool = True
    agreements: int = 0

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "confidence": round(float(self.confidence), 3),
            "language": self.language,
            "stable": bool(self.stable),
            "region": list(self.region),
            "timestamp": round(float(self.timestamp), 3),
            "is_new": bool(self.is_new),
        }


def normalize(text: str) -> str:
    """Forma canónica para comparar: sin tildes, sin dobles espacios, minúsculas."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def similarity(a: str, b: str) -> float:
    """Parecido 0..1 entre dos textos ya normalizados."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


class TextStabilizer:
    def __init__(
        self,
        *,
        min_agreements: int = 3,
        similarity_threshold: float = 0.82,
        window: float = 6.0,
        memory_seconds: float = 90.0,
        min_length: int = 3,
    ) -> None:
        self.min_agreements = int(min_agreements)
        self.similarity_threshold = float(similarity_threshold)
        self.window = float(window)
        self.memory_seconds = float(memory_seconds)
        self.min_length = int(min_length)
        self._samples: Deque[tuple[float, str, str, float, list]] = deque(maxlen=40)
        self._delivered: list[tuple[float, str]] = []
        self._last_stable: StableText | None = None

    # ------------------------------------------------------------------
    def feed(self, text: str, confidence: float = 0.0, region: list | None = None,
             language: str = "es", now: float | None = None) -> StableText | None:
        """Añade una lectura. Devuelve el texto SOLO cuando es estable."""
        now = time.time() if now is None else float(now)
        raw = (text or "").strip()
        norm = normalize(raw)
        if len(norm) < self.min_length:
            return None

        self._samples.append((now, raw, norm, float(confidence), list(region or [])))
        self._prune(now)

        group = [s for s in self._samples
                 if similarity(s[2], norm) >= self.similarity_threshold]
        if len(group) < self.min_agreements:
            return None

        # Variante ganadora: la más repetida; a igualdad, la de mayor confianza.
        counts = Counter(s[1] for s in group)
        top_count = max(counts.values())
        finalists = [t for t, c in counts.items() if c == top_count]
        best_text = max(
            finalists,
            key=lambda t: max((s[3] for s in group if s[1] == t), default=0.0),
        )
        mean_conf = sum(s[3] for s in group) / len(group)
        # La confianza sube con el acuerdo entre fotogramas, sin pasar de 0.99.
        agreement_bonus = min(0.15, 0.03 * (len(group) - self.min_agreements))
        final_conf = min(0.99, mean_conf + agreement_bonus)
        best_region = next((s[4] for s in group if s[1] == best_text and s[4]), [])

        result = StableText(
            text=best_text,
            confidence=round(final_conf, 3),
            language=language,
            stable=True,
            region=best_region,
            timestamp=now,
            is_new=not self.already_read(best_text, now),
            agreements=len(group),
        )
        self._last_stable = result
        return result

    # ------------------------------------------------------------------
    def already_read(self, text: str, now: float | None = None) -> bool:
        """True si ese texto (o uno casi idéntico) ya se entregó hace poco."""
        now = time.time() if now is None else float(now)
        norm = normalize(text)
        self._forget_old(now)
        return any(similarity(norm, prev) >= self.similarity_threshold
                   for _ts, prev in self._delivered)

    def mark_delivered(self, text: str, now: float | None = None) -> None:
        """Marca un texto como ya dicho, para no repetirlo."""
        now = time.time() if now is None else float(now)
        self._delivered.append((now, normalize(text)))
        self._forget_old(now)

    def last_stable(self) -> StableText | None:
        return self._last_stable

    # ------------------------------------------------------------------
    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def _forget_old(self, now: float) -> None:
        cutoff = now - self.memory_seconds
        self._delivered = [(ts, t) for ts, t in self._delivered if ts >= cutoff]

    def reset(self) -> None:
        """Olvida lecturas en curso y lo ya entregado ("olvida lo que viste")."""
        self._samples.clear()
        self._delivered.clear()
        self._last_stable = None

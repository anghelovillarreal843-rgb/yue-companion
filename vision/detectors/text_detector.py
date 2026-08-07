"""Detector de REGIONES con texto (punto 5, primera fase).

Antes de gastar CPU en OCR conviene saber si hay algo que leer. Este módulo
responde barato a "¿hay texto delante de la cámara y dónde?", usando solo
OpenCV clásico (sin modelos ni descargas):

  1. escala de grises + desenfoque suave,
  2. gradiente morfológico (resalta bordes de letras),
  3. binarizado Otsu + cierre horizontal (une letras en renglones),
  4. contornos con relación de aspecto y densidad propias de un texto,
  5. agrupación de renglones cercanos en un bloque.

Devuelve cajas normalizadas 0..1 y una puntuación de "texto probable". Si no hay
OpenCV, degrada a `available=False` y devuelve lista vacía: nadie se rompe.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("vision.text_detector")


@dataclass
class TextRegion:
    """Una región candidata a contener texto."""

    box: dict                       # {x, y, w, h} normalizado
    score: float = 0.0
    lines: int = 1

    def as_list(self) -> list:
        return [round(self.box["x"], 4), round(self.box["y"], 4),
                round(self.box["x"] + self.box["w"], 4),
                round(self.box["y"] + self.box["h"], 4)]

    def area(self) -> float:
        return self.box["w"] * self.box["h"]


class TextDetector:
    name = "text_detector"

    def __init__(self, min_score: float = 0.25, max_regions: int = 4,
                 min_area: float = 0.004) -> None:
        self.min_score = float(min_score)
        self.max_regions = int(max_regions)
        self.min_area = float(min_area)
        self._cv2 = None
        self._checked = False

    @property
    def available(self) -> bool:
        self._ensure_cv2()
        return self._cv2 is not None

    def _ensure_cv2(self):
        if self._checked:
            return self._cv2
        self._checked = True
        try:
            import cv2
            self._cv2 = cv2
        except Exception as exc:  # pragma: no cover
            log.info("Sin OpenCV: la detección de texto queda inactiva (%s)", exc)
            self._cv2 = None
        return self._cv2

    # ------------------------------------------------------------------
    def detect(self, frame_bgr) -> list[TextRegion]:
        """Regiones con aspecto de texto, ordenadas de mayor a menor puntuación."""
        cv2 = self._ensure_cv2()
        if cv2 is None or frame_bgr is None:
            return []
        try:
            import numpy as np
        except Exception:  # pragma: no cover
            return []

        try:
            h, w = frame_bgr.shape[0], frame_bgr.shape[1]
            if h < 40 or w < 40:
                return []
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
            gray = cv2.GaussianBlur(gray, (3, 3), 0)

            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, kernel)
            _, binary = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

            # Cierre horizontal: junta letras del mismo renglón.
            line_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, w // 60), 3))
            closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, line_kernel)

            contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            candidates: list[TextRegion] = []
            for cnt in contours:
                x, y, cw, ch = cv2.boundingRect(cnt)
                if ch < 8 or cw < 20:
                    continue
                ratio = cw / max(1, ch)
                if ratio < 1.6 or ratio > 40:
                    continue
                roi = binary[y:y + ch, x:x + cw]
                if roi.size == 0:
                    continue
                density = float(np.count_nonzero(roi)) / roi.size
                if density < 0.15 or density > 0.85:
                    continue
                area_norm = (cw * ch) / float(w * h)
                if area_norm < self.min_area:
                    continue
                score = min(1.0, density * 1.3 + min(0.4, area_norm * 4))
                candidates.append(TextRegion(
                    box={"x": x / w, "y": y / h, "w": cw / w, "h": ch / h},
                    score=round(score, 3),
                ))

            merged = self._merge(candidates)
            merged = [r for r in merged if r.score >= self.min_score]
            merged.sort(key=lambda r: (r.score, r.area()), reverse=True)
            return merged[: self.max_regions]
        except Exception as exc:
            log.debug("fallo detectando texto: %s", exc)
            return []

    # ------------------------------------------------------------------
    @staticmethod
    def _merge(regions: list[TextRegion], gap: float = 0.035) -> list[TextRegion]:
        """Une renglones verticalmente cercanos y horizontalmente solapados."""
        if not regions:
            return []
        regions = sorted(regions, key=lambda r: r.box["y"])
        blocks: list[TextRegion] = []
        for r in regions:
            placed = False
            for b in blocks:
                bx1, bx2 = b.box["x"], b.box["x"] + b.box["w"]
                rx1, rx2 = r.box["x"], r.box["x"] + r.box["w"]
                overlap = min(bx2, rx2) - max(bx1, rx1)
                vertical_gap = r.box["y"] - (b.box["y"] + b.box["h"])
                if overlap > 0.3 * min(b.box["w"], r.box["w"]) and -0.01 <= vertical_gap <= gap:
                    nx1, ny1 = min(bx1, rx1), min(b.box["y"], r.box["y"])
                    nx2 = max(bx2, rx2)
                    ny2 = max(b.box["y"] + b.box["h"], r.box["y"] + r.box["h"])
                    b.box = {"x": nx1, "y": ny1, "w": nx2 - nx1, "h": ny2 - ny1}
                    b.score = max(b.score, r.score)
                    b.lines += 1
                    placed = True
                    break
            if not placed:
                blocks.append(TextRegion(box=dict(r.box), score=r.score, lines=1))
        return blocks

    def has_text(self, frame_bgr) -> bool:
        return bool(self.detect(frame_bgr))

    def close(self) -> None:
        self._cv2 = None

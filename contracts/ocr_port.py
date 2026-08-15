"""Contrato OCRPort: core lo consume, vision/ocr/ocr_engine lo implementa.

Ajuste de decisión vs. REFACTOR_SPEC §2.3: además de `recognize`, el port
expone `available` y `engine_name` porque core/screen_ocr.py los usa hoy
para su API pública de diagnóstico (no romper comportamiento en PR 3).
screen_ocr NO construye el port: recibe una fábrica en composición
(screen_ocr.set_ocr_port_factory) y levanta la configuración de siempre.
"""

from __future__ import annotations

import abc


class OCRPort(abc.ABC):
    """Motor OCR inyectable: core/screen_ocr lo consume sin conocer vision."""

    @abc.abstractmethod
    def recognize(self, image, *, lang: str = "spa") -> str:
        """Devuelve el texto reconocido de la imagen ("" si no pudo)."""

    @property
    @abc.abstractmethod
    def available(self) -> bool:
        """True si hay un motor OCR utilizable."""

    @property
    @abc.abstractmethod
    def engine_name(self) -> str:
        """Nombre del motor activo (diagnóstico)."""
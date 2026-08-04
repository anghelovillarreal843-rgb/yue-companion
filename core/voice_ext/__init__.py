"""Extensiones de voz de YUE (FASE 4).

Por ahora contiene el lip-sync por visemas (forma de la boca a partir del texto),
que complementa el movimiento por volumen que YUE ya tenía. ADITIVO.
"""
from .visemes import (
    VISEMES,
    text_to_visemes,
    visemes_from_envelope,
    merge_shape_with_envelope,
)

__all__ = [
    "VISEMES",
    "text_to_visemes",
    "visemes_from_envelope",
    "merge_shape_with_envelope",
]

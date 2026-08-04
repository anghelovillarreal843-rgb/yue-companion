"""Generador de trabajos con diseño (Excel y PowerPoint), rápido y sin COM.

Flujo pensado: la IA devuelve la ESTRUCTURA en JSON (parse_table_plan /
parse_deck_plan) y estos constructores la convierten en un .xlsx o .pptx con
diseño aplicado de una sola pasada. ADITIVO: no toca office_control.py.
"""
from .plan import (
    TablePlan, DeckPlan, Palette, PALETTES, get_palette,
    TABLE_JSON_INSTRUCTIONS, DECK_JSON_INSTRUCTIONS,
    parse_table_plan, parse_deck_plan,
)
from .excel_builder import build_table
from .pptx_builder import build_deck

__all__ = [
    "TablePlan", "DeckPlan", "Palette", "PALETTES", "get_palette",
    "TABLE_JSON_INSTRUCTIONS", "DECK_JSON_INSTRUCTIONS",
    "parse_table_plan", "parse_deck_plan",
    "build_table", "build_deck",
]

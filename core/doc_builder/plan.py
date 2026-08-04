"""Planes de documento y paletas de diseño (generador de trabajos).

La idea (para que salga rápido Y con diseño): separamos "pensar el contenido" de
"construir el archivo".

  1) La IA devuelve solo la ESTRUCTURA en JSON (títulos, filas de la tabla,
     contenido de cada diapositiva). Rápido y barato.
  2) Los constructores (excel_builder / pptx_builder) toman esa estructura y arman
     el .xlsx o .pptx de golpe, con una PALETA de diseño aplicada de una vez.

Aquí viven:
  - Las paletas de color (colegio, verde, morado YUE, corporativo…).
  - Las estructuras del plan (TablePlan, DeckPlan y sus diapositivas).
  - El texto de instrucciones para pedirle el JSON a la IA, y un lector robusto
    que convierte ese JSON en las estructuras (tolerante a ```fences``` y ruido).

Solo estructuras y parseo; no toca archivos ni librerías pesadas. Se prueba solo.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


# --------------------------------------------------------------------------- #
# Paletas de diseño. Colores en hex SIN '#'. Pensadas para buen contraste:
# 'primary' para encabezados (texto blanco encima), 'dark' para títulos sobre
# blanco, 'band' para el sombreado alterno de filas.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Palette:
    name: str
    primary: str      # encabezados / barras (con texto blanco encima)
    dark: str         # títulos y texto fuerte sobre fondo blanco
    accent: str       # detalle/segundo color
    band: str         # sombreado suave de filas alternas
    text: str = "222222"     # texto normal
    light: str = "FFFFFF"    # fondo


PALETTES: dict[str, Palette] = {
    "colegio":     Palette("colegio", primary="1F4E79", dark="15385A",
                           accent="2E75B6", band="D9E6F2"),
    "verde":       Palette("verde", primary="2E7D46", dark="1E5631",
                           accent="4CAF6A", band="DCEFE2"),
    "morado_yue":  Palette("morado_yue", primary="5A4A8A", dark="3F3466",
                           accent="8A6FC0", band="E7E1F3"),
    "corporativo": Palette("corporativo", primary="2B2B2B", dark="000000",
                           accent="C79A3B", band="EDEDED"),
    "coral":       Palette("coral", primary="C1443C", dark="8F2E28",
                           accent="E4736B", band="F6DEDC"),
}
DEFAULT_PALETTE = "colegio"


def get_palette(name: str | None) -> Palette:
    return PALETTES.get((name or "").lower(), PALETTES[DEFAULT_PALETTE])


# --------------------------------------------------------------------------- #
# Plan de TABLA (Excel).
# --------------------------------------------------------------------------- #
@dataclass
class TablePlan:
    title: str
    headers: list[str]
    rows: list[list]
    subtitle: str = ""
    sheet_name: str = "Hoja1"
    palette: str = DEFAULT_PALETTE
    # Columnas (por índice, 0-based) que llevan una fila de TOTAL con SUMA.
    total_columns: list[int] = field(default_factory=list)
    # Nota/leyenda al pie (fuente de los datos, aclaraciones…).
    note: str = ""

    def validate(self) -> "TablePlan":
        if not self.title:
            raise ValueError("la tabla necesita un título")
        if not self.headers:
            raise ValueError("la tabla necesita encabezados")
        # Normaliza filas a la cantidad de columnas (rellena o recorta con cuidado).
        ncols = len(self.headers)
        norm = []
        for r in self.rows:
            r = list(r)
            if len(r) < ncols:
                r = r + [""] * (ncols - len(r))
            elif len(r) > ncols:
                r = r[:ncols]
            norm.append(r)
        self.rows = norm
        # El nombre de hoja de Excel no admite algunos caracteres ni > 31 chars.
        self.sheet_name = re.sub(r'[:\\/?*\[\]]', " ", self.sheet_name)[:31] or "Hoja1"
        return self


# --------------------------------------------------------------------------- #
# Plan de PRESENTACIÓN (PowerPoint). Cada diapositiva es un dict con 'type'.
# Tipos soportados: title | section | bullets | table | closing.
# --------------------------------------------------------------------------- #
@dataclass
class DeckPlan:
    title: str
    slides: list[dict]
    subtitle: str = ""
    author: str = ""
    palette: str = DEFAULT_PALETTE

    def validate(self) -> "DeckPlan":
        if not self.title:
            raise ValueError("la presentación necesita un título")
        if not self.slides:
            # Si la IA no mandó diapositivas, al menos hacemos la portada.
            self.slides = [{"type": "title", "title": self.title,
                            "subtitle": self.subtitle}]
        limpias = []
        for s in self.slides:
            if not isinstance(s, dict):
                continue
            t = str(s.get("type", "bullets")).lower()
            if t not in ("title", "section", "bullets", "table", "closing"):
                t = "bullets"
            s = dict(s)
            s["type"] = t
            limpias.append(s)
        self.slides = limpias or [{"type": "title", "title": self.title}]
        return self


# --------------------------------------------------------------------------- #
# Instrucciones para pedirle el plan a la IA (se anexan al prompt) + lector.
# --------------------------------------------------------------------------- #
TABLE_JSON_INSTRUCTIONS = (
    "Devuelve SOLO un JSON válido (sin explicaciones, sin ```), con esta forma "
    "para una tabla:\n"
    '{\n'
    '  "title": "Título de la tabla",\n'
    '  "subtitle": "opcional",\n'
    '  "sheet_name": "Hoja",\n'
    '  "palette": "colegio|verde|morado_yue|corporativo|coral",\n'
    '  "headers": ["Columna 1", "Columna 2", ...],\n'
    '  "rows": [["dato", "dato", ...], ...],\n'
    '  "total_columns": [2],   // índices 0-based de columnas numéricas a sumar\n'
    '  "note": "opcional, fuente o aclaración"\n'
    '}\n'
    "Rellena filas realistas y completas. Usa números de verdad en las columnas "
    "numéricas (no texto)."
)

DECK_JSON_INSTRUCTIONS = (
    "Devuelve SOLO un JSON válido (sin explicaciones, sin ```), con esta forma "
    "para una presentación:\n"
    '{\n'
    '  "title": "Título",\n'
    '  "subtitle": "opcional",\n'
    '  "author": "opcional",\n'
    '  "palette": "colegio|verde|morado_yue|corporativo|coral",\n'
    '  "slides": [\n'
    '    {"type": "title", "title": "...", "subtitle": "..."},\n'
    '    {"type": "section", "title": "Nombre de la sección"},\n'
    '    {"type": "bullets", "title": "...", "bullets": ["punto", "punto", ...]},\n'
    '    {"type": "table", "title": "...", "headers": ["A","B"], "rows": [["1","2"]]},\n'
    '    {"type": "closing", "title": "Gracias", "subtitle": "..."}\n'
    "  ]\n"
    "}\n"
    "Haz de 6 a 12 diapositivas, con títulos claros y viñetas concretas (no más de "
    "6 viñetas por diapositiva, cortas)."
)


def _extract_json(text: str) -> str:
    """Saca el bloque JSON de una respuesta (quita ```fences``` y texto extra)."""
    t = (text or "").strip()
    # Quita cercas de código.
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    # Si hay texto alrededor, agarra desde la primera { hasta su cierre balanceado.
    inicio = t.find("{")
    if inicio == -1:
        return t
    profundidad = 0
    for i in range(inicio, len(t)):
        c = t[i]
        if c == "{":
            profundidad += 1
        elif c == "}":
            profundidad -= 1
            if profundidad == 0:
                return t[inicio:i + 1]
    return t[inicio:]


def parse_table_plan(text: str) -> TablePlan:
    data = json.loads(_extract_json(text))
    plan = TablePlan(
        title=data.get("title", ""),
        subtitle=data.get("subtitle", ""),
        sheet_name=data.get("sheet_name", "Hoja1"),
        palette=data.get("palette", DEFAULT_PALETTE),
        headers=list(data.get("headers", [])),
        rows=[list(r) for r in data.get("rows", [])],
        total_columns=[int(i) for i in data.get("total_columns", [])],
        note=data.get("note", ""),
    )
    return plan.validate()


def parse_deck_plan(text: str) -> DeckPlan:
    data = json.loads(_extract_json(text))
    plan = DeckPlan(
        title=data.get("title", ""),
        subtitle=data.get("subtitle", ""),
        author=data.get("author", ""),
        palette=data.get("palette", DEFAULT_PALETTE),
        slides=list(data.get("slides", [])),
    )
    return plan.validate()

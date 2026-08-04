"""Constructor de presentaciones de PowerPoint con diseño (python-pptx).

Toma un DeckPlan y arma un .pptx 16:9 con diseño coherente: portada y secciones
con fondo de color y texto blanco; diapositivas de contenido sobre blanco con
títulos grandes; viñetas legibles; y tablas con encabezado de color.

Sigue las buenas prácticas de diseño: contraste fuerte de tamaños (títulos ≥ 32pt,
cuerpo 16-18pt), márgenes amplios, fondos blancos en el contenido (no crema) y
SIN rayas/subrayados decorativos bajo los títulos (eso delata plantillas de IA).

No abre PowerPoint ni usa COM: escribe el archivo directo, así es rápido.
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

from .plan import DeckPlan, Palette, get_palette

_FONT = "Calibri"
_FONT_TITLE = "Calibri"

# Lienzo 16:9 grande (13.333" x 7.5").
_W = Inches(13.333)
_H = Inches(7.5)
_MARGIN = Inches(0.9)


def _rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color)


def _blank(prs):
    """Diapositiva en blanco (usamos el último layout, que suele ser vacío)."""
    return prs.slides.add_slide(prs.slide_layouts[6])


def _bg(slide, hex_color: str):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(hex_color)


def _textbox(slide, left, top, width, height):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    return tb, tf


def _set_run(paragraph, text, *, size, bold=False, color="222222",
             font=_FONT, italic=False):
    run = paragraph.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.name = font
    run.font.color.rgb = _rgb(color)
    return run


# --------------------------------------------------------------------------- #
# Diapositivas por tipo.
# --------------------------------------------------------------------------- #
def _slide_title(prs, pal: Palette, s: dict, deck: DeckPlan):
    slide = _blank(prs)
    _bg(slide, pal.primary)  # portada a todo color
    # Título grande centrado.
    _, tf = _textbox(slide, _MARGIN, Inches(2.4), _W - 2 * _MARGIN, Inches(2.0))
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    _set_run(p, s.get("title", deck.title), size=46, bold=True, color="FFFFFF")
    # Subtítulo.
    sub = s.get("subtitle", deck.subtitle)
    if sub:
        _, tf2 = _textbox(slide, _MARGIN, Inches(4.5), _W - 2 * _MARGIN, Inches(1.0))
        p2 = tf2.paragraphs[0]
        p2.alignment = PP_ALIGN.CENTER
        _set_run(p2, sub, size=22, color="F0ECFA")
    # Autor al pie.
    if deck.author:
        _, tf3 = _textbox(slide, _MARGIN, Inches(6.6), _W - 2 * _MARGIN, Inches(0.5))
        p3 = tf3.paragraphs[0]
        p3.alignment = PP_ALIGN.CENTER
        _set_run(p3, deck.author, size=14, color="D9D2EC")


def _slide_section(prs, pal: Palette, s: dict, deck: DeckPlan):
    slide = _blank(prs)
    _bg(slide, pal.dark)
    _, tf = _textbox(slide, _MARGIN, Inches(2.9), _W - 2 * _MARGIN, Inches(1.7))
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    _set_run(p, s.get("title", ""), size=40, bold=True, color="FFFFFF")


def _title_bar(slide, pal: Palette, texto: str):
    """Título de una diapositiva de contenido (sobre blanco, SIN subrayado)."""
    _, tf = _textbox(slide, _MARGIN, Inches(0.55), _W - 2 * _MARGIN, Inches(1.0))
    p = tf.paragraphs[0]
    _set_run(p, texto, size=32, bold=True, color=pal.dark, font=_FONT_TITLE)


def _slide_bullets(prs, pal: Palette, s: dict, deck: DeckPlan):
    slide = _blank(prs)
    _bg(slide, "FFFFFF")
    _title_bar(slide, pal, s.get("title", ""))
    bullets = [b for b in s.get("bullets", []) if str(b).strip()]
    _, tf = _textbox(slide, _MARGIN, Inches(1.9), _W - 2 * _MARGIN, Inches(4.9))
    for i, b in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(12)
        # Viñeta cuadrada de color + texto (textbox sin viñeta heredada: no duplica).
        _set_run(p, "▪  ", size=18, bold=True, color=pal.accent)
        _set_run(p, str(b), size=18, color=pal.text)


def _slide_table(prs, pal: Palette, s: dict, deck: DeckPlan):
    slide = _blank(prs)
    _bg(slide, "FFFFFF")
    _title_bar(slide, pal, s.get("title", ""))

    headers = [str(h) for h in s.get("headers", [])]
    rows = [[str(c) for c in r] for r in s.get("rows", [])]
    if not headers:
        return

    nfilas = len(rows) + 1
    ncols = len(headers)
    left = _MARGIN
    top = Inches(1.9)
    width = _W - 2 * _MARGIN
    height = Inches(min(4.8, 0.5 + 0.42 * nfilas))

    tabla = slide.shapes.add_table(nfilas, ncols, left, top, width, height).table

    # Encabezado con color.
    for j, h in enumerate(headers):
        celda = tabla.cell(0, j)
        celda.fill.solid()
        celda.fill.fore_color.rgb = _rgb(pal.primary)
        tf = celda.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        _set_run(p, h, size=13, bold=True, color="FFFFFF")

    # Filas con banda alterna.
    for i, r in enumerate(rows, start=1):
        banda = (i % 2 == 0)
        for j in range(ncols):
            valor = r[j] if j < len(r) else ""
            celda = tabla.cell(i, j)
            celda.fill.solid()
            celda.fill.fore_color.rgb = _rgb(pal.band if banda else "FFFFFF")
            tf = celda.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            _set_run(p, valor, size=12, color=pal.text)


def _slide_closing(prs, pal: Palette, s: dict, deck: DeckPlan):
    slide = _blank(prs)
    _bg(slide, pal.primary)
    _, tf = _textbox(slide, _MARGIN, Inches(2.7), _W - 2 * _MARGIN, Inches(1.6))
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    _set_run(p, s.get("title", "¡Gracias!"), size=44, bold=True, color="FFFFFF")
    sub = s.get("subtitle", "")
    if sub:
        _, tf2 = _textbox(slide, _MARGIN, Inches(4.4), _W - 2 * _MARGIN, Inches(1.0))
        p2 = tf2.paragraphs[0]
        p2.alignment = PP_ALIGN.CENTER
        _set_run(p2, sub, size=20, color="F0ECFA")


_RENDER = {
    "title": _slide_title,
    "section": _slide_section,
    "bullets": _slide_bullets,
    "table": _slide_table,
    "closing": _slide_closing,
}


def build_deck(plan: DeckPlan, out_path: str | Path) -> str:
    """Construye el .pptx a partir del plan y lo guarda. Devuelve la ruta."""
    plan = plan.validate()
    pal = get_palette(plan.palette)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation()
    prs.slide_width = Emu(int(_W))
    prs.slide_height = Emu(int(_H))

    # Si el plan no empieza por una portada, la ponemos nosotros.
    slides = plan.slides
    if slides and slides[0].get("type") != "title":
        _slide_title(prs, pal, {"title": plan.title, "subtitle": plan.subtitle}, plan)

    for s in slides:
        render = _RENDER.get(s.get("type", "bullets"), _slide_bullets)
        render(prs, pal, s, plan)

    prs.save(out)
    return str(out)

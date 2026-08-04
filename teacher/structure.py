"""Comprensión estructurada de material educativo (§2, §3).

`documents.py` extrae el TEXTO de una fuente; este módulo da el siguiente paso:
construye una REPRESENTACIÓN INTERNA de ese texto para poder enseñarlo, no solo
repetirlo. Detecta, con heurísticas robustas (sin dependencias externas):

    títulos / capítulos / subtítulos
    definiciones ("X es ...", "se define como ...")
    ejemplos ("por ejemplo", "p. ej.")
    ejercicios / problemas ("ejercicio", "resuelve", "problema")
    fórmulas (líneas con =, símbolos matemáticos, notación)
    referencias / bibliografía
    palabras clave (términos frecuentes y significativos)

El resultado (`DocOutline`) alimenta al motor: se resume en el prompt para que YUE
enseñe con estructura (mapa conceptual, preguntas tipo examen, ejercicios, paso a
paso). Todo es material de apoyo; no altera la memoria ni la personalidad de YUE.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


# --------------------------------------------------------------------------
# Modelo de la representación interna
# --------------------------------------------------------------------------
@dataclass
class Section:
    title: str
    level: int          # 1 = título/capítulo, 2 = subtítulo, 3 = menor
    body: str = ""


@dataclass
class DocOutline:
    origin: str = ""
    sections: list = field(default_factory=list)      # [Section]
    definitions: list = field(default_factory=list)   # [str]
    examples: list = field(default_factory=list)      # [str]
    exercises: list = field(default_factory=list)     # [str]
    formulas: list = field(default_factory=list)      # [str]
    references: list = field(default_factory=list)    # [str]
    keywords: list = field(default_factory=list)      # [str]

    def is_rich(self) -> bool:
        """¿Se detectó estructura suficiente para enseñar con ella?"""
        return bool(self.sections or self.definitions or self.exercises or self.formulas)

    def summary_es(self, max_chars: int = 1600) -> str:
        """Resumen compacto de la estructura, para inyectar en el prompt."""
        p = []
        if self.sections:
            titulos = [s.title for s in self.sections if s.level <= 2][:12]
            if titulos:
                p.append("Secciones: " + " · ".join(titulos))
        if self.keywords:
            p.append("Conceptos clave: " + ", ".join(self.keywords[:12]))
        if self.definitions:
            p.append("Definiciones detectadas: "
                     + " | ".join(d[:140] for d in self.definitions[:5]))
        if self.formulas:
            p.append("Fórmulas: " + " ; ".join(f[:60] for f in self.formulas[:6]))
        if self.exercises:
            p.append(f"Contiene {len(self.exercises)} ejercicio(s)/problema(s).")
        if self.examples:
            p.append(f"Contiene {len(self.examples)} ejemplo(s).")
        if self.references:
            p.append(f"Tiene sección de referencias ({len(self.references)}).")
        texto = "\n".join(p)
        return texto[:max_chars]


# --------------------------------------------------------------------------
# Detección
# --------------------------------------------------------------------------
_DEF_RE = re.compile(
    r"(?:^|[.\n:]\s*)((?:el |la |los |las |un |una )?[A-Za-zÁÉÍÓÚÑáéíóúñ][^.\n]{2,60}?)\s+"
    r"(?:es|son|se define(?:n)? como|se conoce como|consiste en|se llama)\s+"
    r"([^.\n]{6,200})",
    re.MULTILINE,
)
_EJEMPLO_RE = re.compile(r"(por ejemplo|p\.?\s?ej\.?|a modo de ejemplo)[:,]?\s+([^.\n]{6,200})",
                         re.IGNORECASE)
_EJERCICIO_RE = re.compile(
    r"(?im)^\s*(?:ejercicio|problema|actividad|resuelve|calcula|demuestra|"
    r"pregunta)\s*\d*[:.)-]?\s*(.{0,180})")
_REF_HEADER_RE = re.compile(r"(?im)^\s*(referencias|bibliograf[ií]a|fuentes|obras citadas)\s*:?\s*$")
_FORMULA_HINT_RE = re.compile(r"[=∑∫√±≤≥≠≈∆π]|[a-zA-Z]\s*=\s*[^=]|\b\d+\s*[+\-*/^]\s*\d+")
_HEADING_NUM_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s+(.{2,80})$")
_HEADING_KW_RE = re.compile(
    r"(?im)^\s*(cap[ií]tulo|unidad|tema|secci[oó]n|lecci[oó]n|parte)\s+[\w\dIVXLC]+"
    r"[:.\-]?\s*(.{0,80})$")

# Palabras vacías mínimas para extraer conceptos clave sin dependencias.
_STOP = set("""
el la los las un una unos unas de del al a ante bajo con contra desde durante en
entre hacia hasta mediante para por segun sin so sobre tras y e o u ni que se su
sus como mas mas pero porque cuando donde cual cuales este esta estos estas ese
esa esos esas aquel es son ser estar hay lo le les me te nos vos si no ya muy tan
tanto cada todo toda todos todas otro otra sino tambien solo puede pueden debe
deben este esta entonces asi asi ademas segun sera seran fue fueron han ha he
""".split())


def _strip_accents_lower(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _looks_like_heading(line: str) -> tuple | None:
    """Devuelve (nivel, titulo) si la línea parece un título; si no, None."""
    linea = line.strip()
    if not (2 <= len(linea) <= 90):
        return None
    m = _HEADING_KW_RE.match(linea)
    if m:
        return (1, linea)
    m = _HEADING_NUM_RE.match(linea)
    if m:
        nivel = 1 + m.group(1).count(".")
        return (min(nivel, 3), m.group(2).strip())
    # Título "visual": corto, sin punto final, en mayúsculas o Título Con Iniciales.
    if not linea.endswith((".", ",", ";", ":")):
        letras = [c for c in linea if c.isalpha()]
        if letras:
            mays = sum(1 for c in letras if c.isupper())
            if mays / len(letras) > 0.7 and len(linea.split()) <= 10:
                return (1, linea)
            palabras = linea.split()
            if 2 <= len(palabras) <= 8 and sum(
                    1 for w in palabras if w[:1].isupper()) >= max(2, len(palabras) - 1):
                return (2, linea)
    return None


def analyze(text: str, origin: str = "") -> DocOutline:
    """Analiza el texto extraído y devuelve su estructura interna."""
    out = DocOutline(origin=origin)
    if not text:
        return out
    lineas = [ln.rstrip() for ln in text.splitlines()]

    # --- Secciones (títulos/capítulos/subtítulos) con su cuerpo ---
    actual = None
    for ln in lineas:
        if not ln.strip():
            continue
        h = _looks_like_heading(ln)
        if h:
            actual = Section(title=h[1], level=h[0], body="")
            out.sections.append(actual)
        elif actual is not None and len(actual.body) < 1200:
            actual.body += (ln.strip() + " ")

    # --- Definiciones: sobre el texto ORIGINAL (respeta inicios de línea). ---
    for m in _DEF_RE.finditer(text):
        termino = m.group(1).strip(" .:¿?¡!")
        definicion = m.group(2).strip()
        if 2 <= len(termino) <= 60 and not termino.lower().startswith(("por ejemplo",)):
            out.definitions.append(f"{termino}: {definicion}")
        if len(out.definitions) >= 40:
            break
    # --- Ejemplos (sobre el texto corrido) ---
    plano = re.sub(r"\s+", " ", text)
    for m in _EJEMPLO_RE.finditer(plano):
        out.examples.append(m.group(2).strip())
        if len(out.examples) >= 30:
            break

    # --- Ejercicios / problemas (por línea) ---
    for m in _EJERCICIO_RE.finditer(text):
        frag = m.group(0).strip()
        if frag:
            out.exercises.append(frag[:180])
        if len(out.exercises) >= 40:
            break

    # --- Fórmulas (líneas con notación matemática) ---
    for ln in lineas:
        s = ln.strip()
        if 1 <= len(s) <= 120 and _FORMULA_HINT_RE.search(s) and not s.endswith((".", ":")):
            # Evita frases normales que casualmente traen "=" en URLs, etc.
            if s.count(" ") <= 14:
                out.formulas.append(s)
        if len(out.formulas) >= 30:
            break

    # --- Referencias / bibliografía (todo lo que sigue al encabezado) ---
    mref = _REF_HEADER_RE.search(text)
    if mref:
        cola = text[mref.end():]
        for ln in cola.splitlines():
            s = ln.strip(" -•\t")
            if len(s) > 8:
                out.references.append(s[:200])
            if len(out.references) >= 40:
                break

    out.keywords = _keywords(text)
    return out


def _keywords(text: str, top: int = 20) -> list:
    """Extrae términos frecuentes y significativos como conceptos clave."""
    from collections import Counter
    tokens = re.findall(r"[a-záéíóúñA-ZÁÉÍÓÚÑ][a-záéíóúñ]{3,}", text)
    cont = Counter()
    forma = {}
    for tk in tokens:
        base = _strip_accents_lower(tk)
        if base in _STOP or len(base) < 4:
            continue
        cont[base] += 1
        forma.setdefault(base, tk.lower())
    # Solo términos que aparecen al menos 2 veces (señal de que son relevantes).
    comunes = [forma[b] for b, n in cont.most_common(top * 2) if n >= 2]
    return comunes[:top]


def concept_map_hint(outline: DocOutline) -> str:
    """Sugerencia textual para que el modelo arme un mapa conceptual (§3)."""
    if not outline.keywords and not outline.sections:
        return ""
    nucleos = outline.keywords[:8] or [s.title for s in outline.sections[:8]]
    return ("Para el mapa conceptual, relaciona estos núcleos: "
            + ", ".join(nucleos) + ".")

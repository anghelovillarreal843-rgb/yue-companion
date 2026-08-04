"""Reparación de rutas de Windows que llegan dañadas al Modo Profesora.

ADITIVO: no reemplaza la detección existente de `documents.py`; solo ofrece
funciones que esta invoca para *sanear* una ruta candidata antes de abrirla o
leerla. Si estas funciones no se llamaran, `documents.py` seguiría comportándose
igual que antes.

Casos que arregla (observados en producción):

1. Falta el backslash raíz tras la letra de unidad:
       'C:Users\\villa\\Downloads\\x.pdf'  ->  'C:\\Users\\villa\\Downloads\\x.pdf'
   Este es EXACTAMENTE el fallo del log: «No existe: C:Users\\villa\\...».
   'C:Users\\...' es una ruta *relativa a la unidad C:* (no absoluta), así que
   os.path.exists() da falso y el PDF nunca se encuentra.

2. Texto pegado tras la extensión del documento:
       'C:\\...\\x.pdf por favor'  ->  'C:\\...\\x.pdf'
   Pasa cuando el usuario escribe la ruta y luego añade palabras («ábrelo»,
   «por favor», «mira esto»…). La detección se queda con la extensión útil.

Todo es best-effort y no lanza excepciones: si no reconoce el patrón, devuelve
la cadena tal cual.
"""
from __future__ import annotations

import re

# Letra de unidad + ':' seguido de algo que NO es '\' ni '/': falta el separador
# raíz (p. ej. 'C:Users...'). No tocamos 'C:\\...' ni 'C:/...' que ya son válidas.
_DRIVE_REL_RE = re.compile(r"^([A-Za-z]):(?![\\/])")

# Extensiones de documento/imagen que YUE sabe abrir o leer.
_DOC_EXTS = (
    ".pdf", ".docx", ".pptx", ".epub", ".txt", ".md",
    ".png", ".jpg", ".jpeg",
)

# Para recortar la ruta justo tras la primera extensión conocida.
_TRIM_RE = re.compile(
    r"(.+?\.(?:pdf|docx|pptx|epub|txt|md|png|jpe?g))(?:[\s\"'].*)?$",
    re.IGNORECASE,
)


def repair_windows_path(cand: str) -> str:
    """Devuelve la ruta con el separador de unidad restaurado si faltaba.

    No toca URLs (http/https/file:) ni rutas POSIX ('/...'). Es idempotente:
    aplicarla dos veces da el mismo resultado.
    """
    if not cand:
        return cand
    s = cand.strip().strip("\"'")
    low = s.lower()
    if low.startswith(("http://", "https://", "file:")):
        return s
    # 'C:Users\\...' -> 'C:\\Users\\...'  (solo si tras ':' NO hay ya una barra).
    m = _DRIVE_REL_RE.match(s)
    if m:
        s = f"{m.group(1)}:\\" + s[2:]
    return s


def trim_to_document(cand: str) -> str:
    """Si tras la extensión quedó texto pegado, corta justo tras la extensión.

    'C:\\...\\x.pdf por favor' -> 'C:\\...\\x.pdf'. Si no encuentra una extensión
    conocida, devuelve la cadena tal cual.
    """
    if not cand:
        return cand
    s = cand.strip().strip("\"'")
    m = _TRIM_RE.match(s)
    if m:
        return m.group(1)
    return s


def candidates(cand: str) -> list[str]:
    """Variantes saneadas a probar, de la más literal a la más reparada.

    `documents.find_source` recorre esta lista y se queda con la PRIMERA que
    exista en disco; si ninguna existe, usa la última (ya reparada) siempre que
    tenga una extensión de documento válida.
    """
    if not cand:
        return []
    base = cand.strip().strip("\"'")
    variantes = [
        base,
        trim_to_document(base),
        repair_windows_path(base),
        repair_windows_path(trim_to_document(base)),
    ]
    # Deduplicar conservando el orden.
    vistos: set[str] = set()
    unicas: list[str] = []
    for v in variantes:
        if v and v not in vistos:
            vistos.add(v)
            unicas.append(v)
    return unicas


def has_doc_ext(cand: str) -> bool:
    """True si la ruta termina en una extensión de documento/imagen conocida."""
    if not cand:
        return False
    low = cand.strip().strip("\"'").lower()
    return any(low.endswith(ext) for ext in _DOC_EXTS)

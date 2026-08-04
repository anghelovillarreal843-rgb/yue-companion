"""Detección de intención para activar/desactivar modos, por voz o texto.

No distingue entre voz y texto: recibe una cadena ya transcrita y decide si el
usuario quiere ENTRAR a un modo, SALIR de él, o si es una frase normal.

No depende de frases exactas. Cada modo aporta:
  - `activation_phrases`: ejemplos naturales ("quiero una clase", "enséñame"…).
  - `activation_keywords`: reglas de palabras clave (listas que deben aparecer
    juntas), que capturan variantes que no están en los ejemplos.
  - `deactivation_phrases` / `deactivation_keywords`: lo mismo para salir.

El reconocimiento combina tres señales, de más fiable a más flexible:
  1. Contención: el texto contiene una frase gatillo (o al revés).
  2. Palabras clave: aparecen todas las palabras de alguna regla.
  3. Parecido difuso (difflib) contra los ejemplos, con umbral alto.

Así una frase como "oye yue, ¿me das una clase?" activa el modo profesora aunque
no esté escrita literalmente en los ejemplos.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher


# Umbral de parecido difuso para aceptar una variante como gatillo.
FUZZY_THRESHOLD = 0.82

# Frases genéricas para "volver a la normalidad", válidas para salir de cualquier
# modo especial (además de las propias de cada modo).
GENERIC_DEACTIVATION_PHRASES = (
    "modo normal",
    "vuelve al modo normal",
    "volver al modo normal",
    "regresa al modo normal",
    "modo companera",
    "modo compañera",
    "regresa al modo companera",
    "vuelve a ser mi companera",
    "vuelve a la normalidad",
    "sal del modo",
    "salir del modo",
    "desactiva el modo",
)
GENERIC_DEACTIVATION_KEYWORDS = (
    ("modo", "normal"),
    ("modo", "companera"),
    ("volver", "normal"),
    ("regresa", "normal"),
)


def normalize(text: str) -> str:
    """Minúsculas, sin acentos, sin signos, espacios colapsados."""
    text = unicodedata.normalize("NFD", (text or "").lower().strip())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class Intent:
    kind: str            # "activate" | "deactivate"
    mode_id: str         # modo objetivo (para activate) o modo del que se sale


# --------------------------------------------------------------------------
# Coincidencia de una frase contra un conjunto de gatillos
# --------------------------------------------------------------------------
def _phrase_hit(text_norm: str, phrases) -> bool:
    for ph in phrases:
        p = normalize(ph)
        if not p:
            continue
        # Contención en cualquier sentido (frase corta dentro de la larga).
        if p in text_norm or text_norm in p:
            return True
        # Parecido difuso para variantes ("ensename ya" ~ "ensename").
        if SequenceMatcher(None, text_norm, p).ratio() >= FUZZY_THRESHOLD:
            return True
    return False


def _keywords_hit(text_norm: str, keyword_rules) -> bool:
    tokens = set(text_norm.split())
    for regla in keyword_rules:
        # Cada palabra de la regla admite variantes por prefijo (profesor/profesora).
        if all(_token_present(tokens, text_norm, palabra) for palabra in regla):
            return True
    return False


def _token_present(tokens: set, text_norm: str, palabra: str) -> bool:
    palabra = normalize(palabra)
    if not palabra:
        return False
    # Coincidencia exacta de token o por prefijo (para género/plurales/conjugaciones).
    if palabra in tokens:
        return True
    return any(t.startswith(palabra) for t in tokens) or (" " + palabra) in (" " + text_norm)


def matches(text_norm: str, phrases, keyword_rules) -> bool:
    return _phrase_hit(text_norm, phrases) or _keywords_hit(text_norm, keyword_rules)


# --------------------------------------------------------------------------
# Detector principal: recibe el registro de modos y el modo actual
# --------------------------------------------------------------------------
def detect(text: str, current_mode: str, registry: dict, default_mode: str) -> Intent | None:
    """Devuelve un `Intent` (activate/deactivate) o None si es una frase normal.

    `registry` es {mode_id: ModeSpec}. Se comprueba primero la salida (si hay un
    modo especial activo) y luego la activación de cualquier modo registrado.
    """
    n = normalize(text)
    if not n:
        return None

    # --- SALIR: solo tiene sentido si hay un modo especial activo ---
    if current_mode != default_mode:
        spec = registry.get(current_mode)
        salida_frases = list(GENERIC_DEACTIVATION_PHRASES)
        salida_keys = list(GENERIC_DEACTIVATION_KEYWORDS)
        if spec is not None:
            salida_frases += list(getattr(spec, "deactivation_phrases", ()))
            salida_keys += list(getattr(spec, "deactivation_keywords", ()))
        if matches(n, salida_frases, salida_keys):
            return Intent("deactivate", current_mode)

    # --- ENTRAR: cualquier modo no-default cuyos gatillos casen ---
    for mode_id, spec in registry.items():
        if mode_id == default_mode:
            continue
        if mode_id == current_mode:
            continue  # ya está activo
        act_frases = list(getattr(spec, "activation_phrases", ()))
        act_keys = list(getattr(spec, "activation_keywords", ()))
        if matches(n, act_frases, act_keys):
            return Intent("activate", mode_id)

    return None

"""Rasgos de personalidad ajustables (FASE 5).

`core/personality.py` define a YUE con un carácter fijo (tsundere, orgullosa,
cariñosa). Funciona, pero no se puede regular: no hay forma de subir el cariño o
bajar el sarcasmo según lo que el usuario prefiera. Este módulo lo hace, sin
tocar personality.py.

Modela la personalidad como una consola de perillas 0..1 (sarcasmo, cariño,
energía, formalidad, curiosidad, paciencia, humor, iniciativa). A partir de esos
valores genera un FRAGMENTO de instrucción en español que se ANEXA al prompt del
sistema. Los valores se guardan en un JSON, así el ajuste persiste entre reinicios.

Regla de oro (no negociable): los rasgos afectan el TONO, nunca la seguridad. Si
hay una directiva de seguridad emocional activa, YUE deja el sarcasmo y el humor
al margen aunque estén al máximo. Eso lo garantiza personality/safety; aquí solo
lo dejamos escrito en el fragmento para reforzarlo.

Solo usa la librería estándar (json); funciona offline y no añade dependencias.
"""
from __future__ import annotations

import json
from pathlib import Path

# Perillas disponibles y su valor por defecto (el carácter tsundere de YUE):
# sarcasmo alto, cariño alto (aunque disimulado), energía media-alta, poca
# formalidad, curiosidad alta, paciencia media, humor alto, iniciativa media.
_DEFAULTS: dict[str, float] = {
    "sarcasmo": 0.70,
    "carino": 0.75,
    "energia": 0.65,
    "formalidad": 0.25,
    "curiosidad": 0.70,
    "paciencia": 0.55,
    "humor": 0.70,
    "iniciativa": 0.50,
}

# Descripción legible de cada rasgo (para el panel y para explicarle al usuario).
_ETIQUETAS = {
    "sarcasmo": "Sarcasmo y pique juguetón",
    "carino": "Cariño y calidez",
    "energia": "Energía y entusiasmo",
    "formalidad": "Formalidad al hablar",
    "curiosidad": "Curiosidad por lo que cuentas",
    "paciencia": "Paciencia al explicar",
    "humor": "Sentido del humor y bromas",
    "iniciativa": "Iniciativa para proponer cosas",
}


def _clamp(v: float) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def _nivel(v: float) -> str:
    """Traduce 0..1 a una palabra para redactar el fragmento."""
    if v >= 0.85:
        return "altísimo"
    if v >= 0.65:
        return "alto"
    if v >= 0.45:
        return "medio"
    if v >= 0.25:
        return "bajo"
    return "casi nulo"


class TraitProfile:
    def __init__(self, path: str | Path | None = None):
        self.path = str(path) if path else None
        self.values: dict[str, float] = dict(_DEFAULTS)
        self.load()

    # ---- Persistencia -----------------------------------------------------
    def load(self):
        if not self.path:
            return
        p = Path(self.path)
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if k in _DEFAULTS:
                        self.values[k] = _clamp(v)
            except (OSError, ValueError):
                pass  # si el archivo está roto, nos quedamos con los defaults

    def save(self):
        if not self.path:
            return
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.values, ensure_ascii=False, indent=2),
                     encoding="utf-8")

    # ---- Ajuste -----------------------------------------------------------
    def set(self, trait: str, value: float) -> float:
        if trait not in _DEFAULTS:
            raise KeyError(f"rasgo desconocido: {trait}")
        self.values[trait] = _clamp(value)
        self.save()
        return self.values[trait]

    def nudge(self, trait: str, delta: float) -> float:
        """Sube o baja un rasgo un poco (p. ej. 'sé un poco más cariñosa')."""
        if trait not in _DEFAULTS:
            raise KeyError(f"rasgo desconocido: {trait}")
        self.values[trait] = _clamp(self.values[trait] + float(delta))
        self.save()
        return self.values[trait]

    def get(self, trait: str) -> float:
        return self.values[trait]

    def reset(self):
        self.values = dict(_DEFAULTS)
        self.save()

    # ---- Salida para el prompt -------------------------------------------
    def to_prompt_fragment(self) -> str:
        """Fragmento en español que describe el carácter actual para el modelo."""
        v = self.values
        partes = [
            "AJUSTE DE CARÁCTER (regula tu tono, no tu criterio ni tu seguridad):",
            f"- Sarcasmo/pique: {_nivel(v['sarcasmo'])}. "
            + ("Puedes picar con gracia, sin herir." if v["sarcasmo"] >= 0.45
               else "Casi sin sarcasmo; sé directa y amable."),
            f"- Cariño/calidez: {_nivel(v['carino'])}. "
            + ("Muéstrate cercana y afectuosa." if v["carino"] >= 0.45
               else "Cariño contenido y sobrio."),
            f"- Energía: {_nivel(v['energia'])}.",
            f"- Formalidad: {_nivel(v['formalidad'])}. "
            + ("Habla formal y cuidada." if v["formalidad"] >= 0.55
               else "Habla natural y coloquial."),
            f"- Curiosidad: {_nivel(v['curiosidad'])}. "
            + ("Haz preguntas y muestra interés." if v["curiosidad"] >= 0.45
               else "No interrogues de más."),
            f"- Paciencia: {_nivel(v['paciencia'])}.",
            f"- Humor: {_nivel(v['humor'])}. "
            + ("Puedes bromear." if v["humor"] >= 0.45 else "Poco humor."),
            f"- Iniciativa: {_nivel(v['iniciativa'])}. "
            + ("Propón ideas por tu cuenta." if v["iniciativa"] >= 0.55
               else "Espera a que te pidan las cosas."),
            "IMPORTANTE: si detectas malestar o crisis, deja el sarcasmo y el humor "
            "de lado sin importar estos valores, y prioriza calidez y contención.",
        ]
        return "\n".join(partes)

    def panel_state(self) -> list[dict]:
        """Lista para un panel de perillas: clave, etiqueta y valor 0..1."""
        return [
            {"clave": k, "etiqueta": _ETIQUETAS[k], "valor": self.values[k]}
            for k in _DEFAULTS
        ]


# Frases sueltas que un router de comandos podría mapear a ajustes rápidos.
# (No se aplican solas; es una ayuda para quien integre comandos de voz.)
AJUSTES_RAPIDOS = {
    "se mas cariñosa": ("carino", +0.15),
    "se menos sarcastica": ("sarcasmo", -0.15),
    "se mas seria": ("formalidad", +0.15),
    "se mas divertida": ("humor", +0.15),
    "con mas calma": ("paciencia", +0.15),
    "toma mas iniciativa": ("iniciativa", +0.15),
}

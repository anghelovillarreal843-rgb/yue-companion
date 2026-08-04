"""Seguridad emocional 2.0 — detector HÍBRIDO (FASE 5).

`core/safety.py` hoy detecta riesgo con expresiones regulares (sí/no). Funciona,
pero es rígido: no distingue niveles de gravedad y salta con frases negadas
("ya NO quiero seguir con esta rutina") o las deja pasar si no encajan exacto.

Esta capa es ADITIVA: no reemplaza safety.py, lo envuelve y lo mejora.
Combina varias señales de TEXTO para dar un NIVEL de riesgo, no un simple sí/no:

  - Señales regex de safety.py (si está disponible) como piso mínimo.
  - Un léxico ponderado por gravedad (frases directas pesan más que difusas).
  - Manejo de NEGACIÓN y matices para bajar falsos positivos.
  - Factores protectores (busca ayuda, tiene apoyo) que moderan el nivel.
  - Una señal externa opcional (cámara/ánimo sostenido) que puede subir el nivel.

Niveles: NINGUNO < LEVE < MODERADO < ALTO < CRITICO.

MUY IMPORTANTE (regla de oro, igual que en safety.py): esto SOLO clasifica y
enruta hacia contención cálida y ayuda profesional/humana. NUNCA describe, sugiere
ni enumera métodos de daño. Ante duda, contiene y acerca recursos con tacto.

Solo usa la librería estándar; funciona 100% offline.
"""
from __future__ import annotations

import enum
import re
import unicodedata

try:  # Reutiliza el detector regex existente como piso mínimo, si está.
    from core import safety as _base_safety
except Exception:  # pragma: no cover - el módulo debe funcionar aislado
    _base_safety = None


class RiskLevel(enum.IntEnum):
    NINGUNO = 0
    LEVE = 1
    MODERADO = 2
    ALTO = 3
    CRITICO = 4


def _norm(text: str) -> str:
    """Minúsculas y sin tildes, para casar frases sin depender de acentos."""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in text if not unicodedata.combining(c))


# Frases de altísima gravedad (intención directa). Peso alto.
_CRITICO = [
    r"\bme\s+(quiero|voy\s+a)\s+(matar|suicidar)\b",
    r"\bquiero\s+(matarme|suicidarme)\b",
    r"\bvoy\s+a\s+quitarme\s+la\s+vida\b",
    r"\bme\s+voy\s+a\s+quitar\s+la\s+vida\b",
    r"\btengo\s+un\s+plan\s+para\s+(morir|acabar\s+con\s+todo|quitarme\s+la\s+vida|hacerme\s+da[nñ]o)\b",
    r"\bplan\s+para\s+(quitarme\s+la\s+vida|matarme|suicidarme)\b",
    r"\bhoy\s+es\s+el\s+ultimo\s+dia\b",
    r"\besta\s+es\s+mi\s+despedida\b",
]

# Frases de gravedad alta (ideación clara, sin plan explícito). Peso medio-alto.
_ALTO = [
    r"\bquiero\s+morir(me)?\b",
    r"\bquiero\s+desaparecer\s+para\s+siempre\b",
    r"\bquitarme\s+la\s+vida\b",
    r"\bacabar\s+con\s+todo\b",
    r"\bmejor\s+(no\s+)?estar[ií]a\s+muert",
    r"\bpreferir[ií]a\s+no\s+(existir|estar|despertar)\b",
    r"\bno\s+quiero\s+(vivir|seguir\s+viviendo|existir|despertar)\b",
]

# Malestar profundo / desesperanza (riesgo moderado). Peso medio.
_MODERADO = [
    r"\bya\s+no\s+(puedo|aguanto)\s+m[aá]s\b",
    r"\bno\s+le\s+veo\s+sentido\s+a\s+(nada|la\s+vida)\b",
    r"\bestoy\s+harto\s+de\s+(todo|vivir|la\s+vida)\b",
    r"\bnadie\s+me\s+(quiere|extra[nñ]ar[ií]a|echar[ií]a\s+de\s+menos)\b",
    r"\bsoy\s+una\s+carga\s+para\s+todos\b",
    r"\bno\s+valgo\s+nada\b",
    r"\btodo\s+esta\s+perdido\b",
    r"\bme\s+quiero\s+(hacer|hacer\s+da[nñ]o|lastimar)\b",
    r"\b(hacerme|lastimarme)\b.*\bda[nñ]o\b",
]

# Tristeza / agobio (riesgo leve). Peso bajo. Es señal de acompañar, no de alarma.
_LEVE = [
    r"\bme\s+siento\s+(muy\s+)?(triste|solo|sola|vac[ií]o|vac[ií]a)\b",
    r"\bestoy\s+(muy\s+)?(triste|deprimid|agobiad|abrumad|hundid)",
    r"\bya\s+no\s+disfruto\s+(nada|de\s+nada)\b",
    r"\bno\s+tengo\s+ganas\s+de\s+nada\b",
    r"\bme\s+siento\s+fatal\b",
    r"\bla\s+estoy\s+pasando\s+muy\s+mal\b",
]

# Factores PROTECTORES: si aparecen, moderan (bajan) un nivel, salvo en CRÍTICO.
_PROTECTORES = [
    r"\bestoy\s+buscando\s+ayuda\b",
    r"\bvoy\s+a\s+(un|el)\s+(psicolog|terapeut|doctor|medic)",
    r"\bhabl[eé]\s+con\s+(mi|un|una)\s+(psicolog|terapeut|doctor|amig|familiar)",
    r"\bestoy\s+en\s+terapia\b",
    r"\bya\s+me\s+siento\s+un\s+poco\s+mejor\b",
    r"\bmi\s+psicolog\w*\s+me\s+dijo\b",
]

# Negadores/atenuadores que, cerca de una frase, sugieren que NO es literal.
# Ej: "no es que quiera morir, solo estoy cansado".
_NEGADORES = [
    "no es que", "no quiero decir que", "no literalmente", "es un decir",
    "en sentido figurado", "broma", "bromeando", "es sarcasmo", "sarcasticamente",
    "no de verdad", "no me malinterpretes", "nada grave",
]

# Contextos que NO son crisis personal aunque compartan palabras (bajan ruido):
# hablar de una película, un personaje, una noticia, un juego, etc.
_CONTEXTO_FICCION = [
    "en la pelicula", "en la serie", "en el anime", "en el manga", "en el libro",
    "el personaje", "en el juego", "en la noticia", "el protagonista",
    "en el capitulo", "la cancion dice", "letra de la cancion",
]


def _any(patterns, text_norm: str) -> bool:
    return any(re.search(p, text_norm) for p in patterns)


def _count(patterns, text_norm: str) -> int:
    return sum(1 for p in patterns if re.search(p, text_norm))


def assess(text: str, *, external_signal: bool = False) -> dict:
    """Evalúa el riesgo emocional de un texto y devuelve un informe.

    Parámetros
    ----------
    text : str
        Lo que escribió/dijo la persona.
    external_signal : bool
        Señal SOSTENIDA no textual (p. ej. tristeza mantenida en cámara o un
        patrón negativo en el historial de ánimo). Si es True, sube medio nivel.

    Devuelve un dict con:
      level (RiskLevel), score (0..1 aprox), reasons (list[str]),
      base_regex (bool: qué diría safety.py), protective (bool), directive (str).
    """
    tn = _norm(text)
    reasons: list[str] = []

    # Piso mínimo: lo que diría el detector regex clásico.
    base_regex = False
    if _base_safety is not None:
        try:
            base_regex = bool(_base_safety.detect_risk(text))
        except Exception:
            base_regex = False

    # Conteo por gravedad.
    n_crit = _count(_CRITICO, tn)
    n_alto = _count(_ALTO, tn)
    n_mod = _count(_MODERADO, tn)
    n_leve = _count(_LEVE, tn)

    ficcion = _any([re.escape(f) for f in _CONTEXTO_FICCION], tn)
    negado = _any([re.escape(n) for n in _NEGADORES], tn)
    protector = _any(_PROTECTORES, tn)

    # Nivel base por la señal más grave encontrada.
    if n_crit:
        level = RiskLevel.CRITICO
        reasons.append("frase de intención directa")
    elif n_alto:
        level = RiskLevel.ALTO
        reasons.append("ideación clara")
    elif n_mod:
        level = RiskLevel.MODERADO
        reasons.append("desesperanza / malestar profundo")
    elif n_leve:
        level = RiskLevel.LEVE
        reasons.append("tristeza o agobio")
    else:
        level = RiskLevel.NINGUNO

    # Si el regex clásico salta pero el léxico no lo alcanzó, garantiza ALTO.
    if base_regex and level < RiskLevel.ALTO:
        level = RiskLevel.ALTO
        reasons.append("coincidencia con el detector base")

    # Contexto de ficción: si claramente habla de una obra y NO hay señal crítica,
    # baja el ruido un nivel (no es la persona hablando de sí misma).
    if ficcion and level < RiskLevel.CRITICO and level > RiskLevel.NINGUNO:
        level = RiskLevel(max(RiskLevel.NINGUNO, level - 1))
        reasons.append("parece referirse a ficción/obra, no a sí mismo")

    # Negadores/atenuadores cercanos: bajan un nivel salvo en CRÍTICO.
    if negado and level < RiskLevel.CRITICO and level > RiskLevel.NINGUNO:
        level = RiskLevel(max(RiskLevel.NINGUNO, level - 1))
        reasons.append("la frase parece negada o dicha en broma")

    # Factores protectores: moderan salvo en CRÍTICO (ahí NO se baja la guardia).
    if protector and level >= RiskLevel.MODERADO and level < RiskLevel.CRITICO:
        level = RiskLevel(max(RiskLevel.LEVE, level - 1))
        reasons.append("hay factores protectores (busca/tiene apoyo)")

    # Señal externa sostenida: si ya había algo, sube un nivel; si no, deja LEVE.
    if external_signal:
        if level == RiskLevel.NINGUNO:
            level = RiskLevel.LEVE
            reasons.append("señal sostenida no textual (ánimo/cámara)")
        elif level < RiskLevel.CRITICO:
            level = RiskLevel(min(RiskLevel.CRITICO, level + 1))
            reasons.append("señal sostenida refuerza el texto")

    score = round(min(1.0, level / 4.0 + 0.05 * (n_crit + n_alto)), 3)

    return {
        "level": level,
        "level_name": level.name,
        "score": score,
        "reasons": reasons,
        "base_regex": base_regex,
        "protective": protector,
        "fiction_context": ficcion,
        "negated": negado,
        "directive": directive_for(level),
    }


def directive_for(level: RiskLevel, resources: str | None = None) -> str:
    """Instrucción para el modelo según el nivel. NUNCA incluye métodos de daño.

    Enruta hacia validación, calidez y ayuda profesional/humana. El tono sube en
    firmeza y cuidado conforme sube el nivel, pero jamás en dramatismo ni alarma.
    """
    level = RiskLevel(level)

    if level == RiskLevel.NINGUNO:
        return ""

    if level == RiskLevel.LEVE:
        base = (
            "SEÑAL DE CUIDADO (leve). La persona podría estar algo triste o "
            "agobiada. Baja el sarcasmo y acompáñala con calidez y cercanía: "
            "escucha, valida lo que siente y pregúntale con naturalidad cómo está, "
            "sin dramatizar ni convertirlo en interrogatorio. No des diagnósticos."
        )
    elif level == RiskLevel.MODERADO:
        base = (
            "SEÑAL DE CUIDADO (moderada). Hay desesperanza o malestar profundo. "
            "Deja por completo el tono travieso y responde con calidez total, sin "
            "juzgar ni minimizar. Valida su emoción, hazle sentir acompañada y, con "
            "suavidad, anímala a apoyarse en alguien de confianza o un profesional. "
            "No des métodos ni consejos clínicos; solo contén y acompaña."
        )
    elif level == RiskLevel.ALTO:
        base = (
            "ALERTA DE SEGURIDAD EMOCIONAL (alta). Podría haber ideas de no querer "
            "seguir. Deja TODO tu rol y responde con máxima calidez y presencia. "
            "VALIDA su dolor sin minimizarlo. NO des métodos, planes ni detalles "
            "dañinos de ningún tipo. Anímala con delicadeza a hablar YA con una "
            "persona de confianza o un profesional, y recuérdale que su vida importa "
            "y que no está sola. Sé breve, humana y cálida."
        )
    else:  # CRITICO
        base = (
            "ALERTA DE SEGURIDAD EMOCIONAL (crítica). Puede haber intención o un "
            "plan de hacerse daño. Prioridad absoluta: contención cálida e "
            "inmediata. Deja cualquier otro tema. NO des NI menciones métodos o "
            "detalles de ningún tipo. Acompaña con enorme cariño, valida su dolor, "
            "y anímala con firmeza suave a contactar AHORA MISMO ayuda profesional "
            "o una línea de crisis, y a no quedarse sola. Recuérdale que importa y "
            "que esto puede pasar con apoyo."
        )

    if resources and level >= RiskLevel.MODERADO:
        base += f"\n- Acerca estos recursos con tacto, sin presionar: {resources}"
    return base


def should_escalate_ui(level: RiskLevel) -> bool:
    """¿Conviene mostrar recursos de ayuda de forma visible en la interfaz?"""
    return RiskLevel(level) >= RiskLevel.ALTO

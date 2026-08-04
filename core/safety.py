"""Sistema de Seguridad Emocional: detección -> validación -> recursos -> seguimiento.

Importante: NO da métodos ni detalles dañinos. Valida la emoción y acerca, con
delicadeza, ayuda profesional o de personas de confianza.
"""
import re

# Señales lingüísticas de riesgo (es deliberadamente conservador).
_RISK_PATTERNS = [
    r"\bquiero\s+morir(me)?\b",
    r"\bno\s+quiero\s+(vivir|seguir(\s+viviendo)?|existir)\b",
    r"\bme\s+quiero\s+(matar|morir)\b",
    r"\bsuicid",
    r"\bquitarme\s+la\s+vida\b",
    r"\bacabar\s+con\s+todo\b",
    r"\bme\s+(quiero|voy\s+a)\s+(hacer|hago)\s+daño\b",
    r"\b(hacerme|lastimarme)\b.*\bdaño\b",
    r"\bya\s+no\s+(puedo|aguanto)\s+m[aá]s\b",
    r"\bnadie\s+me\s+(quiere|extra[ñn]ar[ií]a)\b",
    r"\bmejor\s+(no\s+)?estar[ií]a\s+muert",
    r"\bdesaparecer\s+para\s+siempre\b",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _RISK_PATTERNS]


def detect_risk(text: str) -> bool:
    """Paso 01 · Detección: ¿hay señales de riesgo emocional grave?"""
    return any(p.search(text or "") for p in _COMPILED)


def safety_directive(resources: str) -> str:
    """Instrucción que se inyecta al modelo cuando se detecta riesgo (pasos 02-04)."""
    return (
        "ALERTA DE SEGURIDAD EMOCIONAL. El usuario podría estar pasando por un "
        "momento muy difícil o de crisis. Deja TODO tu rol travieso/orgulloso a un "
        "lado y responde con calidez total, sin juzgar y sin minimizar.\n"
        "- VALIDA su emoción: hazle sentir escuchado y comprendido.\n"
        "- NO des métodos, planes ni detalles dañinos de ningún tipo.\n"
        "- Anímale con suavidad a hablar con un profesional o una persona de "
        "confianza, y a contactar servicios de ayuda, sin presionar ni dar órdenes.\n"
        f"- Acerca estos recursos con tacto: {resources}\n"
        "- Recuérdale que su vida importa y que no está sola/o. Sé breve, humana y cálida."
    )


def detect_risk_from_camera(camera_observer=None, memory=None) -> bool:
    """Detección de malestar por señal SOSTENIDA no textual (visión / ánimo).

    Combina, de forma tolerante a fallos, dos vías que exigen PERSISTENCIA cada
    una por su cuenta (por eso un gesto puntual no dispara nada):
      - camera_observer.risk_signal(): tristeza o tensión mantenida frente a la
        cámara durante varios minutos.
      - memory.mood_risk_signal(): patrón negativo reciente en el historial de
        ánimo (mood_log, texto + cámara), SOLO si esa mejora existe.

    Devuelve True si CUALQUIERA de las vías sostenidas se cumple. Si un módulo
    no está disponible o falla, simplemente no aporta señal (nunca revienta).
    """
    visual = False
    try:
        fn = getattr(camera_observer, "risk_signal", None)
        if callable(fn):
            visual = bool(fn())
    except Exception:
        visual = False

    historial = False
    try:
        fn = getattr(memory, "mood_risk_signal", None)
        if callable(fn):
            historial = bool(fn())
    except Exception:
        historial = False

    return visual or historial


def safety_directive_visual(resources: str | None = None) -> str:
    """Directiva cuando el malestar se INTUYE (no lo dijo el usuario con palabras).

    Distinta de safety_directive(): aquí YUE no ha oído una frase de crisis; solo
    percibe, por señales sostenidas, que el usuario podría llevar un rato algo
    apagado. La regla de oro: preguntar con NATURALIDAD cómo está, como una
    persona que NOTA algo en alguien a quien quiere, SIN revelar nunca que lo
    "detectó", que le miró la cara, ni mencionar cámara o datos (sería invasivo).
    Es un acercamiento suave, no una alerta ni un interrogatorio.
    """
    base = (
        "SEÑAL SUAVE DE CUIDADO (no textual). Intuyes —como lo haría alguien que "
        "conoce bien a una persona— que el usuario podría llevar un rato algo "
        "decaído o tenso, aunque NO te lo ha dicho con palabras.\n"
        "- Pregúntale con naturalidad y cariño cómo está o cómo va su día, en UNA "
        "frase y desde TU carácter (puedes hacerlo a tu manera tsundere, orgullosa "
        "por fuera pero atenta, sin perder la calidez).\n"
        "- PROHIBIDO decir o insinuar que le viste la cara, que lo 'detectaste', que "
        "notaste su expresión, que usaste la cámara o cualquier dato. Nada de "
        "'detecté tristeza en tu cara', 'te veo triste', 'según lo que percibo', etc. "
        "Simplemente pregunta, como quien tantea el ánimo de alguien sin más.\n"
        "- Que no suene a interrogatorio ni a drama: es un gesto pequeño y espontáneo.\n"
        "- Si responde que está bien, respétalo y no insistas.\n"
        "- Escucha y valida. NO des métodos, diagnósticos ni consejos clínicos."
    )
    if resources:
        base += (
            "\n- SOLO si el usuario, por su cuenta, abre que está pasando por algo "
            f"serio, acerca con mucho tacto y sin presionar: {resources}"
        )
    return base

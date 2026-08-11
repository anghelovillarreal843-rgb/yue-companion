"""Traducción del estado interno al PROMPT del modelo conversacional.

El modelo no recibe una respuesta prefabricada: recibe un resumen estructurado
de lo que YUE ha entendido, y escribe con su propia voz a partir de ahí. Eso es
lo que separa «acompañar» de «recitar plantillas».

    USER AFFECT:
      emotion=disappointment
      valence=-0.65  arousal=0.30  confidence=0.78
    SUPPORT:
      need=LISTEN  wants_advice=False
    SAFETY:
      level=NONE
    POLICY:
      validate_first=True  offer_advice=False  ask_question=True  tone=warm

Dos reglas que se le dejan MUY claras al modelo:

  1. Estas etiquetas NO se repiten al usuario. Guían el comportamiento, no son
     texto para leer en voz alta.
  2. Con confianza baja, nada de «sé exactamente cómo te sientes». Si YUE no
     está segura, lo natural es tantear, no proclamar.

El bloque es corto a propósito: unas 15 líneas. Un prompt de instrucciones
gigante compite con la personalidad de YUE y acaba aplanándola.
"""
from __future__ import annotations

from ..affect.models import AffectiveState, Emotion
from .needs import SupportIntent, SupportNeed
from .policy import SafetyAssessment, SafetyLevel, SupportDecision

#: Instrucciones concretas por modo. En español, en segunda persona y en el
#: registro de YUE: son órdenes de COMPORTAMIENTO, no frases para copiar.
_GUIA_MODO: dict[SupportNeed, str] = {
    SupportNeed.LISTEN: (
        "ESCUCHA. Acoge lo que te contó y hazle sentir que lo oíste de verdad. "
        "NO propongas soluciones, NO analices el problema y NO lo conviertas en "
        "una lección. Basta con estar."
    ),
    SupportNeed.COMFORT: (
        "CONSUELA. Valida lo que siente sin minimizarlo ni exagerarlo. Nada de "
        "«todo pasa por algo» ni de arreglarlo. Que note que estás de su lado."
    ),
    SupportNeed.ASK: (
        "PREGUNTA con suavidad. No des por hecho cómo se siente: tantea. UNA "
        "sola pregunta, abierta y sin presionar, y deja la puerta abierta a que "
        "no quiera contestar."
    ),
    SupportNeed.ADVISE: (
        "ACONSEJA. Te lo pidió, así que da tu opinión de forma clara y directa, "
        "sin rodeos ni disclaimers. Breve y concreta."
    ),
    SupportNeed.SOLVE: (
        "RESUELVE. Quiere avanzar en algo concreto: ve al grano y sé práctica. "
        "Si hace falta un paso previo, dilo en una frase."
    ),
    SupportNeed.DISTRACT: (
        "DISTRAE. Te pidió aire: cambia de tema con gracia y saca tu lado "
        "travieso. No vuelvas al asunto doloroso salvo que él lo retome."
    ),
    SupportNeed.CELEBRATE: (
        "CELEBRA. Es una buena noticia: alégrate con él de verdad. NO busques el "
        "lado malo, NO adviertas de riesgos y NO lo conviertas en terapia. "
        "Puedes preguntar por curiosidad, para que te cuente más."
    ),
    SupportNeed.GIVE_SPACE: (
        "DA ESPACIO. Te pidió estar solo. Respóndele MUY corto, sin insistir, "
        "sin preguntas y sin hacerle sentir culpable. Que sepa que estarás ahí "
        "cuando vuelva, y nada más."
    ),
    SupportNeed.SAFETY: (
        "PRIORIDAD ABSOLUTA: contención. Deja cualquier otro tema y cualquier "
        "broma. Valida su dolor sin juzgar. NUNCA menciones métodos ni detalles "
        "de daño. Acompaña con calidez y acerca ayuda humana o profesional."
    ),
}

_GUIA_TONO: dict[str, str] = {
    "warm": "cálido y cercano",
    "gentle": "suave y sin filo; deja el sarcasmo aparcado",
    "steady": "sereno y práctico",
    "playful": "travieso, puedes picarle como siempre",
    "celebratory": "alegre y contagioso",
    "quiet": "muy breve y tranquilo",
    "urgent": "cálido y presente, sin dramatismo pero sin bromas",
}


def _n(valor: float) -> str:
    return f"{valor:+.2f}" if valor < 0 else f"{valor:.2f}"


def build_state_block(affect: AffectiveState, intent: SupportIntent,
                      safety: SafetyAssessment | None = None,
                      decision: SupportDecision | None = None,
                      context_summary: dict | None = None) -> str:
    """Construye el bloque de contexto afectivo para el prompt del sistema.

    Devuelve "" si no hay nada relevante que decir, para no engordar el prompt
    en una conversación trivial.
    """
    safety = safety or SafetyAssessment()
    ctx = context_summary or {}
    if decision is None:
        return ""

    lineas: list[str] = []
    lineas.append("=== LECTURA INTERNA DE YUE (NO la menciones ni la repitas) ===")

    # --- estado del usuario ---
    lineas.append("ESTADO DEL USUARIO (estimación, no un hecho):")
    detalle = f"  emocion={affect.primary_emotion}"
    if affect.secondary_emotion:
        detalle += f" · secundaria={affect.secondary_emotion}"
    lineas.append(detalle)
    lineas.append(
        f"  valencia={_n(affect.valence)} activacion={affect.arousal:.2f} "
        f"confianza={affect.confidence:.2f}"
    )
    if affect.possible_trigger:
        lineas.append(f"  posible causa (según lo que dijo): «{affect.possible_trigger}»")
    if affect.negated_emotions:
        negadas = ", ".join(str(e) for e in affect.negated_emotions)
        lineas.append(
            f"  NEGÓ explícitamente sentir: {negadas}. No se lo atribuyas ni le "
            f"lleves la contraria sobre eso."
        )
    if affect.sarcasm_probability >= 0.45:
        lineas.append(
            f"  ironía/sarcasmo probable ({affect.sarcasm_probability:.2f}): su tono "
            f"suena positivo pero lo que cuenta NO lo es. Responde a lo de debajo, "
            f"no a las palabras."
        )

    # --- tendencia, solo si aporta ---
    if ctx.get("sustained"):
        lineas.append(
            "  este malestar viene ya de varios mensajes, no es de ahora mismo."
        )
    elif ctx.get("trend") == "mejorando":
        lineas.append("  parece ir algo mejor que hace unos mensajes.")

    # --- qué necesita ---
    lineas.append("LO QUE PARECE NECESITAR DE TI:")
    necesidad = f"  {decision.mode}"
    if decision.secondary_mode:
        necesidad += f" (+{decision.secondary_mode})"
    lineas.append(necesidad)
    guia = _GUIA_MODO.get(decision.mode)
    if guia:
        lineas.append("  " + guia)

    # --- prohibiciones explícitas: lo más importante del bloque ---
    prohibiciones: list[str] = []
    if intent.wants_advice is False or not decision.offer_advice:
        prohibiciones.append("NO des consejos ni soluciones salvo que te los pida")
    if not decision.ask_question:
        prohibiciones.append("NO hagas preguntas en esta respuesta")
    if decision.give_space:
        prohibiciones.append("NO insistas ni intentes retenerle la conversación")
    if prohibiciones:
        lineas.append("LÍMITES DE ESTE TURNO:")
        for p in prohibiciones:
            lineas.append(f"  - {p}.")

    # --- cómo comportarse ---
    permisos: list[str] = []
    if decision.validate_first:
        permisos.append("valida lo que siente ANTES de cualquier otra cosa")
    if decision.ask_question:
        permisos.append("puedes hacer UNA pregunta, suave y abierta")
    if decision.offer_advice:
        permisos.append("puedes dar tu opinión o pasos concretos")
    if permisos:
        lineas.append("CÓMO: " + "; ".join(permisos) + ".")

    tono = _GUIA_TONO.get(decision.tone, "cálido")
    lineas.append(f"TONO: {tono}. Longitud: {'2-4 frases' if decision.length == 'medium' else '1-2 frases'}.")

    if decision.acknowledge_uncertainty:
        lineas.append(
            "IMPORTANTE: no estás segura de haberle leído bien. NO digas «sé "
            "exactamente cómo te sientes» ni afirmes su emoción como un hecho. "
            "Deja margen: pregunta o dilo en condicional."
        )

    # --- seguridad ---
    if safety.level > SafetyLevel.NONE:
        lineas.append(f"SEGURIDAD: nivel {safety.level.name}.")
        if safety.directive:
            lineas.append("  " + safety.directive.replace("\n", "\n  "))

    lineas.append(
        "RECUERDA: nada de esto se dice en voz alta. No nombres emociones ni "
        "etiquetas, no expliques lo que has deducido y no suenes a manual. "
        "Sigues siendo tú, con tu carácter de siempre."
    )
    lineas.append("=== FIN DE LA LECTURA INTERNA ===")
    return "\n".join(lineas)


def build_compact_summary(affect: AffectiveState, intent: SupportIntent,
                          safety: SafetyAssessment | None = None,
                          decision: SupportDecision | None = None) -> str:
    """Versión de una línea, para logs y depuración."""
    safety = safety or SafetyAssessment()
    modo = decision.mode if decision else intent.primary_need
    return (
        f"affect={affect.primary_emotion}({affect.confidence:.2f}) "
        f"v={affect.valence:+.2f} sarc={affect.sarcasm_probability:.2f} "
        f"need={intent.primary_need} advice={intent.wants_advice} "
        f"safety={safety.level.name} mode={modo}"
    )

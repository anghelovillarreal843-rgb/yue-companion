"""Léxico emocional en español, ponderado por FUERZA de la evidencia.

Diferencia clave con el sistema anterior (`core/emotion.py`), que sumaba 1.0 por
cada palabra encontrada: aquí cada patrón trae su propio peso según lo
concluyente que sea.

    «estoy destrozado»      → tristeza, peso 1.0 (declaración directa)
    «no vino»               → decepción, peso 0.45 (indicio, no declaración)

Esa diferencia es la que permite que la CONFIANZA final signifique algo, y por
tanto que el intérprete sepa cuándo debe preguntar en lugar de afirmar.

Además, hay dos familias de patrones separadas:

  - DECLARACIONES: la persona nombra lo que siente. Alta confianza.
  - INDICIOS: la persona describe un HECHO cuya emoción se infiere («al final no
    vino», «llevo tres días sin dormir»). Confianza media/baja a propósito.

Solo librería estándar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Emotion


#: Un patrón que YA contiene la negación dentro («no vino», «no me aceptaron»)
#: es INMUNE al análisis de alcance: la negación forma parte del hecho, no lo
#: anula. Sin esta distinción, «no vino nadie» se leería como «niega decepción»,
#: que es exactamente lo contrario de lo que la frase dice.
_RE_LLEVA_NEGADOR = re.compile(r"no\\s|nunca|jamas|nadie|nada|sin\\s|tampoco|ni\\s")


@dataclass(frozen=True)
class LexEntry:
    """Un patrón del léxico."""
    pattern: re.Pattern
    emotion: Emotion
    weight: float
    kind: str            # "declaracion" | "indicio"
    label: str           # descripción legible, para la evidencia
    self_negated: bool = False  # el patrón ya incorpora la negación


def _lleva_negador(regex: str) -> bool:
    """¿El propio patrón incluye un negador como parte del hecho descrito?"""
    return bool(_RE_LLEVA_NEGADOR.search(regex))


def _d(regex: str, emotion: Emotion, weight: float, label: str) -> LexEntry:
    """Atajo para una DECLARACIÓN («estoy triste»)."""
    return LexEntry(re.compile(regex), emotion, weight, "declaracion", label,
                    _lleva_negador(regex))


def _i(regex: str, emotion: Emotion, weight: float, label: str) -> LexEntry:
    """Atajo para un INDICIO (un hecho del que se infiere la emoción)."""
    return LexEntry(re.compile(regex), emotion, weight, "indicio", label,
                    _lleva_negador(regex))


#: Verbos con los que en español se declara un estado: «estoy», «me siento»…
_SER = r"(?:estoy|estaba|me\s+siento|me\s+sentia|me\s+encuentro|ando|me\s+veo|soy)"
#: Intensificadores: no cambian la emoción pero sí su fuerza.
_MUY = r"(?:muy|super|re|bien|tan|bastante|demasiado|completamente|totalmente|del\s+todo|)\s*"

LEXICON: tuple[LexEntry, ...] = (
    # ------------------------------------------------------------------ TRISTEZA
    _d(rf"\b{_SER}\s+{_MUY}(?:triste|deprimid\w*|destrozad\w*|hundid\w*|fatal|"
       rf"mal|deshech\w*|deprimente|apagad\w*|deca[ií]d\w*)\b",
       Emotion.SADNESS, 1.0, "declara tristeza"),
    _d(r"\b(?:me\s+(?:siento|senti)\s+(?:muy\s+)?mal|tengo\s+(?:mucha\s+)?pena|"
       r"me\s+duele\s+(?:mucho|el\s+alma|por\s+dentro)|"
       r"tengo\s+ganas\s+de\s+llorar|no\s+paro\s+de\s+llorar|estuve\s+llorando)\b",
       Emotion.SADNESS, 0.9, "expresa dolor emocional"),
    _i(r"\b(?:llor[eé]|llorando|se\s+me\s+cae\s+el\s+mundo|toco\s+fondo|"
       r"un\s+dia\s+horrible|un\s+dia\s+de\s+mierda|la\s+peor\s+semana)\b",
       Emotion.SADNESS, 0.55, "indicios de tristeza"),
    _i(r"\b(?:se\s+murio|falleci[oó]|perdi\s+a\s+mi|el\s+funeral|"
       r"lo\s+enterramos|se\s+nos\s+fue)\b",
       Emotion.SADNESS, 0.8, "pérdida grave"),

    # ------------------------------------------------------------- DECEPCIÓN
    _d(rf"\b{_SER}\s+{_MUY}(?:decepcionad\w*|desilusionad\w*|defraudad\w*)\b",
       Emotion.DISAPPOINTMENT, 1.0, "declara decepción"),
    _d(r"\b(?:que\s+decepcion|vaya\s+decepcion|me\s+decepcion\w*|"
       r"esperaba\s+(?:mas|otra\s+cosa)|no\s+era\s+lo\s+que\s+esperaba)\b",
       Emotion.DISAPPOINTMENT, 0.85, "expresa decepción"),
    _i(r"\b(?:al\s+final\s+no\s+(?:vino|llego|paso|fue|pudo|salio)|no\s+vino\s+nadie|"
       r"no\s+vino|me\s+dejo\s+plantad\w*|me\s+dejaron\s+plantad\w*|"
       r"no\s+me\s+(?:llamaron|escribieron|contestaron|respondieron|avisaron)|"
       r"no\s+me\s+(?:aceptaron|eligieron|seleccionaron|dieron\s+el\s+puesto)|"
       r"me\s+rechazaron|no\s+qued[eé]|se\s+cancel\w*|me\s+cancel\w*|"
       r"me\s+qued[eé]\s+fuera|me\s+dejaron\s+fuera|"
       r"lo\s+cancelaron|no\s+cumpli[oó])\b",
       Emotion.DISAPPOINTMENT, 0.55, "un hecho esperado no ocurrió"),
    _i(r"\b(?:ya\s+me\s+lo\s+esperaba|era\s+de\s+esperar|como\s+siempre|"
       r"lo\s+de\s+siempre|tipico|otra\s+vez\s+lo\s+mismo|"
       r"tampoco\s+es\s+que\s+me\s+sorprenda)\b",
       Emotion.DISAPPOINTMENT, 0.4, "resignación ante lo esperado"),
    _i(r"\b(?:da\s+igual|que\s+mas\s+da|en\s+fin|que\s+se\s+le\s+va\s+a\s+hacer|"
       r"ni\s+modo|es\s+lo\s+que\s+hay)\b",
       Emotion.DISAPPOINTMENT, 0.3, "resta importancia (posible resignación)"),

    # ---------------------------------------------------------------- SOLEDAD
    _d(rf"\b{_SER}\s+{_MUY}(?:solo|sola)\b",
       Emotion.LONELINESS, 0.95, "declara soledad"),
    _d(r"\b(?:me\s+siento\s+solo|me\s+siento\s+sola|"
       r"nadie\s+me\s+(?:habl|escrib|escuch|entiend|llam|busc)\w*|"
       r"no\s+tengo\s+a\s+nadie|no\s+le\s+importo\s+a\s+nadie|"
       r"nadie\s+se\s+acord\w*\s+de\s+mi)\b",
       Emotion.LONELINESS, 0.95, "expresa soledad"),

    # ------------------------------------------------------------------- ENOJO
    _d(rf"\b{_SER}\s+{_MUY}(?:enojad\w*|enfadad\w*|furios\w*|cabread\w*|"
       rf"molest\w*|indignad\w*|rabios\w*)\b",
       Emotion.ANGER, 1.0, "declara enojo"),
    _d(r"\b(?:me\s+da\s+(?:mucha\s+)?rabia|que\s+rabia|me\s+hierve\s+la\s+sangre|"
       r"estoy\s+harto|estoy\s+harta|no\s+lo\s+soporto|me\s+saca\s+de\s+quicio|"
       r"que\s+coraje|me\s+indigna)\b",
       Emotion.ANGER, 0.85, "expresa enojo"),
    _i(r"\b(?:injust\w*|me\s+mintio|me\s+traiciono|me\s+uso|se\s+burlo\s+de\s+mi|"
       r"me\s+falto\s+el\s+respeto)\b",
       Emotion.ANGER, 0.5, "agravio percibido"),
    _i(r"\b(?:discut[ií]\s+con|(?:nos\s+)?peleamos|me\s+grit\w*|"
       r"dando\s+un\s+portazo|se\s+fue\s+enfadad\w*|me\s+colg[oó]\s+el\s+telefono|"
       r"no\s+me\s+(?:habla|dirige\s+la\s+palabra))\b",
       Emotion.ANGER, 0.5, "conflicto con alguien"),

    # ------------------------------------------------------------- FRUSTRACIÓN
    _d(rf"\b{_SER}\s+{_MUY}(?:frustrad\w*|bloquead\w*|estancad\w*|"
       rf"impotente|atascad\w*)\b",
       Emotion.FRUSTRATION, 1.0, "declara frustración"),
    _d(r"\b(?:no\s+me\s+sale|no\s+lo\s+consigo|llevo\s+horas\s+(?:con|intentando)|"
       r"por\s+mas\s+que\s+lo\s+intento|no\s+hay\s+manera|no\s+avanzo|"
       r"me\s+rindo|estoy\s+atascad\w*|otra\s+vez\s+desde\s+cero)\b",
       Emotion.FRUSTRATION, 0.7, "obstáculo repetido"),
    _i(r"\b(?:no\s+funciona|sigue\s+fallando|vuelve\s+a\s+fallar|otro\s+error|"
       r"se\s+rompio\s+otra\s+vez|se\s+borr\w*|perdi\s+(?:el|todo|horas))\b",
       Emotion.FRUSTRATION, 0.5, "algo falla o se pierde"),

    # -------------------------------------------------------------------- MIEDO
    _d(rf"\b{_SER}\s+{_MUY}(?:asustad\w*|aterrad\w*|con\s+miedo)\b",
       Emotion.FEAR, 1.0, "declara miedo"),
    _d(r"\b(?:tengo\s+(?:mucho\s+)?miedo|me\s+da\s+(?:mucho\s+)?miedo|"
       r"me\s+aterra|tengo\s+panico|temo\s+que)\b",
       Emotion.FEAR, 0.95, "expresa miedo"),

    # ----------------------------------------------------------------- ANSIEDAD
    _d(rf"\b{_SER}\s+{_MUY}(?:ansios\w*|nervios\w*|angustiad\w*|agobiad\w*|"
       rf"abrumad\w*|estresad\w*|inquiet\w*|preocupad\w*)\b",
       Emotion.ANXIETY, 1.0, "declara ansiedad"),
    _d(r"\b(?:me\s+preocupa|me\s+angustia|tengo\s+ansiedad|no\s+puedo\s+dejar\s+de\s+pensar|"
       r"me\s+agobia|no\s+puedo\s+respirar\s+bien|el\s+pecho\s+apretado|"
       r"no\s+puedo\s+dormir\s+de\s+(?:los\s+)?nervios)\b",
       Emotion.ANXIETY, 0.85, "expresa preocupación"),
    _i(r"\b(?:y\s+si\s+(?:no|sale\s+mal|me\s+va\s+mal|pasa\s+algo)|"
       r"(?:manana|hoy|el\s+lunes|esta\s+semana|pasado\s+manana)\s+tengo\s+"
       r"(?:un[ao]?\s+)?(?:entrevista|examen|prueba|operacion|cita|presentacion|"
       r"exposicion|defensa)|"
       r"tengo\s+(?:un[ao]?\s+)?(?:entrevista|examen|prueba|operacion|cita\s+medica|"
       r"presentacion|exposicion)\s+(?:muy\s+)?(?:important\w*|manana|hoy|el\s+lunes)|"
       r"se\s+me\s+viene\s+encima|me\s+juego\s+(?:mucho|todo))\b",
       Emotion.ANXIETY, 0.45, "anticipa algo importante"),

    # -------------------------------------------------------------------- CULPA
    _d(r"\b(?:me\s+siento\s+culpable|es\s+culpa\s+mia|la\s+culpa\s+es\s+mia|"
       r"todo\s+es\s+mi\s+culpa|la\s+cagué|la\s+cague|metí\s+la\s+pata|"
       r"meti\s+la\s+pata|no\s+debi|nunca\s+debi|me\s+arrepiento)\b",
       Emotion.GUILT, 0.95, "expresa culpa"),

    # --------------------------------------------------------------- VERGÜENZA
    _d(r"\b(?:que\s+verguenza|me\s+da\s+verguenza|que\s+pena\s+ajena|"
       r"quiero\s+que\s+me\s+trague\s+la\s+tierra|hice\s+el\s+ridiculo|"
       r"me\s+da\s+corte|que\s+ridiculo)\b",
       Emotion.EMBARRASSMENT, 0.9, "expresa vergüenza"),

    # ----------------------------------------------------------------- CANSANCIO
    _d(rf"\b{_SER}\s+{_MUY}(?:cansad\w*|agotad\w*|exhaust\w*|reventad\w*|"
       rf"molid\w*|sin\s+energia|sin\s+fuerzas|hech\w*\s+polvo)\b",
       Emotion.TIREDNESS, 1.0, "declara cansancio"),
    _d(r"\b(?:no\s+doy\s+mas|no\s+tengo\s+energia|me\s+muero\s+de\s+sueno|"
       r"llevo\s+[\w\s]{0,14}?(?:dias|semanas|noches)\s+sin\s+(?:poder\s+)?dormir|"
       r"no\s+(?:puedo|consigo)\s+dormir|dormi\s+(?:muy\s+)?poco|"
       r"solo\s+cansad\w*|solo\s+cansancio)\b",
       Emotion.TIREDNESS, 0.85, "expresa agotamiento"),

    # ---------------------------------------------------------------- CONFUSIÓN
    _d(r"\b(?:no\s+entiendo|no\s+lo\s+entiendo|estoy\s+confundid\w*|"
       r"no\s+se\s+que\s+(?:pensar|hacer|sentir)|me\s+lio|estoy\s+perdid\w*|"
       r"no\s+me\s+queda\s+claro|no\s+se\s+por\s+donde\s+empezar)\b",
       Emotion.CONFUSION, 0.85, "expresa desconcierto"),

    # ------------------------------------------------------------------ ALEGRÍA
    _d(rf"\b{_SER}\s+{_MUY}(?:feliz|content\w*|alegre|genial|de\s+maravilla|"
       rf"fenomenal|estupend\w*)\b",
       Emotion.JOY, 1.0, "declara alegría"),
    _d(r"\b(?:me\s+alegra|que\s+alegria|estoy\s+encantad\w*|me\s+encanta|"
       r"que\s+bien|que\s+buena\s+noticia|me\s+hizo\s+el\s+dia)\b",
       Emotion.JOY, 0.7, "expresa alegría"),

    # ---------------------------------------------------------------- ENTUSIASMO
    _d(r"\b(?:estoy\s+(?:muy\s+)?emocionad\w*|que\s+emocion|no\s+puedo\s+esperar|"
       r"tengo\s+muchas\s+ganas|estoy\s+que\s+no\s+quepo|"
       r"vamos\s+que\s+nos\s+vamos)\b",
       Emotion.EXCITEMENT, 0.9, "expresa entusiasmo"),

    # -------------------------------------------------------------------- ORGULLO
    _d(r"\b(?:lo\s+consegui|lo\s+logre|me\s+aceptaron|me\s+cogieron|"
       r"me\s+dieron\s+el\s+(?:puesto|trabajo|papel)|aprob[eé]|"
       r"me\s+ascendieron|gan[eé]\s+(?:el|la)|termin[eé]\s+(?:el|la|mi)|"
       r"por\s+fin\s+(?:lo|me)|me\s+salio\s+bien|sali[oó]\s+perfecto|"
       r"estoy\s+orgullos\w*|me\s+gradu[eé])\b",
       Emotion.PRIDE, 0.9, "logro conseguido"),

    # ---------------------------------------------------------------------- ALIVIO
    _d(r"\b(?:que\s+alivio|por\s+fin\s+se\s+acabo|menos\s+mal|"
       r"me\s+quite\s+un\s+peso|ya\s+paso|respiro\s+tranquil\w*|"
       r"al\s+final\s+(?:todo\s+)?salio\s+bien)\b",
       Emotion.RELIEF, 0.85, "expresa alivio"),

    # --------------------------------------------------------------------- CARIÑO
    _d(r"\b(?:te\s+quiero|te\s+adoro|me\s+importas|gracias\s+por\s+estar|"
       r"me\s+alegra\s+tenerte|eres\s+important\w*\s+para\s+mi)\b",
       Emotion.AFFECTION, 0.9, "expresa cariño"),
)


#: Emojis con carga afectiva clara. Peso bajo: acompañan, no deciden.
EMOJI_HINTS: tuple[tuple[str, Emotion, float], ...] = (
    ("😢", Emotion.SADNESS, 0.5), ("😭", Emotion.SADNESS, 0.55),
    ("😔", Emotion.SADNESS, 0.45), ("💔", Emotion.SADNESS, 0.5),
    ("😞", Emotion.DISAPPOINTMENT, 0.45), ("😩", Emotion.FRUSTRATION, 0.4),
    ("😤", Emotion.ANGER, 0.45), ("😠", Emotion.ANGER, 0.5),
    ("😡", Emotion.ANGER, 0.55), ("😰", Emotion.ANXIETY, 0.5),
    ("😨", Emotion.FEAR, 0.5), ("😱", Emotion.FEAR, 0.5),
    ("😖", Emotion.FRUSTRATION, 0.4), ("😅", Emotion.EMBARRASSMENT, 0.3),
    ("😳", Emotion.EMBARRASSMENT, 0.4), ("🥱", Emotion.TIREDNESS, 0.45),
    ("😴", Emotion.TIREDNESS, 0.45), ("😊", Emotion.JOY, 0.4),
    ("😄", Emotion.JOY, 0.45), ("🥳", Emotion.EXCITEMENT, 0.55),
    ("🎉", Emotion.EXCITEMENT, 0.55), ("🤩", Emotion.EXCITEMENT, 0.5),
    ("🥰", Emotion.AFFECTION, 0.5), ("❤️", Emotion.AFFECTION, 0.45),
    ("💕", Emotion.AFFECTION, 0.45), ("🤔", Emotion.CONFUSION, 0.3),
)


#: Palabras que INTENSIFICAN lo que sea que se haya detectado.
RE_INTENSIFICADOR = re.compile(
    r"\b(?:muy|much[oa]s?|super|hiper|mega|tan|tanto|demasiado|"
    r"tremendamente|completamente|totalmente|absolutamente|"
    r"un\s+monton|de\s+verdad|en\s+serio|horriblemente)\b"
)

#: Palabras que ATENÚAN («un poco triste», «algo cansado»).
RE_ATENUADOR = re.compile(
    r"\b(?:un\s+poco|algo|ligeramente|medio|mas\s+o\s+menos|"
    r"no\s+mucho|tampoco\s+tanto|supongo|creo\s+que|quiza|quizas|"
    r"tal\s+vez|puede\s+ser)\b"
)

#: Marcadores de que la persona está describiendo un HECHO, no su emoción.
#: Suben la incertidumbre: hay que inferir, y por tanto conviene preguntar.
RE_SOLO_HECHO = re.compile(
    r"\b(?:al\s+final|resulta\s+que|resulto\s+que|paso\s+que|"
    r"me\s+dijeron\s+que|resulta|total\s+que|bueno,)\b"
)

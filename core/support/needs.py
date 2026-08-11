"""SupportNeed — qué parece querer el usuario DE YUE.

Segunda pregunta del sistema, y probablemente la más importante. Saber que
alguien está triste no dice nada sobre qué hacer: hay quien quiere que lo
escuchen, quien quiere que le ayuden a arreglarlo y quien quiere que lo dejen
en paz un rato. Confundirlos es la forma más rápida de que acompañar se
convierta en molestar.

Distinción crítica de `wants_advice`, y la razón de que sea ternario:

    True   la persona PIDIÓ consejo             → se puede aconsejar
    False  la persona DIJO que no lo quiere     → prohibido aconsejar
    None   no lo sabemos                        → por defecto, NO se aconseja

Que alguien no diga «no me aconsejes» no significa que quiera consejo. Ese
salto lógico es justo lo que hace que un acompañante se vuelva insoportable.

Solo librería estándar. Determinista y offline.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass

from ..affect.models import (
    AffectiveState, Boundary, Emotion, NEGATIVE_EMOTIONS, POSITIVE_EMOTIONS,
)
from ..affect.negation import normalize


class SupportNeed(str, enum.Enum):
    """Modos de acompañamiento que YUE sabe ofrecer."""

    LISTEN = "LISTEN"        # escuchar sin intervenir
    COMFORT = "COMFORT"      # contener, validar, calmar
    ASK = "ASK"              # preguntar con suavidad para entender
    ADVISE = "ADVISE"        # dar una opinión o recomendación
    SOLVE = "SOLVE"          # resolver un problema concreto, paso a paso
    DISTRACT = "DISTRACT"    # cambiar de aire, entretener
    CELEBRATE = "CELEBRATE"  # celebrar un logro
    GIVE_SPACE = "GIVE_SPACE"  # retirarse y no insistir
    SAFETY = "SAFETY"        # contención por riesgo (lo decide seguridad)

    def __str__(self) -> str:  # pragma: no cover
        return self.value


@dataclass(frozen=True)
class SupportIntent:
    """Lo que la persona parece esperar de YUE en este mensaje."""

    primary_need: SupportNeed = SupportNeed.LISTEN
    secondary_need: SupportNeed | None = None
    #: True pidió consejo · False lo rechazó · None no se sabe.
    wants_advice: bool | None = None
    #: ¿La necesidad viene de una petición EXPLÍCITA o se dedujo?
    explicit: bool = False
    confidence: float = 0.4
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "primary_need": str(self.primary_need),
            "secondary_need": str(self.secondary_need) if self.secondary_need else "",
            "wants_advice": self.wants_advice,
            "explicit": self.explicit,
            "confidence": round(self.confidence, 3),
            "evidence": list(self.evidence),
        }


# --------------------------------------------------------------------------
# Patrones de PETICIÓN EXPLÍCITA. Son los que mandan: si la persona lo pide con
# palabras, no hay nada que deducir.
# --------------------------------------------------------------------------
_PIDE_CONSEJO = re.compile(
    r"\b(?:que\s+(?:hago|deberia\s+hacer|harias|me\s+recomiendas|opinas|"
    r"crees\s+que\s+(?:deberia|hago))|"
    r"(?:dame|necesito|quiero)\s+(?:un\s+)?(?:consejo|consejos|tu\s+opinion|"
    r"una\s+recomendacion|ideas)|"
    r"aconsejame|que\s+me\s+aconsejas|como\s+lo\s+ves|"
    r"tu\s+que\s+(?:harias|dirias)|me\s+puedes\s+aconsejar|"
    r"vale\s+la\s+pena|deberia\s+\w+|me\s+conviene)\b"
)

_PIDE_SOLUCION = re.compile(
    r"\b(?:ayudame\s+a\s+(?:resolver|solucionar|arreglar|hacer|escribir|montar|"
    r"terminar|entender|programar|configurar|corregir)|"
    r"(?:como|cómo)\s+(?:puedo\s+|lo\s+|se\s+)?"
    r"(?:hago|arreglo|soluciono|resuelvo|arreglarlo|solucionarlo|resolverlo|"
    r"hacerlo|arreglar\s+esto|solucionar\s+esto)|"
    r"necesito\s+(?:resolver|arreglar|solucionar|terminar)|"
    r"paso\s+a\s+paso|dame\s+los\s+pasos|arreglalo|resuelvelo|"
    r"puedes\s+(?:arreglar|resolver|corregir|hacerlo)|"
    r"echame\s+una\s+mano\s+con)\b"
)

_PIDE_ESCUCHA = re.compile(
    r"\b(?:solo\s+(?:necesitaba|queria|quiero|necesito)\s+(?:contarlo|contarselo|"
    r"contartelo|desahogarme|hablarlo|decirlo|soltarlo|que\s+alguien\s+me\s+"
    r"(?:escuche|oiga|lea))|"
    r"necesito\s+desahogarme|solo\s+escuchame|"
    r"no\s+(?:quiero|busco|necesito)\s+(?:consejos?|soluciones)|"
    r"solo\s+(?:queria|quiero)\s+que\s+(?:me\s+escuches|lo\s+(?:sepas|supieras)|"
    r"lo\s+supieras)|solo\s+(?:queria|quiero)\s+(?:decirtelo|contartelo|comentartelo))\b"
)

_PIDE_ESPACIO = re.compile(
    r"\b(?:dejame\s+(?:solo|sola|en\s+paz|tranquil\w*)|"
    r"necesito\s+(?:estar\s+(?:solo|sola)|espacio|un\s+rato\s+a\s+solas)|"
    r"quiero\s+estar\s+(?:solo|sola)|"
    r"ahora\s+no\s+(?:puedo|quiero)\s+hablar|"
    r"(?:luego|despues)\s+hablamos|hablamos\s+(?:luego|despues|otro\s+dia)|"
    r"dame\s+(?:un\s+rato|espacio))\b"
)

_PIDE_DISTRACCION = re.compile(
    r"\b(?:distraeme|distraerme|entretenme|hazme\s+reir|cuentame\s+(?:algo|un\s+chiste|"
    r"una\s+historia)|hablemos\s+de\s+otra\s+cosa|cambiemos\s+de\s+tema|"
    r"necesito\s+(?:despejarme|distraerme|pensar\s+en\s+otra\s+cosa)|"
    r"ponme\s+(?:musica|algo)|dime\s+algo\s+divertido)\b"
)

_CELEBRA = re.compile(
    r"\b(?:lo\s+(?:consegui|logre|hice)|me\s+aceptaron|me\s+cogieron|"
    r"aprob[eé]|me\s+dieron\s+el\s+(?:puesto|trabajo|papel|si)|"
    r"me\s+ascendieron|gan[eé]|me\s+gradu[eé]|por\s+fin\s+(?:lo|me|termine)|"
    r"termin[eé]\s+(?:el|la|mi)\s+\w+|sali[oó]\s+(?:genial|perfecto|muy\s+bien)|"
    r"me\s+salio\s+bien|me\s+dijeron\s+que\s+si|"
    r"tengo\s+(?:una\s+)?buena\s+noticia|adivina\s+que)\b"
)

_PREGUNTA_INFO = re.compile(
    r"\b(?:que\s+es|quien\s+(?:es|fue)|cuando\s+(?:es|fue)|donde\s+(?:esta|queda)|"
    r"cuanto\s+(?:cuesta|mide|pesa|dura)|explicame|"
    r"sabes\s+(?:si|que|como)|me\s+puedes\s+decir)\b"
)


class NeedDetector:
    """Deduce la intención de apoyo a partir del texto y del estado afectivo.

    Orden de decisión, y el orden ES la política:

      1. Lo que la persona PIDE con palabras (explícito) gana siempre.
      2. Si no pidió nada, se deduce del estado afectivo.
      3. Si la lectura afectiva es dudosa, la respuesta correcta es ASK —
         preguntar, no adivinar en voz alta.
    """

    def detect(self, text: str, affect: AffectiveState | None = None) -> SupportIntent:
        tn = normalize(text)
        affect = affect or AffectiveState()
        ev: list[str] = []

        pide_consejo = bool(_PIDE_CONSEJO.search(tn))
        pide_solucion = bool(_PIDE_SOLUCION.search(tn))
        pide_escucha = bool(_PIDE_ESCUCHA.search(tn))
        pide_espacio = bool(_PIDE_ESPACIO.search(tn)) or affect.has_boundary(Boundary.WANTS_SPACE)
        pide_distraccion = bool(_PIDE_DISTRACCION.search(tn))
        celebra = bool(_CELEBRA.search(tn))

        # --- wants_advice: ternario, y el False es sagrado -------------------
        if affect.has_boundary(Boundary.NO_ADVICE) or pide_escucha:
            wants_advice: bool | None = False
            ev.append("rechazó consejos explícitamente")
        elif pide_consejo or pide_solucion:
            wants_advice = True
            ev.append("pidió consejo o solución")
        else:
            wants_advice = None  # NO saberlo es un resultado válido

        # --- 1) Peticiones explícitas ---------------------------------------
        # GIVE_SPACE va primero: si alguien pide que lo dejen, cualquier otra
        # lectura sobra. Es el límite más fácil de pisar y el que más duele.
        if pide_espacio:
            return SupportIntent(
                SupportNeed.GIVE_SPACE, None, wants_advice, True, 0.90,
                tuple(ev + ["pidió espacio"]))

        if pide_escucha:
            # Escuchar y consolar suelen ir juntos cuando además hay malestar.
            secundaria = SupportNeed.COMFORT if affect.is_negative else None
            return SupportIntent(
                SupportNeed.LISTEN, secundaria, False, True, 0.88,
                tuple(ev + ["pidió solo ser escuchado"]))

        if pide_distraccion:
            return SupportIntent(
                SupportNeed.DISTRACT,
                SupportNeed.COMFORT if affect.is_negative else None,
                wants_advice, True, 0.85, tuple(ev + ["pidió distracción"]))

        if pide_solucion:
            # Alguien puede querer que le resuelvan algo Y estar fatal por ello.
            secundaria = SupportNeed.COMFORT if affect.distress >= 0.45 else None
            return SupportIntent(
                SupportNeed.SOLVE, secundaria, True, True, 0.88,
                tuple(ev + ["pidió resolver algo"]))

        if pide_consejo:
            secundaria = SupportNeed.COMFORT if affect.distress >= 0.45 else None
            return SupportIntent(
                SupportNeed.ADVISE, secundaria, True, True, 0.85,
                tuple(ev + ["pidió consejo"]))

        if celebra or (affect.primary_emotion in (Emotion.PRIDE, Emotion.EXCITEMENT)
                       and affect.valence >= 0.4 and affect.sarcasm_probability < 0.4):
            return SupportIntent(
                SupportNeed.CELEBRATE, None, wants_advice, celebra, 0.85,
                tuple(ev + ["comparte un logro"]))

        # --- 2) Sin petición explícita: se deduce del afecto ------------------
        # Aquí entra la honestidad del sistema: si no sabemos cómo está, la
        # respuesta correcta NO es consolar por si acaso, es preguntar.
        if affect.confidence < 0.45 or affect.uncertainty >= 0.65:
            if affect.is_negative or affect.negated_emotions:
                return SupportIntent(
                    SupportNeed.ASK, SupportNeed.LISTEN, wants_advice, False, 0.55,
                    tuple(ev + ["señales ambiguas: mejor preguntar"]))
            return SupportIntent(
                SupportNeed.LISTEN, None, wants_advice, False, 0.40,
                tuple(ev + ["sin señal clara"]))

        if affect.distress >= 0.55:
            # Dolor claro y nadie pidió soluciones: contener primero.
            return SupportIntent(
                SupportNeed.COMFORT, SupportNeed.LISTEN, wants_advice, False, 0.75,
                tuple(ev + ["malestar claro sin petición de soluciones"]))

        if affect.primary_emotion == Emotion.CONFUSION:
            return SupportIntent(
                SupportNeed.ASK, SupportNeed.SOLVE, wants_advice, False, 0.65,
                tuple(ev + ["desconcierto: conviene aclarar"]))

        if affect.is_negative:
            return SupportIntent(
                SupportNeed.LISTEN, SupportNeed.COMFORT, wants_advice, False, 0.65,
                tuple(ev + ["malestar leve: escuchar"]))

        if affect.is_positive:
            return SupportIntent(
                SupportNeed.CELEBRATE, None, wants_advice, False, 0.60,
                tuple(ev + ["estado positivo"]))

        if _PREGUNTA_INFO.search(tn) or tn.endswith("?"):
            return SupportIntent(
                SupportNeed.ADVISE if wants_advice else SupportNeed.LISTEN,
                None, wants_advice, False, 0.50,
                tuple(ev + ["pregunta informativa"]))

        return SupportIntent(SupportNeed.LISTEN, None, wants_advice, False, 0.45,
                             tuple(ev + ["conversación normal"]))


#: Instancia compartida (sin estado interno).
DEFAULT_DETECTOR = NeedDetector()


def detect(text: str, affect: AffectiveState | None = None) -> SupportIntent:
    """Atajo funcional."""
    return DEFAULT_DETECTOR.detect(text, affect)

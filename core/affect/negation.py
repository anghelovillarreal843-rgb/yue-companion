"""Análisis de ALCANCE de negación en español.

El problema que resuelve: buscar la palabra «no» en un mensaje no sirve de nada.
En «no estoy triste, solo necesito estar solo» la negación afecta a *triste*
pero NO a *solo*; en «no estoy enojado, solo cansado» afecta a *enojado* pero
*cansado* sigue siendo verdad; y en «ya no quiero desaparecer» la negación
invierte por completo una frase que, tal cual, dispararía una alerta.

Estrategia: se localiza cada negador y se calcula su ALCANCE (span de caracteres)
hasta el primer CORTE. Un corte es puntuación fuerte, una conjunción adversativa
(«pero», «sino», «aunque») o un marcador de contraste («solo», «solamente»,
«simplemente», «nada más»), que en español suele introducir lo que SÍ pasa.

Trabaja sobre texto NORMALIZADO (minúsculas, sin tildes) y devuelve offsets
sobre ese mismo texto, para que quien busca palabras emocionales pueda preguntar
directamente «¿esta coincidencia cae dentro de un alcance negado?».

Solo librería estándar. 100% offline y determinista.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def normalize(text: str) -> str:
    """Minúsculas, sin tildes y con espacios colapsados.

    Se conserva la puntuación porque el alcance de la negación depende de ella.
    La longitud puede cambiar respecto al original, por eso TODO el módulo
    trabaja siempre sobre la cadena normalizada.
    """
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[ \t]+", " ", text).strip()


#: Negadores del español. «ya no» y «tampoco» van primero para que ganen a «no».
_NEGADORES = [
    r"\bya\s+no\b",
    r"\bnunca\s+mas\b",
    r"\btampoco\b",
    r"\bnunca\b",
    r"\bjamas\b",
    r"\bnada\s+de\b",
    r"\bni\s+(?:un|una|de)?\s*\b",
    r"\bsin\b",
    r"\bno\b",
]
_RE_NEGADORES = re.compile("|".join(_NEGADORES))

#: Cortes que CIERRAN el alcance de una negación.
#: - Puntuación fuerte: fin de idea.
#: - Adversativas: «pero», «sino», «aunque» introducen el contraste.
#: - Marcadores de contraste: en «no estoy triste, SOLO cansado», «solo» abre
#:   lo que sí es cierto; todo lo que viene después ya no está negado.
_RE_CORTE = re.compile(
    r"[.;:!?\n]"
    r"|\bpero\b|\bsino\b|\baunque\b|\bmas\s+bien\b|\ben\s+realidad\b"
    r"|\bsolo\b|\bsolamente\b|\bsimplemente\b|\bunicamente\b|\bnada\s+mas\b"
    r"|\bes\s+que\b|\bes\s+solo\b"
)

#: Coma seguida de marcador de contraste: la coma sola NO corta (en «no estoy
#: triste, cansado» la negación se extiende), pero «, solo» sí. Ese caso ya lo
#: cubre _RE_CORTE con «solo»; la coma suelta se trata como corte DÉBIL: cierra
#: el alcance salvo que lo siguiente sea claramente parte de la misma negación.
_RE_COMA = re.compile(r",")

#: Cuántos caracteres, como máximo, puede abarcar una negación sin encontrar
#: corte. Evita que un «no» al principio "negue" un párrafo entero.
_MAX_ALCANCE = 60


@dataclass(frozen=True)
class NegationScope:
    """Un tramo negado del texto normalizado."""
    negator: str
    start: int       # primer carácter DESPUÉS del negador
    end: int         # exclusivo
    text: str        # el propio tramo, útil para depurar y para los tests
    neg_start: int = 0  # dónde empieza el propio negador (para poder quitarlo)


def find_scopes(text_norm: str) -> list[NegationScope]:
    """Devuelve todos los tramos negados del texto YA normalizado.

    Ejemplo
    -------
    >>> [s.text for s in find_scopes("no estoy triste, solo necesito estar solo")]
    ['estoy triste']
    """
    if not text_norm:
        return []

    scopes: list[NegationScope] = []
    for m in _RE_NEGADORES.finditer(text_norm):
        inicio = m.end()
        # Si este negador cae DENTRO de un alcance ya abierto («no quiero ni
        # hablar»), no abrimos otro: sería contar dos veces la misma negación.
        if any(s.start <= m.start() < s.end for s in scopes):
            continue
        limite = min(len(text_norm), inicio + _MAX_ALCANCE)
        trozo = text_norm[inicio:limite]

        corte = _RE_CORTE.search(trozo)
        fin_rel = corte.start() if corte else len(trozo)

        # La coma corta también, pero solo si hay algo después: así
        # «no estoy triste,» no pierde su alcance por la coma final.
        coma = _RE_COMA.search(trozo[:fin_rel])
        if coma is not None and coma.start() > 0:
            fin_rel = coma.start()

        fin = inicio + fin_rel
        if fin <= inicio:
            continue
        scopes.append(NegationScope(
            negator=m.group(0).strip(),
            start=inicio,
            end=fin,
            text=text_norm[inicio:fin].strip(),
            neg_start=m.start(),
        ))
    return scopes


def is_negated(text_norm: str, start: int, end: int,
               scopes: list[NegationScope] | None = None) -> bool:
    """¿La coincidencia [start, end) cae dentro de algún alcance negado?"""
    scopes = find_scopes(text_norm) if scopes is None else scopes
    centro = (start + end) // 2
    return any(s.start <= centro < s.end for s in scopes)


def negated_text(text_norm: str, scopes: list[NegationScope] | None = None) -> str:
    """Concatena lo negado. Cómodo para buscar emociones negadas de una pasada."""
    scopes = find_scopes(text_norm) if scopes is None else scopes
    return " | ".join(s.text for s in scopes)


def affirmed_text(text_norm: str, scopes: list[NegationScope] | None = None) -> str:
    """El texto SIN los tramos negados: lo que la persona sí está afirmando.

    En «no estoy enojado, solo cansado» devuelve «... solo cansado», que es
    exactamente donde hay que buscar la emoción real.
    """
    scopes = find_scopes(text_norm) if scopes is None else scopes
    if not scopes:
        return text_norm
    partes: list[str] = []
    cursor = 0
    for s in sorted(scopes, key=lambda x: x.start):
        # Se descarta también el propio negador: si dejáramos el «no» suelto,
        # el buscador de emociones podría volver a tropezar con él.
        arranque = min(s.neg_start, s.start)
        if arranque > cursor:
            partes.append(text_norm[cursor:arranque])
        cursor = max(cursor, s.end)
    if cursor < len(text_norm):
        partes.append(text_norm[cursor:])
    return " ".join(p.strip() for p in partes if p.strip())


#: Frases donde la negación INVIERTE una señal de riesgo: «ya no quiero
#: desaparecer», «no me quiero morir». Se detectan aparte porque su lectura
#: correcta no es "sin riesgo" a secas, sino "mejoría / factor protector".
_RE_RIESGO_NEGADO = re.compile(
    r"\b(?:ya\s+no|no|nunca|jamas)\b[^.;!?\n]{0,40}?"
    r"\b(?:quiero|querer|pienso|pensar|ganas\s+de)\b[^.;!?\n]{0,25}?"
    r"\b(?:morir|morirme|desaparecer|matarme|suicidarme|acabar\s+con\s+todo|"
    r"hacerme\s+dano|quitarme\s+la\s+vida)\b"
)


def risk_phrase_is_negated(text: str) -> bool:
    """True si una frase de riesgo aparece EXPLÍCITAMENTE negada.

    Solo se usa para BAJAR el ruido, nunca para desactivar la seguridad: quien
    llama a esto debe seguir tratando el mensaje con cuidado (la persona está
    hablando del tema), pero sin dispararle una alerta crítica encima.
    """
    return bool(_RE_RIESGO_NEGADO.search(normalize(text)))

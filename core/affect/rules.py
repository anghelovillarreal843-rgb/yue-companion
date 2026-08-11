"""FastAffectiveRules — lectura afectiva LOCAL, rápida y determinista.

Resuelve sin red, sin API y en microsegundos la mayoría de los mensajes reales.
Solo cuando queda ambiguo cede el turno al intérprete semántico (ver
`interpreter.py`); esa es toda la estrategia híbrida.

Orden de trabajo, y el orden importa:

  1. Normalizar y calcular el ALCANCE de las negaciones.
  2. Buscar el léxico SOLO en lo afirmado; lo que cae en zona negada no puntúa
     como emoción sentida, sino que se apunta en `negated_emotions`.
  3. Medir el sarcasmo por contradicción.
  4. Si el sarcasmo es alto, INVERTIR la lectura (lo positivo era disfraz).
  5. Detectar límites explícitos.
  6. Ajustar valencia/activación con intensificadores, atenuadores y puntuación.
  7. Calcular confianza e incertidumbre con honestidad: un indicio no vale lo
     mismo que una declaración.

Lo que este módulo NUNCA hace: inventar una causa que no esté en el texto ni
afirmar con confianza alta algo que dedujo de una sola pista débil.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import negation, sarcasm as sarcasm_mod
from .lexicon import (
    EMOJI_HINTS, LEXICON, RE_ATENUADOR, RE_INTENSIFICADOR, RE_SOLO_HECHO,
)
from .models import (
    AffectiveState, Boundary, Emotion, NEGATIVE_EMOTIONS, clamp, vad_for,
)

# --------------------------------------------------------------------------
# Límites explícitos. Solo cuentan si la persona los DICE; jamás se intuyen.
# --------------------------------------------------------------------------
_BOUNDARY_PATTERNS: tuple[tuple[re.Pattern, Boundary, str], ...] = (
    (re.compile(
        r"\b(?:no\s+(?:quiero|necesito|busco|me\s+des|me\s+diste)\s+"
        r"(?:consejos?|que\s+me\s+aconsejes|soluciones|que\s+lo\s+arregles|"
        r"que\s+me\s+digas\s+que\s+hacer|sermones|lecciones)|"
        r"no\s+me\s+(?:des|aconsejes|sermonees)|"
        r"no\s+hace\s+falta\s+que\s+me\s+aconsejes|"
        r"no\s+busco\s+soluciones|"
        r"solo\s+(?:necesitaba|queria|quiero)\s+(?:contarlo|contarselo|contartelo|"
        r"desahogarme|hablarlo|que\s+alguien\s+me\s+(?:escuche|oiga))|"
        r"solo\s+(?:necesito|quiero)\s+que\s+me\s+escuches)\b"),
     Boundary.NO_ADVICE, "pide que no le aconsejen"),

    (re.compile(
        r"\b(?:dejame\s+(?:solo|sola|en\s+paz|tranquil\w*)|"
        r"quiero\s+estar\s+(?:solo|sola)|necesito\s+estar\s+(?:solo|sola)|"
        r"necesito\s+(?:espacio|un\s+rato\s+a\s+solas)|"
        r"ahora\s+no\s+(?:puedo|quiero)\s+hablar|"
        r"luego\s+hablamos|despues\s+hablamos|hablamos\s+luego|"
        r"dame\s+(?:un\s+rato|espacio|un\s+momento))\b"),
     Boundary.WANTS_SPACE, "pide espacio"),

    (re.compile(
        r"\b(?:no\s+quiero\s+hablar\s+(?:de\s+(?:eso|ello|el\s+tema)|del\s+tema)|"
        r"prefiero\s+no\s+hablar|no\s+me\s+preguntes\s+(?:mas\s+)?(?:de|por)\s+eso|"
        r"cambiemos\s+de\s+tema|no\s+quiero\s+entrar\s+en\s+eso)\b"),
     Boundary.DOES_NOT_WANT_TO_TALK, "no quiere hablar del tema"),

    (re.compile(
        r"\b(?:deja\s+de\s+(?:preguntar|preguntarme)|no\s+me\s+(?:hagas\s+mas\s+)?"
        r"pregunt\w*|basta\s+de\s+preguntas|no\s+es\s+un\s+interrogatorio)\b"),
     Boundary.NO_QUESTIONS, "pide que no le pregunten"),

    (re.compile(
        r"\b(?:no\s+quiero\s+(?:pena|lastima)|no\s+me\s+tengas\s+(?:pena|lastima)|"
        r"no\s+necesito\s+que\s+me\s+compadezcas|sin\s+dramas|no\s+dramatices)\b"),
     Boundary.NO_PITY, "no quiere lástima"),
)


@dataclass
class _Hit:
    emotion: Emotion
    weight: float
    kind: str
    label: str
    negated: bool


class FastAffectiveRules:
    """Analizador afectivo local. Sin estado: se puede compartir e instanciar libremente."""

    def analyze(self, text: str, *, previous_valence: float | None = None) -> AffectiveState:
        original = text or ""
        tn = negation.normalize(original)
        if not tn:
            return AffectiveState(confidence=0.0, uncertainty=1.0,
                                  evidence=("mensaje vacío",))

        scopes = negation.find_scopes(tn)
        hits = self._buscar_lexico(tn, scopes)
        hits += self._buscar_emojis(original)

        senal_sarcasmo = sarcasm_mod.detect(original, previous_valence=previous_valence)
        limites = self._buscar_limites(tn)

        afirmados = [h for h in hits if not h.negated]
        negados = [h for h in hits if h.negated]

        # ---- Sarcasmo alto: lo positivo era disfraz ------------------------
        # Se descartan las emociones positivas detectadas y se instala la
        # emoción de fondo que sugiere el propio detector (frustración o
        # decepción). Sin esto, «súper feliz de haber perdido el trabajo»
        # entraría como alegría, que es justo el fallo que había que arreglar.
        invertido = False
        if senal_sarcasmo.probability >= 0.5:
            invertido = True
            afirmados = [h for h in afirmados
                         if h.emotion in NEGATIVE_EMOTIONS or h.emotion == Emotion.NEUTRAL]
            de_fondo = Emotion(senal_sarcasmo.implied)
            afirmados.append(_Hit(de_fondo, 0.85, "declaracion",
                                  "sarcasmo: emoción de fondo", False))

        # ---- Puntuación por emoción ----------------------------------------
        puntos: dict[Emotion, float] = {}
        evidencias: list[str] = []
        for h in afirmados:
            puntos[h.emotion] = puntos.get(h.emotion, 0.0) + h.weight
            if h.label not in evidencias:
                evidencias.append(h.label)

        if invertido:
            for r in senal_sarcasmo.reasons:
                if r not in evidencias:
                    evidencias.append(r)

        emociones_negadas = tuple(dict.fromkeys(h.emotion for h in negados))
        for h in negados:
            evidencias.append(f"niega {h.emotion}")

        # ---- Emoción principal y secundaria --------------------------------
        if not puntos:
            primaria, secundaria, fuerza = Emotion.NEUTRAL, None, 0.0
        else:
            ordenadas = sorted(puntos.items(), key=lambda kv: kv[1], reverse=True)
            primaria, fuerza = ordenadas[0]
            secundaria = ordenadas[1][0] if len(ordenadas) > 1 and ordenadas[1][1] >= 0.35 else None

        # ---- Valencia y activación -----------------------------------------
        valencia, activacion = vad_for(primaria)
        if secundaria is not None:
            v2, a2 = vad_for(secundaria)
            valencia = valencia * 0.72 + v2 * 0.28
            activacion = activacion * 0.72 + a2 * 0.28

        intensificado = bool(RE_INTENSIFICADOR.search(tn))
        atenuado = bool(RE_ATENUADOR.search(tn))
        if intensificado:
            valencia *= 1.18
            activacion += 0.10
        if atenuado:
            valencia *= 0.70
            activacion -= 0.08

        exclamaciones = original.count("!") + original.count("¡")
        if exclamaciones >= 2:
            activacion += 0.15
        if "..." in original or "…" in original:
            activacion -= 0.10

        valencia = clamp(valencia, -1.0, 1.0)
        activacion = clamp(activacion, 0.0, 1.0)

        # ---- Confianza e incertidumbre (aquí está la honestidad del sistema) -
        confianza, incertidumbre = self._confianza(
            afirmados=afirmados, fuerza=fuerza, texto_norm=tn,
            atenuado=atenuado, sarcasmo=senal_sarcasmo.probability,
            hay_negados=bool(negados), primaria=primaria,
        )

        posible_causa = self._posible_causa(tn, primaria, confianza)

        return AffectiveState(
            primary_emotion=primaria,
            secondary_emotion=secundaria,
            valence=valencia,
            arousal=activacion,
            confidence=confianza,
            uncertainty=incertidumbre,
            possible_trigger=posible_causa,
            sarcasm_probability=senal_sarcasmo.probability,
            negated_emotions=emociones_negadas,
            explicit_boundary=limites,
            evidence=tuple(evidencias[:8]),
            source="rules",
        )

    # ------------------------------------------------------------------ interno
    def _buscar_lexico(self, tn: str, scopes) -> list[_Hit]:
        hits: list[_Hit] = []
        for entry in LEXICON:
            for m in entry.pattern.finditer(tn):
                # Los patrones que YA llevan la negación dentro («no vino»,
                # «no me aceptaron») describen un HECHO negativo: la negación es
                # parte del hecho, no lo anula. Por eso son inmunes al alcance.
                negado = (False if entry.self_negated
                          else negation.is_negated(tn, m.start(), m.end(), scopes))
                hits.append(_Hit(entry.emotion, entry.weight, entry.kind,
                                 entry.label, negado))
                break  # una coincidencia por patrón: no premiamos la repetición
        return hits

    def _buscar_emojis(self, original: str) -> list[_Hit]:
        return [_Hit(emo, peso, "indicio", f"emoji {emo}", False)
                for emoji, emo, peso in EMOJI_HINTS if emoji in original]

    def _buscar_limites(self, tn: str) -> tuple[Boundary, ...]:
        encontrados: list[Boundary] = []
        for patron, limite, _label in _BOUNDARY_PATTERNS:
            if patron.search(tn) and limite not in encontrados:
                encontrados.append(limite)
        return tuple(encontrados)

    def _confianza(self, *, afirmados, fuerza, texto_norm, atenuado,
                   sarcasmo, hay_negados, primaria) -> tuple[float, float]:
        """Cuánto se fía YUE de esta lectura y cuánta ambigüedad queda.

        Reglas del pulgar:
          - Una DECLARACIÓN directa da confianza alta.
          - Un INDICIO solo da confianza media: hay que inferir.
          - Un sarcasmo INTERMEDIO (ni claro ni descartado) es lo más ambiguo
            que existe: dispara incertidumbre.
          - Negar sin afirmar nada («no estoy triste» y punto) deja el terreno
            abierto: confianza baja a propósito, para que YUE pregunte.
        """
        if primaria == Emotion.NEUTRAL:
            # Neutral con negaciones = «me dijo lo que NO siente». Ambiguo.
            if hay_negados:
                return 0.35, 0.75
            return 0.45, 0.55

        declaraciones = [h for h in afirmados if h.kind == "declaracion"]
        base = 0.40 + min(0.42, fuerza * 0.30)
        if declaraciones:
            base += 0.15
        else:
            base -= 0.06  # todo son indicios: se infiere, no se lee

        if RE_SOLO_HECHO.search(texto_norm):
            base -= 0.08  # narra un hecho; la emoción es deducción nuestra
        if atenuado:
            base -= 0.05
        if len(texto_norm) < 10:
            base -= 0.08

        incertidumbre = 1.0 - base
        # El sarcasmo dudoso (zona 0.25–0.7) es el peor escenario posible.
        if 0.25 <= sarcasmo < 0.7:
            base -= 0.12
            incertidumbre += 0.20
        elif sarcasmo >= 0.7:
            incertidumbre += 0.05  # sarcasmo claro: sabemos que hay disfraz

        if hay_negados and not declaraciones:
            base -= 0.10
            incertidumbre += 0.12

        return clamp(base, 0.0, 0.95), clamp(incertidumbre, 0.0, 1.0)

    #: Fragmentos que suelen contener LA CAUSA. Se extraen literalmente del
    #: mensaje: nunca se inventa una explicación que la persona no dio.
    _RE_CAUSA = re.compile(
        r"\b(?:porque|por\s+culpa\s+de|debido\s+a|es\s+que|desde\s+que|"
        r"despues\s+de\s+que|cuando)\s+([^.;!?\n]{4,70})"
    )
    _RE_CAUSA_HECHO = re.compile(
        r"((?:no\s+vino\s*\w*|me\s+dejaron?\s+plantad\w*|no\s+me\s+aceptaron|"
        r"me\s+rechazaron|se\s+borr\w*[^.;!?\n]{0,30}|perdi\s+[^.;!?\n]{0,30}|"
        r"me\s+despidieron|reprobe\s*\w*|suspendi\s*\w*|se\s+cancel\w*[^.;!?\n]{0,20}|"
        r"discuti\s+con\s+\w+|me\s+aceptaron|lo\s+consegui|aprobe\s*\w*))"
    )

    def _posible_causa(self, tn: str, primaria: Emotion, confianza: float) -> str:
        """Extrae el posible disparador SOLO si aparece en el texto."""
        if primaria == Emotion.NEUTRAL or confianza < 0.35:
            return ""
        m = self._RE_CAUSA.search(tn)
        if m:
            return m.group(1).strip()[:70]
        m = self._RE_CAUSA_HECHO.search(tn)
        if m:
            return m.group(1).strip()[:70]
        return ""


#: Instancia compartida: el analizador no guarda estado, así que reutilizarlo
#: evita recompilar nada y es seguro entre hilos.
DEFAULT_RULES = FastAffectiveRules()


def analyze(text: str, *, previous_valence: float | None = None) -> AffectiveState:
    """Atajo funcional sobre la instancia compartida."""
    return DEFAULT_RULES.analyze(text, previous_valence=previous_valence)

"""StoryMemory — memoria NARRATIVA de YUE.

Las capas anteriores contestan preguntas distintas:

    facts              → datos estables        («estudia en el IESTP Paiján»)
    goals              → metas                 («quiere terminar YUE»)
    affect_log         → qué siente AHORA
    emotional_episodes → qué le PASÓ           («discutió con Andrea el martes»)
    memoria_larga      → resumen difuso de meses
    memory_ext         → qué dijo hace tiempo que encaja con esto

Ninguna contesta la que convierte un registro en una relación:

    ¿qué HISTORIA seguimos los dos, y por dónde va?

Un episodio es un punto; una historia es la línea que los une. «Discutió con
Andrea» es un punto. «Andrea es alguien importante para él, tuvieron una
discusión que le dolió, quedó sin cerrar y esa historia sigue viva» es una
historia — y es lo que permite que, quince días después, «Andrea volvió a
escribirme» signifique algo.

Diseño
------
* **ADITIVA.** No sustituye ni toca `memoria_larga`, `emotional_episodes`,
  `goals` ni el retrieval histórico. Se suma al final del stack de memoria.
* **Determinista primero.** La vía principal (`observe`) NO usa modelo: detecta
  personas y relaciones con patrones del español y se apoya en lo que la memoria
  episódica YA calculó (importancia, intensidad, `status`, `yue_action`). El LLM
  es una capa de enriquecimiento OPCIONAL en la consolidación, no un requisito.
* **Nunca duplica.** La clave (`person:andrea`) se normaliza sin tildes ni
  mayúsculas y no depende del título que invente un modelo. Antes de crear se
  busca por entidad y por clave.
* **Nunca pisa el pasado.** Una reconciliación AÑADE un acontecimiento y marca
  el anterior como resuelto; no lo borra ni lo reescribe.
* **Conserva la incertidumbre.** Todo lo inferido lleva `confidence`. Lo que él
  DIJO vale más que lo que YUE dedujo, y una inferencia débil nunca asciende a
  verdad por el mero paso del tiempo.
* **Prefiere no recordar a recordar mal.** Sin señal suficiente no se crea nada.
  «Hoy comí pizza» no es una historia.

Todo es best-effort: cualquier excepción se traga y la conversación sigue.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field

try:  # config es opcional: la capa trae sus propios valores por defecto
    import config as _config
except Exception:  # pragma: no cover
    _config = None


# ===========================================================================
# Configuración
# ===========================================================================
def _cfg(nombre: str, defecto):
    if _config is None:
        return defecto
    return getattr(_config, nombre, defecto)


def _debug() -> bool:
    return bool(_cfg("STORY_DEBUG", False))


def _log(mensaje: str) -> None:
    """Log de depuración. Nunca imprime el mensaje completo del usuario."""
    if _debug():
        print("[STORY]", mensaje)


# ===========================================================================
# Normalización
# ===========================================================================
def _norm(texto: str) -> str:
    """Minúsculas y sin tildes. La base de que «Andrés» y «andres» sean el mismo."""
    base = unicodedata.normalize("NFKD", str(texto or "").lower())
    return "".join(c for c in base if not unicodedata.combining(c))


def slugify(texto: str) -> str:
    """Clave estable: sin tildes, sin mayúsculas, sin signos, con guion bajo.

    «María José» → `maria_jose`. Es lo que impide que una diferencia de
    ortografía cree dos historias sobre la misma persona.
    """
    limpio = re.sub(r"[^a-z0-9]+", "_", _norm(texto)).strip("_")
    return limpio[:60]


#: Familia canónica de cada tipo. `relationship` y `person` comparten espacio de
#: claves a propósito: son la MISMA historia vista con más o menos información.
_FAMILIA = {
    "person": "person", "relationship": "person",
    "goal": "goal", "project": "project",
    "life_event": "life_event", "recurring_problem": "problem",
    "personal_growth": "growth", "other": "other",
}

STORY_TYPES = tuple(_FAMILIA.keys())


def story_key_for(story_type: str, subject: str) -> str:
    """Clave estable de una historia. `person:andrea`, `goal:aprender_programacion`."""
    familia = _FAMILIA.get(str(story_type or "other"), "other")
    sujeto = slugify(subject)
    return f"{familia}:{sujeto}" if sujeto else ""


_STOP = frozenset("""
a al algo alguien alguna alguno algunos ante antes aqui asi aun aunque bien cada
como con contra cual cuando de del desde donde dos el ella ellas ellos en entre
era eran es esa ese eso esta estan este esto estos ha hace hacer hasta hay la las
le les lo los mas me mi mientras mucho muy nada ni no nos nuestra o os otra otro
para pero poco por porque que quien se sea segun ser si sin sobre solo son su sus
tan te tiene todo todos tu tus un una uno unos y ya yo mio tuyo estoy tengo voy
muchas ahora luego siempre nunca tambien tampoco cosa cosas vez veces
""".split())


def _tokens(texto: str) -> set:
    palabras = re.findall(r"[a-z0-9ñ]{3,}", _norm(texto))
    return {p for p in palabras if p not in _STOP}


def _solape(a: set, b: set) -> float:
    """Proporción de `a` cubierta por `b`. Asimétrico a propósito: lo que importa
    es cuánto de la historia aparece en el mensaje, no al revés."""
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a))


def _clamp01(valor, defecto: float = 0.0) -> float:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return defecto
    if numero != numero:  # NaN
        return defecto
    return max(0.0, min(1.0, numero))


def _clip(texto, maximo: int) -> str:
    limpio = re.sub(r"\s+", " ", str(texto or "")).strip()
    return limpio[:maximo]


def _mayuscula_inicial(texto: str) -> str:
    """Primera letra en mayúscula SIN tocar el resto.

    `str.capitalize()` aplastaría los nombres propios («andrea») justo en el
    texto que YUE va a leer.
    """
    limpio = str(texto or "").strip()
    return (limpio[0].upper() + limpio[1:]) if limpio else limpio


def _minuscula_si_comun(texto: str) -> str:
    """Baja la inicial SOLO si no es un nombre propio.

    El resumen se engancha detrás de un «hace unas semanas…», así que un «Ya
    hablamos» debe quedar en minúscula; un «Andrea me escribió», no.
    """
    limpio = str(texto or "").strip()
    if not limpio:
        return limpio
    primera = re.match(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", limpio)
    if primera and slugify(primera.group(0)) in _NO_PERSONAS:
        return limpio[0].lower() + limpio[1:]
    return limpio


#: Marca temporal al PRINCIPIO de una frase. Se quita del resumen guardado.
_RE_TEMPORAL_INICIAL = re.compile(
    r"^(?:hoy|ayer|anteayer|antier|anoche|esta\s+(?:mañana|tarde|noche)|"
    r"esta\s+manana|el\s+otro\s+d[ií]a|hace\s+un\s+rato)\s*,?\s*",
    re.IGNORECASE)


# ===========================================================================
# Detección de personas y relaciones (español, SIN modelo)
# ===========================================================================

#: Sustantivos de relación. Solo se usan cuando él los DICE: YUE nunca decide
#: por su cuenta que alguien mencionado es su madre, su pareja o su amiga.
#: Se listan CON y SIN tilde a propósito: los patrones se buscan sobre el texto
#: ORIGINAL (para no guardar «companera» en un recuerdo que YUE va a leer en voz
#: alta), y él puede escribir con tildes o sin ellas.
_RELACIONES = (
    "mejor amiga", "mejor amigo", "media hermana", "medio hermano",
    "compañera de clase", "companera de clase",
    "amiga", "amigo", "novia", "novio", "pareja", "esposa", "esposo",
    "marido", "hermana", "hermano", "madre", "mamá", "mama", "padre",
    "papá", "papa", "prima", "primo", "tía", "tia", "tío", "tio",
    "abuela", "abuelo", "hija", "hijo", "sobrina", "sobrino",
    "cuñada", "cunada", "cuñado", "cunado", "suegra", "suegro",
    "jefa", "jefe", "profesora", "profesor", "maestra", "maestro",
    "compañera", "companera", "compañero", "companero",
    "vecina", "vecino", "colega", "socia", "socio",
    "doctora", "doctor", "entrenadora", "entrenador",
    "psicóloga", "psicologa", "psicólogo", "psicologo",
    "exnovia", "exnovio", "ex", "conocida", "conocido",
)
#: Ordenadas de más larga a más corta para que «mejor amiga» gane a «amiga».
_RE_REL = "|".join(re.escape(r) for r in
                   sorted(_RELACIONES, key=len, reverse=True))
#: Versión normalizada, solo para comparar (nunca para mostrar).
_RELACIONES_NORM = frozenset(_norm(r) for r in _RELACIONES)

#: Verbos que solo tienen sentido entre PERSONAS. Son la señal que permite
#: reconocer a alguien sin que él diga explícitamente quién es.
_VERBOS_PERSONA = (
    "discut", "pele", "habl", "convers", "sali", "qued", "reconcili",
    "disculp", "enoj", "molest", "extran", "vi", "vio", "encontr", "visit",
    "escrib", "llam", "chate", "termin", "romp", "conoc", "invit", "abraz",
    "grit", "menti", "traicion", "ayud", "acompan", "perdon", "reclam",
)
_RE_VERBO = "|".join(_VERBOS_PERSONA)

#: Un nombre propio: empieza en mayúscula. Se busca sobre el texto ORIGINAL.
_RE_NOMBRE = r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,18})"

#: Palabras que empiezan por mayúscula pero NO son personas. Sin esto, «Hoy» y
#: «Ayer» acabarían siendo amigos del usuario.
_NO_PERSONAS = frozenset(_norm(p) for p in """
Hoy Ayer Anteayer Antier Mañana Manana Ahora Luego Después Despues Siempre Nunca
Cuando Aunque Pero Porque Como Que Quien Donde Si No Sí Ya Bueno Bien Mal Nada
Todo Algo Alguien Este Esta Esto Eso Esa Ese Aquel Mi Tu Su Yo Él El Ella Ellos
Nosotros Usted Ustedes Me Te Le Nos Les Lo La Los Las Un Una Unos Unas Del Al De
En Con Por Para Sin Sobre Entre Hasta Desde Hola Buenas Gracias Perdón Perdon
Oye Mira Escucha Dime Creo Pienso Siento Estoy Tengo Quiero Necesito Puedo Voy
Vine Fui Hice Dije Vi Sé Se Hay Habrá Habra Será Sera Es Son Era Eran Fue Está
Esta Están Estan Lunes Martes Miércoles Miercoles Jueves Viernes Sábado Sabado
Domingo Enero Febrero Marzo Abril Mayo Junio Julio Agosto Septiembre Setiembre
Octubre Noviembre Diciembre Navidad Año Ano Dios Internet Google Windows Linux
Python Java Groq Gemini Claude Chatgpt Yue Lumi Perú Peru Lima Trujillo Paiján
Paijan Facebook Whatsapp Instagram Youtube Tiktok Discord Github Universidad
Instituto Colegio Iestp Feliz Triste Vale Listo Lista Ojalá Ojala Igual Casi
Menos Más Mas Muy Tan Tanto Poco Mucho Nadie Ninguno Cualquier Otro Otra Mismo
Misma Verdad Mentira Claro Obvio Exacto Cierto Falso Nuevo Nueva Viejo Vieja
""".split())

#: Marcas de duda. Bajan la confianza y marcan la información como INFERIDA.
_DUDA = (
    r"\bcreo\s+que\b", r"\bme\s+parece\s+que\b", r"\bquiza\b", r"\bquizas\b",
    r"\btal\s+vez\b", r"\bsupongo\b", r"\bcapaz\s+que\b", r"\bno\s+estoy\s+seguro\b",
    r"\bpuede\s+ser\b", r"\bcasi\s+seguro\b", r"\bcreo\b",
)

#: Señales de que algo QUEDÓ CERRADO. Nunca se marca resuelto sin una de estas.
_CIERRE = (
    r"\bya\s+(hable|hablamos|lo\s+hable|lo\s+hablamos)\b",
    r"\bnos\s+(reconciliamos|amistamos|contentamos)\b",
    r"\b(arreglamos|arregle|solucionamos|solucione|resolvimos|resolvi)\b",
    r"\bhicimos\s+las\s+paces\b", r"\bya\s+esta(mos)?\s+bien\b",
    r"\btodo\s+bien\s+(con|entre)\b", r"\bya\s+(se|quedo)\s+(resolvio|arreglo|soluciono)\b",
    r"\bme\s+(pidio|pedi)\s+(disculpas|perdon)\b", r"\bnos\s+perdonamos\b",
    r"\bya\s+paso\b", r"\bquedo\s+en\s+nada\b",
)

#: Señales de RUPTURA/agravamiento: NO cierran, abren un capítulo nuevo.
_CONFLICTO = (
    r"\bdiscut", r"\bpelea", r"\bpelee", r"\bme\s+dolio\b", r"\bme\s+hizo\s+dano\b",
    r"\bme\s+ignor", r"\bme\s+bloque", r"\bme\s+mintio\b", r"\bme\s+traiciono\b",
    r"\bterminamos\b", r"\bme\s+dejo\b", r"\bnos\s+distanciamos\b",
    r"\bestoy\s+molesto\s+con\b", r"\bmolesta\s+con\b", r"\bme\s+grito\b",
)

#: Frases que IMPLICAN un vínculo sin declararlo («Andrea estudia conmigo»).
#: Producen una relación de BAJA confianza y marcada como inferida: es algo que
#: él dijo de refilón, no una declaración. «Creo que Andrea estudia conmigo» no
#: puede convertirse en la certeza de que son compañeras.
_VINCULO_IMPLICITO = (
    (r"\s+(?:estudia|estudiamos)\s+(?:junto[as]?\s+)?con(?:migo)?\b", "compañera de estudios"),
    (r"\s+(?:trabaja|trabajamos)\s+(?:junto[as]?\s+)?con(?:migo)?\b", "compañera de trabajo"),
    (r"\s+(?:vive|vivimos)\s+(?:junto[as]?\s+)?con(?:migo)?\b", "con quien vive"),
    (r"\s+(?:entrena|entrenamos)\s+(?:junto[as]?\s+)?con(?:migo)?\b", "compañera de entrenamiento"),
    (r"\s+va\s+a\s+(?:mi|la\s+misma)\s+(?:clase|universidad|instituto)\b", "compañera de estudios"),
)

#: Metas/proyectos dichos explícitamente.
_PAT_META = (
    r"\b(?:quiero|me\s+gustaria|voy\s+a|estoy)\s+aprend\w*\s+(?:a\s+)?([a-záéíóúñ\s]{3,40})",
    r"\bmi\s+(?:meta|objetivo|sueno|sueño)\s+es\s+([a-záéíóúñ\s]{3,50})",
    r"\bestoy\s+(?:haciendo|construyendo|desarrollando)\s+(?:mi\s+)?(?:proyecto\s+)?([\w\sáéíóúñ]{3,40})",
)


@dataclass(frozen=True)
class Mention:
    """Una persona detectada en un mensaje, con lo que se sabe de ella.

    `explicit` distingue lo que él DIJO («Andrea es mi amiga») de lo que YUE
    dedujo por el contexto («discutí con Andrea»). Esa diferencia es la que
    decide la confianza y la que impide que una suposición se vuelva un hecho.
    """

    name: str
    normalized: str
    relation: str | None = None
    relation_confidence: float = 0.0
    confidence: float = 0.5
    explicit: bool = False
    cue: str = ""


def _hay_duda(texto_norm: str) -> bool:
    return any(re.search(p, texto_norm) for p in _DUDA)


def _conf_base(explicit: bool, duda: bool) -> float:
    """Confianza de partida. Lo dicho pesa más que lo deducido; la duda, menos."""
    if duda:
        return float(_cfg("STORY_CONF_HEDGED", 0.40))
    if explicit:
        return float(_cfg("STORY_CONF_EXPLICIT", 0.90))
    return float(_cfg("STORY_CONF_INFERRED", 0.55))


def detect_people(text: str, *, conocidos=()) -> list[Mention]:
    """Personas mencionadas en el mensaje. Conservador a propósito.

    Un nombre propio SUELTO no basta: hace falta que él diga la relación, que
    haya un verbo que solo ocurre entre personas, o que YUE ya conozca a esa
    persona de antes (`conocidos`). Así «Hoy fui a Trujillo» no crea un amigo
    llamado Trujillo, y «Andrea volvió a escribirme» sí encuentra a Andrea.
    """
    bruto = str(text or "")
    if not bruto.strip():
        return []
    plano = _norm(bruto)
    duda = _hay_duda(plano)
    conocidos = {str(c).lower() for c in (conocidos or ())}
    hallazgos: dict[str, Mention] = {}

    def registrar(nombre: str, *, relation=None, explicit=False, cue=""):
        limpio = _clip(nombre, 40)
        if not limpio:
            return
        clave = slugify(limpio)
        if not clave or clave in _NO_PERSONAS or len(clave) < 3:
            return
        if clave in _STOP:
            return
        # «con mi amiga» no crea una persona llamada Amiga: eso es un rol, no
        # un nombre. Los patrones de relación explícita ya lo tratan aparte.
        if clave.replace("_", " ") in _RELACIONES_NORM:
            return
        rel = str(relation).strip().lower() if relation else None
        conf = _conf_base(explicit, duda)
        rel_conf = conf if rel else 0.0
        previa = hallazgos.get(clave)
        # Dentro del mismo mensaje gana la evidencia más fuerte.
        if previa is not None:
            if previa.explicit and not explicit:
                return
            if previa.relation and not rel:
                rel, rel_conf = previa.relation, previa.relation_confidence
            conf = max(conf, previa.confidence)
        hallazgos[clave] = Mention(
            name=limpio, normalized=clave, relation=rel,
            relation_confidence=_clamp01(rel_conf), confidence=_clamp01(conf),
            explicit=explicit or bool(previa and previa.explicit),
            cue=cue or (previa.cue if previa else ""))

    # --- (1) RELACIÓN EXPLÍCITA. La evidencia más fuerte que existe. --------
    for patron in (
        rf"\bmi\s+({_RE_REL})\s+{_RE_NOMBRE}",                 # mi amiga Andrea
        rf"\b(?:una|un)\s+({_RE_REL})\s+(?:mia|mio)\s+{_RE_NOMBRE}",
    ):
        for m in re.finditer(patron, bruto, re.IGNORECASE):
            registrar(m.group(2), relation=m.group(1), explicit=True,
                      cue="relacion_explicita")
    for patron in (
        rf"{_RE_NOMBRE}\s+es\s+(?:mi|una|un)\s+({_RE_REL})",   # Andrea es mi amiga
        rf"{_RE_NOMBRE}\s*,\s*(?:mi|una|un)\s+({_RE_REL})",    # Andrea, mi amiga
        rf"{_RE_NOMBRE}\s+y\s+yo\s+somos\s+({_RE_REL})s?",     # Andrea y yo somos amigas
    ):
        for m in re.finditer(patron, bruto, re.IGNORECASE):
            registrar(m.group(1), relation=m.group(2), explicit=True,
                      cue="relacion_explicita")

    # --- (2) VERBO INTERPERSONAL. Hay persona, pero NO se infiere la relación.
    for patron in (
        rf"\b(?:{_RE_VERBO})\w*\s+(?:\w+\s+){{0,2}}?con\s+{_RE_NOMBRE}",
        rf"\b(?:{_RE_VERBO})\w*\s+a\s+{_RE_NOMBRE}",
        rf"{_RE_NOMBRE}\s+(?:me|nos|te)\s+(?:{_RE_VERBO})\w*",
        rf"{_RE_NOMBRE}\s+(?:volvio|vuelve|dejo|sigue)\s+a?\s*\w*",
    ):
        for m in re.finditer(patron, bruto, re.IGNORECASE):
            registrar(m.group(1), explicit=False, cue="verbo_interpersonal")

    # --- (2b) VÍNCULO IMPLÍCITO. Relación deducida, con confianza BAJA. -----
    for patron, relacion in _VINCULO_IMPLICITO:
        for m in re.finditer(_RE_NOMBRE + patron, bruto, re.IGNORECASE):
            registrar(m.group(1), relation=relacion, explicit=False,
                      cue="vinculo_implicito")

    # --- (2c) COORDINACIÓN. «Discutí con Andrea Y CON LUCÍA»: la segunda
    #     persona queda fuera del alcance del patrón anterior, pero el mensaje
    #     ya demostró que se está hablando de gente. Solo con «con», que en
    #     español acompaña casi siempre a una persona.
    if re.search(rf"\b(?:{_RE_VERBO})\w*", plano):
        for m in re.finditer(rf"\bcon\s+{_RE_NOMBRE}", bruto, re.IGNORECASE):
            registrar(m.group(1), explicit=False, cue="verbo_interpersonal")

    # --- (3) YA LA CONOCEMOS. Una historia viva reconoce su propio nombre. --
    if conocidos:
        for m in re.finditer(_RE_NOMBRE, bruto):
            clave = slugify(m.group(1))
            if clave in conocidos and clave not in hallazgos:
                registrar(m.group(1), explicit=False, cue="ya_conocida")

    return list(hallazgos.values())


def detect_closure(text: str) -> bool:
    """¿Está contando que algo se ARREGLÓ? Nunca se cierra nada sin esto."""
    plano = _norm(text)
    return any(re.search(p, plano) for p in _CIERRE)


def detect_conflict(text: str) -> bool:
    """¿Está contando un choque o una herida? Abre capítulo, no lo cierra."""
    plano = _norm(text)
    return any(re.search(p, plano) for p in _CONFLICTO)


# ===========================================================================
# Puntuación: significancia emocional y confianza
# ===========================================================================
def compute_significance(*, importance: float = 0.0, intensity: float = 0.0,
                         mention_count: int = 1, span_days: float = 0.0,
                         unresolved: bool = False,
                         goal_related: bool = False) -> float:
    """Cuánto PESA una historia, de 0 a 1.

    No es una etiqueta suelta («alta») sino un número compuesto de señales que
    el sistema ya tiene medidas, no de la opinión de un modelo:

      importancia del episodio    35 %   lo que ya calculó la memoria episódica
      intensidad emocional        20 %   lo que midió `core.affect`
      recurrencia                 20 %   cuántas veces ha vuelto a aparecer
      duración                    10 %   cuánto tiempo lleva viva
      sigue sin cerrar            10 %   lo pendiente pesa más que lo cerrado
      toca una meta suya           5 %

    La etiqueta legible se deriva después con `significance_label`.
    """
    menciones = max(1, int(mention_count or 1))
    recurrencia = 1.0 - 1.0 / (1.0 + 0.5 * (menciones - 1))
    duracion = min(1.0, max(0.0, float(span_days or 0.0)) / 60.0)
    total = (0.35 * _clamp01(importance)
             + 0.20 * _clamp01(intensity)
             + 0.20 * recurrencia
             + 0.10 * duracion
             + 0.10 * (1.0 if unresolved else 0.0)
             + 0.05 * (1.0 if goal_related else 0.0))
    return round(_clamp01(total), 4)


def significance_label(valor: float) -> str:
    """Etiqueta legible SOLO para mostrar. El número sigue siendo la verdad."""
    v = _clamp01(valor)
    if v >= float(_cfg("STORY_SIGNIFICANCE_HIGH", 0.70)):
        return "alta"
    if v >= float(_cfg("STORY_SIGNIFICANCE_MED", 0.40)):
        return "media"
    return "baja"


def reinforce_confidence(actual: float, *, explicit: bool = False) -> float:
    """La confianza sube con la corroboración; nunca salta a certeza de golpe.

    Una inferencia repetida NO se convierte en un hecho: tiene su propio techo
    (`STORY_CONF_INFERRED_CAP`). Solo algo que él diga con claridad puede llevar
    la confianza al techo alto.
    """
    base = _clamp01(actual, 0.5)
    paso = float(_cfg("STORY_CONF_STEP", 0.30))
    techo = float(_cfg("STORY_CONF_CAP", 0.97) if explicit
                  else _cfg("STORY_CONF_INFERRED_CAP", 0.75))
    if base >= techo:
        return round(base, 4)
    return round(min(techo, base + (1.0 - base) * paso), 4)


#: Emoción (taxonomía de core.affect) → palabra en español para el bloque de
#: contexto. Se comparte vocabulario con el resto de la memoria.
_SENTIMIENTO_ES = {
    "joy": "contento", "excitement": "ilusionado", "pride": "orgulloso",
    "relief": "aliviado", "affection": "cariñoso", "sadness": "triste",
    "disappointment": "decepcionado", "loneliness": "solo", "anger": "enojado",
    "frustration": "frustrado", "fear": "asustado", "anxiety": "preocupado",
    "guilt": "culpable", "embarrassment": "avergonzado",
    "confusion": "confundido", "tiredness": "agotado", "hurt": "dolido",
    "neutral": "",
}


def _sentimiento_es(clave: str) -> str:
    return _SENTIMIENTO_ES.get(_norm(clave), "")


#: Las acciones de YUE se guardan en tercera persona («acompañó»), que es como
#: las normaliza la memoria episódica. Pero el bloque de contexto le habla a YUE
#: de tú, así que «Tú acompañó emocionalmente» chirriaría justo en la frase que
#: debería sonar a «esto lo vivimos juntos». Aquí se conjugan.
_ACCION_TU = {
    "escuchó": "lo escuchaste", "escucho": "lo escuchaste",
    "tranquilizó": "lo tranquilizaste", "tranquilizo": "lo tranquilizaste",
    "validó": "validaste lo que sentía", "valido": "validaste lo que sentía",
    "ofreció consejo": "le diste tu opinión",
    "ayudó a planificar": "le ayudaste a planificarlo",
    "practicaron preguntas": "lo practicasteis juntos",
    "explicó algo": "se lo explicaste",
    "celebró el resultado": "lo celebraste con él",
    "acompañó emocionalmente": "lo acompañaste",
    "acompano emocionalmente": "lo acompañaste",
    "le dio espacio": "le diste espacio",
}


def _accion_en_segunda_persona(accion: str) -> str:
    """«acompañó emocionalmente» → «lo acompañaste».

    Si la etiqueta no está en la tabla se usa una frase genérica: es preferible
    un «estuviste ahí» correcto a una concordancia rota.
    """
    limpio = str(accion or "").strip().lower()
    if not limpio:
        return ""
    return _ACCION_TU.get(limpio) or _ACCION_TU.get(_norm(limpio)) or "estuviste ahí"


# ===========================================================================
# La capa
# ===========================================================================
@dataclass
class StoryMemory:
    """Memoria narrativa: crea, fusiona, hace evolucionar y recupera historias.

    `memory` es el `core.memory.Memory` de siempre (persistencia pura). Toda la
    LÓGICA vive aquí, igual que `EpisodicMemory` con los episodios.

    `engine` es OPCIONAL. Sin él todo sigue funcionando con la vía determinista:
    YUE no se queda sin memoria narrativa por estar sin conexión.
    """

    memory: object
    engine: object | None = None
    #: Presupuesto de llamadas al modelo por sesión (control de coste).
    max_calls: int = 0
    _calls: int = field(default=0, init=False, repr=False)
    _nombres: set = field(default_factory=set, init=False, repr=False)
    _nombres_ts: float = field(default=0.0, init=False, repr=False)
    #: (story_id, event_id) de lo último tocado. Lo usa `note_yue_action` para
    #: anotar QUÉ hizo YUE en ese momento de la historia.
    _ultimos: list = field(default_factory=list, init=False, repr=False)

    def __post_init__(self):
        if not self.max_calls:
            try:
                self.max_calls = int(_cfg("STORY_MAX_LLM_CALLS", 4))
            except Exception:
                self.max_calls = 4

    # ------------------------------------------------------------ utilidades
    @property
    def enabled(self) -> bool:
        return bool(_cfg("STORY_MEMORY_ENABLED", True))

    @property
    def llm_available(self) -> bool:
        if self.engine is None or not hasattr(self.engine, "chat"):
            return False
        if not getattr(self.engine, "api_key", None):
            return False
        if not bool(_cfg("STORY_USE_LLM", True)):
            return False
        return self._calls < self.max_calls

    def _known_names(self, now: float | None = None) -> set:
        """Nombres que YUE ya conoce. Cacheados unos segundos: se consultan en
        cada mensaje y la lista cambia muy poco."""
        ahora = time.time() if now is None else now
        if self._nombres and (ahora - self._nombres_ts) < 60.0:
            return self._nombres
        nombres = set()
        try:
            for historia in self.memory.story_candidates(limit=200) or []:
                for entidad in self.memory.get_story_entities(historia["id"]) or []:
                    clave = str(entidad.get("normalized_name") or "")
                    if clave:
                        nombres.add(clave)
        except Exception:
            return self._nombres
        self._nombres, self._nombres_ts = nombres, ahora
        return nombres

    # --------------------------------------------------------------- CRUD
    def create_story(self, *, story_type: str, subject: str, title: str = "",
                     summary: str = "", motivation: str = "",
                     confidence: float = 0.5, now: float | None = None):
        """Crea una historia con clave NORMALIZADA. Si ya existe, la devuelve."""
        clave = story_key_for(story_type, subject)
        if not clave:
            return None
        sid = self.memory.create_story(
            story_type=story_type, story_key=clave,
            title=title or _clip(subject, 120), summary=summary,
            motivation=motivation, confidence=confidence, now=now)
        if sid:
            self._nombres_ts = 0.0  # invalidar caché de nombres conocidos
            _log(f"historia creada {clave} (id={sid})")
        return sid

    def get_story(self, story_id) -> dict | None:
        return self.memory.get_story(story_id)

    def find_story_by_key(self, story_key: str) -> dict | None:
        return self.memory.find_story_by_key(story_key)

    def update_story(self, story_id, **campos) -> bool:
        return self.memory.update_story(story_id, **campos)

    def get_active_stories(self, limit: int = 20, story_type: str | None = None):
        return self.memory.get_active_stories(limit=limit, story_type=story_type)

    def story_with_detail(self, story_id) -> dict | None:
        """La historia con sus acontecimientos y entidades. Para depurar y ver."""
        historia = self.memory.get_story(story_id)
        if not historia:
            return None
        historia = dict(historia)
        historia["events"] = self.memory.get_story_events(story_id, limit=50)
        historia["entities"] = self.memory.get_story_entities(story_id)
        historia["significance_label"] = significance_label(
            historia.get("emotional_significance"))
        return historia

    # ------------------------------------------------------- FUSIÓN (merge)
    def merge_story(self, *, story_type: str, subject: str,
                    relation: str | None = None, relation_confidence: float = 0.0,
                    confidence: float = 0.5, explicit: bool = False,
                    create: bool = True, now: float | None = None):
        """Encuentra la historia de ese sujeto o la crea. NUNCA duplica.

        Tres intentos, de más fiable a menos:
          1. por ENTIDAD normalizada (`andrea` aparece ya en alguna historia),
          2. por CLAVE estable (`person:andrea`),
          3. crear una nueva, si se permite.

        Devuelve `(story_id, creada)`.
        """
        ahora = time.time() if now is None else now
        clave_sujeto = slugify(subject)
        if not clave_sujeto:
            return None, False

        familia = _FAMILIA.get(story_type, "other")
        tipo_entidad = "person" if familia == "person" else familia

        historia = None
        if familia == "person":
            candidatas = self.memory.find_stories_by_entity(
                clave_sujeto, entity_type="person", limit=3) or []
            if candidatas:
                historia = candidatas[0]
        if historia is None:
            historia = self.memory.find_story_by_key(
                story_key_for(story_type, subject))

        creada = False
        if historia is None:
            if not create:
                return None, False
            sid = self.create_story(
                story_type=story_type, subject=subject,
                confidence=confidence, now=ahora)
            if not sid:
                return None, False
            creada = True
        else:
            sid = int(historia["id"])
            # Una historia de persona ASCIENDE a relación cuando él dice qué es
            # suya. Al revés no: no se degrada lo que ya se sabe.
            if (relation and explicit
                    and str(historia.get("story_type")) == "person"):
                self.memory.update_story(sid, story_type="relationship", now=ahora)
            nueva_conf = reinforce_confidence(
                historia.get("confidence"), explicit=explicit)
            self.memory.update_story(sid, confidence=nueva_conf, now=ahora)
            self.memory.touch_story(sid, now=ahora)

        self.memory.upsert_story_entity(
            sid, entity_type=tipo_entidad, name=_clip(subject, 80),
            normalized_name=clave_sujeto, relation=relation,
            relation_confidence=relation_confidence, confidence=confidence,
            now=ahora)
        self._nombres_ts = 0.0
        return sid, creada

    # -------------------------------------------------------------- EVENTOS
    def add_event(self, story_id, *, summary: str, event_type: str = "",
                  user_feeling: str = "", significance: float = 0.0,
                  intensity: float = 0.0, confidence: float = 0.5,
                  unresolved: bool = False, source_type: str = "",
                  source_id=None, source_message_id=None, yue_action: str = "",
                  happened_at: float | None = None, now: float | None = None):
        """Añade un acontecimiento SIN tocar los anteriores. Devuelve su id."""
        eid = self.memory.add_story_event(
            story_id, source_type=source_type, source_id=source_id,
            source_message_id=source_message_id, event_type=event_type,
            summary=_clip(summary, 300), user_feeling=user_feeling,
            significance=significance, intensity=intensity,
            confidence=confidence, unresolved=unresolved,
            yue_action=yue_action, happened_at=happened_at, now=now)
        if eid:
            self._recordar_ultimo(story_id, eid)
        return eid

    def resolve_event(self, event_id, *, yue_action: str = "",
                      now: float | None = None) -> bool:
        """Cierra un acontecimiento. NO lo borra: la historia guarda su pasado."""
        return self.memory.resolve_story_event(
            event_id, yue_action=yue_action or None, now=now)

    def resolve_open_events(self, story_id, *, now: float | None = None) -> int:
        """Cierra lo que quedaba pendiente de esa historia. Devuelve cuántos."""
        cerrados = 0
        for evento in self.memory.get_story_events(
                story_id, limit=50, only_unresolved=True) or []:
            if self.memory.resolve_story_event(evento["id"], now=now):
                cerrados += 1
        return cerrados

    def _recordar_ultimo(self, story_id, event_id) -> None:
        self._ultimos.append((int(story_id), int(event_id)))
        del self._ultimos[:-3]

    def note_yue_action(self, action: str) -> int:
        """Anota QUÉ hizo YUE en los últimos momentos de historia que tocó.

        Es lo que convierte «tengo un registro sobre ti» en «esto lo vivimos
        juntos»: la historia guarda también la parte de YUE.
        """
        etiqueta = _clip(action, 80)
        if not etiqueta or not self.enabled:
            return 0
        hechos = 0
        for _sid, eid in list(self._ultimos):
            try:
                if self.memory.set_story_event_action(eid, etiqueta):
                    hechos += 1
            except Exception:
                pass
        self._ultimos.clear()
        return hechos

    # ------------------------------------------------------- SIGNIFICANCIA
    def refresh_significance(self, story_id, *, now: float | None = None) -> float:
        """Recalcula el peso de la historia con TODA su evidencia acumulada."""
        ahora = time.time() if now is None else now
        try:
            historia = self.memory.get_story(story_id)
            if not historia:
                return 0.0
            eventos = self.memory.get_story_events(story_id, limit=50) or []
            importancia = max([_clamp01(e.get("significance")) for e in eventos]
                              or [0.0])
            intensidad = max([_clamp01(e.get("intensity")) for e in eventos] or [0.0])
            pendiente = any(int(e.get("unresolved") or 0) for e in eventos)
            creada = float(historia.get("created_at") or ahora)
            dias = max(0.0, (ahora - creada) / 86400.0)
            menciones = max(int(historia.get("mention_count") or 1), len(eventos))
            valor = compute_significance(
                importance=importancia, intensity=intensidad,
                mention_count=menciones, span_days=dias, unresolved=pendiente,
                goal_related=str(historia.get("story_type")) in ("goal", "project"))
            self.memory.update_story(
                story_id, emotional_significance=valor, now=ahora)
            return valor
        except Exception as exc:
            _log(f"no pude recalcular la significancia: {exc}")
            return 0.0

    # ================================================================ OBSERVE
    def observe(self, text: str, *, affect=None, episode=None, episode_id=None,
                message_id: int | None = None, safety_level: int = 0,
                now: float | None = None) -> dict:
        """Punto de entrada por mensaje del usuario. VÍA DETERMINISTA, sin LLM.

        Devuelve siempre un dict con `action`
        (`skipped` | `created` | `updated` | `resolved`) y la lista de historias
        tocadas. Nunca lanza: un fallo aquí no puede impedir conversar.
        """
        resultado = {"action": "skipped", "stories": [], "events": [],
                     "reason": ""}
        if not self.enabled:
            resultado["reason"] = "desactivada"
            return resultado
        ahora = time.time() if now is None else now

        try:
            # La seguridad manda, igual que en la memoria episódica: en terreno
            # de riesgo no se construyen historias ni se toma nota de nada.
            if int(safety_level or 0) >= int(_cfg("STORY_SAFETY_BLOCK_LEVEL", 2)):
                resultado["reason"] = "seguridad"
                return resultado

            texto = str(text or "").strip()
            if len(texto) < int(_cfg("STORY_MIN_CHARS", 8)):
                resultado["reason"] = "mensaje demasiado corto"
                return resultado

            ep = self._episodio(episode, episode_id)
            emocion, intensidad = self._affect_fields(affect)
            cierre = detect_closure(texto)
            conflicto = detect_conflict(texto)
            menciones = detect_people(texto, conocidos=self._known_names(ahora))

            # --- Cierre SIN nombre: «ya hablamos y nos reconciliamos». --------
            # Se resuelve por anáfora sobre la historia tocada más recientemente
            # que tenga algo pendiente. Nunca se cierra nada "por si acaso".
            if not menciones:
                if cierre:
                    cerrada = self._cerrar_por_anafora(
                        texto, emocion=emocion, intensidad=intensidad,
                        message_id=message_id, now=ahora)
                    if cerrada:
                        resultado.update(action="resolved", stories=[cerrada])
                        return resultado
                resultado["reason"] = "sin personas ni cierre reconocible"
                return resultado

            creadas, actualizadas, eventos = [], [], []
            for mencion in menciones:
                salida = self._aplicar_mencion(
                    mencion, texto=texto, ep=ep, emocion=emocion,
                    intensidad=intensidad, cierre=cierre, conflicto=conflicto,
                    message_id=message_id, now=ahora)
                if salida is None:
                    continue
                sid, creada, eid = salida
                (creadas if creada else actualizadas).append(sid)
                if eid:
                    eventos.append(eid)

            tocadas = creadas + actualizadas
            if not tocadas:
                resultado["reason"] = "nada suficientemente relevante"
                return resultado
            resultado["stories"] = tocadas
            resultado["events"] = eventos
            if cierre and eventos:
                resultado["action"] = "resolved"
            elif creadas:
                resultado["action"] = "created"
            else:
                resultado["action"] = "updated"
            return resultado
        except Exception as exc:  # pragma: no cover - red de seguridad
            print("[story-memory] fallo observando el mensaje:", exc)
            resultado["reason"] = "excepción"
            return resultado

    def _aplicar_mencion(self, mencion: Mention, *, texto: str, ep: dict | None,
                         emocion: str, intensidad: float, cierre: bool,
                         conflicto: bool, message_id, now: float):
        """Crea/actualiza la historia de una persona y, si toca, añade evento."""
        historia = self._historia_de(mencion)
        if historia is None and not self._merece_historia(
                mencion, ep=ep, conflicto=conflicto, intensidad=intensidad):
            _log(f"descartada por trivial: {mencion.normalized}")
            return None

        sid, creada = self.merge_story(
            story_type="relationship" if mencion.relation else "person",
            subject=mencion.name, relation=mencion.relation,
            relation_confidence=mencion.relation_confidence,
            confidence=mencion.confidence, explicit=mencion.explicit, now=now)
        if not sid:
            return None

        # Trazabilidad: cuando hay episodio, la fuente es el episodio (y NO se
        # duplica su texto); si no, el mensaje que lo originó.
        if ep is not None and ep.get("id"):
            fuente_tipo, fuente_id = "emotional_episode", int(ep["id"])
        else:
            fuente_tipo, fuente_id = "message", message_id

        eid = None
        # --- CIERRE: la historia conserva lo anterior y suma el desenlace. ----
        if cierre:
            self.resolve_open_events(sid, now=now)
            eid = self.add_event(
                sid, summary=self._resumen_evento(texto, ep, cierre=True),
                event_type="resolution",
                user_feeling=_sentimiento_es(emocion) or "aliviado",
                significance=self._importancia(ep, intensidad),
                intensity=intensidad, confidence=mencion.confidence,
                unresolved=False, source_type=fuente_tipo, source_id=fuente_id,
                source_message_id=message_id, now=now)
            self.memory.update_story(
                sid, current_state="parece encarrilado otra vez", now=now)
        elif self._merece_evento(ep=ep, conflicto=conflicto, intensidad=intensidad):
            pendiente = self._sigue_pendiente(ep, conflicto=conflicto)
            eid = self.add_event(
                sid, summary=self._resumen_evento(texto, ep),
                event_type=(str(ep.get("event_type")) if ep else
                            ("conflict" if conflicto else "moment")),
                user_feeling=_sentimiento_es(emocion),
                significance=self._importancia(ep, intensidad),
                intensity=intensidad, confidence=mencion.confidence,
                unresolved=pendiente, source_type=fuente_tipo,
                source_id=fuente_id, source_message_id=message_id,
                yue_action=str((ep or {}).get("yue_action") or ""),
                happened_at=(ep or {}).get("event_at"), now=now)
            if pendiente:
                self.memory.update_story(
                    sid, current_state="quedó algo sin resolver", now=now)

        self.refresh_significance(sid, now=now)
        return sid, creada, eid

    def _cerrar_por_anafora(self, texto: str, *, emocion: str, intensidad: float,
                            message_id, now: float):
        """«Ya hablamos y nos reconciliamos» sin decir con quién.

        Solo se acepta sobre una historia con algo REALMENTE pendiente y tocada
        hace poco. Si no hay candidata clara, no se cierra nada: es mejor
        seguir creyendo que sigue abierto que dar por cerrado lo que no consta.
        """
        dias = float(_cfg("STORY_ANAPHORA_DAYS", 21.0))
        limite = now - max(1.0, dias) * 86400.0
        candidatas = []
        for historia in self.memory.story_candidates(limit=30) or []:
            # Un «ya hablamos y nos reconciliamos» habla de una PERSONA. No se
            # cierra por anáfora una meta ni un proyecto: ahí haría falta que
            # él dijera qué se terminó.
            if _FAMILIA.get(str(historia.get("story_type"))) != "person":
                continue
            if float(historia.get("last_evidence_at") or 0.0) < limite:
                continue
            abiertos = self.memory.get_story_events(
                historia["id"], limit=10, only_unresolved=True)
            if abiertos:
                candidatas.append((float(historia.get("last_evidence_at") or 0.0),
                                   historia))
        if not candidatas:
            return None
        candidatas.sort(key=lambda par: par[0], reverse=True)
        historia = candidatas[0][1]
        sid = int(historia["id"])
        self.resolve_open_events(sid, now=now)
        self.add_event(
            sid, summary=self._resumen_evento(texto, None, cierre=True),
            event_type="resolution",
            user_feeling=_sentimiento_es(emocion) or "aliviado",
            significance=intensidad, intensity=intensidad, confidence=0.70,
            unresolved=False, source_type="message", source_id=message_id,
            source_message_id=message_id, now=now)
        self.memory.update_story(
            sid, current_state="parece encarrilado otra vez", now=now)
        self.refresh_significance(sid, now=now)
        _log(f"cierre por anáfora en la historia {sid}")
        return sid

    # ------------------------------------------------------- criterios
    def _historia_de(self, mencion: Mention) -> dict | None:
        candidatas = self.memory.find_stories_by_entity(
            mencion.normalized, entity_type="person", limit=1) or []
        if candidatas:
            return candidatas[0]
        return self.memory.find_story_by_key(f"person:{mencion.normalized}")

    def _merece_historia(self, mencion: Mention, *, ep, conflicto: bool,
                         intensidad: float) -> bool:
        """¿Esto da para una historia, o es solo una frase?

        Una historia nueva exige AL MENOS una de estas: que él diga qué es esa
        persona para él, que haya un acontecimiento emocional detrás, que se
        haya contado un choque, o que el mensaje venga con carga emocional real.
        «Hoy comí pizza» no cumple ninguna y no crea nada.
        """
        # Que él diga QUÉ es esa persona para él —aunque lo diga con dudas—
        # basta: eso ya es un vínculo con continuidad. La incertidumbre no se
        # tapa descartando el recuerdo, se guarda en `confidence`.
        if mencion.relation:
            return True
        if ep is not None:
            return True
        if conflicto:
            return True
        return intensidad >= float(_cfg("STORY_MIN_INTENSITY", 0.50))

    def _merece_evento(self, *, ep, conflicto: bool, intensidad: float) -> bool:
        """Una mención de paso no es un acontecimiento; solo suma una mención."""
        if ep is not None or conflicto:
            return True
        return intensidad >= float(_cfg("STORY_MIN_INTENSITY", 0.50))

    @staticmethod
    def _sigue_pendiente(ep, *, conflicto: bool) -> bool:
        """El estado ESTRUCTURADO manda sobre cualquier impresión del texto.

        Si la memoria episódica ya sabe que el episodio está `resolved`, aquí no
        se vuelve a abrir por mucho que el mensaje suene a conflicto.
        """
        if ep is not None:
            estado = str(ep.get("status") or "unresolved")
            if estado in ("resolved", "cancelled", "expired"):
                return False
            return estado == "unresolved"
        return bool(conflicto)

    @staticmethod
    def _importancia(ep, intensidad: float) -> float:
        """Importancia del acontecimiento. La del episodio manda si existe."""
        if ep is not None:
            valor = _clamp01(ep.get("importance"))
            if valor > 0:
                return valor
        return _clamp01(intensidad)

    def _resumen_evento(self, texto: str, ep, *, cierre: bool = False) -> str:
        """Frase corta del acontecimiento. Reutiliza lo que ya extrajo el
        episodio antes de recortar el mensaje: no duplicamos análisis."""
        if ep is not None:
            if cierre and ep.get("outcome_summary"):
                return _clip(ep["outcome_summary"], 200)
            partes = [str(ep.get("event_label") or "").strip(),
                      str(ep.get("reason_summary") or "").strip()]
            frase = ": ".join(p for p in partes if p)
            if frase:
                return _clip(frase, 200)
        # Sin episodio, el resumen es el propio mensaje recortado. Se le quita
        # el «hoy»/«ayer» inicial porque el CUÁNDO ya lo pone el bloque de
        # contexto al narrar («hace unos días…»), y si no quedaría un torpe
        # «hace unos días hoy discutí…».
        return _clip(re.sub(_RE_TEMPORAL_INICIAL, "", str(texto or "").strip()), 200)

    def _episodio(self, episode, episode_id) -> dict | None:
        """El episodio emocional asociado, si lo hay. Fuente estructurada."""
        if isinstance(episode, dict) and episode:
            return episode
        if episode_id:
            try:
                return self.memory.get_emotional_episode(episode_id)
            except Exception:
                return None
        return None

    @staticmethod
    def _affect_fields(affect) -> tuple[str, float]:
        """Emoción e intensidad desde `core.affect`. Aquí NO se reanaliza nada."""
        if affect is None:
            return "neutral", 0.0
        emocion = getattr(affect, "primary_emotion", None)
        emocion = str(getattr(emocion, "value", emocion) or "neutral")
        intensidad = getattr(affect, "arousal", None)
        if intensidad is None:
            intensidad = abs(float(getattr(affect, "valence", 0.0) or 0.0))
        return emocion, _clamp01(intensidad)

    # ============================================================ RECUPERACIÓN
    def get_relevant_stories(self, query: str = "", *, limit: int = 2,
                             now: float | None = None) -> list:
        """Las historias que aportan AHORA, ordenadas. Pocas y bien elegidas.

        Regla dura: si el mensaje NO toca la historia (por nombre, por título o
        por palabras del resumen), no entra. La significancia, lo pendiente y
        la recencia solo REORDENAN lo que ya es pertinente; nunca cuelan una
        historia que no viene a cuento. Meter todas las historias en todos los
        prompts es exactamente lo que convierte a una compañera en un archivo.
        """
        ahora = time.time() if now is None else now
        consulta = str(query or "").strip()
        if not consulta or int(limit) <= 0:
            return []
        try:
            candidatas = self.memory.story_candidates(
                limit=int(_cfg("STORY_MAX_CANDIDATES", 60))) or []
        except Exception:
            return []
        if not candidatas:
            return []

        tokens_msg = _tokens(consulta)
        nombres_msg = {slugify(n) for n in re.findall(_RE_NOMBRE, consulta)}
        nombres_msg |= {t for t in tokens_msg}
        minimo = float(_cfg("STORY_CONTEXT_MIN_SCORE", 0.45))

        puntuadas = []
        for historia in candidatas:
            sid = historia["id"]
            entidades = self.memory.get_story_entities(sid) or []

            # (1) COINCIDENCIA. Sin esto, la historia no entra: da igual lo
            #     importante que sea.
            coincide = 0.0
            for entidad in entidades:
                if str(entidad.get("normalized_name") or "") in nombres_msg:
                    coincide = max(coincide, 1.0)
            if coincide < 1.0:
                tokens_hist = (_tokens(historia.get("title") or "")
                               | _tokens(historia.get("summary") or ""))
                coincide = max(coincide, _solape(tokens_hist, tokens_msg))
            if coincide <= 0.0:
                continue

            score = 1.20 * coincide
            # (2) lo que pesa emocionalmente
            score += 0.50 * _clamp01(historia.get("emotional_significance"))
            # (3) lo que quedó pendiente pide más presencia
            try:
                if self.memory.get_story_events(sid, limit=1, only_unresolved=True):
                    score += 0.35
            except Exception:
                pass
            # (4) recencia de la última evidencia
            ultima = float(historia.get("last_evidence_at")
                           or historia.get("updated_at") or 0.0)
            if ultima:
                dias = max(0.0, (ahora - ultima) / 86400.0)
                score += 0.30 if dias <= 7 else (0.15 if dias <= 45 else 0.0)
            # (5) una historia dormida aporta menos que una viva
            if str(historia.get("status")) == "dormant":
                score -= 0.20

            if score >= minimo:
                enriquecida = dict(historia)
                enriquecida["entities"] = entidades
                enriquecida["events"] = self.memory.get_story_events(sid, limit=8)
                enriquecida["score"] = round(score, 4)
                puntuadas.append((score, enriquecida))

        puntuadas.sort(key=lambda par: par[0], reverse=True)
        return [h for _s, h in puntuadas[:max(1, int(limit))]]

    def context_block(self, query: str = "", *, now: float | None = None) -> str:
        """Bloque de HISTORIAS para el system_prompt. "" si no hay nada que decir.

        Se escribe en prosa natural, no como una ficha: es lo que YUE recuerda
        de alguien, no una consulta a una base de datos.
        """
        if not self.enabled:
            return ""
        ahora = time.time() if now is None else now
        try:
            maximo = int(_cfg("STORY_MAX_CONTEXT", 2))
            historias = self.get_relevant_stories(query, limit=maximo, now=ahora)
        except Exception as exc:
            print("[story-memory] no pude recuperar historias:", exc)
            return ""
        if not historias:
            return ""

        lineas = ["- " + self._describe_story(h, ahora) for h in historias]
        return (
            "\n\nHISTORIAS PERSONALES RELEVANTES (hilos de su vida que venís "
            "siguiendo los dos, no datos sueltos):\n"
            + "\n".join(lineas)
            + "\n(Es lo que recuerdas de él, no una ficha. Úsalo SOLO si encaja "
              "con naturalidad en lo que se está hablando; la mayoría de los "
              "mensajes no lo necesitan.\n"
              " · NUNCA digas «según mi base de datos», «mi memoria indica» ni "
              "expliques cómo lo recuerdas.\n"
              " · NUNCA inventes detalles que no estén escritos arriba: ni "
              "nombres, ni parentescos, ni cómo terminó algo. Si no consta que "
              "se resolviera, no des por hecho que se resolvió.\n"
              " · Si algo aparece como poco seguro, trátalo como una impresión y "
              "pregunta con cariño en vez de afirmarlo.)"
        )

    def _describe_story(self, historia: dict, ahora: float) -> str:
        """La historia contada en una o dos frases, como se la contaría alguien.

        Solo dice lo que consta. Lo dudoso se marca como dudoso, y lo que quedó
        abierto NO se presenta como cerrado.
        """
        titulo = str(historia.get("title") or "").strip() or "algo suyo"
        entidades = historia.get("entities") or []
        eventos = historia.get("events") or []
        tipo = str(historia.get("story_type") or "person")
        partes = []

        # Quién es (solo si él lo dijo y con la seguridad que corresponde).
        principal = entidades[0] if entidades else None
        if principal and principal.get("relation"):
            rel = str(principal["relation"])
            seguro = float(principal.get("relation_confidence") or 0.0) >= 0.75
            if seguro:
                partes.append(f"{titulo} es {rel} suya." if rel[-1] == "a"
                              else f"{titulo} es {rel} suyo.")
            else:
                partes.append(f"{titulo} parece ser {rel} suya, aunque no lo "
                              f"tienes del todo claro.")
        elif tipo in ("goal", "project"):
            motivacion = str(historia.get("motivation") or "").strip()
            frase = f"Tiene entre manos «{titulo}»"
            if motivacion:
                frase += f", que hace por {motivacion}"
            progreso = historia.get("progress")
            if progreso is not None:
                frase += f"; va por un {int(_clamp01(progreso) * 100)}% más o menos"
            partes.append(frase + ".")
        else:
            partes.append(f"{titulo} sale de vez en cuando en lo que te cuenta.")

        # Qué pasó (los últimos acontecimientos, del más viejo al más nuevo).
        recientes = eventos[-2:]
        for evento in recientes:
            resumen = str(evento.get("summary") or "").strip()
            if not resumen:
                continue
            cuando = self._cuando(evento.get("happened_at"), ahora)
            sentimiento = str(evento.get("user_feeling") or "").strip()
            frase = f"{cuando} {_minuscula_si_comun(resumen[:140]).rstrip(' .,;')}"
            if sentimiento:
                frase += f", y se quedó {sentimiento}"
            if int(evento.get("unresolved") or 0):
                frase += "; eso todavía NO consta como resuelto"
            partes.append(_mayuscula_inicial(frase.strip().rstrip(".")) + ".")
            accion = _accion_en_segunda_persona(evento.get("yue_action"))
            if accion:
                partes.append(f"Tú {accion} cuando ocurrió.")

        estado = str(historia.get("current_state") or "").strip()
        if estado:
            partes.append(f"Ahora mismo: {estado}.")
        if float(historia.get("confidence") or 1.0) < 0.55:
            partes.append("(De esto no estás muy segura.)")
        return " ".join(partes)

    @staticmethod
    def _cuando(ts, ahora: float) -> str:
        """Cuándo pasó, en lenguaje de persona. Nunca una fecha exacta."""
        try:
            dias = (float(ahora) - float(ts)) / 86400.0
        except (TypeError, ValueError):
            return "en algún momento"
        if dias < 1:
            return "hoy"
        if dias < 2:
            return "ayer"
        if dias < 8:
            return "hace unos días"
        if dias < 35:
            return "hace unas semanas"
        if dias < 200:
            return "hace unos meses"
        return "hace bastante tiempo"

    # ==================================================================== METAS
    def sync_goals(self, *, now: float | None = None) -> int:
        """Da una historia a cada meta ACTIVA del sistema de metas de siempre.

        NO reemplaza `goals`: esa tabla sigue mandando. Aquí solo se abre el
        hilo narrativo para que la meta pueda acumular acontecimientos y, algún
        día, progreso CON EVIDENCIA. `progress` se queda en NULL: preferimos no
        saber a inventar un número.
        """
        if not self.enabled or not bool(_cfg("STORY_GOALS_ENABLED", True)):
            return 0
        ahora = time.time() if now is None else now
        creadas = 0
        try:
            metas = self.memory.list_goals(only_active=True) or []
        except Exception:
            return 0
        for meta in metas[:int(_cfg("STORY_GOALS_MAX", 10))]:
            texto = str(meta.get("text") or "").strip()
            if len(texto) < 4:
                continue
            sid, creada = self.merge_story(
                story_type="goal", subject=texto, confidence=0.90,
                explicit=True, now=ahora)
            if not sid:
                continue
            if creada:
                creadas += 1
                self.memory.update_story(
                    sid, title=_clip(texto, 120), summary=_clip(texto, 300),
                    now=ahora)
            self.refresh_significance(sid, now=ahora)
        if creadas:
            _log(f"metas sincronizadas: {creadas} historia(s) nueva(s)")
        return creadas

    # ============================================================ CONSOLIDACIÓN
    def _paquete_evidencia(self, *, desde: float, hasta: float,
                           now: float) -> str:
        """Lo que se le manda al modelo: EVIDENCIA YA PROCESADA, no un volcado.

        Mandar cientos de mensajes crudos es caro, lento y peor: obliga al
        modelo a redescubrir lo que el sistema ya sabe con certeza (qué episodio
        sigue abierto, qué metas hay, qué historias existen). Aquí llega
        digerido, y el modelo solo aporta lo único que no es determinista:
        entender qué hilo narrativo une las piezas.
        """
        bloques = []

        # (1) Historias que ya existen: sin esto, el modelo duplicaría a Andrea.
        try:
            historias = self.memory.story_candidates(limit=15) or []
        except Exception:
            historias = []
        if historias:
            lineas = []
            for h in historias:
                abiertos = self.memory.get_story_events(
                    h["id"], limit=3, only_unresolved=True) or []
                estado = "con algo pendiente" if abiertos else str(
                    h.get("status") or "active")
                lineas.append(
                    f"- [{h.get('story_key')}] {h.get('title')} "
                    f"({h.get('story_type')}, {estado})")
            bloques.append("HISTORIAS QUE YA EXISTEN (no las dupliques; si algo "
                           "nuevo pertenece a una de ellas, reutiliza su "
                           "story_key):\n" + "\n".join(lineas))

        # (2) Episodios: la fuente estructurada de qué le pasó y cómo va.
        try:
            episodios = self.memory.get_relevant_episodes(
                now=now, recent_days=float(_cfg("STORY_EVIDENCE_DAYS", 30.0)),
                limit=12) or []
        except Exception:
            episodios = []
        if episodios:
            lineas = []
            for ep in episodios:
                lineas.append(
                    f"- #{ep.get('id')} {ep.get('event_label') or ep.get('event_type')}"
                    f" · emoción: {ep.get('emotion')}"
                    f" · estado: {ep.get('status')}"
                    f" · importancia: {round(_clamp01(ep.get('importance')), 2)}"
                    + (f" · causa: {ep.get('reason_summary')}"
                       if ep.get("reason_summary") else "")
                    + (f" · desenlace: {ep.get('outcome_summary')}"
                       if ep.get("outcome_summary") else "")
                    + (f" · tú {ep.get('yue_action')}" if ep.get("yue_action") else ""))
            bloques.append("EPISODIOS EMOCIONALES REGISTRADOS (el campo 'estado' "
                           "es la VERDAD del sistema: no lo contradigas):\n"
                           + "\n".join(lineas))

        # (3) Metas activas.
        try:
            metas = self.memory.list_goals(only_active=True) or []
        except Exception:
            metas = []
        if metas:
            bloques.append("METAS ACTIVAS:\n" + "\n".join(
                f"- {m.get('text')}" for m in metas[:8]))

        # (4) Conversación del periodo, acotada.
        try:
            mensajes = self.memory.messages_between(desde, hasta) or []
        except Exception:
            mensajes = []
        usuario = [m for m in mensajes if m.get("role") == "user"]
        if usuario:
            tope = int(_cfg("STORY_EVIDENCE_MAX_CHARS", 4000))
            lineas, total = [], 0
            for m in reversed(usuario[-120:]):
                contenido = _clip(m.get("content"), 240)
                if len(contenido) < 8:
                    continue
                linea = f"- (#{m.get('id')}) {contenido}"
                total += len(linea)
                if total > tope:
                    break
                lineas.append(linea)
            lineas.reverse()
            if lineas:
                bloques.append("LO QUE ÉL CONTÓ EN ESTE PERIODO (el número entre "
                               "paréntesis es el id del mensaje):\n"
                               + "\n".join(lineas))
        return "\n\n".join(bloques)

    def _consolidar_llm(self, evidencia: str) -> list:
        """Pide al modelo la lectura narrativa. Devuelve [] ante cualquier duda."""
        if not self.llm_available or not evidencia.strip():
            return []
        timeout = float(_cfg("STORY_LLM_TIMEOUT", 25.0))
        try:
            self._calls += 1
            salida = self.engine.chat(
                [{"role": "system", "content": _EXTRACTOR_SYSTEM},
                 {"role": "user", "content": evidencia}], timeout=timeout)
        except Exception as exc:
            _log(f"la extracción narrativa falló: {exc}")
            return []
        return parse_stories(salida)

    def consolidate(self, *, now: float | None = None,
                    force: bool = False) -> dict:
        """Revisa el periodo y hace evolucionar las historias.

        Dos mitades, y la primera NO necesita modelo:
          1. determinista — sincroniza metas y recalcula la significancia de lo
             que ya existe con la evidencia acumulada;
          2. narrativa (opcional) — con motor disponible, pide al LLM que una
             las piezas en historias, y aplica el resultado con reglas estrictas.

        Devuelve un resumen de lo hecho. Nunca lanza.
        """
        informe = {"goals": 0, "created": 0, "updated": 0, "events": 0,
                   "resolved": 0, "skipped": 0, "reason": ""}
        if not self.enabled:
            informe["reason"] = "desactivada"
            return informe
        ahora = time.time() if now is None else now

        try:
            # --- ¿toca? -------------------------------------------------------
            dias = float(_cfg("STORY_CONSOLIDATION_DAYS", 7.0))
            desde = 0.0
            marca = None
            try:
                marca = self.memory.get_state(_ESTADO_KEY)
            except Exception:
                marca = None
            if marca:
                try:
                    desde = float(marca)
                except (TypeError, ValueError):
                    desde = 0.0
                if not force and dias > 0 and (ahora - desde) < dias * 86400.0:
                    informe["reason"] = "aún no toca"
                    return informe

            # --- (1) determinista --------------------------------------------
            informe["goals"] = self.sync_goals(now=ahora)
            for historia in self.memory.story_candidates(limit=60) or []:
                self.refresh_significance(historia["id"], now=ahora)
            self._dormir_historias_viejas(now=ahora)

            # --- (2) narrativa ------------------------------------------------
            if bool(_cfg("STORY_CONSOLIDATION_ENABLED", True)) and self.llm_available:
                evidencia = self._paquete_evidencia(
                    desde=desde, hasta=ahora, now=ahora)
                for propuesta in self._consolidar_llm(evidencia):
                    self._aplicar_propuesta(propuesta, informe, now=ahora)

            try:
                self.memory.set_state(_ESTADO_KEY, ahora)
            except Exception:
                pass
            _log(f"consolidación narrativa: {informe}")
            return informe
        except Exception as exc:  # pragma: no cover
            print("[story-memory] la consolidación narrativa falló:", exc)
            informe["reason"] = "excepción"
            return informe

    def _dormir_historias_viejas(self, *, now: float) -> int:
        """Lo que lleva mucho sin aparecer pasa a `dormant`. No se borra nada:
        una historia dormida sigue ahí si él vuelve a nombrarla."""
        dias = float(_cfg("STORY_DORMANT_DAYS", 120.0))
        if dias <= 0:
            return 0
        limite = now - dias * 86400.0
        dormidas = 0
        try:
            for historia in self.memory.get_active_stories(limit=100) or []:
                if str(historia.get("status")) != "active":
                    continue
                ultima = float(historia.get("last_evidence_at")
                               or historia.get("updated_at") or now)
                if ultima < limite:
                    if self.memory.update_story(
                            historia["id"], status="dormant", now=now):
                        dormidas += 1
        except Exception:
            pass
        return dormidas

    def _aplicar_propuesta(self, propuesta: dict, informe: dict, *,
                           now: float) -> None:
        """Aplica UNA historia propuesta por el modelo, con reglas estrictas.

        El modelo propone; el sistema decide. Aquí es donde se impide que una
        alucinación se convierta en un recuerdo: la significancia se recalcula
        con datos reales, la confianza tiene techo por venir de una inferencia,
        el progreso sin evidencia se queda en NULL y nada se da por resuelto si
        no había constancia de que estuviera abierto.
        """
        tipo = str(propuesta.get("story_type") or "other")
        sujeto = _clip(propuesta.get("subject"), 80)
        if not sujeto or tipo not in STORY_TYPES:
            informe["skipped"] += 1
            return

        conf_llm = min(_clamp01(propuesta.get("confidence"), 0.5),
                       float(_cfg("STORY_LLM_CONF_CAP", 0.80)))
        if conf_llm < float(_cfg("STORY_MIN_CONFIDENCE", 0.45)):
            informe["skipped"] += 1
            return

        existente = None
        clave = propuesta.get("story_key") or story_key_for(tipo, sujeto)
        try:
            existente = self.memory.find_story_by_key(clave)
            if existente is None and _FAMILIA.get(tipo) == "person":
                candidatas = self.memory.find_stories_by_entity(
                    slugify(sujeto), entity_type="person", limit=1) or []
                existente = candidatas[0] if candidatas else None
        except Exception:
            existente = None

        # Una historia NUEVA tiene que valer la pena; una que ya existe se
        # actualiza siempre (ya demostró que importaba).
        if existente is None:
            umbral = float(_cfg("STORY_MIN_SIGNIFICANCE", 0.35))
            if _clamp01(propuesta.get("emotional_significance")) < umbral:
                informe["skipped"] += 1
                return

        sid, creada = self.merge_story(
            story_type=tipo, subject=sujeto,
            relation=propuesta.get("relation"),
            relation_confidence=conf_llm if propuesta.get("relation") else 0.0,
            confidence=conf_llm, explicit=False, now=now)
        if not sid:
            informe["skipped"] += 1
            return
        informe["created" if creada else "updated"] += 1

        campos = {}
        if propuesta.get("title"):
            campos["title"] = _clip(propuesta["title"], 120)
        if propuesta.get("summary"):
            campos["summary"] = _clip(propuesta["summary"], 600)
        if propuesta.get("current_state"):
            campos["current_state"] = _clip(propuesta["current_state"], 200)
        if propuesta.get("motivation"):
            campos["motivation"] = _clip(propuesta["motivation"], 200)
        # PROGRESO: solo con evidencia declarada. Sin ella se queda en NULL, que
        # es información honesta; un número inventado no lo es.
        progreso = propuesta.get("progress")
        if progreso is not None and str(propuesta.get("progress_evidence") or "").strip():
            campos["progress"] = _clamp01(progreso)
        if campos:
            self.memory.update_story(sid, now=now, **campos)

        existentes = self.memory.get_story_events(sid, limit=50) or []
        resumenes = [_tokens(e.get("summary") or "") for e in existentes]

        for evento in propuesta.get("events") or []:
            resumen = _clip(evento.get("summary"), 300)
            if len(resumen) < 6:
                continue
            tokens = _tokens(resumen)
            if any(_solape(tokens, previos) >= 0.75 for previos in resumenes if previos):
                continue  # ya estaba contado
            eid = self.add_event(
                sid, summary=resumen,
                event_type=_clip(evento.get("type"), 40),
                user_feeling=_clip(evento.get("user_feeling"), 40),
                significance=_clamp01(propuesta.get("emotional_significance")),
                intensity=_clamp01(evento.get("intensity")),
                confidence=conf_llm,
                unresolved=bool(evento.get("unresolved")),
                source_type=_clip(evento.get("source_type"), 40) or "consolidation",
                source_id=_entero(evento.get("source_id")),
                source_message_id=_entero(evento.get("source_message_id")),
                now=now)
            if eid:
                informe["events"] += 1
                resumenes.append(tokens)

        # RESOLUCIÓN: solo si el sistema tenía constancia de algo abierto. Un
        # modelo no puede cerrar por su cuenta algo que nunca constó pendiente.
        if propuesta.get("resolves_previous"):
            abiertos = self.memory.get_story_events(
                sid, limit=20, only_unresolved=True) or []
            if abiertos:
                for evento in abiertos:
                    if self.resolve_event(evento["id"], now=now):
                        informe["resolved"] += 1
                if propuesta.get("current_state"):
                    self.memory.update_story(
                        sid, current_state=_clip(propuesta["current_state"], 200),
                        now=now)

        self.refresh_significance(sid, now=now)


# ===========================================================================
# Extracción narrativa con LLM (capa OPCIONAL de enriquecimiento)
# ===========================================================================
_ESTADO_KEY = "ultima_consolidacion_stories"

_EXTRACTOR_SYSTEM = (
    "Eres el módulo de MEMORIA NARRATIVA de una IA compañera. A partir de la "
    "evidencia YA PROCESADA que se te da (historias existentes, episodios "
    "emocionales con su estado real, metas y lo que el usuario contó), "
    "identificas los pocos HILOS de su vida que merecen recordarse y seguirse "
    "en el tiempo.\n"
    "Devuelves SOLO un objeto JSON, sin markdown ni explicaciones:\n"
    '{"stories": [{"story_type": str, "subject": str, "story_key": str|null, '
    '"title": str, "relation": str|null, "summary": str, "current_state": '
    'str|null, "motivation": str|null, "progress": float|null, '
    '"progress_evidence": str|null, "emotional_significance": float, '
    '"confidence": float, "resolves_previous": bool, "events": [{"type": str, '
    '"summary": str, "user_feeling": str|null, "intensity": float, '
    '"unresolved": bool, "source_type": str|null, "source_id": int|null, '
    '"source_message_id": int|null}]}]}\n'
    f"story_type debe ser uno de: {', '.join(STORY_TYPES)}.\n"
    "REGLAS INNEGOCIABLES:\n"
    "- No inventes NADA. Si no hay evidencia, usa null. Es mejor devolver "
    '{"stories": []} que rellenar huecos.\n'
    "- No inventes nombres, parentescos ni desenlaces. NO asumas que alguien "
    "mencionado es familiar, pareja o amigo si no lo dijo.\n"
    "- Si una historia YA EXISTE, reutiliza su story_key exacto en vez de crear "
    "otra. Nunca dupliques a la misma persona.\n"
    "- No confundas dos personas de nombre parecido, ni mezcles acontecimientos "
    "de historias distintas.\n"
    "- El campo 'estado' de los episodios es la verdad del sistema. "
    "resolves_previous=true SOLO si hay evidencia explícita de que algo que "
    "estaba pendiente se resolvió.\n"
    "- progress solo si hay evidencia concreta (hitos, tareas hechas, algo que "
    "él dijo). Si no, progress=null y progress_evidence=null.\n"
    "- Distingue lo DICHO de lo DEDUCIDO: baja la confianza de lo deducido.\n"
    "- Pocas historias y relevantes. Una historia necesita continuidad en el "
    "tiempo, peso emocional, una persona recurrente, una meta, un proyecto o "
    "algo sin resolver. Comer pizza o ver un vídeo gracioso NO son historias.\n"
    "- Rellena source_type/source_id apuntando al episodio ('emotional_episode') "
    "o source_message_id al id del mensaje, cuando puedas."
)

_RE_JSON = re.compile(r"\{.*\}", re.S)


def _entero(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def parse_stories(raw: str) -> list:
    """Convierte la respuesta del modelo en propuestas limpias. Tolerante a ruido.

    Ante cualquier problema devuelve []: no consolidar esta vez es infinitamente
    mejor que grabar una historia inventada.
    """
    texto = (raw or "").strip().replace("```json", " ").replace("```", " ")
    m = _RE_JSON.search(texto)
    if not m:
        return []
    try:
        datos = json.loads(m.group(0))
    except Exception:
        return []
    if not isinstance(datos, dict):
        return []
    crudas = datos.get("stories")
    if not isinstance(crudas, list):
        return []

    tope = int(_cfg("STORY_MAX_PER_CONSOLIDATION", 4))
    limpias = []
    for cruda in crudas[:tope]:
        if not isinstance(cruda, dict):
            continue
        sujeto = _clip(cruda.get("subject") or cruda.get("title"), 80)
        if not sujeto:
            continue
        tipo = _norm(cruda.get("story_type") or "other")
        if tipo not in STORY_TYPES:
            tipo = "other"
        eventos = []
        for evento in (cruda.get("events") or [])[:6]:
            if not isinstance(evento, dict):
                continue
            resumen = _clip(evento.get("summary"), 300)
            if not resumen:
                continue
            eventos.append({
                "type": _clip(evento.get("type"), 40),
                "summary": resumen,
                "user_feeling": _clip(evento.get("user_feeling"), 40),
                "intensity": _clamp01(evento.get("intensity")),
                "unresolved": bool(evento.get("unresolved")),
                "source_type": _clip(evento.get("source_type"), 40),
                "source_id": _entero(evento.get("source_id")),
                "source_message_id": _entero(evento.get("source_message_id")),
            })
        clave = _clip(cruda.get("story_key"), 80).lower() or None
        if clave and ":" not in clave:
            clave = None  # una clave mal formada es peor que ninguna
        limpias.append({
            "story_type": tipo,
            "subject": sujeto,
            "story_key": clave,
            "title": _clip(cruda.get("title") or sujeto, 120),
            "relation": _clip(cruda.get("relation"), 60) or None,
            "summary": _clip(cruda.get("summary"), 600),
            "current_state": _clip(cruda.get("current_state"), 200) or None,
            "motivation": _clip(cruda.get("motivation"), 200) or None,
            "progress": (None if cruda.get("progress") is None
                         else _clamp01(cruda.get("progress"))),
            "progress_evidence": _clip(cruda.get("progress_evidence"), 200) or None,
            "emotional_significance": _clamp01(cruda.get("emotional_significance")),
            "confidence": _clamp01(cruda.get("confidence"), 0.5),
            "resolves_previous": bool(cruda.get("resolves_previous")),
            "events": eventos,
        })
    return limpias


__all__ = [
    "StoryMemory", "Mention", "STORY_TYPES",
    "slugify", "story_key_for", "detect_people", "detect_closure",
    "detect_conflict", "compute_significance", "significance_label",
    "reinforce_confidence", "parse_stories",
]

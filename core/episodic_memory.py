"""EpisodicMemory — memoria EPISÓDICA EMOCIONAL de YUE.

El resto de la memoria de YUE responde a *«¿qué sé del usuario?»*:

    facts        → datos estables      («estudia en el IESTP Paiján»)
    goals        → metas               («quiere terminar YUE»)
    mood_log     → ánimo de fondo      («esta semana ha estado decaído»)
    affect_log   → lectura por mensaje («ahora mismo: anxiety, valence -0.5»)
    memoria_larga→ resumen de meses

Ninguna responde a la pregunta que hace que una amiga se sienta una amiga:

    ¿QUÉ le está pasando, CUÁNDO, POR QUÉ le importa,
    QUÉ hicimos al respecto y CÓMO terminó?

Eso es un EPISODIO. Esta capa es ADITIVA: no sustituye ni interfiere con nada
de lo anterior. `affect_log` sigue contestando «qué siente»; `emotional_episodes`
contesta «qué está viviendo».

Diseño
------
* **Híbrido y barato.** Un filtro local (regex, sin coste ni red) descarta la
  inmensa mayoría de mensajes. Solo si hay señales reales se pide al modelo una
  extracción estructurada — y aun así, si no hay motor o falla, un extractor
  local se encarga. YUE nunca se queda sin memoria episódica por estar offline.
* **Reutiliza el análisis existente.** La emoción, la intensidad, el disparador
  y la confianza salen de `core.affect` / `CompanionResult`. Aquí NO se vuelve a
  analizar el estado emocional: solo el ACONTECIMIENTO.
* **Una sola pregunta.** Tras el seguimiento automático, YUE no vuelve a
  insistir. Un recuerdo que se convierte en interrogatorio deja de ser cariño.
* **La seguridad manda.** Si `core.safety_ext` ve riesgo moderado o superior, no
  se crea episodio ni se programa seguimiento: ese terreno es del sistema de
  seguridad, y un «¿cómo te fue?» automático ahí sería insensible.

Todo es best-effort: cualquier excepción se traga y la conversación sigue.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

try:  # config es opcional: la capa funciona con sus propios valores por defecto
    import config as _config
except Exception:  # pragma: no cover
    _config = None


# ===========================================================================
# Configuración (con valores por defecto propios si no hay config.py)
# ===========================================================================
def _cfg(nombre: str, defecto):
    if _config is None:
        return defecto
    return getattr(_config, nombre, defecto)


def _debug() -> bool:
    return bool(_cfg("EPISODIC_DEBUG", False)) or bool(_cfg("AFFECT_DEBUG", False))


def _log(mensaje: str) -> None:
    """Log de depuración. Nunca imprime el mensaje completo del usuario."""
    if _debug():
        print("[EPISODE]", mensaje)


# ===========================================================================
# Normalización de texto
# ===========================================================================
def _norm(texto: str) -> str:
    """Minúsculas, sin tildes. Para casar sin depender de la ortografía."""
    base = unicodedata.normalize("NFKD", str(texto or "").lower())
    return "".join(c for c in base if not unicodedata.combining(c))


_STOP = frozenset("""
a al algo alguna alguno algunos ante antes aqui asi aun aunque bien cada como con
contra cual cuando de del desde donde dos el ella ellas ellos en entre era eran es
esa ese eso esta estan este esto estos ha hace hacer hasta hay la las le les lo los
mas me mi mientras mucho muy nada ni no nos nuestra o os otra otro para pero poco
por porque que quien se sea segun ser si sin sobre solo son su sus tan te tiene
todo todos tu tus un una uno unos y ya yo mio tuyo estoy tengo voy toque toca
""".split())


def _tokens(texto: str) -> set:
    """Palabras con contenido, normalizadas. Para similitud entre episodios."""
    palabras = re.findall(r"[a-z0-9ñ]{3,}", _norm(texto))
    return {p for p in palabras if p not in _STOP}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / float(len(a | b))


# ===========================================================================
# Señales locales (el filtro barato que evita llamar al modelo por todo)
# ===========================================================================

#: Expresiones temporales. La clave es el patrón; el valor, una etiqueta.
_TEMPORAL = (
    r"\bpasado\s+manana\b", r"\bmanana\b", r"\bhoy\b", r"\bayer\b",
    r"\banteayer\b", r"\bantier\b",
    r"\besta\s+(tarde|noche|manana|semana)\b", r"\beste\s+(finde|fin de semana)\b",
    r"\bel\s+(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b",
    r"\beste\s+(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b",
    r"\bproximo\s+(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b",
    r"\b(la\s+)?(proxima|siguiente)\s+semana\b", r"\bla\s+semana\s+que\s+viene\b",
    r"\b(el\s+)?(proximo|siguiente)\s+mes\b", r"\bel\s+mes\s+que\s+viene\b",
    r"\ben\s+\d{1,2}\s+(dias|semanas|horas)\b",
    r"\bdentro\s+de\s+\d{1,2}\s+(dias|semanas|horas)\b",
    r"\bel\s+\d{1,2}\s+de\s+[a-z]+\b", r"\bel\s+\d{1,2}/\d{1,2}\b",
    r"\ba\s+las\s+\d{1,2}\b",
)

#: Sustantivos de acontecimiento → tipo canónico. Ampliable: el modelo puede
#: reconocer eventos que no estén aquí, esta lista solo abarata el filtro.
_EVENTOS = {
    "job_interview": ("entrevista", "entrevista de trabajo", "postulacion",
                      "postular", "reclutador", "seleccion de personal"),
    "exam": ("examen", "parcial", "final", "prueba", "evaluacion", "test",
             "sustentacion", "defensa", "tesis", "practica calificada"),
    "presentation": ("exposicion", "exponer", "presentacion", "presentar",
                     "ponencia", "feria", "concurso de proyectos", "demo"),
    "meeting": ("reunion", "junta", "asamblea", "meeting", "llamada importante"),
    "medical": ("operacion", "cirugia", "consulta medica", "medico", "doctor",
                "dentista", "analisis", "examenes medicos", "terapia", "psicologo",
                "hospital", "resultados medicos"),
    "travel": ("viaje", "viajar", "vuelo", "avion", "mudanza", "mudarme"),
    "celebration": ("cumpleanos", "boda", "casa", "matrimonio", "graduacion",
                    "ceremonia", "fiesta", "aniversario", "bautizo", "quinceanero"),
    "date": ("cita", "salir con", "primera cita", "quedada"),
    "conversation": ("hablar con", "conversacion", "decirle", "confesarle",
                     "aclarar las cosas", "pedirle perdon", "conversacion importante"),
    "result": ("resultado", "resultados", "notas", "nota final", "me entregan",
               "publican", "respuesta de", "me responden"),
    "delivery": ("entrega", "entregar", "deadline", "fecha limite", "sustentar",
                 "presentar el proyecto", "informe final"),
    "competition": ("competencia", "torneo", "campeonato", "concurso", "partido",
                    "carrera", "maraton", "audicion"),
    "work": ("primer dia", "renuncia", "renunciar", "despido", "ascenso",
             "evaluacion de desempeno", "capacitacion"),
}

#: Patrones de "me va a pasar algo" / "me preocupa algo".
_PATRONES = (
    r"\b(manana|hoy|pasado manana|el \w+)\s+(tengo|tenemos|me toca|voy a|vamos a)\b",
    r"\b(tengo|tenemos)\s+(una|un|mi|el|la)\b",
    r"\bme\s+toca\b", r"\bvoy\s+a\s+(ir|tener|dar|rendir|presentar|hablar|ver)\b",
    r"\bquede\s+en\b", r"\bqueda(mos|ron)?\s+en\b",
    r"\bestoy\s+(nervioso|nerviosa|ansioso|ansiosa|emocionado|emocionada|"
    r"preocupado|preocupada|asustado|asustada|ilusionado|ilusionada)\b",
    r"\bme\s+(preocupa|da\s+miedo|asusta|emociona|ilusiona|estresa)\b",
    r"\btengo\s+miedo\s+de\b", r"\bespero\s+que\b", r"\bojala\b",
    r"\bno\s+se\s+como\s+me\s+(ira|va\s+a\s+ir)\b",
    r"\bestoy\s+cagado\b", r"\bme\s+muero\s+de\s+(nervios|ganas)\b",
)

#: «Acuérdate de esto» explícito: sube muchísimo la importancia.
_RECUERDA = (
    r"\brecuerda(me)?\b", r"\bno\s+(te\s+)?olvides\b", r"\bacuerdate\b",
    r"\bten\s+presente\b", r"\bapunta(lo)?\b", r"\bguarda(lo)?\s+en\s+tu\s+memoria\b",
)

#: Señales de RESULTADO: «ya pasó y esto es lo que ocurrió».
_DESENLACE = (
    r"\bal\s+final\b", r"\bya\s+(paso|fue|termino|acabo)\b",
    r"\bme\s+fue\s+(muy\s+)?(bien|mal|genial|fatal|regular|horrible)\b",
    r"\bsalio\s+(bien|mal|genial|fatal|todo\s+bien)\b",
    r"\b(aprobe|desaprobe|jale|pase|reprobe)\b",
    r"\bsaque\s+\d{1,2}\b", r"\bme\s+pusieron\s+\d{1,2}\b",
    r"\bme\s+(van\s+a\s+)?(contratar|aceptaron|rechazaron|escogieron|eligieron)\b",
    r"\bquede\s+(seleccionado|fuera|dentro)\b",
    r"\bfue\s+(genial|horrible|increible|un\s+desastre|mejor\s+de\s+lo\s+que)\b",
    r"\bya\s+(la|lo)\s+(di|tuve|hice|presente)\b",
    r"\btermino\s+(bien|mal)\b",
)

#: Referencias anafóricas: «esa entrevista», «lo de mañana», «eso que te conté».
_ANAFORA = (
    r"\b(esa|ese|aquella|aquel)\s+\w+", r"\blo\s+de\s+(manana|ayer|hoy|la\s+\w+)\b",
    r"\beso\s+que\s+te\s+(conte|dije)\b", r"\bde\s+lo\s+que\s+(hablamos|te\s+hable)\b",
    r"\bla\s+(anterior|pasada|ultima)\b", r"\bel\s+(anterior|pasado|ultimo)\b",
)

#: Rutinas: cosas que pasan todo el tiempo y NO merecen ser un recuerdo.
_RUTINA = (
    r"\b(todos\s+los\s+dias|siempre|como\s+siempre|de\s+costumbre|"
    r"cada\s+(dia|semana|lunes|martes|miercoles|jueves|viernes))\b",
    r"\b(gimnasio|gym|correr|trotar|almorzar|cenar|desayunar|dormir|"
    r"clases?\s+normales?|comprar|supermercado|mercado|pan|papel\s+higienico|"
    r"lavar|limpiar|ducharme|banarme)\b",
)

#: Marcadores que convierten una rutina en acontecimiento real.
_NOVEDAD = (
    r"\bpor\s+primera\s+vez\b", r"\bdespues\s+de\s+\w+\s+(meses|anos|semanas)\b",
    r"\bvuelvo\s+a\b", r"\bnunca\s+(he|habia)\b", r"\bes\s+la\s+primera\b",
    r"\bdespues\s+de\s+tanto\b",
)

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_DIAS_SEMANA = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4,
    "sabado": 5, "domingo": 6,
}

#: Etiqueta legible por tipo, cuando no se puede extraer del propio mensaje.
_ETIQUETA_TIPO = {
    "job_interview": "una entrevista de trabajo",
    "exam": "un examen",
    "presentation": "una exposición",
    "meeting": "una reunión",
    "medical": "una cita médica",
    "travel": "un viaje",
    "celebration": "una celebración",
    "date": "una cita",
    "conversation": "una conversación importante",
    "result": "unos resultados",
    "delivery": "una entrega",
    "competition": "una competencia",
    "work": "algo del trabajo",
    "other": "algo importante",
}

#: Acciones normalizadas de YUE (qué hizo ella en ese episodio).
_ACCIONES_YUE = (
    "escuchó", "tranquilizó", "validó", "ofreció consejo", "ayudó a planificar",
    "practicaron preguntas", "explicó algo", "celebró el resultado",
    "acompañó emocionalmente", "le dio espacio",
)


# ===========================================================================
# Resultado del filtro local
# ===========================================================================
@dataclass(frozen=True)
class EpisodeSignals:
    """Lo que el filtro LOCAL vio en un mensaje. Sin red y sin coste."""

    temporal: bool = False
    event_type: str = ""
    event_hit: str = ""
    pattern: bool = False
    remember_request: bool = False
    outcome_cue: bool = False
    anaphoric: bool = False
    routine: bool = False
    novelty: bool = False
    prescore: float = 0.0

    @property
    def is_candidate(self) -> bool:
        return self.prescore >= float(_cfg("EPISODIC_CANDIDATE_THRESHOLD", 0.45))

    def to_dict(self) -> dict:
        return {
            "temporal": self.temporal, "event_type": self.event_type,
            "pattern": self.pattern, "remember_request": self.remember_request,
            "outcome_cue": self.outcome_cue, "anaphoric": self.anaphoric,
            "routine": self.routine, "novelty": self.novelty,
            "prescore": round(self.prescore, 2),
        }


def detect_signals(text: str, *, emotional: bool = False) -> EpisodeSignals:
    """Filtro local barato. Decide si MERECE la pena mirar más de cerca.

    `emotional` viene del análisis afectivo que YA se hizo (no se recalcula
    nada aquí): un mensaje con carga emocional pesa más como candidato.
    """
    base = _norm(text)
    if not base.strip():
        return EpisodeSignals()

    temporal = any(re.search(p, base) for p in _TEMPORAL)

    event_type = ""
    event_hit = ""
    for tipo, palabras in _EVENTOS.items():
        for palabra in palabras:
            if re.search(r"\b" + re.escape(_norm(palabra)), base):
                event_type, event_hit = tipo, palabra
                break
        if event_type:
            break

    pattern = any(re.search(p, base) for p in _PATRONES)
    remember = any(re.search(p, base) for p in _RECUERDA)
    outcome = any(re.search(p, base) for p in _DESENLACE)
    anafora = any(re.search(p, base) for p in _ANAFORA)
    rutina = any(re.search(p, base) for p in _RUTINA)
    novedad = any(re.search(p, base) for p in _NOVEDAD)

    score = 0.0
    if event_type:
        score += 0.40
    if temporal:
        score += 0.30
    if pattern:
        score += 0.20
    if emotional:
        score += 0.15
    if outcome:
        score += 0.25
    if anafora:
        score += 0.10
    if remember:
        score += 0.60
    if rutina and not (novedad or event_type):
        # Una rutina pura resta: «mañana voy al gimnasio» no es un recuerdo.
        score -= 0.35
    if novedad:
        score += 0.15

    return EpisodeSignals(
        temporal=temporal, event_type=event_type, event_hit=event_hit,
        pattern=pattern, remember_request=remember, outcome_cue=outcome,
        anaphoric=anafora, routine=rutina, novelty=novedad,
        prescore=max(0.0, min(1.5, score)),
    )


# ===========================================================================
# Resolución de expresiones temporales
# ===========================================================================
def _inicio_dia(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def resolve_event_time(text: str, now: float | None = None) -> tuple[float | None, str]:
    """Convierte «mañana», «el viernes», «a las 10» en (timestamp, precisión).

    Devuelve `(None, "unknown")` si el mensaje no sitúa el acontecimiento en el
    tiempo. NUNCA se inventa una hora: si el usuario no la dijo, la precisión
    queda en «day» y la hora guardada es solo una referencia interna.
    """
    ahora = datetime.fromtimestamp(time.time() if now is None else now)
    base = _norm(text)
    fecha: datetime | None = None
    precision = "unknown"

    # --- día ---------------------------------------------------------------
    if re.search(r"\bpasado\s+manana\b", base):
        fecha, precision = _inicio_dia(ahora + timedelta(days=2)), "day"
    elif re.search(r"\banteayer\b|\bantier\b", base):
        fecha, precision = _inicio_dia(ahora - timedelta(days=2)), "day"
    elif re.search(r"\bmanana\b", base) and not re.search(r"\besta\s+manana\b", base):
        fecha, precision = _inicio_dia(ahora + timedelta(days=1)), "day"
    elif re.search(r"\bayer\b", base):
        fecha, precision = _inicio_dia(ahora - timedelta(days=1)), "day"
    elif re.search(r"\bhoy\b|\besta\s+(tarde|noche|manana)\b", base):
        fecha, precision = _inicio_dia(ahora), "day"

    # «el viernes», «este lunes», «el próximo martes»
    if fecha is None:
        m = re.search(
            r"\b(?:el|este|proximo|siguiente)\s+"
            r"(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b", base)
        if m:
            objetivo = _DIAS_SEMANA[m.group(1)]
            delta = (objetivo - ahora.weekday()) % 7
            if delta == 0 or re.search(r"\b(proximo|siguiente)\b", base):
                delta = delta or 7
            fecha, precision = _inicio_dia(ahora + timedelta(days=delta)), "day"

    # «el 15 de agosto», «el 15/08»
    if fecha is None:
        m = re.search(r"\bel\s+(\d{1,2})\s+de\s+([a-z]+)", base)
        if m and m.group(2) in _MESES:
            dia, mes = int(m.group(1)), _MESES[m.group(2)]
            ano = ahora.year + (1 if (mes, dia) < (ahora.month, ahora.day) else 0)
            try:
                fecha, precision = datetime(ano, mes, dia), "day"
            except ValueError:
                fecha = None
    if fecha is None:
        m = re.search(r"\bel\s+(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", base)
        if m:
            dia, mes = int(m.group(1)), int(m.group(2))
            ano = int(m.group(3) or ahora.year)
            if ano < 100:
                ano += 2000
            try:
                fecha, precision = datetime(ano, mes, dia), "day"
            except ValueError:
                fecha = None

    # «en 3 días», «dentro de 2 semanas»
    if fecha is None:
        m = re.search(r"\b(?:en|dentro\s+de)\s+(\d{1,2})\s+(dias?|semanas?|horas?)\b", base)
        if m:
            n = int(m.group(1))
            unidad = m.group(2)
            if unidad.startswith("hora"):
                fecha, precision = ahora + timedelta(hours=n), "exact"
            elif unidad.startswith("semana"):
                fecha, precision = _inicio_dia(ahora + timedelta(weeks=n)), "week"
            else:
                fecha, precision = _inicio_dia(ahora + timedelta(days=n)), "day"

    # --- semana / mes ------------------------------------------------------
    if fecha is None:
        if re.search(r"\b(la\s+)?(proxima|siguiente)\s+semana\b|"
                     r"\bla\s+semana\s+que\s+viene\b", base):
            fecha, precision = _inicio_dia(ahora + timedelta(days=7)), "week"
        elif re.search(r"\besta\s+semana\b|\beste\s+(finde|fin de semana)\b", base):
            dias = max(1, 6 - ahora.weekday())
            fecha, precision = _inicio_dia(ahora + timedelta(days=dias)), "week"
        elif re.search(r"\b(el\s+)?(proximo|siguiente)\s+mes\b|"
                       r"\bel\s+mes\s+que\s+viene\b", base):
            fecha, precision = _inicio_dia(ahora + timedelta(days=30)), "month"

    if fecha is None:
        return None, "unknown"

    # --- franja del día (referencia interna, NO sube la precisión) ---------
    if re.search(r"\b(esta\s+)?tarde\b", base):
        fecha = fecha.replace(hour=16)
    elif re.search(r"\b(esta\s+)?noche\b", base):
        fecha = fecha.replace(hour=21)

    # --- hora explícita: ESTO sí es precisión exacta -----------------------
    m = re.search(r"\ba\s+las?\s+(\d{1,2})(?::(\d{2}))?\b", base)
    if m:
        hora = int(m.group(1))
        minuto = int(m.group(2) or 0)
        if re.search(r"\b(de\s+la\s+tarde|pm|de\s+la\s+noche)\b", base) and hora < 12:
            hora += 12
        if 0 <= hora <= 23 and 0 <= minuto <= 59:
            fecha = fecha.replace(hour=hora, minute=minuto)
            precision = "exact"

    return fecha.timestamp(), precision


def parse_iso_event_time(valor, now: float | None = None) -> float | None:
    """Convierte lo que devuelva el modelo (`2026-08-10`, `2026-08-10T10:00`)."""
    texto = str(valor or "").strip()
    if not texto or texto.lower() in ("null", "none", "unknown", ""):
        return None
    texto = texto.replace("Z", "").replace(" ", "T")
    for formato in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto[:len(datetime.now().strftime(formato))],
                                     formato).timestamp()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(texto).timestamp()
    except Exception:
        return None


def schedule_follow_up(event_at: float | None, precision: str,
                       now: float | None = None) -> float | None:
    """Cuándo tiene sentido preguntar «¿cómo te fue?».

    Es un campo DISTINTO de `event_at` a propósito: nunca se pregunta antes de
    que el acontecimiento haya terminado razonablemente.

      * hora exacta  → un par de horas después
      * solo el día  → esa misma tarde/noche (EPISODIC_FOLLOWUP_HOUR)
      * semana / mes → al final del periodo, a esa misma hora
      * sin fecha    → nada, salvo que el llamador decida otra cosa
    """
    ahora = time.time() if now is None else now
    if not event_at:
        return None

    hora_tarde = int(_cfg("EPISODIC_FOLLOWUP_HOUR", 19))
    margen = float(_cfg("EPISODIC_FOLLOWUP_AFTER_HOURS", 2.0)) * 3600.0

    if precision == "exact":
        objetivo = event_at + margen
    else:
        dia = datetime.fromtimestamp(event_at)
        if precision == "week":
            dia = dia + timedelta(days=6)
        elif precision == "month":
            dia = dia + timedelta(days=25)
        objetivo = dia.replace(hour=hora_tarde, minute=0, second=0,
                               microsecond=0).timestamp()
        # Si el acontecimiento cae más tarde que la hora de seguimiento (una
        # cena, un concierto), se espera igualmente a que termine.
        objetivo = max(objetivo, event_at + 1.5 * 3600.0)

    # Un evento ya pasado se pregunta pronto, pero nunca "ahora mismo".
    if objetivo <= ahora:
        objetivo = max(objetivo, ahora + 60.0)
    return objetivo


# ===========================================================================
# Importancia
# ===========================================================================
def score_importance(*, has_date: bool, event_type: str, intensity: float,
                     has_reason: bool, should_follow_up: bool,
                     remember_request: bool, future: bool, routine: bool,
                     novelty: bool, mentions: int = 1,
                     goal_related: bool = False) -> float:
    """Importancia local del episodio, entre 0 y 1.

    No se delega al modelo: un LLM tiende a considerarlo todo importante, y esa
    es exactamente la forma de acabar con una base de datos llena de «mañana
    compraré papel higiénico».
    """
    score = 0.0
    if has_date:
        score += 0.20
    if event_type and event_type != "other":
        score += 0.20
    if intensity >= 0.55:
        score += 0.20
    elif intensity >= 0.35:
        score += 0.10
    if has_reason:
        score += 0.15
    if should_follow_up:
        score += 0.15
    if remember_request:
        score += 0.30
    if future:
        score += 0.05
    if goal_related:
        score += 0.10
    if mentions > 1:
        score += min(0.15, 0.05 * (mentions - 1))
    if routine and not novelty:
        score -= 0.35
    if novelty:
        score += 0.10
    return max(0.0, min(1.0, score))


# ===========================================================================
# Extracción estructurada
# ===========================================================================
@dataclass
class EpisodeDraft:
    """Lo que se ha entendido del ACONTECIMIENTO (no de la emoción)."""

    is_episode: bool = False
    event_type: str = "other"
    event_label: str = ""
    event_at: float | None = None
    date_precision: str = "unknown"
    reason_summary: str = ""
    needs_follow_up: bool = False
    confidence: float = 0.5
    is_routine: bool = False
    resolves_previous: bool = False
    outcome_summary: str = ""
    source: str = "local"

    def to_dict(self) -> dict:
        return {
            "is_episode": self.is_episode, "event_type": self.event_type,
            "event_label": self.event_label, "event_at": self.event_at,
            "date_precision": self.date_precision,
            "reason_summary": self.reason_summary,
            "needs_follow_up": self.needs_follow_up,
            "confidence": round(self.confidence, 2),
            "is_routine": self.is_routine,
            "resolves_previous": self.resolves_previous,
            "outcome_summary": self.outcome_summary, "source": self.source,
        }


_TIPOS_VALIDOS = tuple(_EVENTOS.keys()) + ("other",)

_EXTRACTOR_SYSTEM = (
    "Extraes ACONTECIMIENTOS concretos de la vida de una persona a partir de un "
    "mensaje en español. Devuelves SOLO un objeto JSON, sin markdown ni "
    "explicaciones.\n"
    "Campos exactos:\n"
    '{"is_episode": bool, "event_type": str, "event_label": str, '
    '"event_at": str|null, "date_precision": str, "reason_summary": str, '
    '"needs_follow_up": bool, "is_routine": bool, "resolves_previous": bool, '
    '"outcome_summary": str, "confidence": float}\n'
    f"event_type debe ser uno de: {', '.join(_TIPOS_VALIDOS)}. Usa \"other\" si no "
    "encaja en ninguno, pero SÍ es un acontecimiento real.\n"
    "date_precision: exact (se dijo la hora) | day | week | month | unknown.\n"
    "REGLAS:\n"
    "- is_episode=true SOLO si hay un acontecimiento CONCRETO de su vida "
    "(entrevista, examen, operación, boda, viaje, una conversación importante…). "
    "Charla general, dudas técnicas o preguntas NO son episodios.\n"
    "- is_routine=true si es algo que hace habitualmente (ir al gimnasio, "
    "comprar pan, clases normales) SIN nada que lo haga especial esta vez.\n"
    "- event_label: 3-6 palabras en español, en minúsculas, describiendo el "
    "acontecimiento («entrevista de trabajo», «examen de matemáticas»).\n"
    "- event_at en formato ISO (YYYY-MM-DD o YYYY-MM-DDTHH:MM). NO inventes la "
    "hora si no la dijo: deja solo la fecha. null si no se sabe cuándo.\n"
    "- reason_summary: por qué le afecta, EN SUS PROPIOS TÉRMINOS y en menos de "
    "15 palabras («la entrevista anterior salió mal»). Vacío si no lo dice.\n"
    "- resolves_previous=true si está contando CÓMO TERMINÓ algo que ya ocurrió; "
    "en ese caso rellena outcome_summary en menos de 20 palabras.\n"
    "- No inventes nada que el mensaje no diga. Ante la duda, confidence baja."
)

_RE_JSON = re.compile(r"\{.*\}", re.S)


def _clip(texto, maximo: int) -> str:
    limpio = re.sub(r"\s+", " ", str(texto or "")).strip()
    return limpio[:maximo]


def extract_local(text: str, signals: EpisodeSignals,
                  now: float | None = None) -> EpisodeDraft:
    """Extractor SIN modelo. Es el que mantiene viva la memoria offline.

    Menos fino que el LLM, pero suficiente para los casos frecuentes: saca el
    tipo del sustantivo detectado, la fecha del resolutor temporal y la causa de
    una subordinada causal («…porque la última me salió mal»).
    """
    tipo = signals.event_type or "other"
    event_at, precision = resolve_event_time(text, now=now)

    # Etiqueta: el sustantivo detectado más su complemento inmediato. Se busca
    # sobre el texto ORIGINAL (no el normalizado) para no guardar recuerdos sin
    # tildes: esto acaba en el prompt y YUE lo lee.
    etiqueta = ""
    if signals.event_hit:
        raiz = re.escape(signals.event_hit)
        patron = raiz + r"(?:\s+(?:de|con|en)\s+[\wáéíóúüñ]+){0,2}"
        m = re.search(patron, text, re.IGNORECASE)
        if m:
            etiqueta = _clip(m.group(0), 60).lower()
    # Un sustantivo suelto («entrevista») dice poco dentro de un recuerdo: se
    # completa con la etiqueta canónica del tipo.
    if not etiqueta or len(etiqueta.split()) < 2:
        etiqueta = _ETIQUETA_TIPO.get(tipo, "algo importante")
        etiqueta = re.sub(r"^(un|una|unos|unas)\s+", "", etiqueta)

    # Causa explícita.
    razon = ""
    for patron in (r"\bporque\s+(.{3,90})", r"\bes\s+que\s+(.{3,90})",
                   r"\bya\s+que\s+(.{3,90})", r"\bdebido\s+a\s+que\s+(.{3,90})"):
        m = re.search(patron, text, re.IGNORECASE)
        if m:
            razon = _clip(m.group(1), 120).rstrip(" .,;")
            break

    # Desenlace.
    desenlace = ""
    if signals.outcome_cue:
        desenlace = _clip(text, 140)

    futuro = bool(event_at and event_at > (time.time() if now is None else now))
    return EpisodeDraft(
        is_episode=bool(signals.event_type) or signals.remember_request
                   or (signals.temporal and signals.pattern),
        event_type=tipo,
        event_label=etiqueta,
        event_at=event_at,
        date_precision=precision,
        reason_summary=razon,
        needs_follow_up=futuro or signals.remember_request,
        confidence=0.55 if signals.event_type else 0.40,
        is_routine=signals.routine and not signals.novelty,
        resolves_previous=signals.outcome_cue,
        outcome_summary=desenlace,
        source="local",
    )


def parse_extraction(raw: str, *, now: float | None = None,
                     fallback: EpisodeDraft | None = None) -> EpisodeDraft | None:
    """Convierte la respuesta del modelo en un EpisodeDraft. Tolerante a ruido."""
    texto = (raw or "").strip().replace("```json", " ").replace("```", " ")
    m = _RE_JSON.search(texto)
    if not m:
        return None
    try:
        datos = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(datos, dict):
        return None

    tipo = _norm(datos.get("event_type") or "other")
    if tipo not in _TIPOS_VALIDOS:
        tipo = "other"

    precision = _norm(datos.get("date_precision") or "unknown")
    if precision not in ("exact", "day", "week", "month", "unknown"):
        precision = "unknown"

    event_at = parse_iso_event_time(datos.get("event_at"), now=now)
    if event_at is None and fallback is not None:
        # El resolutor local suele acertar con «mañana» mejor que el modelo,
        # que a veces se despista con el año en curso.
        event_at, precision_local = fallback.event_at, fallback.date_precision
        if precision == "unknown":
            precision = precision_local
    if event_at is None:
        precision = "unknown"

    etiqueta = _clip(datos.get("event_label"), 60)
    if not etiqueta and fallback is not None:
        etiqueta = fallback.event_label

    try:
        confianza = max(0.0, min(0.95, float(datos.get("confidence", 0.6))))
    except (TypeError, ValueError):
        confianza = 0.6

    return EpisodeDraft(
        is_episode=bool(datos.get("is_episode", False)),
        event_type=tipo,
        event_label=etiqueta or _ETIQUETA_TIPO.get(tipo, "algo importante"),
        event_at=event_at,
        date_precision=precision,
        reason_summary=_clip(datos.get("reason_summary"), 160),
        needs_follow_up=bool(datos.get("needs_follow_up", False)),
        confidence=confianza,
        is_routine=bool(datos.get("is_routine", False)),
        resolves_previous=bool(datos.get("resolves_previous", False)),
        outcome_summary=_clip(datos.get("outcome_summary"), 200),
        source="llm",
    )


# ===========================================================================
# La capa
# ===========================================================================
@dataclass
class EpisodicMemory:
    """Memoria episódica emocional.

    `memory` es el objeto `core.memory.Memory` (persistencia/CRUD puro). Toda la
    LÓGICA vive aquí: detectar, extraer, puntuar, deduplicar, resolver, decidir
    cuándo se puede preguntar y cómo se cuenta el recuerdo.

    `engine` es opcional. Sin él, todo funciona con el extractor local.
    """

    memory: object
    engine: object | None = None
    #: Presupuesto de llamadas de extracción por sesión (control de coste).
    max_calls: int = 0
    _calls: int = field(default=0, init=False, repr=False)
    _fallos: int = field(default=0, init=False, repr=False)
    _bloqueado_hasta: float = field(default=0.0, init=False, repr=False)
    #: Hasta cuándo YUE NO puede iniciar un seguimiento (el usuario pidió calma).
    _quiet_until: float = field(default=0.0, init=False, repr=False)
    _last_touched: list = field(default_factory=list, init=False, repr=False)

    def __post_init__(self):
        if not self.max_calls:
            try:
                self.max_calls = int(_cfg("EPISODIC_MAX_LLM_CALLS", 25))
            except Exception:
                self.max_calls = 25

    # ------------------------------------------------------------- utilidades
    @property
    def enabled(self) -> bool:
        return bool(_cfg("EPISODIC_MEMORY_ENABLED", True))

    @property
    def llm_available(self) -> bool:
        if self.engine is None or not hasattr(self.engine, "chat"):
            return False
        if not bool(_cfg("EPISODIC_USE_LLM", True)):
            return False
        if self._calls >= self.max_calls:
            return False
        return time.time() >= self._bloqueado_hasta

    def note_boundary(self, *, give_space: bool = False,
                      boundaries=(), now: float | None = None) -> None:
        """El usuario pidió espacio / no quiere preguntas: silencio proactivo.

        Se guarda como una ventana de calma. Es lo que impide que un episodio
        pendiente atropelle un «déjame solo» — y funciona igual en los tests que
        en la aplicación, sin depender del gestor de estado ni de Qt.
        """
        marcas = {str(b).lower() for b in (boundaries or ())}
        silencio = give_space or bool(marcas & {
            "wants_space", "no_questions", "does_not_want_to_talk", "give_space",
        })
        if not silencio:
            return
        minutos = float(_cfg("EPISODIC_QUIET_AFTER_BOUNDARY_MIN", 180.0))
        ahora = time.time() if now is None else now
        self._quiet_until = max(self._quiet_until, ahora + minutos * 60.0)
        _log("quiet window activada tras límite explícito")

    def in_quiet_window(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) < self._quiet_until

    # ----------------------------------------------------------------- OBSERVE
    def observe(self, text: str, *, affect=None, decision=None, safety_level: int = 0,
                message_id: int | None = None, allow_llm: bool | None = None,
                now: float | None = None) -> dict:
        """Punto de entrada por mensaje del usuario.

        Devuelve siempre un dict con `action`:
        `skipped` | `created` | `updated` | `resolved`, más `episode_id` cuando
        corresponda. Nunca lanza.
        """
        resultado = {"action": "skipped", "episode_id": None, "reason": ""}
        if not self.enabled:
            resultado["reason"] = "desactivada"
            return resultado
        ahora = time.time() if now is None else now

        try:
            # --- Límites explícitos: se anotan SIEMPRE, aunque no haya episodio.
            self.note_boundary(
                give_space=bool(getattr(decision, "give_space", False)),
                boundaries=getattr(affect, "explicit_boundary", ()) or (),
                now=ahora)

            # --- SEGURIDAD. A partir de MODERADO manda el sistema de seguridad.
            #     Ni se crea episodio ni se programa seguimiento: un «¿cómo te
            #     fue?» automático en ese terreno sería insensible.
            if int(safety_level or 0) >= int(_cfg("EPISODIC_SAFETY_BLOCK_LEVEL", 2)):
                _log("descartado por nivel de seguridad")
                resultado["reason"] = "seguridad"
                return resultado

            emocion, intensidad, confianza_afecto = self._affect_fields(affect)
            emocional = emocion != "neutral" and intensidad >= 0.3
            signals = detect_signals(text, emotional=emocional)

            # --- ¿Está contando CÓMO TERMINÓ algo que ya guardamos? -----------
            # Ojo: un mismo mensaje puede CERRAR uno y ABRIR otro («mañana tengo
            # otra entrevista, la anterior me salió fatal»). Por eso, si además
            # apunta a un acontecimiento FUTURO, no se sale aquí.
            cerrado = None
            if signals.outcome_cue or signals.anaphoric:
                cerrado = self._try_resolve(text, signals, emocion=emocion,
                                            intensidad=intensidad, now=ahora)
                if cerrado is not None and not self._points_to_future(text, ahora):
                    return cerrado

            if not signals.is_candidate:
                # Aunque no haya señales para un episodio NUEVO, el mensaje
                # puede estar añadiendo algo a uno abierto («es que la anterior
                # me salió mal»). Es una comprobación local: ni una llamada al
                # modelo, ni un episodio de más.
                enriquecido = self._enrich_related(
                    text, signals, emocion=emocion, intensidad=intensidad,
                    decision=decision, now=ahora)
                if enriquecido is not None:
                    return enriquecido
                resultado["reason"] = "sin señales"
                return resultado
            _log(f"candidate detected {signals.to_dict()}")

            # --- Extracción estructurada --------------------------------------
            draft = self._extract(text, signals, allow_llm=allow_llm, now=ahora)
            if draft is None or not draft.is_episode:
                # No es un acontecimiento NUEVO, pero puede estar añadiendo algo
                # a uno que ya existe: «es que la anterior me salió mal» no
                # abre un episodio, explica el que ya está abierto.
                enriquecido = self._enrich_related(
                    text, signals, emocion=emocion, intensidad=intensidad,
                    decision=decision, now=ahora)
                if enriquecido is not None:
                    return enriquecido
                resultado["reason"] = "no es episodio"
                return cerrado or resultado
            if draft.is_routine and not signals.novelty and not signals.remember_request:
                _log("descartado: rutina sin nada que la haga especial")
                resultado["reason"] = "rutina"
                return cerrado or resultado

            # El modelo puede darse cuenta de que es un desenlace aunque el
            # filtro local no viera la pista.
            if draft.resolves_previous and not signals.outcome_cue:
                cerrado = self._try_resolve(text, signals, emocion=emocion,
                                            intensidad=intensidad, draft=draft,
                                            now=ahora)
                if cerrado is not None:
                    return cerrado

            # --- Deduplicación -------------------------------------------------
            relacionado = self.find_related_open_episode(
                text, event_type=draft.event_type, event_label=draft.event_label,
                event_at=draft.event_at, now=ahora)
            if relacionado is not None:
                actualizado = self._merge_into(relacionado, draft, text,
                                              emocion=emocion, intensidad=intensidad,
                                              decision=decision, signals=signals,
                                              now=ahora)
                _log(f"duplicate matched id={relacionado['id']}")
                self._touch(relacionado["id"], ahora)
                return {"action": "updated", "episode_id": relacionado["id"],
                        "reason": "", "importance": actualizado}

            # --- Importancia ----------------------------------------------------
            futuro = bool(draft.event_at and draft.event_at > ahora)
            importancia = score_importance(
                has_date=bool(draft.event_at),
                event_type=draft.event_type,
                intensity=intensidad,
                has_reason=bool(draft.reason_summary),
                should_follow_up=bool(getattr(decision, "should_follow_up", False))
                                 or draft.needs_follow_up,
                remember_request=signals.remember_request,
                future=futuro,
                routine=draft.is_routine,
                novelty=signals.novelty,
                mentions=1,
                goal_related=self._matches_goal(draft.event_label),
            )
            minimo = float(_cfg("EPISODIC_MIN_IMPORTANCE", 0.55))
            if importancia < minimo and not signals.remember_request:
                _log(f"descartado por importancia {importancia:.2f} < {minimo:.2f}")
                resultado["reason"] = "poco importante"
                return cerrado or resultado

            # --- Seguimiento ----------------------------------------------------
            follow_up_at = None
            necesita = (draft.needs_follow_up
                        or bool(getattr(decision, "should_follow_up", False)))
            if necesita:
                follow_up_at = schedule_follow_up(draft.event_at,
                                                  draft.date_precision, now=ahora)
                if follow_up_at is None and importancia >= 0.7:
                    horas = float(_cfg("EPISODIC_FOLLOWUP_UNKNOWN_HOURS", 24.0))
                    follow_up_at = ahora + horas * 3600.0

            episode_id = self.memory.add_emotional_episode(
                source_message_id=message_id,
                event_type=draft.event_type,
                event_label=draft.event_label,
                event_at=draft.event_at,
                date_precision=draft.date_precision,
                emotion=emocion,
                intensity=intensidad,
                reason_summary=draft.reason_summary,
                importance=importancia,
                support_mode=str(getattr(decision, "mode", "") or ""),
                yue_action=None,          # se rellena cuando YUE haya respondido
                follow_up_at=follow_up_at,
                follow_up_state="pending" if follow_up_at else "skipped",
                confidence=min(draft.confidence, max(0.35, confianza_afecto or 0.5)),
                now=ahora,
            )
            if episode_id:
                _log(f"created id={episode_id} type={draft.event_type} "
                     f"imp={importancia:.2f} src={draft.source}")
                self._touch(episode_id, ahora)
                return {"action": "created", "episode_id": episode_id,
                        "reason": "", "importance": importancia}
            resultado["reason"] = "no se pudo guardar"
            return resultado
        except Exception as exc:  # pragma: no cover - nunca tumba la conversación
            print("[episodic] fallo observando el mensaje:", exc)
            resultado["reason"] = "error"
            return resultado

    # ------------------------------------------------------------- RESOLUCIÓN
    def _try_resolve(self, text: str, signals: EpisodeSignals, *, emocion: str,
                     intensidad: float, draft: EpisodeDraft | None = None,
                     now: float | None = None) -> dict | None:
        """¿Está contando cómo terminó un episodio abierto? Lo cierra si sí.

        Funciona AUNQUE YUE no haya preguntado: si el usuario entra diciendo
        «saqué 18 :D» y hay un examen abierto de ayer, eso es el desenlace.
        """
        ahora = time.time() if now is None else now
        candidato = self.find_related_open_episode(
            text,
            event_type=(draft.event_type if draft else signals.event_type),
            event_label=(draft.event_label if draft else ""),
            event_at=None, now=ahora, prefer_past=True)
        if candidato is None:
            return None

        # Solo se cierra si el acontecimiento ya pudo ocurrir. Un «me fue bien»
        # sobre algo que es la semana que viene es otra cosa (o una confusión).
        evento = candidato.get("event_at")
        ya_paso = (not evento) or float(evento) <= ahora + 3600.0
        if not ya_paso and candidato.get("follow_up_state") != "asked":
            return None
        if not (signals.outcome_cue or (draft and draft.resolves_previous)):
            return None

        resumen = ""
        if draft and draft.outcome_summary:
            resumen = draft.outcome_summary
        if not resumen:
            resumen = _clip(text, 200)

        # Emoción DEL DESENLACE. Cuando la frase dice explícitamente cómo fue
        # («me fue genial», «desaprobé»), esa pista manda sobre la lectura
        # afectiva general: el arrastre del contexto emocional de los mensajes
        # anteriores —el usuario llevaba días nervioso por esto— haría que un
        # final feliz quedara archivado como ansiedad. No se llama a ningún
        # modelo para decidirlo; es la misma frase la que lo dice.
        emocion_final = _outcome_emotion(text) or emocion or "neutral"

        self.memory.resolve_episode(
            candidato["id"], outcome_summary=resumen,
            outcome_emotion=emocion_final, now=ahora)
        _log(f"resolved id={candidato['id']}")
        self._touch(candidato["id"], ahora)
        return {"action": "resolved", "episode_id": candidato["id"], "reason": ""}

    def _enrich_related(self, text: str, signals: EpisodeSignals, *, emocion: str,
                        intensidad: float, decision=None,
                        now: float | None = None) -> dict | None:
        """Añade contexto a un episodio abierto desde un mensaje sin evento propio.

        Es lo que hace que una conversación real funcione: el usuario suelta el
        acontecimiento en un mensaje y la CAUSA dos mensajes después. Sin esto,
        la causa se perdería o —peor— se crearía un episodio duplicado.
        """
        if not (signals.anaphoric or signals.outcome_cue or signals.temporal):
            return None
        ahora = time.time() if now is None else now
        # Umbral MÁS BAJO que el de deduplicación a propósito: aquí no se está
        # decidiendo si crear un episodio (lo caro), sino si un comentario suelto
        # pertenece a una conversación que ya está abierta (lo barato). El coste
        # de equivocarse es anotar una frase de más, no duplicar un recuerdo.
        relacionado = self.find_related_open_episode(
            text, event_type=signals.event_type, event_label="",
            event_at=None, now=ahora,
            threshold=float(_cfg("EPISODIC_CONTINUATION_THRESHOLD", 0.30)))
        if relacionado is None:
            return None

        draft = extract_local(text, signals, now=ahora)
        draft.is_episode = True     # solo para reutilizar la fusión
        draft.event_label = ""      # nunca renombra el episodio existente
        self._merge_into(relacionado, draft, text, emocion=emocion,
                         intensidad=intensidad, decision=decision,
                         signals=signals, now=ahora)
        _log(f"updated id={relacionado['id']} (contexto añadido)")
        self._touch(relacionado["id"], ahora)
        return {"action": "updated", "episode_id": relacionado["id"], "reason": ""}

    # ---------------------------------------------------------- DEDUPLICACIÓN
    def find_related_open_episode(self, text: str, *, event_type: str = "",
                                  event_label: str = "", event_at=None,
                                  now: float | None = None,
                                  prefer_past: bool = False,
                                  threshold: float | None = None) -> dict | None:
        """Busca un episodio ABIERTO que hable de lo mismo.

        Combina cuatro señales: tipo de acontecimiento, parecido textual de la
        etiqueta, cercanía de fechas y referencias anafóricas («esa entrevista»,
        «lo de mañana»). Empieza simple a propósito; el hueco para una búsqueda
        semántica de verdad está marcado más abajo.
        """
        ahora = time.time() if now is None else now
        try:
            dias = float(_cfg("EPISODIC_DEDUP_WINDOW_DAYS", 30.0))
            abiertos = self.memory.open_episodes(since=ahora - dias * 86400.0,
                                                 limit=40)
        except Exception:
            return None
        if not abiertos:
            return None

        tokens_msg = _tokens(text)
        tokens_label = _tokens(event_label)
        umbral = (float(_cfg("EPISODIC_DEDUP_THRESHOLD", 0.45))
                  if threshold is None else float(threshold))

        mejor, mejor_score = None, 0.0
        for ep in abiertos:
            score = 0.0
            tipo_ep = str(ep.get("event_type") or "")
            tokens_ep = _tokens(ep.get("event_label") or "")

            if event_type and tipo_ep and tipo_ep == event_type:
                score += 0.45
            elif event_type and tipo_ep and tipo_ep != event_type:
                score -= 0.15

            # Parecido de etiqueta y aparición de la etiqueta en el mensaje.
            if tokens_label and tokens_ep:
                score += 0.35 * _jaccard(tokens_label, tokens_ep)
            if tokens_ep and tokens_msg:
                solape = len(tokens_ep & tokens_msg) / float(len(tokens_ep))
                score += 0.40 * solape

            # Cercanía de fechas: mismo día = mismo acontecimiento, casi seguro.
            fecha_ep = ep.get("event_at")
            if event_at and fecha_ep:
                delta = abs(float(fecha_ep) - float(event_at))
                if delta <= 86400.0:
                    score += 0.30
                elif delta <= 3 * 86400.0:
                    score += 0.10
                else:
                    score -= 0.20

            # Referencia anafórica a algo reciente: «esa», «lo de mañana».
            if any(re.search(p, _norm(text)) for p in _ANAFORA):
                reciente = ahora - float(ep.get("last_mentioned_at")
                                         or ep.get("created_at") or 0.0)
                if reciente <= float(_cfg("EPISODIC_ANAPHORA_WINDOW_HOURS", 72.0)) * 3600.0:
                    score += 0.25

            if prefer_past and fecha_ep and float(fecha_ep) <= ahora:
                score += 0.15

            # Si YUE acabó de preguntar por este episodio, lo que el usuario
            # diga a continuación es casi con seguridad la respuesta —aunque no
            # repita la palabra «entrevista»—. Es el mismo prior que usa
            # cualquiera al conversar: pregunté algo, esto me contesta.
            if prefer_past and str(ep.get("follow_up_state") or "") == "asked":
                desde_pregunta = (ahora - float(ep.get("updated_at") or 0.0)) / 3600.0
                if desde_pregunta <= float(_cfg("EPISODIC_ANSWER_WINDOW_HOURS", 24.0)):
                    score += 0.45

            # Un episodio mencionado hace nada pesa más: si se habló de ello
            # en esta misma conversación, «la anterior» casi seguro es eso.
            edad_h = (ahora - float(ep.get("last_mentioned_at") or 0.0)) / 3600.0
            if edad_h <= float(_cfg("EPISODIC_SAME_TALK_MIN", 45.0)) / 60.0:
                score += 0.25
            elif edad_h <= 24.0:
                score += 0.10

            if score > mejor_score:
                mejor, mejor_score = ep, score

        # --- HUECO FASE 2 (revisado) --------------------------------------
        # Aquí encajaría `core.memory_ext.MemoryExtension.search_history()` para
        # desempatar por similitud semántica cuando el parecido textual quede en
        # la franja dudosa (0.30-0.45).
        #
        # Se sigue dejando fuera A PROPÓSITO, aunque memory_ext YA esté integrado
        # en el runtime (main.py lo enchufa al system_prompt). El motivo ahora es
        # otro: son memorias con trabajos distintos. memory_ext recupera TEXTO
        # CRUDO del historial; esta función decide a qué EPISODIO ESTRUCTURADO
        # pertenece un mensaje. Atar la segunda a la primera haría que un cambio
        # de umbral en la búsqueda del historial moviera, de rebote, la creación
        # y fusión de episodios. Si algún día se hace, que sea una decisión
        # explícita y con sus propias pruebas.
        if mejor is not None and mejor_score >= umbral:
            return mejor
        return None

    def _merge_into(self, episodio: dict, draft: EpisodeDraft, text: str, *,
                    emocion: str, intensidad: float, decision=None,
                    signals: EpisodeSignals | None = None,
                    now: float | None = None) -> float:
        """Enriquece un episodio existente con lo nuevo que aporta el mensaje.

        Nunca pisa información buena con información vacía: si ya había una
        causa y ahora no viene ninguna, se conserva la que había.
        """
        ahora = time.time() if now is None else now
        cambios = {}

        if draft.event_at and not episodio.get("event_at"):
            cambios["event_at"] = draft.event_at
            cambios["date_precision"] = draft.date_precision
        elif (draft.event_at and draft.date_precision == "exact"
              and episodio.get("date_precision") != "exact"):
            # Ahora sí sabemos la hora: se afina la fecha y el seguimiento.
            cambios["event_at"] = draft.event_at
            cambios["date_precision"] = "exact"

        if draft.reason_summary and not (episodio.get("reason_summary") or "").strip():
            cambios["reason_summary"] = draft.reason_summary
        elif (signals is not None and signals.anaphoric
              and not (episodio.get("reason_summary") or "").strip()
              and re.search(r"\b(anterior|pasada|ultima|ultimo)\b", _norm(text))):
            # «La anterior me salió mal»: es la causa, aunque no venga un «porque».
            cambios["reason_summary"] = _clip(text, 160)

        if not (episodio.get("event_label") or "").strip() and draft.event_label:
            cambios["event_label"] = draft.event_label

        # La emoción se actualiza si la nueva lectura es más intensa: lo que
        # importa del episodio es su pico emocional, no el último apunte.
        try:
            previa = float(episodio.get("intensity") or 0.0)
        except (TypeError, ValueError):
            previa = 0.0
        if emocion != "neutral" and intensidad >= previa:
            cambios["emotion"] = emocion
            cambios["intensity"] = intensidad

        # Reconsideración de la importancia: mencionarlo otra vez ya cuenta.
        menciones = int(episodio.get("mention_count") or 1) + 1
        nueva_importancia = score_importance(
            has_date=bool(cambios.get("event_at") or episodio.get("event_at")),
            event_type=str(episodio.get("event_type") or ""),
            intensity=max(intensidad, previa),
            has_reason=bool(cambios.get("reason_summary")
                            or episodio.get("reason_summary")),
            should_follow_up=bool(getattr(decision, "should_follow_up", False))
                             or draft.needs_follow_up,
            remember_request=bool(signals and signals.remember_request),
            future=bool((cambios.get("event_at") or episodio.get("event_at") or 0) > ahora),
            routine=False,
            novelty=bool(signals and signals.novelty),
            mentions=menciones,
            goal_related=self._matches_goal(episodio.get("event_label") or ""),
        )
        try:
            if nueva_importancia > float(episodio.get("importance") or 0.0):
                cambios["importance"] = nueva_importancia
            else:
                nueva_importancia = float(episodio.get("importance") or 0.0)
        except (TypeError, ValueError):
            cambios["importance"] = nueva_importancia
        cambios["mention_count"] = menciones

        # Si ahora sabemos la fecha y no había seguimiento, se programa.
        fecha = cambios.get("event_at") or episodio.get("event_at")
        if (not episodio.get("follow_up_at")
                and episodio.get("follow_up_state") in ("pending", "skipped", None)
                and fecha):
            objetivo = schedule_follow_up(
                float(fecha),
                str(cambios.get("date_precision") or episodio.get("date_precision")
                    or "day"), now=ahora)
            if objetivo:
                cambios["follow_up_at"] = objetivo
                cambios["follow_up_state"] = "pending"

        cambios["last_mentioned_at"] = ahora
        self.memory.update_emotional_episode(episodio["id"], now=ahora, **cambios)
        _log(f"updated id={episodio['id']} campos={sorted(cambios)}")
        return nueva_importancia

    # -------------------------------------------------------------- SEGUIMIENTO
    def due_followup(self, now: float | None = None) -> dict | None:
        """Episodio pendiente cuyo momento de seguimiento ya llegó. O None.

        Aquí se aplican los frenos que hacen que esto no sea un robot de
        recordatorios: ventana de calma tras un límite explícito, riesgo
        reciente y tope de preguntas por episodio.
        """
        if not self.enabled:
            return None
        ahora = time.time() if now is None else now
        if self.in_quiet_window(ahora):
            _log("seguimiento aplazado: el usuario pidió espacio")
            return None

        # Seguridad: si hubo eventos de riesgo recientes, el acompañamiento lo
        # lleva el sistema de seguridad, no un «¿cómo te fue?» automático.
        try:
            dias = int(_cfg("EPISODIC_RISK_BLOCK_DAYS", 2))
            if dias > 0 and int(self.memory.count_risk_events(dias) or 0) > 0:
                _log("seguimiento bloqueado por evento de riesgo reciente")
                return None
        except Exception:
            pass

        try:
            maximo = int(_cfg("EPISODIC_FOLLOWUP_MAX", 1))
            episodio = self.memory.find_due_followup(now=ahora, max_asked=maximo)
        except Exception as exc:
            print("[episodic] no pude buscar seguimientos:", exc)
            return None
        if episodio:
            _log(f"followup due id={episodio['id']}")
        return episodio

    def mark_followup_asked(self, episode_id: int, now: float | None = None) -> bool:
        """Marca la pregunta como HECHA. Se llama ANTES de hablar, a propósito.

        Si se marcara después y algo fallara por el camino, YUE podría repetir
        la misma pregunta en el siguiente latido. Preferimos perder una pregunta
        antes que insistir.
        """
        try:
            ok = bool(self.memory.mark_followup_asked(episode_id, now=now))
            if ok:
                _log(f"followup asked id={episode_id}")
            return ok
        except Exception as exc:
            print("[episodic] no pude marcar el seguimiento:", exc)
            return False

    def build_followup_prompt(self, episodio: dict, now: float | None = None) -> str:
        """Instrucción para que el MODELO escriba el seguimiento con su voz.

        No es una plantilla: es una descripción del recuerdo más las reglas de
        cómo se retoma algo con alguien de confianza.
        """
        ahora = time.time() if now is None else now
        etiqueta = (episodio.get("event_label") or "eso que le preocupaba").strip()
        cuando = self._describe_when(episodio.get("event_at"),
                                     episodio.get("date_precision"), ahora)
        emocion_es = _EMOCION_ES.get(str(episodio.get("emotion") or ""), "")
        razon = (episodio.get("reason_summary") or "").strip()

        partes = [f"Hace poco te contó que tenía {etiqueta}"]
        if cuando:
            partes.append(f"({cuando})")
        if emocion_es:
            partes.append(f"y se le notaba {emocion_es}")
        if razon:
            partes.append(f"porque {razon}")
        recuerdo = " ".join(partes) + "."

        return (
            "Vas a retomar tú misma una conversación anterior, por iniciativa "
            "propia.\n\n"
            f"LO QUE RECUERDAS: {recuerdo}\n\n"
            "Escribe UN SOLO mensaje corto (1-2 frases) preguntándole cómo "
            "terminó, como lo haría una amiga cercana que se acordó de golpe.\n"
            "REGLAS:\n"
            "- Habla en español, con tu voz de siempre.\n"
            "- NO digas «según mis registros», «en mi memoria», «tengo "
            "anotado», «detecté» ni nada parecido. No existen bases de datos.\n"
            "- No suene a recordatorio automático ni a encuesta.\n"
            "- Puedes mencionar cómo estaba, pero sin ponerte solemne ni "
            "psicóloga.\n"
            "- Una sola pregunta. Nada de listas ni de varios temas.\n"
            "- No repitas literalmente esta descripción: cuéntalo a tu manera."
        )

    def fallback_followup_text(self, episodio: dict, now: float | None = None) -> str:
        """Seguimiento SIN modelo. Sigue sonando a persona, no a recordatorio."""
        import random

        ahora = time.time() if now is None else now
        etiqueta = (episodio.get("event_label") or "eso").strip()
        emocion_es = _EMOCION_ES.get(str(episodio.get("emotion") or ""), "")
        cuando = self._describe_when(episodio.get("event_at"),
                                     episodio.get("date_precision"), ahora)

        con_art = _con_articulo(etiqueta)
        plantillas = [
            f"Oye… ¿y al final cómo te fue con {con_art}?",
            f"Por cierto, ¿cómo salió {con_art}?",
            f"Me acordé de lo de {con_art}. ¿Cómo terminó?",
            f"¿Y bien? ¿Qué tal {con_art}?",
        ]
        frase = random.choice(plantillas)
        if emocion_es and random.random() < 0.7:
            coletillas = [
                f" {cuando.capitalize() if cuando else 'El otro día'} se te notaba {emocion_es}.",
                f" Te vi bastante {emocion_es} por eso.",
                f" Estabas {emocion_es}, y me quedé pensando.",
            ]
            frase += random.choice(coletillas)
        return frase.replace("  ", " ").strip()

    # ------------------------------------------------------------- YUE_ACTION
    def note_yue_response(self, response_text: str, *, decision=None,
                          now: float | None = None) -> int:
        """Rellena `yue_action` de los episodios tocados en este turno.

        El episodio se crea ANTES de que YUE termine de responder, así que en
        ese momento no se puede saber qué hizo ella. Se completa aquí, con un
        resumen CORTO y normalizado: guardar su respuesta entera sería duplicar
        `messages` sin ninguna ganancia.
        """
        ahora = time.time() if now is None else now
        accion = self._normalize_action(response_text, decision)
        if not accion:
            return 0
        actualizados = 0
        try:
            ventana = float(_cfg("EPISODIC_ACTION_WINDOW_SEC", 180.0))
            ids = [i for (i, ts) in self._last_touched if (ahora - ts) <= ventana]
            if not ids:
                ids = [ep["id"] for ep in
                       self.memory.recently_touched_episodes(since=ahora - ventana)]
            for episode_id in dict.fromkeys(ids):
                if self.memory.update_yue_action(episode_id, accion, now=ahora,
                                                 only_if_empty=True):
                    actualizados += 1
            if actualizados:
                _log(f"yue_action «{accion}» en {actualizados} episodio(s)")
        except Exception as exc:
            print("[episodic] no pude anotar la acción de YUE:", exc)
        self._last_touched = []
        return actualizados

    @staticmethod
    def _normalize_action(response_text: str, decision=None) -> str:
        """Reduce lo que hizo YUE a una de las acciones normalizadas."""
        modo = str(getattr(decision, "mode", "") or "").lower()
        por_modo = {
            "listen": "escuchó", "comfort": "acompañó emocionalmente",
            "ask": "escuchó", "advise": "ofreció consejo", "solve": "ayudó a planificar",
            "distract": "acompañó emocionalmente", "celebrate": "celebró el resultado",
            "give_space": "le dio espacio", "safety": "acompañó emocionalmente",
        }
        base = _norm(response_text)
        # Lo que se lee en la respuesta pesa más que el modo previsto: YUE pudo
        # acabar haciendo algo distinto de lo que la política sugería.
        if re.search(r"\bpractic|\bensay|\bte pregunto yo\b|\bsimul|"
                     r"\bhagamos.*(preguntas|simulacro)", base):
            return "practicaron preguntas"
        if re.search(r"\bfelicidades\b|\benhorabuena\b|\bque bien\b|\bme alegro\b", base):
            return "celebró el resultado"
        if re.search(r"\brespira\b|\btranquil|\bcalma\b|\bva a salir bien\b|"
                     r"\bno estas solo\b", base):
            return "tranquilizó"
        if re.search(r"\bpaso 1\b|\bprimero\b.*\bdespues\b|\bplan\b|\bpreparar", base):
            return "ayudó a planificar"
        if re.search(r"\bes normal\b|\btiene sentido\b|\bes logico que\b|"
                     r"\bcualquiera estaria\b", base):
            return "validó"
        return por_modo.get(modo, "escuchó")

    # ----------------------------------------------------------------- CONTEXTO
    def context_block(self, text: str = "", now: float | None = None) -> str:
        """Bloque de RECUERDOS para el prompt del sistema. Vacío si no hay nada.

        Se inyectan pocos y bien elegidos. Meter todos los episodios convertiría
        a YUE en alguien que no puede hablar sin sacar el historial.
        """
        if not self.enabled:
            return ""
        ahora = time.time() if now is None else now
        try:
            maximo = int(_cfg("EPISODIC_MAX_CONTEXT", 3))
            episodios = self.get_relevant_episodes(text, limit=maximo, now=ahora)
        except Exception as exc:
            print("[episodic] no pude recuperar recuerdos:", exc)
            return ""
        if not episodios:
            return ""

        lineas = []
        for ep in episodios:
            lineas.append("- " + self._describe_episode(ep, ahora))

        return (
            "\n\nRECUERDOS CONCRETOS RELEVANTES (cosas que él te contó y que "
            "vivisteis juntos):\n"
            + "\n".join(lineas)
            + "\n(Son recuerdos personales, no datos que debas recitar. Úsalos "
              "SOLO si encajan de forma natural en lo que se está hablando. No "
              "menciones bases de datos, registros ni memoria interna, y no "
              "digas de dónde lo sacas. No fuerces la referencia: la mayoría de "
              "los mensajes no necesitan ninguna. Si de un evento ya preguntaste "
              "por tu cuenta, no vuelvas a preguntar por él.)"
        )

    def get_relevant_episodes(self, text: str = "", *, limit: int = 3,
                              now: float | None = None) -> list:
        """Los episodios que más aportan AHORA, por prioridad.

        1. relacionado con lo que se está hablando
        2. acontecimiento futuro cercano
        3. seguimiento pendiente
        4. resuelto hace poco y relevante
        """
        ahora = time.time() if now is None else now
        try:
            candidatos = self.memory.get_relevant_episodes(
                now=ahora,
                recent_days=float(_cfg("EPISODIC_CONTEXT_DAYS", 21.0)),
                limit=25)
        except Exception:
            return []
        if not candidatos:
            return []

        tokens_msg = _tokens(text)
        puntuados = []
        for ep in candidatos:
            score = float(ep.get("importance") or 0.0) * 0.5

            tokens_ep = _tokens(ep.get("event_label") or "")
            if tokens_ep and tokens_msg:
                solape = len(tokens_ep & tokens_msg) / float(len(tokens_ep))
                score += 1.20 * solape          # (1) lo que se está hablando

            evento = ep.get("event_at")
            estado = str(ep.get("status") or "unresolved")
            if evento and estado == "unresolved":
                horas = (float(evento) - ahora) / 3600.0
                if 0 <= horas <= 48:
                    score += 0.90               # (2) futuro cercano
                elif 0 <= horas <= 24 * 7:
                    score += 0.45
            if (estado == "unresolved"
                    and str(ep.get("follow_up_state") or "") == "pending"
                    and ep.get("follow_up_at")):
                score += 0.35                   # (3) seguimiento pendiente
            if estado == "resolved":
                edad_dias = (ahora - float(ep.get("updated_at") or ahora)) / 86400.0
                score += 0.30 if edad_dias <= 7 else 0.05   # (4) resuelto reciente

            puntuados.append((score, ep))

        puntuados.sort(key=lambda par: par[0], reverse=True)
        minimo = float(_cfg("EPISODIC_CONTEXT_MIN_SCORE", 0.35))
        return [ep for score, ep in puntuados[:max(1, int(limit))] if score >= minimo]

    # ---------------------------------------------------------- MANTENIMIENTO
    def maintenance(self, now: float | None = None) -> dict:
        """Caducidad y limpieza. Se llama de vez en cuando, no en cada mensaje."""
        resumen = {"expired": 0, "purged": 0}
        if not self.enabled:
            return resumen
        try:
            dias = float(_cfg("EPISODIC_RETENTION_DAYS", 90.0))
            resumen["expired"] = int(self.memory.expire_old_episodes(
                retention_days=dias, now=now) or 0)
            resumen["purged"] = int(self.memory.purge_trivial_episodes(
                retention_days=dias,
                min_importance=float(_cfg("EPISODIC_MIN_IMPORTANCE", 0.55)),
                now=now) or 0)
            if resumen["expired"] or resumen["purged"]:
                _log(f"mantenimiento: {resumen}")
        except Exception as exc:
            print("[episodic] fallo en el mantenimiento:", exc)
        return resumen

    def recurring_patterns(self, *, min_repeticiones: int = 3,
                           now: float | None = None) -> list:
        """Patrones PRUDENTES a partir de episodios repetidos.

        Solo describe lo observado («las entrevistas de trabajo suelen ponerle
        nervioso»). NUNCA infiere rasgos de personalidad ni nada que se parezca
        a un diagnóstico: eso no es memoria, es etiquetar a alguien.
        """
        try:
            filas = self.memory.episode_emotion_counts(
                now=now, days=float(_cfg("EPISODIC_PATTERN_DAYS", 180.0)))
        except Exception:
            return []
        frases = []
        for fila in filas or []:
            if int(fila.get("n") or 0) < int(min_repeticiones):
                continue
            tipo = str(fila.get("event_type") or "")
            emocion = _EMOCION_ES.get(str(fila.get("emotion") or ""), "")
            etiqueta = _ETIQUETA_TIPO.get(tipo, "")
            if not (emocion and etiqueta):
                continue
            frases.append(f"{etiqueta.capitalize()} suele dejarle {emocion}.")
        return frases[:3]

    # ------------------------------------------------------------------ interno
    def _extract(self, text: str, signals: EpisodeSignals, *,
                 allow_llm: bool | None = None,
                 now: float | None = None) -> EpisodeDraft | None:
        """Extractor híbrido: modelo si se puede, local siempre como red."""
        local = extract_local(text, signals, now=now)
        usar_llm = self.llm_available if allow_llm is None else (
            bool(allow_llm) and self.llm_available)
        if not usar_llm:
            return local

        ahora = datetime.fromtimestamp(time.time() if now is None else now)
        contenido = (
            f"Hoy es {ahora.strftime('%Y-%m-%d')} "
            f"({_DIA_ES[ahora.weekday()]}), hora {ahora.strftime('%H:%M')}.\n"
            "Resuelve las expresiones temporales con esa fecha real.\n\n"
            "Mensaje:\n" + _clip(text, 700)
        )
        self._calls += 1
        try:
            crudo = self.engine.chat(  # type: ignore[union-attr]
                [{"role": "system", "content": _EXTRACTOR_SYSTEM},
                 {"role": "user", "content": contenido}],
                timeout=int(_cfg("EPISODIC_LLM_TIMEOUT", 12)))
        except Exception:
            self._penalizar()
            return local

        draft = parse_extraction(crudo, now=now, fallback=local)
        if draft is None:
            self._penalizar()
            return local
        self._fallos = 0
        # Las reglas locales conservan la última palabra sobre la RUTINA: el
        # modelo tiende a considerar importante cualquier cosa que le cuenten.
        if signals.routine and not signals.novelty:
            draft.is_routine = True
        return draft

    def _penalizar(self) -> None:
        self._fallos += 1
        if self._fallos >= 3:
            self._bloqueado_hasta = time.time() + 300.0
            self._fallos = 0

    def _touch(self, episode_id, ts: float) -> None:
        if episode_id:
            self._last_touched.append((int(episode_id), float(ts)))
            del self._last_touched[:-5]

    @staticmethod
    def _points_to_future(text: str, now: float) -> bool:
        """¿El mensaje habla además de algo que TODAVÍA no ha pasado?

        Es lo que permite que «mañana tengo otra entrevista, la anterior me
        salió fatal» cierre el episodio viejo y abra el nuevo en la misma frase.
        """
        fecha, _ = resolve_event_time(text, now=now)
        if fecha and fecha > now:
            return True
        return bool(re.search(
            r"\b(voy\s+a|tengo\s+(otra|otro|una|un)|me\s+toca|sera|va\s+a\s+ser)\b",
            _norm(text)))

    @staticmethod
    def _affect_fields(affect) -> tuple[str, float, float]:
        """Reutiliza el análisis afectivo YA hecho. No vuelve a analizar nada."""
        if affect is None:
            return "neutral", 0.0, 0.0
        try:
            emocion = str(getattr(affect, "primary_emotion", "neutral") or "neutral")
            valence = float(getattr(affect, "valence", 0.0) or 0.0)
            arousal = float(getattr(affect, "arousal", 0.0) or 0.0)
            confianza = float(getattr(affect, "confidence", 0.0) or 0.0)
            return emocion, max(abs(valence), arousal), confianza
        except Exception:
            return "neutral", 0.0, 0.0

    def _matches_goal(self, etiqueta: str) -> bool:
        """¿El acontecimiento toca una meta activa? Sube su importancia."""
        tokens = _tokens(etiqueta)
        if not tokens:
            return False
        try:
            metas = self.memory.list_goals(only_active=True) or []
        except Exception:
            return False
        for meta in metas[:10]:
            texto = meta.get("text") if isinstance(meta, dict) else str(meta)
            if _jaccard(tokens, _tokens(texto)) >= 0.25:
                return True
        return False

    @staticmethod
    def _describe_when(event_at, precision, now: float) -> str:
        """«mañana», «ayer», «el viernes»… en lenguaje corriente."""
        if not event_at:
            return ""
        try:
            fecha = datetime.fromtimestamp(float(event_at))
        except Exception:
            return ""
        hoy = datetime.fromtimestamp(now).date()
        dias = (fecha.date() - hoy).days
        if dias == 0:
            base = "hoy"
        elif dias == 1:
            base = "mañana"
        elif dias == -1:
            base = "ayer"
        elif dias == 2:
            base = "pasado mañana"
        elif -7 < dias < 0:
            base = f"el {_DIA_ES[fecha.weekday()]} pasado"
        elif 0 < dias < 7:
            base = f"el {_DIA_ES[fecha.weekday()]}"
        elif dias < 0:
            base = f"hace {abs(dias)} días"
        else:
            base = f"en {dias} días"
        if precision == "exact":
            base += fecha.strftime(" a las %H:%M")
        return base

    def _describe_episode(self, ep: dict, now: float) -> str:
        """Una línea legible del episodio, para el prompt."""
        cuando = self._describe_when(ep.get("event_at"), ep.get("date_precision"), now)
        etiqueta = (ep.get("event_label") or "algo importante").strip()
        emocion = _EMOCION_ES.get(str(ep.get("emotion") or ""), "")
        razon = (ep.get("reason_summary") or "").strip()
        estado = str(ep.get("status") or "unresolved")

        try:
            pasado = bool(ep.get("event_at")) and float(ep["event_at"]) <= now
        except (TypeError, ValueError):
            pasado = False
        verbo = "tuvo" if pasado else "tiene"

        partes = []
        partes.append(f"{cuando.capitalize()} {verbo} {etiqueta}." if cuando
                      else f"{verbo.capitalize()} {etiqueta}.")
        if emocion:
            partes.append(f"Se le notaba {emocion}.")
        if razon:
            partes.append(f"Motivo: {razon}.")

        if estado == "resolved":
            desenlace = (ep.get("outcome_summary") or "").strip()
            emocion_final = _EMOCION_ES.get(str(ep.get("outcome_emotion") or ""), "")
            partes.append("Estado: ya pasó y terminó"
                          + (f" así: {desenlace}" if desenlace else "")
                          + (f" (acabó {emocion_final})" if emocion_final else "")
                          + ".")
        elif str(ep.get("follow_up_state") or "") == "asked":
            partes.append("Estado: pendiente; YA le preguntaste cómo fue, NO "
                          "vuelvas a preguntarlo tú.")
        else:
            partes.append("Estado: pendiente.")

        accion = (ep.get("yue_action") or "").strip()
        if accion:
            partes.append(f"Tú {accion}.")
        return " ".join(partes)


def _con_articulo(etiqueta: str) -> str:
    """«entrevista de trabajo» → «la entrevista de trabajo».

    Sin esto, el seguimiento de respaldo suena a telegrama («¿cómo salió
    entrevista de trabajo?»). Heurística de género suficiente para las
    etiquetas que genera este módulo.
    """
    limpio = re.sub(r"^(el|la|los|las|un|una|unos|unas)\s+", "",
                    str(etiqueta or "").strip(), flags=re.IGNORECASE)
    if not limpio:
        return "eso"
    primera = limpio.split()[0].lower()
    plural = primera.endswith("s") and not primera.endswith("is")
    femenino = (primera.endswith(("a", "ción", "sión", "dad", "tad", "umbre"))
                and not primera.endswith("ma"))
    if plural:
        return ("las " if femenino else "los ") + limpio
    return ("la " if femenino else "el ") + limpio


def _outcome_emotion(text: str) -> str:
    """Emoción DEL DESENLACE deducida de la propia frase de resultado.

    Solo se usa cuando el análisis afectivo se quedó en neutro. No sustituye a
    `core.affect`: lo complementa en el único caso en que se queda corto, las
    frases telegráficas de resultado («saqué 18», «me fue fatal»).
    """
    base = _norm(text)
    if re.search(r"\b(genial|increible|buenisimo|excelente|perfecto|"
                 r"mejor de lo que|me aceptaron|me contratan|me van a contratar|"
                 r"aprobe|pase|quede seleccionado|sali bien|salio bien|"
                 r"me fue (muy )?bien)\b", base):
        return "joy"
    if re.search(r"\b(fatal|horrible|un desastre|pesimo|me fue mal|salio mal|"
                 r"desaprobe|jale|reprobe|me rechazaron|quede fuera)\b", base):
        return "disappointment"
    if re.search(r"\b(por fin|menos mal|ya paso|me quite un peso|uf)\b", base):
        return "relief"
    return ""


#: Emociones en español para hablar del recuerdo con naturalidad.
_EMOCION_ES = {
    "joy": "contento", "excitement": "emocionado", "pride": "orgulloso",
    "relief": "aliviado", "affection": "cariñoso", "sadness": "triste",
    "disappointment": "decepcionado", "loneliness": "solo", "anger": "molesto",
    "frustration": "frustrado", "fear": "asustado", "anxiety": "nervioso",
    "guilt": "culpable", "embarrassment": "avergonzado", "confusion": "confundido",
    "tiredness": "agotado", "neutral": "",
}

_DIA_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


__all__ = [
    "EpisodicMemory", "EpisodeSignals", "EpisodeDraft",
    "detect_signals", "extract_local", "parse_extraction",
    "resolve_event_time", "parse_iso_event_time", "schedule_follow_up",
    "score_importance",
]

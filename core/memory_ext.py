"""Memoria 2.0 — capa ADITIVA sobre la base de datos actual (FASE 3).

No reemplaza core/memory.py ni toca sus tablas. Añade, con CREATE TABLE IF NOT
EXISTS, lo que faltaba según la auditoría:

  - conversations      : cada charla con id, tema, modo, dispositivo, resumen…
  - conv_messages       : mensajes ligados a su conversación, con emoción,
                          importancia, confianza, marca de editado/borrado.
  - mem_backups         : registro de copias de seguridad hechas.

Y encima ofrece:

  1) Búsqueda "por significado" SIN internet ni modelo pesado: un TF-IDF con
     coseno sobre el texto en español (minúsculas, sin tildes, sin palabras
     vacías). Devuelve el mensaje ORIGINAL, su FECHA y un nivel de CONFIANZA,
     para que YUE pueda decir «me hablaste de esto el día X» y distinguir un
     recuerdo exacto de uno aproximado o inferido.
  2) Copias de seguridad seguras con verificación de integridad y restauración
     (nunca se pisa una base sin respaldo previo).

-------------------------------------------------------------------------
NUEVO · CAPA 3: MEMORIA HISTÓRICA RELEVANTE  (`search_history` / `context_block`)
-------------------------------------------------------------------------
Esta es la parte que YUE usa EN CADA MENSAJE. Su trabajo es distinto al de las
otras dos memorias y no las sustituye:

    MEMORIA INMEDIATA   memory.recent_messages(12)   los últimos turnos
    MEMORIA EPISÓDICA   core/episodic_memory.py      acontecimientos importantes
    MEMORIA HISTÓRICA   esta capa                    pasado relevante AHORA

El punto clave: `search_history()` NO lee `conv_messages` (la tabla de arriba),
sino la tabla **`messages` de core/memory.py**, que es la ÚNICA fuente real del
historial. Así no se duplica ni un solo mensaje, y si el usuario borra su
historial, aquí no queda ninguna copia olvidada. `add_message()` de esta clase
sigue existiendo por compatibilidad, pero el runtime de YUE no la usa.

Qué resuelve:

  * Recuperar lo que se dijo hace semanas o meses cuando vuelve a venir a cuento
    («Andrea volvió a escribirme» → aquella pelea con Andrea de hace meses).
  * NO devolver el mensaje actual (que ya está guardado y sería el más parecido,
    con similitud ≈ 1.0) ni los que ya entran por `recent_messages()`: se
    excluyen POR ID, no por parecido.
  * NO convertir respuestas antiguas de YUE en hechos del usuario: por defecto
    solo se buscan mensajes con `role="user"`.
  * Umbral de relevancia: si nada supera `min_score`, no se manda NADA al
    prompt. Es preferible no recordar a recordar cualquier cosa.

El algoritmo vive detrás de una interfaz (`BaseRetriever`), así que se puede
cambiar TF-IDF por embeddings sin tocar main.py ni el resto del proyecto.

Solo usa la librería estándar (sqlite3, re, math, unicodedata, shutil, time), así
que funciona en modo totalmente offline y no añade dependencias.
"""
from __future__ import annotations

import math
import re
import shutil
import sqlite3
import time
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

try:  # config es opcional: la capa trae sus propios valores por defecto.
    import config as _config
except Exception:  # pragma: no cover
    _config = None


def _cfg(nombre: str, defecto):
    """Lee una opción de config.py; si no existe, usa el valor por defecto."""
    if _config is None:
        return defecto
    return getattr(_config, nombre, defecto)


def _debug() -> bool:
    return bool(_cfg("MEMORY_RELEVANCE_DEBUG", False))


#: Marca "no me lo han dicho, léelo de config". Hace falta porque `None` ya
#: significa algo distinto en `role` (= no filtrar por rol).
_AUTO = object()


def _log(mensaje: str) -> None:
    """Log de depuración. NUNCA llega al usuario y se apaga con la config."""
    if _debug():
        print("[memory-ext] " + str(mensaje))

# Palabras vacías del español (lista corta pero útil para el TF-IDF).
_STOP = {
    "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por",
    "un", "para", "con", "no", "una", "su", "al", "lo", "como", "mas", "pero",
    "sus", "le", "ya", "o", "este", "si", "porque", "esta", "entre", "cuando",
    "muy", "sin", "sobre", "tambien", "me", "hasta", "hay", "donde", "quien",
    "desde", "todo", "nos", "durante", "todos", "uno", "les", "ni", "contra",
    "otros", "ese", "eso", "ante", "ellos", "e", "esto", "mi", "antes", "algunos",
    "que", "unos", "yo", "otro", "otras", "otra", "el", "tanto", "esa", "estos",
    "mucho", "quienes", "nada", "muchos", "cual", "poco", "ella", "estar", "estas",
    "algunas", "algo", "nosotros", "tu", "te", "ti", "es", "soy", "era", "son",
}


def _normalize(text: str) -> list[str]:
    """Minúsculas, sin tildes, solo palabras, sin palabras vacías ni cortas."""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    tokens = re.findall(r"[a-zñ0-9]+", text)
    return [t for t in tokens if len(t) > 2 and t not in _STOP]


# ===========================================================================
# Análisis del español SIN dependencias (para la capa histórica)
# ===========================================================================
# Palabras vacías AMPLIADAS. Va aparte de `_STOP` a propósito: `_STOP` la usa la
# búsqueda antigua (`search()`) y tocarla cambiaría su comportamiento. Aquí se
# añaden los verbos y muletillas que en una conversación aparecen en todas
# partes y que, si no se filtran, hacen que «estoy preocupado por mi examen»
# recupere «estoy destrozado por mi perro» solo por el «estoy».
_STOP_HIST = _STOP | {
    "estoy", "estas", "esta", "estan", "estamos", "estaba", "estaban",
    "estuve", "estuvo", "eres", "eran", "fue", "fui", "fueron", "seria",
    "ser", "sido", "siendo", "sera", "seran", "hacer", "hago", "hace",
    "haces", "hizo", "hecho", "haciendo", "tengo", "tiene", "tienes",
    "tienen", "tenia", "tener", "tuve", "tuvo", "quiero", "quiere",
    "quieres", "queria", "puedo", "puede", "puedes", "podia", "poder",
    "voy", "vas", "van", "vamos", "ver", "veo", "ves", "dijo", "dije",
    "dices", "decir", "digo", "creo", "crees", "sabes", "sabe", "solo",
    "cada", "asi", "aqui", "alla", "ahi", "ahora", "luego", "entonces",
    "tal", "vez", "cosa", "cosas", "bien", "bueno", "buena", "buenas",
    "buenos", "gracias", "hola", "jaja", "jajaja", "pues", "igual",
    "verdad", "claro", "ok", "okay", "vale", "siempre", "nunca", "aun",
    "aunque", "sino", "cual", "cuanto", "cuantos", "quiza", "quizas",
    "realmente", "bastante", "demasiado", "encima", "acaso", "supongo",
    "parece", "sigue", "sigo", "vuelve", "vuelvo", "volvio",
}


def _normalize_hist(text: str) -> list[str]:
    """Como `_normalize`, pero con la lista de palabras vacías ampliada."""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    tokens = re.findall(r"[a-zñ0-9]+", text)
    return [t for t in tokens if len(t) > 2 and t not in _STOP_HIST]


# Un TF-IDF crudo falla en dos sitios muy visibles para una compañera:
#   1) «mi perro murió» y «extraño a mi mascota» no comparten NI UNA palabra.
#   2) «mi examen» y «los exámenes» tampoco, aunque hablen de lo mismo.
# Se arregla sin instalar nada: una raíz aproximada (stemmer ligero) para (2) y
# un pequeño diccionario de CONCEPTOS del día a día para (1).

# Sufijos habituales del español, del más largo al más corto. Se quita como
# mucho uno, y solo si lo que queda sigue teniendo cuerpo (>= 4 letras): así
# «casa» no se convierte en «cas» ni «mes» en «m».
_SUFIJOS = (
    "amientos", "imientos", "aciones", "iciones", "adores", "adoras",
    "amiento", "imiento", "aremos", "eremos", "iremos", "abamos",
    "acion", "icion", "ancia", "encia", "mente", "ndose", "iendo",
    "ieron", "aron", "aste", "iste", "amos", "emos", "imos", "aban",
    "ados", "adas", "idos", "idas", "ando", "ador", "edor", "ista",
    "ismo", "able", "ible", "cito", "cita", "illo", "illa",
    "aba", "ado", "ada", "ido", "ida", "ara", "era", "ira",
    "oso", "osa", "ito", "ita", "dad", "tad",
    "ar", "er", "ir", "es",
)


def _stem(token: str) -> str:
    """Raíz APROXIMADA de una palabra española. No es lingüística, es útil.

    «trabajar», «trabajo» y «trabajamos» acaban en «trabaj»; «preocupado» y
    «preocupada» en «preocup». Es intencionadamente conservador: prefiere no
    tocar una palabra antes que destrozarla.
    """
    t = token
    if len(t) <= 4:
        return t
    for suf in _SUFIJOS:
        if t.endswith(suf) and (len(t) - len(suf)) >= 4:
            t = t[: -len(suf)]
            break
    if len(t) > 4 and t.endswith("s"):          # plural residual
        t = t[:-1]
    if len(t) > 4 and t[-1] in "oae":           # género / vocal temática
        t = t[:-1]
    return t


# Conceptos del día a día. La clave es el TEMA y el valor las palabras que lo
# evocan. No pretende ser un diccionario completo: cubre lo que de verdad sale
# en una conversación personal (gente, mascotas, salud, estudios, dinero…).
_CONCEPTOS_FUENTE = {
    "mascota": (
        "perro perra perrito perrita cachorro gato gata gatito gatita michi "
        "mascota mascotas hamster conejo loro canario pez tortuga veterinario"
    ),
    "muerte": (
        "murio murieron muerte muerto muerta fallecio fallecido fallecida "
        "falleci difunto entierro funeral velorio luto sepelio tumba"
    ),
    "perdida": (
        "extrano extranar extrano anoro anorar ausencia duelo luto perdi "
        "perdida murio muerte fallecio adios despedida vacio falta"
    ),
    "tristeza": (
        "triste tristeza llorar llore llorando lloro pena dolido decaido "
        "decaida deprimido deprimida bajon melancolia desanimado "
        "extrano extranar anoro destrozado destrozada devastado devastada "
        "roto rota duele dolia vacio nostalgia"
    ),
    "ansiedad": (
        "preocupado preocupada preocupa preocupacion ansiedad ansioso ansiosa "
        "nervioso nerviosa angustia panico estres estresado estresada agobio "
        "miedo temor inquieto"
    ),
    "enfado": (
        "enojo enojado enojada molesto molesta furioso furiosa rabia ira "
        "bronca harto harta indignado cabreado fastidio"
    ),
    "alegria": (
        "feliz felicidad contento contenta alegre alegria ilusion emocionado "
        "emocionada encanta genial maravilloso orgulloso orgullosa"
    ),
    "conflicto": (
        "pelea pelee peleamos pelearon discusion discuti discutimos rina "
        "conflicto malentendido distanciamos enfadamos reconciliamos "
        "traiciono mintio mentira engano perdon disculpa"
    ),
    "amistad": (
        "amigo amiga amigos amigas amistad companero companera colega pana "
        "confidente"
    ),
    "pareja": (
        "novio novia pareja enamorado enamorada esposo esposa marido "
        "matrimonio relacion ruptura terminamos cortamos aniversario cita "
        "beso amor"
    ),
    "familia": (
        "mama papa madre padre hermano hermana abuelo abuela tio tia primo "
        "prima hijo hija familia sobrino sobrina cunado suegra"
    ),
    "estudio": (
        "examen examenes prueba parcial tarea universidad instituto colegio "
        "escuela clase clases profesor profesora maestro materia curso "
        "estudiar estudio estudios nota notas calificacion carrera tesis "
        "semestre ciclo beca aprobar reprobar jalado sustentacion"
    ),
    "trabajo": (
        "trabajo trabajar jefe jefa oficina empleo entrevista curriculum "
        "contrato sueldo salario despido despidieron renuncia ascenso chamba "
        "laburo turno practicas pasantia proyecto cliente"
    ),
    "dinero": (
        "dinero plata pagar pago deuda deudas prestamo ahorro ahorrar gasto "
        "caro barato banco alquiler renta factura recibo"
    ),
    "salud": (
        "enfermo enferma enfermedad hospital clinica medico doctor doctora "
        "medicina pastilla pastillas fiebre gripe operacion cirugia consulta "
        "analisis diagnostico dolor tratamiento terapia psicologo"
    ),
    "autoestima": (
        "inutil fracaso fracasado culpa culpable verguenza solo sola soledad "
        "vacio nadie sirvo capaz inseguro insegura"
    ),
    "hogar": (
        "casa departamento cuarto habitacion mudanza mudamos mudarme vecino "
        "vecina barrio alquiler"
    ),
    "viaje": (
        "viaje viajar viajamos vacaciones playa avion vuelo hotel turismo "
        "mochila pasaje maleta"
    ),
    "comida": (
        "comida comer almuerzo almorce cena cenar desayuno restaurante "
        "cocinar receta hambre pizza pollo postre"
    ),
    "juego": (
        "juego jugar jugue videojuego partida consola gamer nintendo "
        "playstation steam"
    ),
    "musica": (
        "musica cancion canciones banda concierto guitarra piano cantar "
        "album grupo tocar"
    ),
    "arte": (
        "dibujo dibujar pintura pintar anime manga serie pelicula libro leer "
        "escribir novela"
    ),
    "tecnologia": (
        "codigo programar programacion python java aplicacion software "
        "computadora laptop error bug servidor base"
    ),
    "deporte": (
        "gimnasio correr futbol entrenar entrenamiento deporte partido "
        "equipo bicicleta natacion"
    ),
    "descanso": (
        "dormir sueno insomnio cansado cansada cansancio descansar agotado "
        "agotada desvelado"
    ),
    "tiempo_libre": (
        "fiesta salir salimos cumpleanos celebrar reunion quedada"
    ),
}

# Conceptos de carga emocional: se usan para dar un pequeño extra de
# IMPORTANCIA a un recuerdo (una pelea pesa más que un almuerzo).
_CONCEPTOS_EMOCIONALES = frozenset({
    "muerte", "perdida", "tristeza", "ansiedad", "enfado", "conflicto",
    "salud", "autoestima", "pareja", "alegria",
})


def _construir_indice_conceptos() -> dict:
    """Invierte el diccionario de arriba a  raíz -> (concepto, concepto…)."""
    indice: dict[str, set] = {}
    for concepto, palabras in _CONCEPTOS_FUENTE.items():
        for palabra in palabras.split():
            for clave in {palabra, _stem(palabra)}:
                indice.setdefault(clave, set()).add(concepto)
    return {k: tuple(sorted(v)) for k, v in indice.items()}


_CONCEPTOS = _construir_indice_conceptos()

# Pesos de cada tipo de término dentro del vector. La palabra literal manda; la
# raíz casi tanto; el concepto pesa menos (une temas, no afirma nada); los
# n-gramas de letras son una red de seguridad para variantes y erratas.
_PESO_PALABRA = 1.00
_PESO_RAIZ = 0.85
_PESO_CONCEPTO = 0.70
_PESO_NGRAMA = 0.10
_NGRAMA_N = 4
_NGRAMA_MIN_LEN = 6


def _ngramas(token: str, n: int = _NGRAMA_N):
    if len(token) < n:
        return ()
    return tuple(token[i:i + n] for i in range(len(token) - n + 1))


def _terminos(text: str) -> dict:
    """Vector de términos PESADOS de un texto (palabras + raíces + conceptos).

    Los prefijos evitan colisiones: «=» raíz, «~» concepto, «#» n-grama.
    """
    tokens = _normalize_hist(text)
    if not tokens:
        return {}
    pesos: dict[str, float] = {}

    def sumar(term: str, peso: float):
        pesos[term] = pesos.get(term, 0.0) + peso

    for t in tokens:
        sumar(t, _PESO_PALABRA)
        raiz = _stem(t)
        sumar("=" + raiz, _PESO_RAIZ)
        for concepto in _CONCEPTOS.get(raiz, ()) or _CONCEPTOS.get(t, ()):
            sumar("~" + concepto, _PESO_CONCEPTO)
        if len(t) >= _NGRAMA_MIN_LEN:
            for g in _ngramas(t):
                sumar("#" + g, _PESO_NGRAMA)
    return pesos


def _conceptos_de(terminos: dict) -> set:
    return {k[1:] for k in terminos if k.startswith("~")}


def _raices_de(terminos: dict) -> set:
    return {k[1:] for k in terminos if k.startswith("=")}


def _importancia(terminos: dict) -> float:
    """Cuánto 'pesa' un recuerdo por sí mismo (0..1), sin mirar la consulta.

    No hay columna de importancia en `messages`, así que se estima por la carga
    emocional del propio texto. Es un empujón pequeño, nunca el criterio.
    """
    emocionales = _conceptos_de(terminos) & _CONCEPTOS_EMOCIONALES
    return min(1.0, len(emocionales) * 0.34)


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def _recorta(texto: str, maximo: int) -> str:
    """Acorta un recuerdo sin cortar palabras a la mitad.

    Prefiere terminar en un punto; si no hay ninguno razonable, corta en el
    último espacio y añade puntos suspensivos.
    """
    texto = " ".join(str(texto or "").split())
    if maximo <= 0 or len(texto) <= maximo:
        return texto
    recorte = texto[:maximo]
    # ¿Hay un final de frase decente en el último tercio?
    corte_frase = max(recorte.rfind(". "), recorte.rfind("? "), recorte.rfind("! "))
    if corte_frase >= int(maximo * 0.6):
        return recorte[:corte_frase + 1].strip()
    corte_palabra = recorte.rfind(" ")
    if corte_palabra >= int(maximo * 0.5):
        recorte = recorte[:corte_palabra]
    return recorte.rstrip(" ,;:.") + "…"


_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def _fecha_humana(ts: float, ahora: float | None = None) -> str:
    """«ayer», «hace 3 semanas», «hace 4 meses»… como lo diría una persona."""
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return ""
    ahora = time.time() if ahora is None else float(ahora)
    dias = max(0.0, (ahora - ts) / 86400.0)
    if dias < 1.0:
        return "hoy"
    if dias < 2.0:
        return "ayer"
    if dias < 7.0:
        return f"hace {int(dias)} días"
    if dias < 14.0:
        return "hace una semana"
    if dias < 31.0:
        return f"hace {int(dias // 7)} semanas"
    if dias < 62.0:
        return "hace un mes"
    if dias < 365.0:
        return f"hace {int(dias // 30)} meses"
    anios = int(dias // 365)
    return "hace un año" if anios <= 1 else f"hace {anios} años"


def _fecha_corta(ts: float) -> str:
    """«15 de marzo de 2026». Vacío si la marca de tiempo no sirve."""
    try:
        d = datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError, OSError, OverflowError):
        return ""
    return f"{d.day} de {_MESES[d.month - 1]} de {d.year}"


# ===========================================================================
# Motores de recuperación (intercambiables)
# ===========================================================================
class BaseRetriever:
    """Interfaz de un motor de recuperación.

    Cambiar TF-IDF por embeddings es implementar esta clase y registrarla; ni
    main.py ni `search_history()` cambian una línea.
    """

    nombre = "base"

    def puntuar(self, consulta: str, documentos: list[dict]) -> list[float]:
        """Devuelve una similitud 0..1 por documento, en el mismo orden.

        Cada documento es un dict con al menos 'texto'. El motor PUEDE usar la
        clave 'terminos' si ya viene calculada (caché), pero no debe exigirla.
        """
        raise NotImplementedError


class TfidfRetriever(BaseRetriever):
    """TF-IDF + coseno sobre términos enriquecidos. Offline y sin dependencias.

    Enriquecido = palabra literal + raíz aproximada + concepto + n-gramas de
    letras. Es lo que permite que «extraño a mi mascota» encuentre «mi perro
    murió» sin instalar un modelo de embeddings.

    La puntuación mezcla DOS medidas, porque cada una sola se equivoca:

      * COSENO — parecido simétrico. Bueno, pero castiga a los mensajes largos
        y se diluye cuando la consulta trae palabras de relleno: «Andrea volvió
        a escribirme» se parece poco a un mensaje de veinte palabras aunque
        ambos hablen de Andrea.
      * COBERTURA — qué proporción del PESO de la consulta cubre el documento.
        Responde a «¿este recuerdo trata de lo que acaba de decir?», que es
        justo la pregunta que importa aquí, y no depende de lo largo que sea.

    La media de ambas es bastante más estable que cualquiera por separado.
    """

    nombre = "tfidf"

    #: Reparto entre coseno y cobertura. Expuesto por si se quiere afinar.
    peso_coseno = 0.5

    def puntuar(self, consulta: str, documentos: list[dict]) -> list[float]:
        q_terms = _terminos(consulta)
        if not q_terms or not documentos:
            return [0.0] * len(documentos)

        doc_terms = [d.get("terminos") or _terminos(d.get("texto", ""))
                     for d in documentos]
        n_docs = len(documentos)

        df = Counter()
        for terms in doc_terms:
            for t in terms:
                df[t] += 1

        def idf(t: str) -> float:
            return math.log((n_docs + 1) / (df.get(t, 0) + 1)) + 1.0

        def vector(terms: dict):
            v = {t: p * idf(t) for t, p in terms.items()}
            norma = math.sqrt(sum(w * w for w in v.values())) or 1.0
            return v, norma

        qv, qn = vector(q_terms)
        masa_q = sum(qv.values()) or 1.0
        w_cos = float(self.peso_coseno)
        w_cob = 1.0 - w_cos

        salida = []
        for terms in doc_terms:
            if not terms:
                salida.append(0.0)
                continue
            dv, dn = vector(terms)
            comunes = qv.keys() & dv.keys()
            if not comunes:
                salida.append(0.0)
                continue
            coseno = sum(qv[t] * dv[t] for t in comunes) / (qn * dn)
            cobertura = sum(qv[t] for t in comunes) / masa_q
            sim = w_cos * coseno + w_cob * cobertura
            salida.append(max(0.0, min(1.0, sim)))
        return salida


class EmbeddingRetriever(BaseRetriever):
    """Motor OPCIONAL por embeddings. No se activa solo y nunca descarga nada.

    Solo entra en juego si se pide expresamente (MEMORY_RELEVANCE_BACKEND =
    "embeddings") Y el modelo ya está instalado en la máquina. Si no lo está,
    cae a TF-IDF sin avisar al usuario y YUE sigue funcionando offline igual
    que siempre. Está aquí para demostrar que el diseño es intercambiable, no
    para añadir una dependencia por la puerta de atrás.
    """

    nombre = "embeddings"

    def __init__(self, modelo: str | None = None):
        self._respaldo = TfidfRetriever()
        self._modelo = None
        nombre_modelo = modelo or str(
            _cfg("MEMORY_RELEVANCE_EMBEDDING_MODEL",
                 "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"))
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            self._modelo = SentenceTransformer(nombre_modelo)
            _log(f"motor de embeddings listo: {nombre_modelo}")
        except Exception as exc:  # pragma: no cover - depende de la máquina
            _log(f"embeddings no disponibles ({exc}); uso TF-IDF")
            self._modelo = None

    def puntuar(self, consulta: str, documentos: list[dict]) -> list[float]:
        if self._modelo is None:
            return self._respaldo.puntuar(consulta, documentos)
        try:
            textos = [d.get("texto", "") for d in documentos]
            vectores = self._modelo.encode([consulta] + textos,
                                           normalize_embeddings=True)
            q = vectores[0]
            return [max(0.0, min(1.0, float(sum(q * v)))) for v in vectores[1:]]
        except Exception as exc:  # pragma: no cover
            _log(f"fallo al puntuar con embeddings ({exc}); uso TF-IDF")
            return self._respaldo.puntuar(consulta, documentos)


# Registro de motores. `register_retriever("mio", MiClase)` basta para añadir uno.
_MOTORES: dict = {
    "tfidf": TfidfRetriever,
    "embeddings": EmbeddingRetriever,
}


def register_retriever(nombre: str, fabrica) -> None:
    """Registra un motor de recuperación alternativo."""
    _MOTORES[str(nombre).strip().lower()] = fabrica


def build_retriever(nombre: str | None = None) -> BaseRetriever:
    """Crea el motor pedido. Ante cualquier problema, TF-IDF."""
    clave = str(nombre or _cfg("MEMORY_RELEVANCE_BACKEND", "tfidf")).strip().lower()
    fabrica = _MOTORES.get(clave, TfidfRetriever)
    try:
        return fabrica()
    except Exception as exc:  # pragma: no cover
        _log(f"no pude crear el motor '{clave}' ({exc}); uso TF-IDF")
        return TfidfRetriever()


class MemoryExtension:
    def __init__(self, db_path: str | Path, retriever: BaseRetriever | None = None):
        self.db_path = str(db_path)
        self._ensure_schema()
        # Motor de recuperación para la capa histórica. Se puede inyectar en las
        # pruebas o cambiar por embeddings desde config, sin tocar main.py.
        self.retriever: BaseRetriever = retriever or build_retriever()
        # Caché de tokenización por id de mensaje. Lo caro es partir el texto,
        # no multiplicar números: reutilizarlo entre turnos deja la búsqueda en
        # unos pocos milisegundos aunque haya miles de mensajes.
        self._cache_terminos: dict[int, dict] = {}

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _ensure_schema(self):
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT DEFAULT 'default',
                    title TEXT DEFAULT '',
                    topic TEXT DEFAULT '',
                    mode TEXT DEFAULT '',
                    device TEXT DEFAULT '',
                    started_at TEXT,
                    ended_at TEXT,
                    summary TEXT DEFAULT '',
                    emotion TEXT DEFAULT 'neutral',
                    tags TEXT DEFAULT '',
                    privacy TEXT DEFAULT 'normal',
                    status TEXT DEFAULT 'abierta'
                );
                CREATE TABLE IF NOT EXISTS conv_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER,
                    user_id TEXT DEFAULT 'default',
                    role TEXT,
                    content TEXT,
                    ts TEXT,
                    source TEXT DEFAULT 'teclado',
                    emotion TEXT DEFAULT '',
                    importance REAL DEFAULT 0.5,
                    confidence REAL DEFAULT 1.0,
                    edited INTEGER DEFAULT 0,
                    deleted INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_conv_msg_conv
                    ON conv_messages(conversation_id);
                CREATE INDEX IF NOT EXISTS idx_conv_msg_user
                    ON conv_messages(user_id);
                CREATE TABLE IF NOT EXISTS mem_backups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT,
                    path TEXT,
                    size_bytes INTEGER,
                    integrity TEXT
                );
                """
            )

    # ---- Ciclo de vida de una conversación --------------------------------
    def start_conversation(self, user_id="default", title="", topic="",
                           mode="", device="") -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO conversations (user_id,title,topic,mode,device,started_at,status)"
                " VALUES (?,?,?,?,?,?, 'abierta')",
                (user_id, title, topic, mode, device, now),
            )
            return cur.lastrowid

    def add_message(self, conversation_id: int, role: str, content: str,
                    user_id="default", source="teclado", emotion="",
                    importance=0.5, confidence=1.0) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO conv_messages "
                "(conversation_id,user_id,role,content,ts,source,emotion,importance,confidence)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (conversation_id, user_id, role, content, now, source,
                 emotion, importance, confidence),
            )
            return cur.lastrowid

    def end_conversation(self, conversation_id: int, summary="",
                         emotion="neutral", tags="", status="cerrada"):
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute(
                "UPDATE conversations SET ended_at=?, summary=?, emotion=?, tags=?, status=?"
                " WHERE id=?",
                (now, summary, emotion, tags, status, conversation_id),
            )

    def conversation_messages(self, conversation_id: int, include_deleted=False):
        q = "SELECT * FROM conv_messages WHERE conversation_id=?"
        if not include_deleted:
            q += " AND deleted=0"
        q += " ORDER BY id"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, (conversation_id,))]

    def mark_deleted(self, message_id: int):
        """Borrado lógico: el usuario puede eliminar un recuerdo sin romper la charla."""
        with self._conn() as c:
            c.execute("UPDATE conv_messages SET deleted=1 WHERE id=?", (message_id,))

    # ---- Búsqueda "por significado" (TF-IDF + coseno, local) --------------
    def _corpus(self, user_id="default"):
        """Trae mensajes no borrados + resúmenes de conversación como documentos."""
        docs = []
        with self._conn() as c:
            for r in c.execute(
                "SELECT id, conversation_id, role, content, ts FROM conv_messages"
                " WHERE deleted=0 AND user_id=?", (user_id,)
            ):
                docs.append({
                    "tipo": "mensaje", "id": r["id"], "conv": r["conversation_id"],
                    "role": r["role"], "texto": r["content"], "fecha": r["ts"],
                })
            for r in c.execute(
                "SELECT id, summary, ended_at, started_at FROM conversations"
                " WHERE user_id=? AND summary != ''", (user_id,)
            ):
                docs.append({
                    "tipo": "resumen", "id": r["id"], "conv": r["id"],
                    "role": "resumen", "texto": r["summary"],
                    "fecha": r["ended_at"] or r["started_at"],
                })
        return docs

    def search(self, query: str, top_k=5, min_score=0.05, user_id="default"):
        """Devuelve los recuerdos más parecidos al 'query' por significado.

        Cada resultado trae: texto original, fecha, conversación, rol, score
        (0..1) y 'grado' (exacto/aproximado/inferido) según la confianza.
        """
        q_tokens = _normalize(query)
        if not q_tokens:
            return []
        docs = self._corpus(user_id=user_id)
        if not docs:
            return []

        # Tokenizar corpus y calcular IDF.
        doc_tokens = [_normalize(d["texto"]) for d in docs]
        N = len(docs)
        df = Counter()
        for toks in doc_tokens:
            for t in set(toks):
                df[t] += 1
        idf = {t: math.log((N + 1) / (df_t + 1)) + 1.0 for t, df_t in df.items()}

        def vec(tokens):
            tf = Counter(tokens)
            v = {t: (tf[t] / len(tokens)) * idf.get(t, math.log(N + 1) + 1.0)
                 for t in tf}
            norm = math.sqrt(sum(w * w for w in v.values())) or 1.0
            return v, norm

        qv, qn = vec(q_tokens)

        resultados = []
        for d, toks in zip(docs, doc_tokens):
            if not toks:
                continue
            dv, dn = vec(toks)
            # Coseno solo sobre términos comunes.
            common = set(qv) & set(dv)
            if not common:
                continue
            dot = sum(qv[t] * dv[t] for t in common)
            score = dot / (qn * dn)
            if score >= min_score:
                resultados.append({**d, "score": round(score, 4),
                                   "grado": _grado(score)})

        resultados.sort(key=lambda r: r["score"], reverse=True)
        return resultados[:top_k]

    def remember(self, query: str, user_id="default") -> dict | None:
        """Atajo: el mejor recuerdo con una frase lista para que YUE la diga."""
        hits = self.search(query, top_k=1, user_id=user_id)
        if not hits:
            return None
        h = hits[0]
        fecha = (h["fecha"] or "")[:10]
        frase = {
            "exacto": f"Me hablaste de esto el {fecha}",
            "aproximado": f"Creo que algo de esto salió alrededor del {fecha}",
            "inferido": f"No estoy del todo segura, pero por el {fecha} tocamos algo parecido",
        }[h["grado"]]
        return {**h, "frase": frase, "fecha_corta": fecha}

    # =======================================================================
    # CAPA 3 · MEMORIA HISTÓRICA RELEVANTE (sobre la tabla `messages`)
    # =======================================================================
    # Todo lo de aquí abajo lee la tabla `messages` de core/memory.py, que es la
    # ÚNICA fuente del historial. No se copia ni un mensaje: si el usuario borra
    # su historial, aquí no queda nada.

    def _tabla_messages_existe(self) -> bool:
        try:
            with self._conn() as c:
                fila = c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
                ).fetchone()
            return fila is not None
        except sqlite3.Error:
            return False

    def _corte_reciente(self, skip_recent: int) -> int | None:
        """Id a partir del cual un mensaje se considera "reciente".

        Todo lo que tenga `id >= corte` ya viaja al modelo por
        `memory.recent_messages()`, así que NO puede volver como "recuerdo": ni
        el mensaje actual (que es el más nuevo y sería idéntico a la consulta),
        ni los últimos turnos. Se excluye por ID, no por parecido, que es la
        forma limpia de hacerlo cuando la arquitectura lo permite.
        """
        if skip_recent <= 0:
            return None
        try:
            with self._conn() as c:
                filas = c.execute(
                    "SELECT id FROM messages ORDER BY id DESC LIMIT ?",
                    (int(skip_recent),),
                ).fetchall()
        except sqlite3.Error:
            return None
        if not filas:
            return None
        return int(filas[-1]["id"])

    def _candidatos(self, *, role: str | None, skip_recent: int,
                    exclude_ids=None, max_candidates: int,
                    min_chars: int) -> list[dict]:
        """Mensajes ANTIGUOS que pueden llegar a ser un recuerdo."""
        if not self._tabla_messages_existe():
            return []
        condiciones = []
        parametros: list = []

        corte = self._corte_reciente(skip_recent)
        if corte is not None:
            condiciones.append("id < ?")
            parametros.append(corte)

        if role and str(role).lower() not in ("any", "todos", "*"):
            condiciones.append("role = ?")
            parametros.append(str(role))

        consulta = "SELECT id, role, content, ts FROM messages"
        if condiciones:
            consulta += " WHERE " + " AND ".join(condiciones)
        consulta += " ORDER BY id DESC LIMIT ?"
        parametros.append(int(max_candidates))

        try:
            with self._conn() as c:
                filas = c.execute(consulta, parametros).fetchall()
        except sqlite3.Error as exc:
            _log(f"no pude leer 'messages': {exc}")
            return []

        excluidos = {int(i) for i in (exclude_ids or [])}
        salida = []
        for fila in filas:
            mid = int(fila["id"])
            if mid in excluidos:
                continue
            texto = str(fila["content"] or "").strip()
            # Ruido que nunca es un recuerdo: comandos y respuestas de una palabra.
            if len(texto) < min_chars or texto.startswith("/"):
                continue
            terminos = self._cache_terminos.get(mid)
            if terminos is None:
                terminos = _terminos(texto)
                self._cache_terminos[mid] = terminos
            salida.append({
                "id": mid,
                "role": str(fila["role"] or ""),
                "texto": texto,
                "ts": float(fila["ts"] or 0.0),
                "terminos": terminos,
            })
        # La caché no puede crecer sin fin si YUE lleva meses encendida.
        if len(self._cache_terminos) > 8000:
            self._cache_terminos = {d["id"]: d["terminos"] for d in salida}
        return salida

    def search_history(self, query: str, *, top_k: int | None = None,
                       min_score: float | None = None,
                       skip_recent: int | None = None,
                       role=_AUTO,
                       exclude_ids=None,
                       max_chars: int | None = None,
                       now: float | None = None) -> list[dict]:
        """Recuerdos ANTIGUOS relevantes para `query`. La interfaz pública.

        Esta firma es el contrato con el resto del proyecto: si mañana el motor
        interno pasa a ser de embeddings, main.py no se entera.

        Parámetros
        ----------
        query        : el mensaje actual del usuario.
        top_k        : cuántos recuerdos como mucho (por defecto, config).
        min_score    : relevancia mínima. Si nada la supera, devuelve [].
        skip_recent  : cuántos mensajes del final se consideran "recientes" y
                       por tanto quedan FUERA (incluye el mensaje actual).
        role         : "user" por defecto (vía config). Las respuestas de YUE no
                       se convierten en hechos sobre el usuario. `None` o "any"
                       busca en todos los roles.
        exclude_ids  : ids concretos que no deben salir nunca.
        max_chars    : longitud máxima de cada recuerdo devuelto.

        Devuelve una lista de dicts: id, role, texto, ts, fecha, fecha_humana,
        score, similitud, grado. Lista vacía si no hay nada que merezca la pena.
        """
        ahora = time.time() if now is None else float(now)
        top_k = int(_cfg("MEMORY_RELEVANCE_TOP_K", 3) if top_k is None else top_k)
        if min_score is None:
            min_score = float(_cfg("MEMORY_RELEVANCE_MIN_SCORE", 0.20))
        if skip_recent is None:
            skip_recent = int(_cfg("MEMORY_RELEVANCE_SKIP_RECENT", 12))
        if max_chars is None:
            max_chars = int(_cfg("MEMORY_RELEVANCE_MAX_CHARS", 320))
        if role is _AUTO:
            role = _cfg("MEMORY_RELEVANCE_ROLE", "user")

        if not str(query or "").strip() or top_k <= 0:
            return []

        q_terms = _terminos(query)
        if not q_terms:
            return []

        docs = self._candidatos(
            role=role,
            skip_recent=int(skip_recent),
            exclude_ids=exclude_ids,
            max_candidates=int(_cfg("MEMORY_RELEVANCE_MAX_CANDIDATES", 4000)),
            min_chars=int(_cfg("MEMORY_RELEVANCE_MIN_CHARS", 12)),
        )
        _log(f"query: {str(query)[:80]}")
        _log(f"candidatos: {len(docs)}")
        if not docs:
            return []

        # Red de seguridad además del corte por id: si por lo que sea el mismo
        # texto sigue vivo más atrás en el historial, tampoco es un "recuerdo".
        q_raices = _raices_de(q_terms)

        try:
            similitudes = self.retriever.puntuar(query, docs)
        except Exception as exc:
            _log(f"el motor '{getattr(self.retriever, 'nombre', '?')}' falló: {exc}")
            return []

        vida_media = max(1.0, float(_cfg("MEMORY_RELEVANCE_HALFLIFE_DAYS", 45.0)))
        peso_recencia = float(_cfg("MEMORY_RELEVANCE_RECENCY_WEIGHT", 0.15))
        peso_importancia = float(_cfg("MEMORY_RELEVANCE_IMPORTANCE_WEIGHT", 0.10))

        puntuados = []
        for doc, sim in zip(docs, similitudes):
            if sim < min_score:
                continue
            if doc["texto"].strip().lower() == str(query).strip().lower():
                continue
            edad_dias = max(0.0, (ahora - doc["ts"]) / 86400.0) if doc["ts"] else 999.0
            recencia = 0.5 ** (edad_dias / vida_media)
            importancia = _importancia(doc["terminos"])
            # La relevancia MANDA: recencia e importancia solo multiplican un
            # poco. Así «Andrea me engañó» (hace 3 meses) gana a «hoy almorcé
            # pollo» (ayer) cuando se pregunta por Andrea.
            final = sim * (1.0 + peso_recencia * recencia
                           + peso_importancia * importancia)
            puntuados.append((final, sim, doc))

        if not puntuados:
            _log("ningún recuerdo supera el umbral; no se manda nada al prompt")
            return []

        puntuados.sort(key=lambda x: x[0], reverse=True)

        # --- Diversidad: nada de tres versiones del mismo momento ------------
        umbral_dup = float(_cfg("MEMORY_RELEVANCE_DIVERSITY", 0.6))
        elegidos: list[tuple] = []
        raices_elegidas: list[set] = []
        for final, sim, doc in puntuados:
            raices = _raices_de(doc["terminos"])
            if any(_jaccard(raices, previas) >= umbral_dup
                   for previas in raices_elegidas):
                continue
            elegidos.append((final, sim, doc))
            raices_elegidas.append(raices)
            if len(elegidos) >= top_k:
                break

        salida = []
        for posicion, (final, sim, doc) in enumerate(elegidos, start=1):
            _log(f"resultado {posicion} score={round(final, 4)} "
                 f"sim={round(sim, 4)} id={doc['id']}")
            salida.append({
                "id": doc["id"],
                "role": doc["role"],
                "texto": _recorta(doc["texto"], max_chars),
                "texto_completo": doc["texto"],
                "ts": doc["ts"],
                "fecha": _fecha_corta(doc["ts"]),
                "fecha_humana": _fecha_humana(doc["ts"], ahora),
                "score": round(float(final), 4),
                "similitud": round(float(sim), 4),
                "grado": _grado(sim),
                "coincide_en": sorted(_conceptos_de(doc["terminos"])
                                      & _conceptos_de(q_terms)),
            })
        return salida

    def context_block(self, query: str, *, now: float | None = None,
                      **kwargs) -> str:
        """Bloque de RECUERDOS listo para pegar al system_prompt. "" si no hay.

        Best-effort de principio a fin: cualquier fallo devuelve "" y YUE sigue
        conversando con el resto de sus memorias.
        """
        if not bool(_cfg("MEMORY_RELEVANCE_ENABLED", True)):
            return ""
        try:
            hits = self.search_history(query, now=now, **kwargs)
        except Exception as exc:
            _log(f"no pude recuperar recuerdos: {exc}")
            return ""
        if not hits:
            return ""

        lineas = []
        for h in hits:
            cuando = h["fecha_humana"] or ""
            fecha = h["fecha"] or ""
            marca = " · ".join(p for p in (cuando, fecha) if p)
            quien = ("Él te dijo" if h["role"] == "user"
                     else "TÚ (YUE) dijiste, NO es un dato suyo")
            lineas.append(f"- [{marca}] {quien}: «{h['texto']}»")

        return (
            "\n\nRECUERDOS RELEVANTES DEL HISTORIAL (cosas que él mismo te contó "
            "hace tiempo y que encajan con lo que está diciendo AHORA):\n"
            + "\n".join(lineas)
            + "\n(Úsalos SOLO si de verdad ayudan a responder este mensaje. La "
              "mayoría de los mensajes no necesitan ninguno: si no encaja con "
              "naturalidad, ignóralos por completo y no los menciones.\n"
              " · NUNCA digas «según mi base de datos», «encontré un recuerdo», "
              "«mi sistema de memoria indica» ni nada parecido. Eres una "
              "compañera que se acuerda de lo que hablaron, no un buscador.\n"
              " · NUNCA inventes nada que no esté escrito ahí arriba. Si el "
              "recuerdo no basta para estar segura, pregunta con cariño "
              "—«¿te refieres a la Andrea de aquel problema?»— en vez de "
              "afirmar algo que podría ser falso.\n"
              " · No cites la fecha salvo que el usuario pregunte por ella.)"
        )

    def clear_cache(self) -> None:
        """Olvida la tokenización guardada (tras borrar o editar el historial)."""
        self._cache_terminos.clear()

    # ---- Copias de seguridad + integridad ---------------------------------
    def backup(self, dest_dir: str | Path) -> dict:
        """Copia consistente de la base (API backup de SQLite) + verificación."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = dest / f"yue_backup_{stamp}.db"

        src = sqlite3.connect(self.db_path)
        try:
            dst = sqlite3.connect(str(out))
            with dst:
                src.backup(dst)
            dst.close()
        finally:
            src.close()

        integridad = self.verify_integrity(out)
        size = out.stat().st_size
        with self._conn() as c:
            c.execute(
                "INSERT INTO mem_backups (created_at,path,size_bytes,integrity)"
                " VALUES (?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), str(out), size, integridad),
            )
        return {"path": str(out), "size_bytes": size, "integrity": integridad,
                "ok": integridad == "ok"}

    @staticmethod
    def verify_integrity(db_path: str | Path) -> str:
        """Devuelve 'ok' si la base está sana, o el mensaje de error de SQLite."""
        try:
            c = sqlite3.connect(str(db_path))
            row = c.execute("PRAGMA integrity_check").fetchone()
            c.close()
            return row[0] if row else "desconocido"
        except sqlite3.Error as e:
            return f"error: {e}"

    def restore(self, backup_path: str | Path) -> dict:
        """Restaura desde un backup, PERO respalda primero la base actual.

        Nunca se pierde el estado previo: si algo sale mal, queda la copia de
        seguridad '.pre_restore' para volver atrás.
        """
        backup_path = Path(backup_path)
        if not backup_path.exists():
            return {"ok": False, "error": "el backup no existe"}
        if self.verify_integrity(backup_path) != "ok":
            return {"ok": False, "error": "el backup está corrupto; no se restaura"}

        safety = Path(self.db_path).with_suffix(
            Path(self.db_path).suffix + f".pre_restore_{datetime.now():%Y%m%d_%H%M%S}"
        )
        if Path(self.db_path).exists():
            shutil.copy2(self.db_path, safety)
        shutil.copy2(backup_path, self.db_path)
        ok = self.verify_integrity(self.db_path) == "ok"
        return {"ok": ok, "safety_copy": str(safety) if safety.exists() else None}

    def list_backups(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM mem_backups ORDER BY id DESC")]


def _grado(score: float) -> str:
    if score >= 0.45:
        return "exacto"
    if score >= 0.20:
        return "aproximado"
    return "inferido"

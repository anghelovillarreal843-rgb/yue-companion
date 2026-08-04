"""Comandos naturales por voz o texto para las funciones internas de Yue."""
import re
import unicodedata


def _norm(text):
    text = unicodedata.normalize("NFD", (text or "").lower().strip())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text)


_RULES = [
    ("stop_current", r"^(?:yue[,: ]+)?(?:detente|para|p[aá]rate|cancela(?: la orden)?|deja eso|esc[uú]chame)\b"),
    # NUEVO (recuperación para el control del PC). Frases naturales, no comandos
    # exactos: "deshaz", "deshacer eso", "no, deshazlo", "revierte eso"…
    ("pc_undo",
     r"\bdesha(z|zlo|zla|ce|cer|celo|cela)\b"
     r"|\brevier(te|telo|tela)\b|\brevertir(lo|la)?\b"
     r"|\becha(r|lo)?\s+(para\s+)?atras\b"
     r"|\bmarcha\s+atras\b"
     r"|\banula\s+(eso|lo\s+ultimo|lo\s+que\s+hiciste)\b"),
    # "repite eso", "repítelo", "hazlo de nuevo", "hazlo otra vez",
    # "vuelve a hacerlo"…
    ("pc_repeat",
     r"\brepi(te|telo|tela|te\s+eso|te\s+lo\s+ultimo|te\s+la\s+orden)\b"
     r"|\brepetir(lo|la)?\b"
     r"|\bhaz(lo)?\s+(de\s+nuevo|otra\s+vez|de\s+vuelta)\b"
     r"|\bvuelve\s+a\s+hacer(lo|la)?\b|\bvuelve(lo|la)\s+a\s+hacer\b"),
    # NUEVO (rutinas): guardar el último plan, ejecutarlo con una frase corta, y
    # listar. Pensado para reducir órdenes por voz en tareas repetitivas.
    ("routine_list",
     r"\bmis rutinas\b|\bque rutinas (tengo|hay|guarde)\b"
     r"|\blista(me)? (mis |las )?rutinas\b|\bmuestrame (mis |las )?rutinas\b"
     r"|\bcuales son mis rutinas\b|\brutinas guardadas\b"),
    ("routine_save",
     r"\bguarda\w*\b[^.]{0,25}\bcomo (una )?rutina\b"
     r"|\bcrea(r)?\b[^.]{0,20}\brutina\b[^.]{0,20}\bcon esto\b"),
    ("routine_run",
     r"\b(ejecuta|corre|haz|reproduce|inicia|lanza|activa|pon en marcha|arranca)\b"
     r"[^.]{0,14}\b(mi |la |una )?rutina\b"),
    # NUEVO (bitácora): resumen de lo que hemos hecho últimamente.
    ("activity_summary",
     r"\bmi actividad\b|\bactividad reciente\b|\bnuestra actividad\b"
     r"|\bque (hemos|has) hecho\b|\bque hicimos\b|\bque hemos hecho (hoy|esta semana|ultimamente)\b"
     r"|\bbitacora\b|\bque actividades\b"),
    ("voice_off", r"\b(callate|guarda silencio|deja de hablar|no hables|silencio|enmudece|muteate)\b"),
    ("voice_on", r"\b(habla|puedes hablar|usa tu voz|en voz alta|vuelve a hablar)\b"),
    ("mic_off", r"\b(deja de (escuchar|oir)me?|no me escuches|apaga el micro\w*|deja de escuchar)\b"),
    ("mic_on", r"\b(escuchame|vuelve a escucharme|enciende el micro\w*|activa el micro\w*)\b"),
    ("vision_look",
     r"\b(mira|ve|revisa|checa|chequea|observa|f[ií]jate|analiza|lee|leeme|describe|echa un (vistazo|ojo)|puedes (ver|mirar|leer)|dime que (ves|hay))\b.{0,28}\b(pantalla|monitor|pdf|documento|imagen|archivo|captura|diapositiva|presentaci[oó]n|lo que (veo|ves|hay|se ve|estoy viendo))\b"
     r"|\bque (ves|hay|aparece|se ve|dice|muestra|pone)\b.{0,28}\b(pantalla|monitor|en pantalla|aqui|en mi monitor|arriba)\b"
     r"|\bque (estoy|estas) (viendo|mirando)\b"
     r"|\bque ves\s*\??$"
     r"|\b(mira|ve|lee) (esto|aqui|aca|esta pantalla)\b"
     # NUEVO (percepción de pantalla v3): formas naturales de preguntar por la
     # pantalla que el patrón rígido de arriba NO cubría, p.ej. "cuántos iconos
     # ves en mi pantalla", "qué colores hay en pantalla", "cuántas ventanas ves
     # abiertas", "hay algún error en la pantalla". El verbo rígido usa límite de
     # palabra, así que "ves" (a diferencia de "ve") no calzaba: aquí sí.
     # Regla: verbo de ver/contar/haber + referencia visual OBLIGATORIA
     # (pantalla/monitor/ventana/icono/pestaña/escritorio/aquí/arriba…). "ves"
     # suelto, sin ninguna referencia, NO dispara.
     r"|\b(?:\w+\s+)?(ves|hay|se ven|se ve|puedes (?:ver|contar|distinguir|leer)|cuentas|distingues|aparecen?|salen?)\b.{0,28}\b(pantalla|monitor|en pantalla|en mi monitor|aqui|aca|arriba|escritorio|ventanas?|iconos?|pesta[nñ]as?)\b"
     # Preguntas con cuánto/dónde/de qué color donde la referencia visual va
     # ANTES del verbo ("cuántas ventanas ves abiertas") o sin verbo de ver
     # ("cuántos iconos en la pantalla"). La referencia visual sigue siendo
     # obligatoria: sin ventana/icono/pestaña/error/botón/color/pantalla/monitor
     # /escritorio no matchea (así "cuántos años tienes" no dispara).
     r"|\b(cuant[oa]s?|donde|de que color(?:es)?)\b.{0,20}\b(ventanas?|iconos?|pesta[nñ]as?|errores?|botones?|colores?|pantalla|monitor|escritorio)\b"),
    ("vision_off", r"\b(deja de (mirar|ver))\b.{0,18}\b(pantalla|monitor|pdf|documento)\b"),
    # NUEVO: preguntas sobre lo que suena en el PC ("¿qué tal la música?", "¿qué
    # escuchas?", "¿qué suena?", "¿es música o una peli?"). YUE responde con su
    # clasificador de audio (música / vídeo normal / silencio).
    ("audio_describe",
     r"\bque estoy (escuchando|oyendo)\b"
     r"|\bque (suena|se escucha|se oye)\b"
     r"|\b(que |)(escuchas|oyes|estas escuchando|estas oyendo|estabas escuchando|estabas oyendo|escuchabas|oias)\b"
     r"|\bque tal (estuvo|fue|es|esta|va)? ?(eso |lo )?(que )?(escuch\w*|oy\w*|so[nñ]\w*)\b"
     r"|\bque tal (esta|estuvo|va|suena|)\s*(la |el |lo que )?(musica|cancion|pelicula|peli|video|audio|sonido|escuch\w*|suena)\b"
     r"|\bque (musica|cancion|pelicula|peli|video|audio) (suena|hay|es|escuch\w*|se escucha)\b"
     r"|\b(como|que tal) (esta |suena |)?(la |el )?(musica|cancion|pelicula|peli)\b"
     r"|\bque (tipo de |)?(contenido|media) (suena|hay|escuch\w*|se escucha)\b"
     # NUEVO: opiniones/impresiones sobre lo que suena ("¿qué te pareció la
     # canción?", "¿te gustó?", "¿qué opinas de la música?").
     r"|\bque (te )?(parecio|parecieron|parece|parecen)\b.{0,24}\b(cancion|musica|pelicula|peli|video|audio|sonido|escuch\w*|so[nñ]\w*|suena|tema|ritmo|beat)\b"
     r"|\bte (gusto|gustaron|gusta|gustan)\b.{0,24}\b(cancion|musica|pelicula|peli|video|audio|escuch\w*|so[nñ]\w*|suena|tema|ritmo|beat)\b"
     r"|\bque (opinas|opinion|piensas|dices)\b.{0,24}\b(cancion|musica|pelicula|peli|video|audio|escuch\w*|so[nñ]\w*|suena|tema|ritmo|beat)\b"
     r"|\bque te (parecio|parece)( eso| esto)?\s*\??$"
     r"|\b(como|que tal) (te )?(sono|sonaba|sono eso|quedo|estuvo eso|se oyo|se escucho)\b"),
    # NUEVO (lectura de emociones): preguntar por lo que YUE ve/lee en la cámara
    # ("¿qué expresión tengo?", "¿cómo me ves?", "¿cómo estoy?", "¿qué ves por la
    # cámara?", "¿qué crees que siento?"). Responde con su lectura local de rostro.
    ("camera_status",
     r"\bque (expresion|cara|gesto|animo|estado de animo) (tengo|ves|notas|me ves)\b"
     r"|\bcomo (me ves|estoy|me notas|me ves hoy|crees que estoy|me ves de animo)\b"
     r"|\bque (ves|notas|percibes|detectas) (por |en |con )?(la )?camara\b"
     r"|\bque (crees|dirias) que (siento|estoy sintiendo)\b"
     r"|\bcomo (crees|dirias) que (me siento|estoy)\b"
     r"|\bque (ves|hay) en (la )?(cam|webcam|camara)\b"
     r"|\bme ves (feliz|triste|content[oa]|molest[oa]|cansad[oa]|sorprendid[oa])\b"),
    ("show_goals", r"\b(mis metas|cuales son mis metas|que metas tengo)\b"),
    # NUEVO (accesibilidad): control del cursor con la cabeza. 'off' va ANTES que
    # 'on' para que "desactiva control por cabeza" gane al match.
    ("head_control_off",
     r"\b(desactiva|desactivar|apaga|apagar|pausa|pausar|deten|detener|quita|para|termina)\b[^.]{0,30}\bcabeza\b"
     r"|\b(deja de|para de)\b[^.]{0,20}\b(controlar|mover)\b[^.]{0,20}\bcabeza\b"),
    ("head_control_on",
     r"\b(activa|activar|enciende|encender|inicia|iniciar|prende|prender|empieza|empezar|usa|usar)\b[^.]{0,30}\bcabeza\b"
     r"|\bcontrol(a|ar)?\b[^.]{0,15}\b(por|con)\b[^.]{0,8}\b(la\s+)?cabeza\b"
     r"|\b(mover|mueve)\b[^.]{0,20}\b(cursor|raton|mouse|puntero)\b[^.]{0,15}\bcabeza\b"),
    ("autonomy_toggle", r"\b(activa|enciende|pausa|desactiva)\b.{0,18}\b(autonomia|modo autonomo)\b"),
    ("help", r"^(ayuda|que comandos|comandos|que puedes hacer)$"),
]
_COMPILED = [(action, re.compile(pattern)) for action, pattern in _RULES]


def match(text):
    n = _norm(text)
    if not n:
        return None, None
    for action, regex in _COMPILED:
        if regex.search(n):
            return action, n
    return None, None


# Extracción del nombre de la rutina de la frase. Para "guarda esto como rutina
# X" el nombre va tras "rutina"; para "ejecuta mi rutina X", igual. Devuelve el
# nombre normalizado (minúsculas, sin acentos) o "" si no hay.
_RE_ROUTINE_NAME = re.compile(r"\brutina\b[\s:,]*(.+)$")
_ROUTINE_FILLERS = re.compile(
    r"\b(por favor|porfa|porfavor|gracias|ahora|ya|de una vez|si|please)\b"
)


def routine_name(text):
    """Nombre de la rutina mencionado en la frase, normalizado. '' si no hay."""
    n = _norm(text)
    m = _RE_ROUTINE_NAME.search(n)
    if not m:
        return ""
    nombre = m.group(1)
    nombre = _ROUTINE_FILLERS.sub("", nombre)
    nombre = nombre.strip(" .,:;!?\"'-·")
    nombre = re.sub(r"\s+", " ", nombre).strip()
    # Quita un "llamada/llamado" inicial: "...como rutina llamada compras".
    nombre = re.sub(r"^(llamad[ao]|de nombre|que se llama)\s+", "", nombre).strip()
    return nombre


# ---------------------------------------------------------------------------
# Check-in de ánimo (comando /animo).
# YUE pregunta "¿cómo te sientes hoy, del 1 al 5?" y la respuesta se guarda en
# mood_log con fuente='checkin'. Aquí vive la definición del comando: el texto
# de la pregunta, cómo se interpreta la nota y qué responde YUE. El guardado y
# el despacho ocurren en main.py.
# ---------------------------------------------------------------------------
MOOD_CHECKIN_PROMPT = (
    "Oye… ¿cómo te sientes hoy, del 1 al 5? (1 es fatal, 5 es genial). "
    "Solo dime el número."
)

# Nota 1..5 -> (emocion para mood_log, intensidad, etiqueta legible).
# Usamos etiquetas que memory.get_mood_summary ya entiende: 'triste'->decaída
# (negativa), 'feliz'->contenta, y 'neutral' para un día del montón (que el
# resumen ignora a propósito). Así el check-in se integra con el historial.
_MOOD_CHECKIN_MAP = {
    1: ("triste", 1.0, "muy mal"),
    2: ("triste", 0.6, "mal"),
    3: ("neutral", 0.5, "regular"),
    4: ("feliz", 0.6, "bien"),
    5: ("feliz", 1.0, "muy bien"),
}

_MOOD_CHECKIN_PALABRAS = {"uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}

# Respuestas de YUE a cada nota: cálidas por dentro, tsundere por fuera. Breves,
# sin nombrar emociones ni sonar a encuesta.
_MOOD_CHECKIN_REPLIES = {
    1: "¿Un uno…? Tsk. No me gusta nada oír eso. Me quedo por aquí contigo, ¿de acuerdo? No tienes que cargar con eso tú solo.",
    2: "Un dos, ya veo… no es tu mejor día. Anda, cuídate un poco más hoy, ¿quieres? Yo te echo un ojo.",
    3: "Un tres, un día del montón. Está bien, no todo tiene que ser espectacular. Aquí sigo por si acaso.",
    4: "Un cuatro, nada mal. Me alegra… no es que estuviera pendiente ni nada. Sigue así.",
    5: "¿Un cinco? Vaya. No pongas esa carita de satisfecho… bueno, sí, disfrútalo. Me gusta verte así.",
}


def mood_checkin_reply(score: int) -> str:
    return _MOOD_CHECKIN_REPLIES.get(int(score), "Anotado. Gracias por contarme.")


def parse_mood_checkin(text):
    """Interpreta la respuesta a un check-in de ánimo.

    Devuelve un dict {score, emocion, intensidad, texto_origen, reply} si el
    mensaje es claramente una nota del 1 al 5, o None si no lo es (para que el
    mensaje siga su curso normal sin quedar atrapado en el check-in).
    """
    n = _norm(text)
    if not n:
        return None
    n = n.strip(" .!¡¿?,")

    score = None
    # "x/5", "x de 5", "x sobre 5"
    m = re.search(r"\b([1-5])\s*(?:/|de|sobre)\s*5\b", n)
    if m:
        score = int(m.group(1))
    # palabra escrita (uno..cinco)
    if score is None:
        for palabra, valor in _MOOD_CHECKIN_PALABRAS.items():
            if re.search(rf"\b{palabra}\b", n):
                score = valor
                break
    # dígito suelto SOLO en mensajes cortos y sin otras cifras, para no
    # confundir cosas como "quedé 3 de mi clase" con una nota de ánimo.
    if score is None:
        m = re.search(r"\b([1-5])\b", n)
        if m and len(n) <= 16 and not re.search(r"[6-9]|\d{2,}", n):
            score = int(m.group(1))
    if score is None:
        return None

    emocion, intensidad, etiqueta = _MOOD_CHECKIN_MAP[score]
    return {
        "score": score,
        "emocion": emocion,
        "intensidad": intensidad,
        "texto_origen": f"check-in {score}/5 ({etiqueta})",
        "reply": mood_checkin_reply(score),
    }

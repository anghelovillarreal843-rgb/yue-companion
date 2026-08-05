"""Configuración central de YUE. Lee el archivo .env (privado)."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Funciona tanto ejecutando con Python como empaquetado en .exe (PyInstaller).
if getattr(sys, "frozen", False):
    # RES_DIR: recursos empaquetados (avatar.html, assets, .vrm) dentro del bundle.
    RES_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    # APP_DIR: junto al ejecutable, donde viven los datos del usuario (.env, data, recuerdos).
    APP_DIR = Path(sys.executable).resolve().parent
else:
    RES_DIR = Path(__file__).resolve().parent
    APP_DIR = RES_DIR

BASE_DIR = RES_DIR  # se usa para servir avatar.html y los assets por http local
# El .env vive junto al ejecutable (o al proyecto) y NO se sube a internet.
load_dotenv(APP_DIR / ".env")

# ---- Groq ----
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
# Respaldos por si Groq retira el modelo del .env (pasa cada pocos meses y es la
# causa del 404 en cadena: el .env apunta a un modelo que ya no existe).
GROQ_MODEL_FALLBACKS = tuple(
    m.strip() for m in os.getenv(
        "GROQ_MODEL_FALLBACKS",
        "llama-3.3-70b-versatile,openai/gpt-oss-120b,llama-3.1-8b-instant",
    ).split(",") if m.strip()
)
GROQ_VISION_FALLBACKS = tuple(
    m.strip() for m in os.getenv(
        "GROQ_VISION_FALLBACKS",
        "meta-llama/llama-4-maverick-17b-128e-instruct,"
        "meta-llama/llama-4-scout-17b-16e-instruct",
    ).split(",") if m.strip()
)


# --- Visión por otro proveedor ------------------------------
# Groq retiró TODOS sus modelos con visión de algunas cuentas. Como Together y
# OpenAI hablan el mismo dialecto (API compatible con OpenAI), se puede mandar
# SOLO la visión a otro sitio y dejar el chat en Groq.
# VISION_PROVIDER: groq | together | openai | none
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "groq").strip().lower()
VISION_BASE_URL = os.getenv("VISION_BASE_URL", "").strip().rstrip("/")
VISION_API_KEY = os.getenv("VISION_API_KEY", "").strip()
VISION_MODEL = os.getenv("VISION_MODEL", "").strip()
# Si es true, al arrancar YUE valida la clave de visión (GET /models) y avisa en
# el chat si el proveedor la rechaza (401/403) en vez de fallar en silencio.
VISION_PREFLIGHT = os.getenv("VISION_PREFLIGHT", "true").strip().lower() in (
    "1", "true", "yes", "si", "sí", "on",
)
# Si es true, la visión de pantalla NO usa modelo multimodal: lee el TEXTO de la
# pantalla por OCR y responde con el modelo de chat. Útil cuando no hay clave de
# visión válida. Perfecto para PDFs, documentos y páginas con texto; no describe
# fotos ni imágenes sin texto.
VISION_OCR_ONLY = os.getenv("VISION_OCR_ONLY", "false").strip().lower() in (
    "1", "true", "yes", "si", "sí", "on",
)


def vision_endpoint():
    """Devuelve (base_url, api_key, modelo) para las llamadas con imagen.

    Si algo falta, cae al proveedor de siempre (Groq) y que el respaldo de
    core/ai_fallback.py decida. Nunca revienta.
    """
    prov = VISION_PROVIDER
    if prov == "none":
        return ("", "", "")
    if prov == "together":
        return (
            VISION_BASE_URL or TOGETHER_BASE_URL,
            VISION_API_KEY or TOGETHER_API_KEY,
            VISION_MODEL or "meta-llama/Llama-4-Scout-17B-16E-Instruct",
        )
    if prov == "openai":
        return (
            VISION_BASE_URL or OPENAI_IMAGE_BASE_URL,
            VISION_API_KEY or OPENAI_IMAGE_KEY,
            VISION_MODEL or "gpt-4o-mini",
        )
    if prov == "custom":
        return (VISION_BASE_URL, VISION_API_KEY, VISION_MODEL)
    return (
        VISION_BASE_URL or GROQ_BASE_URL,
        VISION_API_KEY or GROQ_API_KEY,
        VISION_MODEL or GROQ_VISION_MODEL,
    )


TEMPERATURE = float(os.getenv("TEMPERATURE", "0.85"))


# ---- Visión de pantalla ----
def _truthy(v):
    return str(v).lower() in ("1", "true", "yes", "si", "sí", "on")

# La visión de pantalla queda disponible desde que inicia la IA. No comenta
# periódicamente: solo analiza la pantalla al recibir una orden de PC o una
# petición explícita como /mira.
VISION_ENABLED = _truthy(os.getenv("VISION_ENABLED", "true"))
VISION_AUTOCOMMENT = _truthy(os.getenv("VISION_AUTOCOMMENT", "false"))
VISION_INTERVAL = int(os.getenv("VISION_INTERVAL", "90"))  # compatibilidad

# ---- Cámara local automática ----
CAMERA_ENABLED = _truthy(os.getenv("CAMERA_ENABLED", "true"))
CAMERA_ANALYSIS_INTERVAL = float(os.getenv("CAMERA_ANALYSIS_INTERVAL", "1.2"))
CAMERA_RETRY_INTERVAL = float(os.getenv("CAMERA_RETRY_INTERVAL", "60"))
# NUEVO (autoconexión): cada cuántos segundos se sondea si YA hay una cámara
# disponible cuando todavía no se encontró ninguna. Bajo a propósito (3 s) para
# que, si enciendes/conectas una cámara con YUE abierta, se enganche sola casi
# al momento sin reiniciar. Súbelo si no quieres que sondee tan seguido.
CAMERA_SEARCH_INTERVAL = float(os.getenv("CAMERA_SEARCH_INTERVAL", "3"))
CAMERA_MAX_INDEX = int(os.getenv("CAMERA_MAX_INDEX", "3"))
CAMERA_WIDTH = int(os.getenv("CAMERA_WIDTH", "640"))
CAMERA_HEIGHT = int(os.getenv("CAMERA_HEIGHT", "480"))
CAMERA_MAX_PEOPLE = int(os.getenv("CAMERA_MAX_PEOPLE", "2"))
CAMERA_CONTEXT_IN_CHAT = _truthy(os.getenv("CAMERA_CONTEXT_IN_CHAT", "true"))
CAMERA_CONTEXT_MAX_AGE = float(os.getenv("CAMERA_CONTEXT_MAX_AGE", "8"))

# NUEVO: si es false, YUE solo HABLA sus respuestas y no las escribe en el chat.
# Salvaguarda: si la voz está apagada, el texto se muestra igual para que no se
# quede muda. Los avisos de estado (p. ej. "Voz desactivada") siempre se ven.
CHAT_MOSTRAR_RESPUESTAS = _truthy(os.getenv("CHAT_MOSTRAR_RESPUESTAS", "false"))
CAMERA_DOWNLOAD_MODELS = _truthy(os.getenv("CAMERA_DOWNLOAD_MODELS", "true"))
CAMERA_FACE_MODEL_URL = os.getenv(
    "CAMERA_FACE_MODEL_URL",
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task",
)
CAMERA_POSE_MODEL_URL = os.getenv(
    "CAMERA_POSE_MODEL_URL",
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
)

# ---- NUEVO (lectura de emociones por cámara) ----
# YUE estima cómo se siente la persona por su expresión y ACOMPAÑA con la cara
# del avatar (si te ve triste, se preocupa; no imita el enfado). Solo pone gesto,
# no habla sola. Se apaga con CAMERA_EMPATHY_ENABLED=false.
CAMERA_EMPATHY_ENABLED = _truthy(os.getenv("CAMERA_EMPATHY_ENABLED", "true"))
# Confianza mínima de la lectura facial para que YUE reaccione con el avatar.
CAMERA_EMPATHY_MIN_CONFIDENCE = float(os.getenv("CAMERA_EMPATHY_MIN_CONFIDENCE", "0.55"))
# Segundos mínimos entre reacciones empáticas del mismo ánimo (anti-parpadeo).
CAMERA_EMPATHY_MIN_GAP = float(os.getenv("CAMERA_EMPATHY_MIN_GAP", "8.0"))

# ---- NUEVO (YUE HABLA al ver tu emoción por la cámara) ----
# Cuando YUE ve por la cámara una emoción clara (o detecta distrés sostenido =
# "peligro"), te lo comenta EN VOZ y te pregunta por qué estás así, con su tono.
# NO habla a cada gesto: solo cuando la emoción es fuerte y se mantiene, o cuando
# hay señal de peligro. Con todo un sistema de cuentagotas para no ser pesada.
# Se apaga por completo con CAMERA_EMOTION_TALK_ENABLED=false.
CAMERA_EMOTION_TALK_ENABLED = _truthy(os.getenv("CAMERA_EMOTION_TALK_ENABLED", "true"))
# Confianza mínima para considerar la emoción "muy fuerte" y digna de preguntar.
CAMERA_EMOTION_TALK_MIN_CONFIDENCE = float(os.getenv("CAMERA_EMOTION_TALK_MIN_CONFIDENCE", "0.72"))
# Cuántas lecturas seguidas de la MISMA emoción hacen falta antes de hablar (evita
# reaccionar a un gesto de un segundo). La cámara analiza ~1 vez/seg.
CAMERA_EMOTION_TALK_MIN_STREAK = int(os.getenv("CAMERA_EMOTION_TALK_MIN_STREAK", "3"))
# Cuentagotas GLOBAL: segundos mínimos entre dos comentarios hablados de emoción,
# sea cual sea (para que no hable seguido). 240 s = 4 min por defecto.
CAMERA_EMOTION_TALK_MIN_GAP = float(os.getenv("CAMERA_EMOTION_TALK_MIN_GAP", "240.0"))
# Cuentagotas por MISMA emoción: no vuelve a preguntar por el mismo ánimo hasta
# pasado este tiempo, aunque siga. 900 s = 15 min por defecto.
CAMERA_EMOTION_TALK_SAME_GAP = float(os.getenv("CAMERA_EMOTION_TALK_SAME_GAP", "900.0"))

# ---- NUEVO (reacción emocional al audio: música / vídeos) ----
# YUE afina la CARA con lo que siente al escuchar (melancólica con algo lento y
# oscuro, tierna con algo suave, con chispa con lo movido…). Aditivo sobre la
# reacción de audio existente. Se apaga con AUDIO_FEEL_EMOTIONS=false.
AUDIO_FEEL_EMOTIONS = _truthy(os.getenv("AUDIO_FEEL_EMOTIONS", "true"))

# ---- Voz ----
# Motor de voz: "kokoro" (voz neuronal local, recomendado) | "gtts" (Google, online)
TTS_ENGINE = os.getenv("TTS_ENGINE", "edge").strip().lower()
TTS_ENABLED = os.getenv("TTS_ENABLED", "true").lower() in ("1", "true", "yes", "si", "sí")
TTS_LANG = os.getenv("TTS_LANG", "es")
TTS_TLD = os.getenv("TTS_TLD", "com.mx")

# --- Edge-TTS (voz neuronal de Microsoft, motor principal de Yue) ---
# Voz femenina joven y expresiva en español mexicano; encaja con la
# personalidad tsundere/juguetona de Yue. AQUÍ SE CAMBIA LA VOZ.
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "es-MX-DaliaNeural")
# Ajustes de prosodia: un poco más rápida (como KOKORO_SPEED=1.06) y con
# tono ligeramente más agudo para sonar más juvenil/anime.
EDGE_TTS_RATE = os.getenv("EDGE_TTS_RATE", "+8%")
EDGE_TTS_PITCH = os.getenv("EDGE_TTS_PITCH", "+12Hz")

# ---- Kokoro TTS (voz neuronal local, funciona sin internet) ----
# Voz: "ef_dora" es la voz FEMENINA en español de Kokoro (ideal para Yue).
KOKORO_VOICE = os.getenv("KOKORO_VOICE", "ef_dora")
KOKORO_LANG = os.getenv("KOKORO_LANG", "es")
# Velocidad del habla (1.0 normal; un poco más ágil le pega a su carácter chispeante):
KOKORO_SPEED = float(os.getenv("KOKORO_SPEED", "1.06"))
# Archivos del modelo Kokoro. Por defecto se buscan en assets/kokoro/.
# Descárgalos una sola vez (ver README): kokoro-v1.0.onnx y voices-v1.0.bin
_KOKORO_DIR = RES_DIR / "assets" / "kokoro"
KOKORO_MODEL = os.getenv("KOKORO_MODEL", str(_KOKORO_DIR / "kokoro-v1.0.onnx"))
KOKORO_VOICES = os.getenv("KOKORO_VOICES", str(_KOKORO_DIR / "voices-v1.0.bin"))

# ---- Microfono (escucha automatica) ----
# Yue escucha sola, sin boton: reconoce tu voz y responde.
MIC_ENABLED = _truthy(os.getenv("MIC_ENABLED", "true"))
# Idioma del reconocimiento: es-ES, es-MX, es-PE, en-US, ...
MIC_LANG = os.getenv("MIC_LANG", "es-ES")
# Indice del microfono a usar. Vacio = micro por defecto del sistema.
# Para ver la lista de micros y sus indices:  python main.py --mics
_mic_idx = os.getenv("MIC_DEVICE_INDEX", "").strip()
MIC_DEVICE_INDEX = int(_mic_idx) if _mic_idx.lstrip("-").isdigit() else None
# Sensibilidad: umbral de energia minimo. Si "no te escucha", BAJA este numero
# (p.ej. 150). Si capta ruido de fondo de mas, SUBELO (p.ej. 600).
MIC_ENERGY_THRESHOLD = int(os.getenv("MIC_ENERGY_THRESHOLD", "250"))
# Ajuste automatico del umbral segun el ruido ambiente (recomendado: true).
MIC_DYNAMIC = _truthy(os.getenv("MIC_DYNAMIC", "true"))
# Silencio (segundos) que marca el fin de una frase. MÁS BAJO = responde antes
# (detecta el fin de tu frase más rápido). Bajado de 0.8 a 0.55 para que YUE
# reaccione más rápido cuando le hablas por voz. Si te corta a media frase, súbelo.
MIC_PAUSE = float(os.getenv("MIC_PAUSE", "0.55"))
MIC_PHRASE_LIMIT = int(os.getenv("MIC_PHRASE_LIMIT", "14"))
# Tiempo adicional sin captura despues de hablar y similitud para descartar eco.
# Bajado de 1.2 a 0.6 s: tras dejar de hablar, YUE vuelve a escucharte antes.
MIC_ECHO_COOLDOWN = float(os.getenv("MIC_ECHO_COOLDOWN", "0.6"))
MIC_ECHO_SIMILARITY = float(os.getenv("MIC_ECHO_SIMILARITY", "0.68"))
# Interrupción natural: el micrófono sigue activo mientras Yue habla y descarta su eco.
MIC_BARGE_IN_ENABLED = _truthy(os.getenv("MIC_BARGE_IN_ENABLED", "true"))
MIC_BARGE_IN_REQUIRE_WAKE_WORD = _truthy(os.getenv("MIC_BARGE_IN_REQUIRE_WAKE_WORD", "false"))
MIC_BARGE_IN_MIN_CHARS = int(os.getenv("MIC_BARGE_IN_MIN_CHARS", "5"))
MIC_WAKE_WORDS = tuple(
    item.strip() for item in os.getenv("MIC_WAKE_WORDS", "yue,oye yue").split(",") if item.strip()
)

# ---- NUEVO (interrupción por voz fiable) ----
# Puerta "inteligente" de barge-in: hace que, al hablarle por voz mientras YUE
# habla, se detenga y arranque la nueva conversación (igual que al escribir).
# Ponlo en false para volver EXACTAMENTE al filtrado anterior.
MIC_BARGE_IN_SMART = _truthy(os.getenv("MIC_BARGE_IN_SMART", "true"))
# Longitud mínima (caracteres) para interrumpir con voz que NO sea una orden de
# mando. Más bajo que el doble-mínimo anterior, para que tu voz real pase.
MIC_BARGE_IN_GATE_MIN_CHARS = int(os.getenv("MIC_BARGE_IN_GATE_MIN_CHARS", "4"))
# Solape de palabras con el TTS por encima del cual se trata como eco (0-1).
# Más ALTO = más permisivo con tu voz mientras YUE habla. (Antes efectivo ~0.34.)
MIC_BARGE_IN_ECHO_OVERLAP = float(os.getenv("MIC_BARGE_IN_ECHO_OVERLAP", "0.62"))
# Palabras extra que fuerzan la interrupción inmediata (se suman a las de serie:
# para, espera, detente, cállate, oye, stop, yue…). Separadas por comas.
MIC_BARGE_IN_WORDS = tuple(
    item.strip() for item in os.getenv("MIC_BARGE_IN_WORDS", "").split(",") if item.strip()
)

# ---- NUEVO (petición 2): filtro de audio de vídeo/música en el micrófono ----
# Cuando hay un vídeo o música sonando por los altavoces, ese sonido no debe
# escribirse en el chat como si le hablaras tú a Yue.
# Si está activo, mientras suene media el micro exige la palabra clave («Yue…»)
# para tomarte en cuenta (así puedes seguir dándole órdenes viendo algo).
MIC_MEDIA_GUARD_ENABLED = _truthy(os.getenv("MIC_MEDIA_GUARD_ENABLED", "true"))
MIC_MEDIA_GUARD_REQUIRE_WAKE_WORD = _truthy(os.getenv("MIC_MEDIA_GUARD_REQUIRE_WAKE_WORD", "true"))
# Longitud mínima (caracteres) para aceptar algo sin palabra clave con media sonando.
MIC_MEDIA_GUARD_MIN_CHARS = int(os.getenv("MIC_MEDIA_GUARD_MIN_CHARS", "8"))

# ---- NUEVO (petición 3): refuerzo contra que Yue se escuche a sí misma ----
# Segundos extra de "vigilancia de eco" tras dejar de hablar (además del cooldown).
MIC_SELF_LISTEN_TAIL = float(os.getenv("MIC_SELF_LISTEN_TAIL", "2.5"))
# Umbral de solape de palabras con su propio TTS para tratar algo como eco parcial
# mientras habla o en la cola (más bajo = más estricto). Antes estaba fijo en 0.40.
MIC_SPEAKING_ECHO_OVERLAP = float(os.getenv("MIC_SPEAKING_ECHO_OVERLAP", "0.34"))

# ---- NUEVO (arreglo "no me escucha"): tope y auto-recuperación del micro ----
# Tope MÁXIMO del umbral de energía tras el ajuste al ruido ambiente. Antes estaba
# fijo (código) en max(MIC_ENERGY_THRESHOLD,120)*4 = 1000 por defecto. Si tu micro
# es "flojo"/lejano y "no te escucha", BAJA este número (p.ej. 400) para que la voz
# normal cruce el umbral. Valor por defecto = 1000 (idéntico al comportamiento previo).
MIC_ENERGY_MAX = int(os.getenv("MIC_ENERGY_MAX", "1000"))
# Vigilante del micro: cada X segundos, si el micro debería estar escuchando pero
# no arrancó (p.ej. el dispositivo estaba ocupado al abrir el programa), lo reintenta
# solo. Antes, si el arranque fallaba una vez, se quedaba mudo hasta reiniciar.
# Pon 0 para desactivar el reintento automático.
MIC_WATCHDOG_SECONDS = int(os.getenv("MIC_WATCHDOG_SECONDS", "20"))
# ---- NUEVO (arreglo "tarda en escucharme"): reconocimiento en hilo aparte ----
# El reconocimiento de voz de Google es una llamada por internet. Si se hace en el
# mismo hilo que captura el micro, BLOQUEA la escucha hasta terminar (por eso
# "tardaba" y se comía el inicio de la frase siguiente). Con esto, el micro encola
# el audio y vuelve a escuchar al instante; un hilo aparte reconoce en orden (FIFO).
# Pon false para volver al comportamiento anterior (reconocer en el mismo hilo).
MIC_ASYNC_RECOGNITION = _truthy(os.getenv("MIC_ASYNC_RECOGNITION", "true"))

# ---- NUEVO (petición 1): perfilador de contenido (vídeo normal vs musical) ----
# Ventana de memoria (segundos) y ajustes de decisión del clasificador.
MEDIA_PROFILE_WINDOW = float(os.getenv("MEDIA_PROFILE_WINDOW", "12.0"))
MEDIA_PROFILE_MIN_SAMPLES = int(os.getenv("MEDIA_PROFILE_MIN_SAMPLES", "6"))
MEDIA_PROFILE_HYSTERESIS = int(os.getenv("MEDIA_PROFILE_HYSTERESIS", "3"))
MEDIA_PROFILE_SILENCE_RATIO = float(os.getenv("MEDIA_PROFILE_SILENCE_RATIO", "0.75"))
MEDIA_PROFILE_MUSIC_RATIO = float(os.getenv("MEDIA_PROFILE_MUSIC_RATIO", "0.62"))
MEDIA_PROFILE_VOICE_RATIO = float(os.getenv("MEDIA_PROFILE_VOICE_RATIO", "0.30"))
MEDIA_PROFILE_TEMPO_CV = float(os.getenv("MEDIA_PROFILE_TEMPO_CV", "0.18"))
# Antirrebote del estado "hay media sonando" (nº de ventanas de ~1 s).
AUDIO_MEDIA_ON_BLOCKS = int(os.getenv("AUDIO_MEDIA_ON_BLOCKS", "2"))
AUDIO_MEDIA_OFF_BLOCKS = int(os.getenv("AUDIO_MEDIA_OFF_BLOCKS", "6"))

# ---- Acompañamiento multimedia continuo ----
# Coordina audio + frames individuales de pantalla + memoria + avatar. No graba
# vídeo ni audio. La captura visual se adapta entre 0.5 y 2 segundos.
MEDIA_COMPANION_ENABLED = _truthy(os.getenv("MEDIA_COMPANION_ENABLED", "true"))
MEDIA_COMPANION_VISUAL_ENABLED = _truthy(os.getenv("MEDIA_COMPANION_VISUAL_ENABLED", "true"))
MEDIA_COMPANION_COMMENTS_ENABLED = _truthy(os.getenv("MEDIA_COMPANION_COMMENTS_ENABLED", "true"))
MEDIA_COMPANION_AVATAR_ENABLED = _truthy(os.getenv("MEDIA_COMPANION_AVATAR_ENABLED", "true"))
MEDIA_COMPANION_VISUAL_INTERVAL = float(os.getenv("MEDIA_COMPANION_VISUAL_INTERVAL", "1.0"))
MEDIA_COMPANION_VISUAL_MIN_INTERVAL = float(os.getenv("MEDIA_COMPANION_VISUAL_MIN_INTERVAL", "0.5"))
MEDIA_COMPANION_VISUAL_MAX_INTERVAL = float(os.getenv("MEDIA_COMPANION_VISUAL_MAX_INTERVAL", "2.0"))
MEDIA_COMPANION_SCENE_CHANGE_THRESHOLD = float(os.getenv("MEDIA_COMPANION_SCENE_CHANGE_THRESHOLD", "0.025"))
MEDIA_COMPANION_VISUAL_STALE_SECONDS = float(os.getenv("MEDIA_COMPANION_VISUAL_STALE_SECONDS", "12"))
MEDIA_COMPANION_VISION_TIMEOUT = float(os.getenv("MEDIA_COMPANION_VISION_TIMEOUT", "18"))
MEDIA_COMPANION_VISION_RETRY_SECONDS = float(os.getenv("MEDIA_COMPANION_VISION_RETRY_SECONDS", "60"))
MEDIA_COMPANION_COMMENT_MIN_GAP = float(os.getenv("MEDIA_COMPANION_COMMENT_MIN_GAP", "45"))
MEDIA_COMPANION_COMMENT_MAX_GAP = float(os.getenv("MEDIA_COMPANION_COMMENT_MAX_GAP", "150"))
MEDIA_COMPANION_SPONTANEITY = float(os.getenv("MEDIA_COMPANION_SPONTANEITY", "0.48"))
MEDIA_COMPANION_EMOTION_INTENSITY = float(os.getenv("MEDIA_COMPANION_EMOTION_INTENSITY", "1.0"))
MEDIA_COMPANION_EMOTION_RISE_SECONDS = float(os.getenv("MEDIA_COMPANION_EMOTION_RISE_SECONDS", "3.2"))
MEDIA_COMPANION_EMOTION_FALL_SECONDS = float(os.getenv("MEDIA_COMPANION_EMOTION_FALL_SECONDS", "7.0"))
MEDIA_COMPANION_MUSIC_SENSITIVITY = float(os.getenv("MEDIA_COMPANION_MUSIC_SENSITIVITY", "1.0"))
MEDIA_COMPANION_VISUAL_SENSITIVITY = float(os.getenv("MEDIA_COMPANION_VISUAL_SENSITIVITY", "1.0"))
MEDIA_COMPANION_AVATAR_MOTION = float(os.getenv("MEDIA_COMPANION_AVATAR_MOTION", "0.72"))


# ---- Control seguro del PC ----
PC_MAX_ACTIONS = int(os.getenv("PC_MAX_ACTIONS", "24"))
PC_MAX_CYCLES = int(os.getenv("PC_MAX_CYCLES", "6"))
PC_MAX_ACTIONS_PER_CYCLE = int(os.getenv("PC_MAX_ACTIONS_PER_CYCLE", "4"))
PC_VISUAL_RECHECK = _truthy(os.getenv("PC_VISUAL_RECHECK", "true"))
PC_ACTION_PAUSE = float(os.getenv("PC_ACTION_PAUSE", "0.08"))
PC_TYPE_INTERVAL = float(os.getenv("PC_TYPE_INTERVAL", "0.012"))
PC_MAX_TYPE_CHARS = int(os.getenv("PC_MAX_TYPE_CHARS", "4000"))
# ---- Motor autónomo de ejecución ----
PC_AGENT_MAX_WORKERS = int(os.getenv("PC_AGENT_MAX_WORKERS", "3"))
PC_AGENT_PARALLEL = _truthy(os.getenv("PC_AGENT_PARALLEL", "true"))
PC_AGENT_RETRY_ATTEMPTS = int(os.getenv("PC_AGENT_RETRY_ATTEMPTS", "3"))
PC_AGENT_WAIT_TIMEOUT = float(os.getenv("PC_AGENT_WAIT_TIMEOUT", "10"))
PC_AGENT_APP_TIMEOUT = float(os.getenv("PC_AGENT_APP_TIMEOUT", "15"))
PC_AGENT_MAX_REPLANS = int(os.getenv("PC_AGENT_MAX_REPLANS", str(PC_MAX_CYCLES)))
PC_AGENT_LOG_PATH = Path(os.getenv(
    "PC_AGENT_LOG_PATH", str(APP_DIR / "data" / "logs" / "pc_agent.jsonl")
))

# ---- Permisos granulares del control del PC ----
# Un permiso en false RECHAZA la intención; no basta con confirmar por voz.
PERM_ENVIAR_CORREOS = _truthy(os.getenv("PERM_ENVIAR_CORREOS", "true"))
PERM_BORRAR_ARCHIVOS = _truthy(os.getenv("PERM_BORRAR_ARCHIVOS", "true"))
PERM_USAR_TERMINAL = _truthy(os.getenv("PERM_USAR_TERMINAL", "true"))
PERM_INSTALAR_SOFTWARE = _truthy(os.getenv("PERM_INSTALAR_SOFTWARE", "true"))
PERM_CERRAR_VENTANAS = _truthy(os.getenv("PERM_CERRAR_VENTANAS", "true"))
PERM_SOBRESCRIBIR_ARCHIVOS = _truthy(os.getenv("PERM_SOBRESCRIBIR_ARCHIVOS", "true"))

# ---- Office COM y catálogo dinámico de aplicaciones ----
PC_OFFICE_COM_ENABLED = _truthy(os.getenv("PC_OFFICE_COM_ENABLED", "true"))
PC_OFFICE_FALLBACK_KEYBOARD = _truthy(os.getenv("PC_OFFICE_FALLBACK_KEYBOARD", "true"))
PC_APP_SCAN_ON_START = _truthy(os.getenv("PC_APP_SCAN_ON_START", "true"))
PC_APP_CATALOG_MAX_AGE_HOURS = float(os.getenv("PC_APP_CATALOG_MAX_AGE_HOURS", "72"))
PC_APP_CATALOG_PATH = Path(os.getenv("PC_APP_CATALOG_PATH", str(APP_DIR / "data" / "apps_catalogo.json")))
PC_ACTION_LOG_SIZE = int(os.getenv("PC_ACTION_LOG_SIZE", "8"))

# ---- Verificación por acción (¿la pantalla cambió?) ----
PC_VERIFY_ACTIONS = _truthy(os.getenv("PC_VERIFY_ACTIONS", "true"))
PC_VERIFY_DELAY = float(os.getenv("PC_VERIFY_DELAY", "0.35"))       # espera antes de recomprobar
PC_VERIFY_HASH_DISTANCE = int(os.getenv("PC_VERIFY_HASH_DISTANCE", "1"))
PC_VERIFY_PIXEL_RATIO = float(os.getenv("PC_VERIFY_PIXEL_RATIO", "0.0015"))
PC_VERIFY_PIXEL_DELTA = int(os.getenv("PC_VERIFY_PIXEL_DELTA", "14"))
# Verificación por celdas: detecta cambios pequeños y concentrados (un dígito en
# la calculadora) que el ratio global de toda la pantalla no llega a ver.
PC_VERIFY_TILES = int(os.getenv("PC_VERIFY_TILES", "8"))
PC_VERIFY_TILE_RATIO = float(os.getenv("PC_VERIFY_TILE_RATIO", "0.02"))
# Ventanas propias de Yue: se enmascaran antes de comparar, porque el avatar VRM
# parpadea y respira y si no la pantalla "cambia" siempre.
PC_VERIFY_IGNORE_TITLES = tuple(
    item.strip() for item in os.getenv("PC_VERIFY_IGNORE_TITLES", "yue_companion,yue").split(",")
    if item.strip()
)

# ---- Contexto de UI real (pywinauto) ----
PC_UI_CONTEXT = _truthy(os.getenv("PC_UI_CONTEXT", "true"))
PC_UI_MAX_ELEMENTS = int(os.getenv("PC_UI_MAX_ELEMENTS", "40"))
# OCR como "ojos" del planificador: sin modelo de vision, el arbol UIA dice que
# BOTONES hay pero no que se esta VIENDO (el visor de la calculadora, el texto
# ya escrito). Sin esto el modelo repite el mismo clic sin saber que ya surtio
# efecto. Requiere Tesseract; si no esta, se ignora sin romper nada.
PC_OCR_CONTEXT = _truthy(os.getenv("PC_OCR_CONTEXT", "true"))
# Apartar las ventanas de Yue del mouse mientras controla el PC. Se aplica SIEMPRE
# desde el hilo de Qt (ver core/ui_bridge.py): hacerlo desde el hilo del control
# cuelga la aplicacion. Ponlo en false si sospechas de este mecanismo.
PC_CLICK_THROUGH = _truthy(os.getenv("PC_CLICK_THROUGH", "true"))
PC_CLICK_THROUGH_TIMEOUT = float(os.getenv("PC_CLICK_THROUGH_TIMEOUT", "1.0"))
PC_OCR_MAX_CHARS = int(os.getenv("PC_OCR_MAX_CHARS", "900"))
PC_UI_MAX_WINDOWS = int(os.getenv("PC_UI_MAX_WINDOWS", "18"))

# ---- Accesibilidad: control del cursor con la cabeza ----
# Reutiliza los MISMOS landmarks faciales de la cámara (MediaPipe) que ya se
# obtienen para las emociones; NO abre otra cámara ni otro pipeline. Pensado
# para personas con dificultad de motricidad fina en las manos.
HEAD_CONTROL_ENABLED = _truthy(os.getenv("HEAD_CONTROL_ENABLED", "true"))
# Cadencia rápida de la cámara SOLO mientras el control por cabeza está activo
# (el resto del tiempo la cámara sigue a su ritmo lento de siempre).
HEAD_CONTROL_FPS = float(os.getenv("HEAD_CONTROL_FPS", "20"))
# Zona muerta central (en unidades normalizadas de orientación): mirar de frente
# NO mueve el cursor, así los temblores/microgestos naturales no lo desplazan.
HEAD_CONTROL_DEADZONE = float(os.getenv("HEAD_CONTROL_DEADZONE", "0.06"))
# Sensibilidad: píxeles de desplazamiento por unidad de giro y por fotograma.
HEAD_CONTROL_GAIN = float(os.getenv("HEAD_CONTROL_GAIN", "55"))
# Tope de velocidad (px por fotograma) para que nunca dé saltos bruscos.
HEAD_CONTROL_MAX_SPEED = float(os.getenv("HEAD_CONTROL_MAX_SPEED", "38"))
# Suavizado exponencial (0..1): más alto = más suave pero con algo más de lag.
HEAD_CONTROL_SMOOTHING = float(os.getenv("HEAD_CONTROL_SMOOTHING", "0.5"))
# Dwell-click: si el cursor se queda dentro de un radio pequeño N ms, clic izq.
HEAD_CONTROL_DWELL_MS = int(os.getenv("HEAD_CONTROL_DWELL_MS", "900"))
HEAD_CONTROL_DWELL_RADIUS = int(os.getenv("HEAD_CONTROL_DWELL_RADIUS", "22"))
# Refractario tras un clic por dwell (ms) para que no repita clics sin querer.
HEAD_CONTROL_DWELL_COOLDOWN_MS = int(os.getenv("HEAD_CONTROL_DWELL_COOLDOWN_MS", "700"))
# Margen (px) para no acercar el cursor a las esquinas (evita el FAILSAFE).
HEAD_CONTROL_MARGIN = int(os.getenv("HEAD_CONTROL_MARGIN", "3"))
# Invertir ejes si el usuario lo prefiere (por defecto, mapeo natural).
HEAD_CONTROL_INVERT_X = _truthy(os.getenv("HEAD_CONTROL_INVERT_X", "false"))
HEAD_CONTROL_INVERT_Y = _truthy(os.getenv("HEAD_CONTROL_INVERT_Y", "false"))

# ---- Accesibilidad: confirmación verbal de acciones irreversibles ----
# Para usuarios que no pueden corregir rápido con el mouse. Cuando está activo,
# antes de ejecutar un paso IRREVERSIBLE pero legítimo (eliminar archivos, enviar
# correos/mensajes, cerrar sin guardar, sobrescribir archivos), YUE se detiene en
# ESE paso y pide confirmación por voz ("sí/no"). Los demás pasos del plan siguen
# normalmente. Es ADICIONAL a los bloqueos duros de pc_control (pagos,
# credenciales, apagado…), que siguen prohibidos siempre.
ACCESSIBILITY_CONFIRM_REQUIRED = _truthy(os.getenv("ACCESSIBILITY_CONFIRM_REQUIRED", "true"))
# Segundos que YUE espera la respuesta por voz. Si no llega, por seguridad NO
# ejecuta ese paso (y sigue con el resto del plan).
ACCESSIBILITY_CONFIRM_TIMEOUT = float(os.getenv("ACCESSIBILITY_CONFIRM_TIMEOUT", "25"))

# ---- OCR de pantalla (acción click_text) ----
OCR_ENGINE = os.getenv("OCR_ENGINE", "pytesseract")
OCR_LANG = os.getenv("OCR_LANG", "spa+eng")
OCR_MIN_CONF = float(os.getenv("OCR_MIN_CONF", "55"))
# Ruta al binario de Tesseract si no está en el PATH, por ejemplo:
# C:\Program Files\Tesseract-OCR\tesseract.exe
OCR_TESSERACT_CMD = os.getenv("OCR_TESSERACT_CMD", "")

# ---- Aprendizaje continuo de órdenes de PC ----
LEARNING_ENABLED = _truthy(os.getenv("LEARNING_ENABLED", "true"))
LEARNING_SIM_THRESHOLD = float(os.getenv("LEARNING_SIM_THRESHOLD", "0.85"))
LEARNING_MIN_SUCCESSES = int(os.getenv("LEARNING_MIN_SUCCESSES", "2"))
LEARNING_MIN_RATIO = float(os.getenv("LEARNING_MIN_RATIO", "0.7"))
LEARNING_FAIL_STREAK = int(os.getenv("LEARNING_FAIL_STREAK", "2"))
LEARNING_LESSON_THRESHOLD = float(os.getenv("LEARNING_LESSON_THRESHOLD", "0.55"))
LEARNING_MAX_SKILLS = int(os.getenv("LEARNING_MAX_SKILLS", "200"))
LEARNING_MAX_LESSONS = int(os.getenv("LEARNING_MAX_LESSONS", "200"))
LEARNING_MAX_PLAN_STEPS = int(os.getenv("LEARNING_MAX_PLAN_STEPS", "24"))
LEARNING_REFLECT = _truthy(os.getenv("LEARNING_REFLECT", "true"))

# ---- Autonomia local y segura ----
AUTONOMY_ENABLED = _truthy(os.getenv("AUTONOMY_ENABLED", "true"))
AUTONOMY_INTERVAL = int(os.getenv("AUTONOMY_INTERVAL", "600"))
AUTONOMY_IDLE_SECONDS = int(os.getenv("AUTONOMY_IDLE_SECONDS", "120"))
AUTONOMY_NOTIFY = _truthy(os.getenv("AUTONOMY_NOTIFY", "true"))
AUTONOMY_MAX_PER_SESSION = int(os.getenv("AUTONOMY_MAX_PER_SESSION", "3"))
# Iniciativa automática: guarda creaciones y, cuando el usuario está inactivo,
# puede abrir el archivo generado usando el sistema operativo.
AUTONOMY_PC_ENABLED = _truthy(os.getenv("AUTONOMY_PC_ENABLED", "true"))
AUTONOMY_OPEN_CREATIONS = _truthy(os.getenv("AUTONOMY_OPEN_CREATIONS", "true"))

# ---- Check-in de ánimo (opcional y NO intrusivo) ----
# YUE puede, como mucho una vez al día y solo tras un rato de inactividad,
# preguntar de forma natural cómo se ha sentido el usuario. Se desactiva por
# completo poniendo CHECKIN_ENABLED=false.
CHECKIN_ENABLED = _truthy(os.getenv("CHECKIN_ENABLED", "true"))
# Horas mínimas que deben pasar desde el último check-in (explícito o proactivo)
# para que YUE vuelva a preguntar por iniciativa propia.
CHECKIN_MIN_HOURS = float(os.getenv("CHECKIN_MIN_HOURS", "20"))
# Cada cuántos segundos se evalúa si toca un check-in proactivo (barato).
CHECKIN_CHECK_INTERVAL = int(os.getenv("CHECKIN_CHECK_INTERVAL", "90"))

# ---- Apariencia ----
PET_HEIGHT = int(os.getenv("PET_HEIGHT", "330"))
# Usar el avatar 3D VRM si esta disponible (si falla, usa el PNG):
USE_VRM = _truthy(os.getenv("USE_VRM", "true"))
AVATAR_WIDTH = int(os.getenv("AVATAR_WIDTH", "320"))
AVATAR_HEIGHT = int(os.getenv("AVATAR_HEIGHT", "520"))
# Giro de la cara en grados. VRM 1.0 = 0 (te mira). Si ves su espalda, pon 180.
AVATAR_TURN = float(os.getenv("AVATAR_TURN", "0"))
# --- Pose de los brazos (en radianes) ---
# Cuanto bajan los brazos al costado (1.57 = totalmente verticales/pegados):
AVATAR_ARM_DOWN = float(os.getenv("AVATAR_ARM_DOWN", "1.48"))
# Adelante (+) / atras (-). Si los brazos se ven DETRAS del cuerpo, SUBE este numero
# (p.ej. 0.5). Si se ven muy adelante, bajalo:
AVATAR_ARM_FWD = float(os.getenv("AVATAR_ARM_FWD", "0.35"))
# Giro de los antebrazos hacia adentro (manos hacia el cuerpo):
AVATAR_ARM_IN = float(os.getenv("AVATAR_ARM_IN", "0.12"))
# --- Encuadre de la camara ---
# Distancia de la camara. Mas grande = se ve mas cuerpo y toda la cabeza:
AVATAR_CAM_DIST = float(os.getenv("AVATAR_CAM_DIST", "1.85"))
# Aim vertical relativo a la cabeza. Mas negativo = la cabeza sube en el encuadre
# (util si se corta el pelo de arriba):
AVATAR_CAM_Y = float(os.getenv("AVATAR_CAM_Y", "-0.16"))
# Modo prueba: pinta un fondo oscuro para comprobar que el 3D renderiza.
AVATAR_DEBUG = _truthy(os.getenv("AVATAR_DEBUG", "false"))
# Cuanta gesticulacion tiene el avatar. 0 = solo el balanceo de antes,
# 1 = normal, 1.5 = teatral. Los gestos nunca sacan al hueso de su rango.
AVATAR_GESTURE_GAIN = float(os.getenv("AVATAR_GESTURE_GAIN", "1.0"))

# ---- Generacion multimedia opcional: imagen, video y musica ----
# Se conserva como modulo independiente de generacion creativa.

# Proveedor de FLUX: together (gratis con FLUX.1-schnell) | bfl | openai | local
FLUX_PROVIDER = os.getenv("FLUX_PROVIDER", "together").strip().lower()

# -- Together AI (recomendado: tiene un FLUX.1-schnell gratuito) --
# Consigue tu key gratis en https://api.together.ai/settings/api-keys
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "").strip()
TOGETHER_MODEL = os.getenv("TOGETHER_MODEL", "black-forest-labs/FLUX.1-schnell-Free")
TOGETHER_BASE_URL = os.getenv("TOGETHER_BASE_URL", "https://api.together.xyz/v1").rstrip("/")

# -- Black Forest Labs (API oficial, de pago) --
# Key en https://dashboard.bfl.ai  (se envia en la cabecera x-key)
BFL_API_KEY = os.getenv("BFL_API_KEY", "").strip()
BFL_BASE_URL = os.getenv("BFL_BASE_URL", "https://api.bfl.ai").rstrip("/")
BFL_MODEL = os.getenv("BFL_MODEL", "flux-dev")  # flux-dev | flux-pro-1.1 | flux-2-pro ...

# -- Generico compatible con OpenAI Images (DALL-E style /images/generations) --
OPENAI_IMAGE_KEY = os.getenv("OPENAI_IMAGE_KEY", "").strip()
OPENAI_IMAGE_BASE_URL = os.getenv("OPENAI_IMAGE_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")

# -- Local (diffusers + GPU; opcional, pesado) --
LOCAL_FLUX_MODEL = os.getenv("LOCAL_FLUX_MODEL", "black-forest-labs/FLUX.1-schnell")

# Tamano de la imagen generada (multiplos de 16; vertical para el escritorio):
IMAGE_WIDTH = int(os.getenv("IMAGE_WIDTH", "768"))
IMAGE_HEIGHT = int(os.getenv("IMAGE_HEIGHT", "1024"))
FLUX_STEPS = int(os.getenv("FLUX_STEPS", "4"))  # schnell va bien con 4

# -- Video (FFmpeg) --
# Ruta al ejecutable de ffmpeg/ffprobe (si no estan en el PATH del sistema):
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")
VIDEO_SECONDS = int(os.getenv("VIDEO_SECONDS", "16"))  # duracion del recuerdo
VIDEO_FPS = int(os.getenv("VIDEO_FPS", "30"))
# Carpeta opcional con tu propia musica por estado de animo:
#   assets/music/feliz/*.mp3 , assets/music/triste/*.mp3 , etc.
# Si no hay archivos, Yue compone una melodia ambiental segun la emocion.

# ---- Rutas ----
ASSET = RES_DIR / "assets" / "yue.png"
VRM = RES_DIR / "assets" / "yue.vrm"
AVATAR_HTML = RES_DIR / "ui" / "avatar.html"
DATA_DIR = APP_DIR / "data"        # datos del usuario, junto al .exe
DATA_DIR.mkdir(parents=True, exist_ok=True)

# --- Palabra de activación «Yue» ---
# Si está activo, YUE solo responde a frases (voz o texto) que empiecen por su
# nombre. NO afecta a la escucha de música/vídeo del sistema (subsistema aparte).
# DESACTIVADO por defecto: YUE responde a todo lo que le escribas o digas sin que
# tengas que empezar por «Yue». Ponlo en true en el .env si prefieres el gatillo.
WAKE_WORD_ENABLED = _truthy(os.getenv("WAKE_WORD_ENABLED", "false"))
# Palabras aceptadas al inicio (separadas por coma). Incluye variantes típicas del
# reconocedor de voz para «Yue». Añade las que veas que confunde tu micrófono.
WAKE_WORDS = os.getenv("WAKE_WORDS", "yue,yué,llue,jue,hue")

# --- Mensajes de estado en la consola ---
# Si es false (por defecto), YUE NO llena la terminal con avisos repetidos de
# «modo multimedia activo/en reposo» ni «escuchando…». Ponlo en true solo si
# necesitas depurar y quieres ver esos estados otra vez.
DEBUG_STATUS = _truthy(os.getenv("DEBUG_STATUS", "false"))

# --- Modo Profesora: auto-avance de la lectura guiada de PDF ---
# Si está en True, cuando YUE termina de explicar una página pasa sola a la
# siguiente. Se puede pausar en voz ("pausa") o reanudar ("sigue sola").
TEACHER_AUTOADVANCE_DEFAULT = _truthy(os.getenv("TEACHER_AUTOADVANCE", "true"))
# Pausa (ms) entre que termina de hablar una página y pasa a la siguiente.
TEACHER_AUTOADVANCE_PAUSE_MS = int(os.getenv("TEACHER_AUTOADVANCE_PAUSE_MS", "2500"))
DB_PATH = DATA_DIR / "yue.db"
# ---- Memoria a largo plazo (consolidación) ----
# Cada cuántos días, COMO MÍNIMO, se comprime el historial viejo en un resumen
# persistente corto que vuelve a entrar al prompt. Best-effort: si no hay clave de
# IA o la llamada falla, no consolida esta vez y lo reintenta la próxima. Un valor
# <= 0 desactiva la consolidación por completo.
MEMORY_CONSOLIDATION_DAYS = int(os.getenv("MEMORY_CONSOLIDATION_DAYS", "7"))
AUTONOMY_DIR = DATA_DIR / "autonomy"
PC_SCREENSHOT_DIR = DATA_DIR / "screenshots"
LEARNING_DIR = DATA_DIR / "learning"       # skills.json y lessons.json
LEARNING_DIR.mkdir(parents=True, exist_ok=True)
MEMORIES_DIR = DATA_DIR / "memories"   # imagenes y videos generados
MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
# Musica propia: primero junto al .exe (assets/music), si no, la del bundle.
MUSIC_DIR = APP_DIR / "assets" / "music"
if not MUSIC_DIR.exists():
    MUSIC_DIR = RES_DIR / "assets" / "music"

# ---- Seguridad ----
CRISIS_RESOURCES = os.getenv(
    "CRISIS_RESOURCES",
    "Si estás en peligro inmediato, por favor contacta YA a los servicios de "
    "emergencia de tu país o a una persona de confianza. No estás sola/o.",
)

# ---- Recordatorio discreto de bienestar ----
# YUE recuerda, de tanto en tanto y desde SU carácter (no como aviso del
# sistema), que no sustituye el apoyo de personas de confianza ni profesional.
# Se desactiva por completo con WELLBEING_NUDGE_ENABLED=false.
WELLBEING_NUDGE_ENABLED = _truthy(os.getenv("WELLBEING_NUDGE_ENABLED", "true"))
# Condición 1: el riesgo textual saltó al menos esta cantidad de veces en la
# ventana de días indicada ("más de una vez" -> 2).
WELLBEING_RISK_DAYS = int(os.getenv("WELLBEING_RISK_DAYS", "7"))
WELLBEING_MIN_RISK_EVENTS = int(os.getenv("WELLBEING_MIN_RISK_EVENTS", "2"))
# Condición 2: uso muy intensivo -> una sesión continua de más de X horas
# (un hueco mayor a WELLBEING_SESSION_GAP_MIN minutos corta la sesión).
WELLBEING_SESSION_HOURS = float(os.getenv("WELLBEING_SESSION_HOURS", "3"))
WELLBEING_SESSION_GAP_MIN = float(os.getenv("WELLBEING_SESSION_GAP_MIN", "30"))
# Espaciado: al cumplirse alguna condición se abre una "ventana" breve en la que
# YUE puede sacar el tema con naturalidad; luego se calla un buen rato.
WELLBEING_NUDGE_WINDOW_MIN = float(os.getenv("WELLBEING_NUDGE_WINDOW_MIN", "20"))
WELLBEING_NUDGE_COOLDOWN_HOURS = float(os.getenv("WELLBEING_NUDGE_COOLDOWN_HOURS", "8"))


# ==========================================================================
# YUE V3 PERCEPTION SYSTEM (cámara que "ve" a la persona: presencia, rostro,
# emoción, atención). ADITIVO y OPT-IN: apagado por defecto para no pelear por
# la webcam con el observador clásico (core/camera_observer.py). Enciéndelo con
# VISION_V3_ENABLED=true y, si el clásico también usa la misma cámara, pon
# CAMERA_ENABLED=false o dale a V3 otro índice con CAMERA_INDEX.
# ==========================================================================
VISION_V3_ENABLED = _truthy(os.getenv("VISION_V3_ENABLED", "false"))
# Índice de cámara del sistema V3 (0 = webcam principal).
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
# Privacidad: constancia explícita de que NO se guardan ni envían imágenes.
CAMERA_PRIVACY_MODE = _truthy(os.getenv("CAMERA_PRIVACY_MODE", "true"))

# Perfil de rendimiento por hardware: auto | LOW | MEDIUM | HIGH.
#   LOW    -> DeepFace cada 8 s, ~12 FPS   (i3 antiguos, 8 GB, gráficos integrados)
#   MEDIUM -> DeepFace cada 5 s, ~20 FPS   (16 GB, GPU media)
#   HIGH   -> DeepFace cada 2 s, ~30 FPS   (GPU dedicada)
VISION_PERF_PROFILE = os.getenv("VISION_PERF_PROFILE", "auto").strip()
# Overrides finos opcionales (0 = usar lo del perfil, sin tocar).
VISION_EMOTION_INTERVAL = float(os.getenv("VISION_EMOTION_INTERVAL", "0"))
VISION_TARGET_FPS = float(os.getenv("VISION_TARGET_FPS", "0"))

# Emociones -> comportamiento. Persona que comenta lo que ve: YUE | KAI.
VISION_EMOTION_PERSONA = os.getenv("VISION_EMOTION_PERSONA", "YUE").strip()
# Tope de frases por emoción a la hora (para que no hable de más).
MAX_EMOTION_RESPONSES_PER_HOUR = int(os.getenv("MAX_EMOTION_RESPONSES_PER_HOUR", "4"))
# Confianza mínima (0..100) para hacer caso a una emoción.
VISION_EMOTION_MIN_CONFIDENCE = int(os.getenv("VISION_EMOTION_MIN_CONFIDENCE", "60"))
# Enfriamiento (s): no repetir la misma emoción hablada tan seguido.
VISION_EMOTION_COOLDOWN = float(os.getenv("VISION_EMOTION_COOLDOWN", "120"))
# Anti-spam del gesto empático del avatar (s).
VISION_AVATAR_MIN_GAP = float(os.getenv("VISION_AVATAR_MIN_GAP", "6"))

# Atención: segundos de ausencia continua para avisar (10 min por defecto) y
# semiancho de la "zona central" para considerar que mira de frente (0..0.5).
VISION_ABSENCE_SECONDS = float(os.getenv("VISION_ABSENCE_SECONDS", "600"))
VISION_ATTENTION_CENTER_BAND = float(os.getenv("VISION_ATTENTION_CENTER_BAND", "0.22"))


# ===========================================================================
# SISTEMA DE VISIÓN POR CÁMARA — MediaPipe Tasks (paquete vision/)  [ADITIVO]
# ---------------------------------------------------------------------------
# Bloque agregado por la integración de visión artificial con una sola cámara.
# NO redefine VISION_ENABLED (esa es la visión de PANTALLA). El interruptor
# maestro de ESTE sistema es VISION_MP_ENABLED y está APAGADO por defecto para
# no pelear por la webcam con el observador clásico ni con core.vision.
# El paquete vision/ igual funciona sin este bloque (tiene sus propios valores
# por defecto); esto es para descubribilidad y para poder sobrescribir.
# ===========================================================================
VISION_MP_ENABLED = _truthy(os.getenv("VISION_MP_ENABLED", "false"))

# FPS de captura de la cámara para el pipeline MediaPipe (0 = usa el del perfil).
CAMERA_TARGET_FPS = float(os.getenv("CAMERA_TARGET_FPS", "0") or "0")

# Perfil de rendimiento: low | balanced | high (ajusta resolución y FPS base).
VISION_PERFORMANCE_MODE = os.getenv("VISION_PERFORMANCE_MODE", "balanced").strip().lower()

# Módulos (on/off). Holistic apaga automáticamente rostro-landmarks/pose/gestos.
VISION_FACE_DETECTOR_ENABLED = _truthy(os.getenv("VISION_FACE_DETECTOR_ENABLED", "true"))
VISION_FACE_LANDMARKER_ENABLED = _truthy(os.getenv("VISION_FACE_LANDMARKER_ENABLED", "true"))
VISION_POSE_ENABLED = _truthy(os.getenv("VISION_POSE_ENABLED", "true"))
VISION_GESTURE_ENABLED = _truthy(os.getenv("VISION_GESTURE_ENABLED", "true"))
VISION_OBJECTS_ENABLED = _truthy(os.getenv("VISION_OBJECTS_ENABLED", "true"))
VISION_IMAGE_CLASSIFIER_ENABLED = _truthy(os.getenv("VISION_IMAGE_CLASSIFIER_ENABLED", "false"))
VISION_IMAGE_SEGMENTER_ENABLED = _truthy(os.getenv("VISION_IMAGE_SEGMENTER_ENABLED", "false"))
VISION_INTERACTIVE_SEGMENTER_ENABLED = _truthy(os.getenv("VISION_INTERACTIVE_SEGMENTER_ENABLED", "false"))
VISION_HOLISTIC_ENABLED = _truthy(os.getenv("VISION_HOLISTIC_ENABLED", "false"))

# Frecuencias por módulo (FPS). 0 = usa el valor del perfil de rendimiento.
VISION_FACE_DETECTOR_FPS = float(os.getenv("VISION_FACE_DETECTOR_FPS", "0") or "0")
VISION_FACE_LANDMARKER_FPS = float(os.getenv("VISION_FACE_LANDMARKER_FPS", "0") or "0")
VISION_POSE_FPS = float(os.getenv("VISION_POSE_FPS", "0") or "0")
VISION_GESTURE_FPS = float(os.getenv("VISION_GESTURE_FPS", "0") or "0")
VISION_OBJECT_FPS = float(os.getenv("VISION_OBJECT_FPS", "0") or "0")
VISION_CLASSIFIER_FPS = float(os.getenv("VISION_CLASSIFIER_FPS", "0") or "0")

# Límites de detección.
VISION_MAX_FACES = int(os.getenv("VISION_MAX_FACES", "3"))
VISION_MAX_HANDS = int(os.getenv("VISION_MAX_HANDS", "2"))

# Confianzas mínimas (0..1).
VISION_FACE_MIN_CONFIDENCE = float(os.getenv("VISION_FACE_MIN_CONFIDENCE", "0.5"))
VISION_POSE_MIN_CONFIDENCE = float(os.getenv("VISION_POSE_MIN_CONFIDENCE", "0.5"))
VISION_GESTURE_MIN_CONFIDENCE = float(os.getenv("VISION_GESTURE_MIN_CONFIDENCE", "0.6"))
VISION_OBJECT_MIN_CONFIDENCE = float(os.getenv("VISION_OBJECT_MIN_CONFIDENCE", "0.5"))

# Privacidad y depuración. Por diseño NO se guardan ni se envían imágenes.
VISION_SAVE_FRAMES = _truthy(os.getenv("VISION_SAVE_FRAMES", "false"))
VISION_EXTERNAL_UPLOAD = _truthy(os.getenv("VISION_EXTERNAL_UPLOAD", "false"))
VISION_DEBUG_OVERLAY = _truthy(os.getenv("VISION_DEBUG_OVERLAY", "false"))

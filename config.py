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


# ---------------------------------------------------------------------------
# LECTURA NUMERICA TOLERANTE DEL .env  [ADITIVO — corrige el arranque]
# ---------------------------------------------------------------------------
# Antes cada numero se leia con int(os.getenv(...)) / float(os.getenv(...)) sin
# red de seguridad. Bastaba con que el .env trajera "0.45" donde el codigo
# esperaba un entero, o una linea vacia, o un comentario pegado al valor, para
# que config.py reventara EN EL IMPORT y YUE no arrancara:
#     ValueError: invalid literal for int() with base 10: '0.45'
# Estos ayudantes nunca lanzan excepcion: si el valor no se entiende, se usa el
# valor por defecto y la aplicacion sigue viva.
def _env_raw(name, default):
    """Texto crudo del .env; None/"" -> se usa el valor por defecto."""
    v = os.getenv(name, None)
    if v is None:
        return default
    v = str(v).strip()
    # Tolera comentarios pegados al valor: "3  # comentario"
    if "#" in v:
        v = v.split("#", 1)[0].strip()
    # Tolera comillas: VAR="3"
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1].strip()
    if v == "":
        return default
    return v


def _env_float(name, default):
    """Numero decimal del .env. Nunca revienta: si no se entiende, usa default."""
    v = _env_raw(name, default)
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        try:
            return float(str(default).replace(",", "."))
        except (TypeError, ValueError):
            return 0.0


def _env_int(name, default):
    """Numero entero del .env, aceptando decimales ("0.45" -> 0, "5.0" -> 5).

    Este era EXACTAMENTE el fallo del arranque: int("0.45") revienta, pero
    int(float("0.45")) no. Se pasa siempre por float primero.
    """
    v = _env_raw(name, default)
    try:
        return int(float(str(v).replace(",", ".")))
    except (TypeError, ValueError):
        try:
            return int(float(str(default).replace(",", ".")))
        except (TypeError, ValueError):
            return 0


def _env_bool(name, default=False):
    """Booleano tolerante del .env: true/1/si/on -> True. Nunca revienta."""
    v = _env_raw(name, "true" if default else "false")
    return str(v).strip().lower() in ("1", "true", "si", "sí", "yes", "on")


def _env_conf01(name, default):
    """Confianza normalizada a 0..1 aceptando porcentaje.

    VISION_EMOTION_MIN_CONFIDENCE la comparten dos sistemas con escalas
    distintas (core/vision la usa en % y el paquete vision/ en 0..1). Aqui se
    acepta cualquiera de las dos escrituras: 0.45 y 45 significan lo mismo.
    """
    valor = _env_float(name, default)
    if valor > 1.0:
        valor = valor / 100.0
    return max(0.0, min(1.0, valor))


def _env_conf_pct(name, default):
    """La misma confianza pero en 0..100, para el sistema de percepcion V3."""
    return round(_env_conf01(name, default) * 100.0, 2)


# ---- Groq (único proveedor de conversación) ----
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
# Modelo de conversación. Qwen 3.6 27B es un modelo Groq actual; evita depender
# de llama-3.3-70b-versatile, cuyo apagado está anunciado para agosto de 2026.
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.6-27b").strip()
# Groq no expone actualmente un modelo multimodal de producción en el catálogo
# usado por este proyecto. La pantalla queda en OCR/local salvo que esto cambie.
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "").strip()

# Pool de claves Groq AUTORIZADAS. La principal sigue siendo GROQ_API_KEY.
# Se aceptan GROQ_API_KEY_2..GROQ_API_KEY_9 y/o GROQ_API_KEYS separadas por coma.
# El router NO usa otra clave para eludir un HTTP 429/rate-limit; el relevo es
# para credenciales revocadas, permisos, red o fallos temporales del servicio.
#
# CAMBIO: por defecto YUE trabaja con UNA SOLA CUENTA de Groq (la de
# GROQ_API_KEY). Si quieres recuperar el relevo entre varias credenciales,
# pon GROQ_UNA_SOLA_CUENTA=false en el .env.
GROQ_UNA_SOLA_CUENTA = _env_bool("GROQ_UNA_SOLA_CUENTA", True)


def _groq_keys():
    if GROQ_UNA_SOLA_CUENTA:
        return (GROQ_API_KEY,) if GROQ_API_KEY else ()
    valores = [GROQ_API_KEY]
    valores.extend(
        x.strip() for x in os.getenv("GROQ_API_KEYS", "").split(",") if x.strip()
    )
    for i in range(2, 10):
        k = os.getenv(f"GROQ_API_KEY_{i}", "").strip()
        if k:
            valores.append(k)
    # Mantiene el orden y evita probar dos veces la misma clave.
    return tuple(dict.fromkeys(k for k in valores if k))

GROQ_API_KEYS = _groq_keys()

# --- Respuestas rápidas y siempre en español  [ADITIVO] ---------------------
# GROQ_SIN_RAZONAMIENTO: pide a Groq que NO devuelva el monólogo interno del
#   modelo (el famoso <think>...</think> en inglés) y que no gaste tokens en él.
#   Es lo que hacía que YUE dijera cosas raras en inglés y tardara tanto.
# GROQ_MAX_TOKENS_CHAT: YUE habla de 1 a 4 frases; sin tope el modelo puede
#   generar mucho más y hacerte esperar de más.
GROQ_SIN_RAZONAMIENTO = _env_bool("GROQ_SIN_RAZONAMIENTO", True)
GROQ_MAX_TOKENS_CHAT = _env_int("GROQ_MAX_TOKENS_CHAT", 220)
# Si aun así respondiera en inglés, YUE lo pide una vez más en español.
FORZAR_ESPANOL = _env_bool("FORZAR_ESPANOL", True)

# Respaldos de MODELO dentro del mismo proveedor Groq.
GROQ_MODEL_FALLBACKS = tuple(
    m.strip() for m in os.getenv(
        "GROQ_MODEL_FALLBACKS",
        "qwen/qwen3.6-27b,openai/gpt-oss-120b,openai/gpt-oss-20b",
    ).split(",") if m.strip()
)
GROQ_VISION_FALLBACKS = tuple(
    m.strip() for m in os.getenv("GROQ_VISION_FALLBACKS", "").split(",") if m.strip()
)


# --- Visión por otro proveedor ------------------------------
# Groq retiró TODOS sus modelos con visión de algunas cuentas. Como Together y
# OpenAI hablan el mismo dialecto (API compatible con OpenAI), se puede mandar
# SOLO la visión a otro sitio y dejar el chat en Groq.
# VISION_PROVIDER: auto | gemini | openrouter | groq | together | openai |
#                  custom | none
#   auto   -> usa el VisionRouter con TODOS los proveedores multimodales que
#             tengan clave, en fila y con relevo automatico. Es lo recomendado.
#   <nombre> -> pone ese proveedor el PRIMERO, pero deja los demas de respaldo
#             (asi una caida de ese proveedor no deja ciega a YUE).
#   none   -> vision multimodal DESACTIVADA a proposito. YUE sigue mirando la
#             pantalla por OCR + router de texto (nunca dice "no puedo ver" si
#             hay texto legible).
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "auto").strip().lower()
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


# --- Claves de los proveedores multimodales del VisionRouter ---------------
# Ya las usaba `core/ai_router.py` leyéndolas del entorno; aquí quedan también
# como constantes para que `vision_endpoint()` y el diagnóstico las vean.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_BASE_URL = os.getenv(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-2.0-flash").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_VISION_MODEL = os.getenv(
    "OPENROUTER_VISION_MODEL", "meta-llama/llama-3.2-11b-vision-instruct:free").strip()
GROQ_VISION_MODEL_ROUTER = os.getenv(
    "GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct").strip()
TOGETHER_VISION_MODEL = os.getenv(
    "TOGETHER_VISION_MODEL", "meta-llama/Llama-4-Scout-17B-16E-Instruct").strip()
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip()


# --- Visión de PANTALLA (ScreenAnalyzer + VisionRouter) --------------------
# Monitor que YUE mira por defecto (1 = principal). Con varias pantallas puedes
# ponerlo en 2, 3… o pedírselo por voz.
SCREEN_VISION_MONITOR = _env_int("SCREEN_VISION_MONITOR", "1")
# Una captura más antigua que esto NO se analiza: evita que YUE describa lo que
# había en pantalla hace un rato creyendo que es lo de ahora.
SCREEN_VISION_MAX_FRAME_AGE = _env_float("SCREEN_VISION_MAX_FRAME_AGE", "3.0")
# Reutilización de una observación reciente cuando la pantalla no ha cambiado.
# Ponlo en 0 para desactivar la caché por completo.
SCREEN_VISION_CACHE_SECONDS = _env_float("SCREEN_VISION_CACHE_SECONDS", "25")
SCREEN_VISION_CACHE_HASH_DISTANCE = _env_int("SCREEN_VISION_CACHE_HASH_DISTANCE", "3")
# Estrategia forzada: vacío = decide el clasificador. Valores: ocr|vision|ambos.
SCREEN_VISION_STRATEGY = os.getenv("SCREEN_VISION_STRATEGY", "").strip().lower()
# Calidad/tamaño de la imagen que se manda al modelo multimodal.
SCREEN_VISION_MAX_WIDTH = _env_int("SCREEN_VISION_MAX_WIDTH", "1280")
SCREEN_VISION_JPEG_QUALITY = _env_int("SCREEN_VISION_JPEG_QUALITY", "70")
SCREEN_VISION_MAX_TOKENS = _env_int("SCREEN_VISION_MAX_TOKENS", "420")
SCREEN_VISION_TEMPERATURE = _env_float("SCREEN_VISION_TEMPERATURE", "0.2")
SCREEN_VISION_TIMEOUT = _env_int("SCREEN_VISION_TIMEOUT", "45")
# OCR de pantalla.
SCREEN_VISION_OCR_MAX_CHARS = _env_int("SCREEN_VISION_OCR_MAX_CHARS", "8000")
SCREEN_VISION_OCR_HINT_CHARS = _env_int("SCREEN_VISION_OCR_HINT_CHARS", "1500")
# Memoria temporal para vídeo: cuántos frames recuerda (t-4 … t).
SCREEN_VISION_TEMPORAL_FRAMES = _env_int("SCREEN_VISION_TEMPORAL_FRAMES", "5")
# Mandar además el keyframe anterior cuando hay mucho movimiento.
SCREEN_VISION_SEND_KEYFRAME = os.getenv(
    "SCREEN_VISION_SEND_KEYFRAME", "true").strip().lower() in (
    "1", "true", "yes", "si", "sí", "on")
# Movimiento mínimo entre capturas para considerar que hay VÍDEO (sin audio).
SCREEN_VIDEO_MOTION_THRESHOLD = _env_float("SCREEN_VIDEO_MOTION_THRESHOLD", "0.18")


def vision_endpoint():
    """Devuelve (base_url, api_key, modelo) para las llamadas con imagen.

    OJO: esto es el camino ANTIGUO (un solo endpoint). El camino nuevo y
    recomendado es `core/vision_router.py`, que prueba VARIOS proveedores
    multimodales en fila. Esta función se mantiene por compatibilidad con
    `AIEngine.look()`, el planificador de PC y el preflight.

    Nunca revienta: si algo falta devuelve ("", "", "") y el llamador decide.
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
    if prov == "gemini":
        return (
            VISION_BASE_URL or "https://generativelanguage.googleapis.com/v1beta/openai",
            VISION_API_KEY or GEMINI_API_KEY,
            VISION_MODEL or os.getenv("GEMINI_VISION_MODEL", "gemini-2.0-flash").strip(),
        )
    if prov == "openrouter":
        return (
            VISION_BASE_URL or OPENROUTER_BASE_URL,
            VISION_API_KEY or OPENROUTER_API_KEY,
            VISION_MODEL or os.getenv(
                "OPENROUTER_VISION_MODEL",
                "meta-llama/llama-3.2-11b-vision-instruct:free").strip(),
        )
    if prov == "custom":
        return (VISION_BASE_URL, VISION_API_KEY, VISION_MODEL)
    if prov == "auto":
        # En modo auto manda el VisionRouter. Aquí devolvemos el PRIMER
        # proveedor multimodal con clave, solo para quien todavía use el
        # camino de un único endpoint (planificador de PC, preflight).
        candidatos = (
            ("https://generativelanguage.googleapis.com/v1beta/openai", GEMINI_API_KEY,
             os.getenv("GEMINI_VISION_MODEL", "gemini-2.0-flash").strip()),
            (OPENROUTER_BASE_URL, OPENROUTER_API_KEY,
             os.getenv("OPENROUTER_VISION_MODEL",
                       "meta-llama/llama-3.2-11b-vision-instruct:free").strip()),
            (GROQ_BASE_URL, GROQ_API_KEY, GROQ_VISION_MODEL),
            (TOGETHER_BASE_URL, TOGETHER_API_KEY,
             "meta-llama/Llama-4-Scout-17B-16E-Instruct"),
        )
        if VISION_BASE_URL and VISION_API_KEY and VISION_MODEL:
            return (VISION_BASE_URL, VISION_API_KEY, VISION_MODEL)
        for base, clave, modelo in candidatos:
            if base and clave and modelo:
                return (base, clave, modelo)
        return ("", "", "")
    return (
        VISION_BASE_URL or GROQ_BASE_URL,
        VISION_API_KEY or GROQ_API_KEY,
        VISION_MODEL or GROQ_VISION_MODEL,
    )


TEMPERATURE = _env_float("TEMPERATURE", "0.85")


# ---- Visión de pantalla ----
def _truthy(v):
    return str(v).lower() in ("1", "true", "yes", "si", "sí", "on")



# La visión de pantalla queda disponible desde que inicia la IA. No comenta
# periódicamente: solo analiza la pantalla al recibir una orden de PC o una
# petición explícita como /mira.
VISION_ENABLED = _truthy(os.getenv("VISION_ENABLED", "true"))
VISION_AUTOCOMMENT = _truthy(os.getenv("VISION_AUTOCOMMENT", "false"))
VISION_INTERVAL = _env_int("VISION_INTERVAL", "90")  # compatibilidad

# ---- Cámara local automática ----
CAMERA_ENABLED = _truthy(os.getenv("CAMERA_ENABLED", "true"))
CAMERA_ANALYSIS_INTERVAL = _env_float("CAMERA_ANALYSIS_INTERVAL", "1.2")
CAMERA_RETRY_INTERVAL = _env_float("CAMERA_RETRY_INTERVAL", "60")
# NUEVO (autoconexión): cada cuántos segundos se sondea si YA hay una cámara
# disponible cuando todavía no se encontró ninguna. Bajo a propósito (3 s) para
# que, si enciendes/conectas una cámara con YUE abierta, se enganche sola casi
# al momento sin reiniciar. Súbelo si no quieres que sondee tan seguido.
CAMERA_SEARCH_INTERVAL = _env_float("CAMERA_SEARCH_INTERVAL", "3")
CAMERA_MAX_INDEX = _env_int("CAMERA_MAX_INDEX", "3")
CAMERA_WIDTH = _env_int("CAMERA_WIDTH", "640")
CAMERA_HEIGHT = _env_int("CAMERA_HEIGHT", "480")
CAMERA_MAX_PEOPLE = _env_int("CAMERA_MAX_PEOPLE", "2")
CAMERA_CONTEXT_IN_CHAT = _truthy(os.getenv("CAMERA_CONTEXT_IN_CHAT", "true"))
CAMERA_CONTEXT_MAX_AGE = _env_float("CAMERA_CONTEXT_MAX_AGE", "8")

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
CAMERA_EMPATHY_MIN_CONFIDENCE = _env_float("CAMERA_EMPATHY_MIN_CONFIDENCE", "0.55")
# Segundos mínimos entre reacciones empáticas del mismo ánimo (anti-parpadeo).
CAMERA_EMPATHY_MIN_GAP = _env_float("CAMERA_EMPATHY_MIN_GAP", "8.0")

# ---- NUEVO (YUE HABLA al ver tu emoción por la cámara) ----
# Cuando YUE ve por la cámara una emoción clara (o detecta distrés sostenido =
# "peligro"), te lo comenta EN VOZ y te pregunta por qué estás así, con su tono.
# NO habla a cada gesto: solo cuando la emoción es fuerte y se mantiene, o cuando
# hay señal de peligro. Con todo un sistema de cuentagotas para no ser pesada.
# Se apaga por completo con CAMERA_EMOTION_TALK_ENABLED=false.
CAMERA_EMOTION_TALK_ENABLED = _truthy(os.getenv("CAMERA_EMOTION_TALK_ENABLED", "true"))
# Confianza mínima para considerar la emoción "muy fuerte" y digna de preguntar.
CAMERA_EMOTION_TALK_MIN_CONFIDENCE = _env_float("CAMERA_EMOTION_TALK_MIN_CONFIDENCE", "0.72")
# Cuántas lecturas seguidas de la MISMA emoción hacen falta antes de hablar (evita
# reaccionar a un gesto de un segundo). La cámara analiza ~1 vez/seg.
CAMERA_EMOTION_TALK_MIN_STREAK = _env_int("CAMERA_EMOTION_TALK_MIN_STREAK", "3")
# Cuentagotas GLOBAL: segundos mínimos entre dos comentarios hablados de emoción,
# sea cual sea (para que no hable seguido). 240 s = 4 min por defecto.
CAMERA_EMOTION_TALK_MIN_GAP = _env_float("CAMERA_EMOTION_TALK_MIN_GAP", "240.0")
# Cuentagotas por MISMA emoción: no vuelve a preguntar por el mismo ánimo hasta
# pasado este tiempo, aunque siga. 900 s = 15 min por defecto.
CAMERA_EMOTION_TALK_SAME_GAP = _env_float("CAMERA_EMOTION_TALK_SAME_GAP", "900.0")

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
KOKORO_SPEED = _env_float("KOKORO_SPEED", "1.06")
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
MIC_ENERGY_THRESHOLD = _env_int("MIC_ENERGY_THRESHOLD", "250")
# Ajuste automatico del umbral segun el ruido ambiente (recomendado: true).
MIC_DYNAMIC = _truthy(os.getenv("MIC_DYNAMIC", "true"))
# Silencio (segundos) que marca el fin de una frase. MÁS BAJO = responde antes
# (detecta el fin de tu frase más rápido). Bajado de 0.8 a 0.55 para que YUE
# reaccione más rápido cuando le hablas por voz. Si te corta a media frase, súbelo.
MIC_PAUSE = _env_float("MIC_PAUSE", "0.55")
MIC_PHRASE_LIMIT = _env_int("MIC_PHRASE_LIMIT", "14")
# Tiempo adicional sin captura despues de hablar y similitud para descartar eco.
# Bajado de 1.2 a 0.6 s: tras dejar de hablar, YUE vuelve a escucharte antes.
MIC_ECHO_COOLDOWN = _env_float("MIC_ECHO_COOLDOWN", "0.6")
MIC_ECHO_SIMILARITY = _env_float("MIC_ECHO_SIMILARITY", "0.68")
# Interrupción natural: el micrófono sigue activo mientras Yue habla y descarta su eco.
MIC_BARGE_IN_ENABLED = _truthy(os.getenv("MIC_BARGE_IN_ENABLED", "true"))
MIC_BARGE_IN_REQUIRE_WAKE_WORD = _truthy(os.getenv("MIC_BARGE_IN_REQUIRE_WAKE_WORD", "false"))
MIC_BARGE_IN_MIN_CHARS = _env_int("MIC_BARGE_IN_MIN_CHARS", "5")
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
MIC_BARGE_IN_GATE_MIN_CHARS = _env_int("MIC_BARGE_IN_GATE_MIN_CHARS", "4")
# Solape de palabras con el TTS por encima del cual se trata como eco (0-1).
# Más ALTO = más permisivo con tu voz mientras YUE habla. (Antes efectivo ~0.34.)
MIC_BARGE_IN_ECHO_OVERLAP = _env_float("MIC_BARGE_IN_ECHO_OVERLAP", "0.62")
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
MIC_MEDIA_GUARD_MIN_CHARS = _env_int("MIC_MEDIA_GUARD_MIN_CHARS", "8")

# ---- NUEVO (petición 3): refuerzo contra que Yue se escuche a sí misma ----
# Segundos extra de "vigilancia de eco" tras dejar de hablar (además del cooldown).
MIC_SELF_LISTEN_TAIL = _env_float("MIC_SELF_LISTEN_TAIL", "2.5")
# Umbral de solape de palabras con su propio TTS para tratar algo como eco parcial
# mientras habla o en la cola (más bajo = más estricto). Antes estaba fijo en 0.40.
MIC_SPEAKING_ECHO_OVERLAP = _env_float("MIC_SPEAKING_ECHO_OVERLAP", "0.34")

# ---- NUEVO (arreglo "no me escucha"): tope y auto-recuperación del micro ----
# Tope MÁXIMO del umbral de energía tras el ajuste al ruido ambiente. Antes estaba
# fijo (código) en max(MIC_ENERGY_THRESHOLD,120)*4 = 1000 por defecto. Si tu micro
# es "flojo"/lejano y "no te escucha", BAJA este número (p.ej. 400) para que la voz
# normal cruce el umbral. Valor por defecto = 1000 (idéntico al comportamiento previo).
MIC_ENERGY_MAX = _env_int("MIC_ENERGY_MAX", "1000")
# Vigilante del micro: cada X segundos, si el micro debería estar escuchando pero
# no arrancó (p.ej. el dispositivo estaba ocupado al abrir el programa), lo reintenta
# solo. Antes, si el arranque fallaba una vez, se quedaba mudo hasta reiniciar.
# Pon 0 para desactivar el reintento automático.
MIC_WATCHDOG_SECONDS = _env_int("MIC_WATCHDOG_SECONDS", "20")
# ---- NUEVO (arreglo "tarda en escucharme"): reconocimiento en hilo aparte ----
# El reconocimiento de voz de Google es una llamada por internet. Si se hace en el
# mismo hilo que captura el micro, BLOQUEA la escucha hasta terminar (por eso
# "tardaba" y se comía el inicio de la frase siguiente). Con esto, el micro encola
# el audio y vuelve a escuchar al instante; un hilo aparte reconoce en orden (FIFO).
# Pon false para volver al comportamiento anterior (reconocer en el mismo hilo).
MIC_ASYNC_RECOGNITION = _truthy(os.getenv("MIC_ASYNC_RECOGNITION", "true"))

# ---- NUEVO (petición 1): perfilador de contenido (vídeo normal vs musical) ----
# Ventana de memoria (segundos) y ajustes de decisión del clasificador.
MEDIA_PROFILE_WINDOW = _env_float("MEDIA_PROFILE_WINDOW", "12.0")
MEDIA_PROFILE_MIN_SAMPLES = _env_int("MEDIA_PROFILE_MIN_SAMPLES", "6")
MEDIA_PROFILE_HYSTERESIS = _env_int("MEDIA_PROFILE_HYSTERESIS", "3")
MEDIA_PROFILE_SILENCE_RATIO = _env_float("MEDIA_PROFILE_SILENCE_RATIO", "0.75")
MEDIA_PROFILE_MUSIC_RATIO = _env_float("MEDIA_PROFILE_MUSIC_RATIO", "0.62")
MEDIA_PROFILE_VOICE_RATIO = _env_float("MEDIA_PROFILE_VOICE_RATIO", "0.30")
MEDIA_PROFILE_TEMPO_CV = _env_float("MEDIA_PROFILE_TEMPO_CV", "0.18")
# Antirrebote del estado "hay media sonando" (nº de ventanas de ~1 s).
AUDIO_MEDIA_ON_BLOCKS = _env_int("AUDIO_MEDIA_ON_BLOCKS", "2")
AUDIO_MEDIA_OFF_BLOCKS = _env_int("AUDIO_MEDIA_OFF_BLOCKS", "6")

# ---- Acompañamiento multimedia continuo ----
# Coordina audio + frames individuales de pantalla + memoria + avatar. No graba
# vídeo ni audio. La captura visual se adapta entre 0.5 y 2 segundos.
MEDIA_COMPANION_ENABLED = _truthy(os.getenv("MEDIA_COMPANION_ENABLED", "true"))
MEDIA_COMPANION_VISUAL_ENABLED = _truthy(os.getenv("MEDIA_COMPANION_VISUAL_ENABLED", "true"))
MEDIA_COMPANION_COMMENTS_ENABLED = _truthy(os.getenv("MEDIA_COMPANION_COMMENTS_ENABLED", "true"))
MEDIA_COMPANION_AVATAR_ENABLED = _truthy(os.getenv("MEDIA_COMPANION_AVATAR_ENABLED", "true"))
MEDIA_COMPANION_VISUAL_INTERVAL = _env_float("MEDIA_COMPANION_VISUAL_INTERVAL", "1.0")
MEDIA_COMPANION_VISUAL_MIN_INTERVAL = _env_float("MEDIA_COMPANION_VISUAL_MIN_INTERVAL", "0.5")
MEDIA_COMPANION_VISUAL_MAX_INTERVAL = _env_float("MEDIA_COMPANION_VISUAL_MAX_INTERVAL", "2.0")
MEDIA_COMPANION_SCENE_CHANGE_THRESHOLD = _env_float("MEDIA_COMPANION_SCENE_CHANGE_THRESHOLD", "0.025")
MEDIA_COMPANION_VISUAL_STALE_SECONDS = _env_float("MEDIA_COMPANION_VISUAL_STALE_SECONDS", "12")
MEDIA_COMPANION_VISION_TIMEOUT = _env_float("MEDIA_COMPANION_VISION_TIMEOUT", "18")
MEDIA_COMPANION_VISION_RETRY_SECONDS = _env_float("MEDIA_COMPANION_VISION_RETRY_SECONDS", "60")
MEDIA_COMPANION_COMMENT_MIN_GAP = _env_float("MEDIA_COMPANION_COMMENT_MIN_GAP", "45")
MEDIA_COMPANION_COMMENT_MAX_GAP = _env_float("MEDIA_COMPANION_COMMENT_MAX_GAP", "150")
MEDIA_COMPANION_SPONTANEITY = _env_float("MEDIA_COMPANION_SPONTANEITY", "0.48")
MEDIA_COMPANION_EMOTION_INTENSITY = _env_float("MEDIA_COMPANION_EMOTION_INTENSITY", "1.0")
MEDIA_COMPANION_EMOTION_RISE_SECONDS = _env_float("MEDIA_COMPANION_EMOTION_RISE_SECONDS", "3.2")
MEDIA_COMPANION_EMOTION_FALL_SECONDS = _env_float("MEDIA_COMPANION_EMOTION_FALL_SECONDS", "7.0")
MEDIA_COMPANION_MUSIC_SENSITIVITY = _env_float("MEDIA_COMPANION_MUSIC_SENSITIVITY", "1.0")
MEDIA_COMPANION_VISUAL_SENSITIVITY = _env_float("MEDIA_COMPANION_VISUAL_SENSITIVITY", "1.0")
MEDIA_COMPANION_AVATAR_MOTION = _env_float("MEDIA_COMPANION_AVATAR_MOTION", "0.72")


# ---- Control seguro del PC ----
PC_MAX_ACTIONS = _env_int("PC_MAX_ACTIONS", "24")
PC_MAX_CYCLES = _env_int("PC_MAX_CYCLES", "6")
PC_MAX_ACTIONS_PER_CYCLE = _env_int("PC_MAX_ACTIONS_PER_CYCLE", "4")
PC_VISUAL_RECHECK = _truthy(os.getenv("PC_VISUAL_RECHECK", "true"))
PC_ACTION_PAUSE = _env_float("PC_ACTION_PAUSE", "0.08")
PC_TYPE_INTERVAL = _env_float("PC_TYPE_INTERVAL", "0.012")
PC_MAX_TYPE_CHARS = _env_int("PC_MAX_TYPE_CHARS", "4000")
# ---- Motor autónomo de ejecución ----
PC_AGENT_MAX_WORKERS = _env_int("PC_AGENT_MAX_WORKERS", "3")
PC_AGENT_PARALLEL = _truthy(os.getenv("PC_AGENT_PARALLEL", "true"))
PC_AGENT_RETRY_ATTEMPTS = _env_int("PC_AGENT_RETRY_ATTEMPTS", "3")
PC_AGENT_WAIT_TIMEOUT = _env_float("PC_AGENT_WAIT_TIMEOUT", "10")
PC_AGENT_APP_TIMEOUT = _env_float("PC_AGENT_APP_TIMEOUT", "15")
PC_AGENT_MAX_REPLANS = _env_int("PC_AGENT_MAX_REPLANS", str(PC_MAX_CYCLES))
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
PC_APP_CATALOG_MAX_AGE_HOURS = _env_float("PC_APP_CATALOG_MAX_AGE_HOURS", "72")
PC_APP_CATALOG_PATH = Path(os.getenv("PC_APP_CATALOG_PATH", str(APP_DIR / "data" / "apps_catalogo.json")))
PC_ACTION_LOG_SIZE = _env_int("PC_ACTION_LOG_SIZE", "8")

# ---- Verificación por acción (¿la pantalla cambió?) ----
PC_VERIFY_ACTIONS = _truthy(os.getenv("PC_VERIFY_ACTIONS", "true"))
PC_VERIFY_DELAY = _env_float("PC_VERIFY_DELAY", "0.35")       # espera antes de recomprobar
PC_VERIFY_HASH_DISTANCE = _env_int("PC_VERIFY_HASH_DISTANCE", "1")
PC_VERIFY_PIXEL_RATIO = _env_float("PC_VERIFY_PIXEL_RATIO", "0.0015")
PC_VERIFY_PIXEL_DELTA = _env_int("PC_VERIFY_PIXEL_DELTA", "14")
# Verificación por celdas: detecta cambios pequeños y concentrados (un dígito en
# la calculadora) que el ratio global de toda la pantalla no llega a ver.
PC_VERIFY_TILES = _env_int("PC_VERIFY_TILES", "8")
PC_VERIFY_TILE_RATIO = _env_float("PC_VERIFY_TILE_RATIO", "0.02")
# Ventanas propias de Yue: se enmascaran antes de comparar, porque el avatar VRM
# parpadea y respira y si no la pantalla "cambia" siempre.
PC_VERIFY_IGNORE_TITLES = tuple(
    item.strip() for item in os.getenv("PC_VERIFY_IGNORE_TITLES", "yue_companion,yue").split(",")
    if item.strip()
)

# ---- Contexto de UI real (pywinauto) ----
PC_UI_CONTEXT = _truthy(os.getenv("PC_UI_CONTEXT", "true"))
PC_UI_MAX_ELEMENTS = _env_int("PC_UI_MAX_ELEMENTS", "40")
# OCR como "ojos" del planificador: sin modelo de vision, el arbol UIA dice que
# BOTONES hay pero no que se esta VIENDO (el visor de la calculadora, el texto
# ya escrito). Sin esto el modelo repite el mismo clic sin saber que ya surtio
# efecto. Requiere Tesseract; si no esta, se ignora sin romper nada.
PC_OCR_CONTEXT = _truthy(os.getenv("PC_OCR_CONTEXT", "true"))
# Apartar las ventanas de Yue del mouse mientras controla el PC. Se aplica SIEMPRE
# desde el hilo de Qt (ver core/ui_bridge.py): hacerlo desde el hilo del control
# cuelga la aplicacion. Ponlo en false si sospechas de este mecanismo.
PC_CLICK_THROUGH = _truthy(os.getenv("PC_CLICK_THROUGH", "true"))
PC_CLICK_THROUGH_TIMEOUT = _env_float("PC_CLICK_THROUGH_TIMEOUT", "1.0")
PC_OCR_MAX_CHARS = _env_int("PC_OCR_MAX_CHARS", "900")
PC_UI_MAX_WINDOWS = _env_int("PC_UI_MAX_WINDOWS", "18")

# ---- Accesibilidad: control del cursor con la cabeza ----
# Reutiliza los MISMOS landmarks faciales de la cámara (MediaPipe) que ya se
# obtienen para las emociones; NO abre otra cámara ni otro pipeline. Pensado
# para personas con dificultad de motricidad fina en las manos.
HEAD_CONTROL_ENABLED = _truthy(os.getenv("HEAD_CONTROL_ENABLED", "true"))
# Cadencia rápida de la cámara SOLO mientras el control por cabeza está activo
# (el resto del tiempo la cámara sigue a su ritmo lento de siempre).
HEAD_CONTROL_FPS = _env_float("HEAD_CONTROL_FPS", "20")
# Zona muerta central (en unidades normalizadas de orientación): mirar de frente
# NO mueve el cursor, así los temblores/microgestos naturales no lo desplazan.
HEAD_CONTROL_DEADZONE = _env_float("HEAD_CONTROL_DEADZONE", "0.06")
# Sensibilidad: píxeles de desplazamiento por unidad de giro y por fotograma.
HEAD_CONTROL_GAIN = _env_float("HEAD_CONTROL_GAIN", "55")
# Tope de velocidad (px por fotograma) para que nunca dé saltos bruscos.
HEAD_CONTROL_MAX_SPEED = _env_float("HEAD_CONTROL_MAX_SPEED", "38")
# Suavizado exponencial (0..1): más alto = más suave pero con algo más de lag.
HEAD_CONTROL_SMOOTHING = _env_float("HEAD_CONTROL_SMOOTHING", "0.5")
# Dwell-click: si el cursor se queda dentro de un radio pequeño N ms, clic izq.
HEAD_CONTROL_DWELL_MS = _env_int("HEAD_CONTROL_DWELL_MS", "900")
HEAD_CONTROL_DWELL_RADIUS = _env_int("HEAD_CONTROL_DWELL_RADIUS", "22")
# Refractario tras un clic por dwell (ms) para que no repita clics sin querer.
HEAD_CONTROL_DWELL_COOLDOWN_MS = _env_int("HEAD_CONTROL_DWELL_COOLDOWN_MS", "700")
# Margen (px) para no acercar el cursor a las esquinas (evita el FAILSAFE).
HEAD_CONTROL_MARGIN = _env_int("HEAD_CONTROL_MARGIN", "3")
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
ACCESSIBILITY_CONFIRM_TIMEOUT = _env_float("ACCESSIBILITY_CONFIRM_TIMEOUT", "25")

# ---- OCR de pantalla (acción click_text) ----
OCR_ENGINE = os.getenv("OCR_ENGINE", "pytesseract")
OCR_LANG = os.getenv("OCR_LANG", "spa+eng")
OCR_MIN_CONF = _env_float("OCR_MIN_CONF", "55")
# Ruta al binario de Tesseract si no está en el PATH, por ejemplo:
# C:\Program Files\Tesseract-OCR\tesseract.exe
OCR_TESSERACT_CMD = os.getenv("OCR_TESSERACT_CMD", "")

# ---- Aprendizaje continuo de órdenes de PC ----
LEARNING_ENABLED = _truthy(os.getenv("LEARNING_ENABLED", "true"))
LEARNING_SIM_THRESHOLD = _env_float("LEARNING_SIM_THRESHOLD", "0.85")
LEARNING_MIN_SUCCESSES = _env_int("LEARNING_MIN_SUCCESSES", "2")
LEARNING_MIN_RATIO = _env_float("LEARNING_MIN_RATIO", "0.7")
LEARNING_FAIL_STREAK = _env_int("LEARNING_FAIL_STREAK", "2")
LEARNING_LESSON_THRESHOLD = _env_float("LEARNING_LESSON_THRESHOLD", "0.55")
LEARNING_MAX_SKILLS = _env_int("LEARNING_MAX_SKILLS", "200")
LEARNING_MAX_LESSONS = _env_int("LEARNING_MAX_LESSONS", "200")
LEARNING_MAX_PLAN_STEPS = _env_int("LEARNING_MAX_PLAN_STEPS", "24")
LEARNING_REFLECT = _truthy(os.getenv("LEARNING_REFLECT", "true"))

# ---- Autonomia local y segura ----
AUTONOMY_ENABLED = _truthy(os.getenv("AUTONOMY_ENABLED", "true"))
AUTONOMY_INTERVAL = _env_int("AUTONOMY_INTERVAL", "600")
AUTONOMY_IDLE_SECONDS = _env_int("AUTONOMY_IDLE_SECONDS", "120")
AUTONOMY_NOTIFY = _truthy(os.getenv("AUTONOMY_NOTIFY", "true"))
AUTONOMY_MAX_PER_SESSION = _env_int("AUTONOMY_MAX_PER_SESSION", "3")
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
CHECKIN_MIN_HOURS = _env_float("CHECKIN_MIN_HOURS", "20")
# Cada cuántos segundos se evalúa si toca un check-in proactivo (barato).
CHECKIN_CHECK_INTERVAL = _env_int("CHECKIN_CHECK_INTERVAL", "90")

# ---- Apariencia ----
PET_HEIGHT = _env_int("PET_HEIGHT", "330")
# Usar el avatar 3D VRM si esta disponible (si falla, usa el PNG):
USE_VRM = _truthy(os.getenv("USE_VRM", "true"))
AVATAR_WIDTH = _env_int("AVATAR_WIDTH", "320")
AVATAR_HEIGHT = _env_int("AVATAR_HEIGHT", "520")
# Giro de la cara en grados. VRM 1.0 = 0 (te mira). Si ves su espalda, pon 180.
AVATAR_TURN = _env_float("AVATAR_TURN", "0")
# --- Pose de los brazos (en radianes) ---
# Cuanto bajan los brazos al costado (1.57 = totalmente verticales/pegados):
AVATAR_ARM_DOWN = _env_float("AVATAR_ARM_DOWN", "1.48")
# Adelante (+) / atras (-). Si los brazos se ven DETRAS del cuerpo, SUBE este numero
# (p.ej. 0.5). Si se ven muy adelante, bajalo:
AVATAR_ARM_FWD = _env_float("AVATAR_ARM_FWD", "0.35")
# Giro de los antebrazos hacia adentro (manos hacia el cuerpo):
AVATAR_ARM_IN = _env_float("AVATAR_ARM_IN", "0.12")
# --- Encuadre de la camara ---
# Distancia de la camara. Mas grande = se ve mas cuerpo y toda la cabeza:
AVATAR_CAM_DIST = _env_float("AVATAR_CAM_DIST", "1.85")
# Aim vertical relativo a la cabeza. Mas negativo = la cabeza sube en el encuadre
# (util si se corta el pelo de arriba):
AVATAR_CAM_Y = _env_float("AVATAR_CAM_Y", "-0.16")
# Modo prueba: pinta un fondo oscuro para comprobar que el 3D renderiza.
AVATAR_DEBUG = _truthy(os.getenv("AVATAR_DEBUG", "false"))
# Cuanta gesticulacion tiene el avatar. 0 = solo el balanceo de antes,
# 1 = normal, 1.5 = teatral. Los gestos nunca sacan al hueso de su rango.
AVATAR_GESTURE_GAIN = _env_float("AVATAR_GESTURE_GAIN", "1.0")

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
IMAGE_WIDTH = _env_int("IMAGE_WIDTH", "768")
IMAGE_HEIGHT = _env_int("IMAGE_HEIGHT", "1024")
FLUX_STEPS = _env_int("FLUX_STEPS", "4")  # schnell va bien con 4

# -- Video (FFmpeg) --
# Ruta al ejecutable de ffmpeg/ffprobe (si no estan en el PATH del sistema):
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")
VIDEO_SECONDS = _env_int("VIDEO_SECONDS", "16")  # duracion del recuerdo
VIDEO_FPS = _env_int("VIDEO_FPS", "30")
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
TEACHER_AUTOADVANCE_PAUSE_MS = _env_int("TEACHER_AUTOADVANCE_PAUSE_MS", "2500")
DB_PATH = DATA_DIR / "yue.db"
# ---- Memoria a largo plazo (consolidación) ----
# Cada cuántos días, COMO MÍNIMO, se comprime el historial viejo en un resumen
# persistente corto que vuelve a entrar al prompt. Best-effort: si no hay clave de
# IA o la llamada falla, no consolida esta vez y lo reintenta la próxima. Un valor
# <= 0 desactiva la consolidación por completo.
MEMORY_CONSOLIDATION_DAYS = _env_int("MEMORY_CONSOLIDATION_DAYS", "7")
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
WELLBEING_RISK_DAYS = _env_int("WELLBEING_RISK_DAYS", "7")
WELLBEING_MIN_RISK_EVENTS = _env_int("WELLBEING_MIN_RISK_EVENTS", "2")
# Condición 2: uso muy intensivo -> una sesión continua de más de X horas
# (un hueco mayor a WELLBEING_SESSION_GAP_MIN minutos corta la sesión).
WELLBEING_SESSION_HOURS = _env_float("WELLBEING_SESSION_HOURS", "3")
WELLBEING_SESSION_GAP_MIN = _env_float("WELLBEING_SESSION_GAP_MIN", "30")
# Espaciado: al cumplirse alguna condición se abre una "ventana" breve en la que
# YUE puede sacar el tema con naturalidad; luego se calla un buen rato.
WELLBEING_NUDGE_WINDOW_MIN = _env_float("WELLBEING_NUDGE_WINDOW_MIN", "20")
WELLBEING_NUDGE_COOLDOWN_HOURS = _env_float("WELLBEING_NUDGE_COOLDOWN_HOURS", "8")


# ==========================================================================
# YUE V3 PERCEPTION SYSTEM (cámara que "ve" a la persona: presencia, rostro,
# emoción, atención). ADITIVO y OPT-IN: apagado por defecto para no pelear por
# la webcam con el observador clásico (core/camera_observer.py). Enciéndelo con
# VISION_V3_ENABLED=true y, si el clásico también usa la misma cámara, pon
# CAMERA_ENABLED=false o dale a V3 otro índice con CAMERA_INDEX.
# ==========================================================================
VISION_V3_ENABLED = _truthy(os.getenv("VISION_V3_ENABLED", "false"))
# Índice de cámara del sistema V3 (0 = webcam principal).
CAMERA_INDEX = _env_int("CAMERA_INDEX", "0")
# Privacidad: constancia explícita de que NO se guardan ni envían imágenes.
CAMERA_PRIVACY_MODE = _truthy(os.getenv("CAMERA_PRIVACY_MODE", "true"))

# Perfil de rendimiento por hardware: auto | LOW | MEDIUM | HIGH.
#   LOW    -> DeepFace cada 8 s, ~12 FPS   (i3 antiguos, 8 GB, gráficos integrados)
#   MEDIUM -> DeepFace cada 5 s, ~20 FPS   (16 GB, GPU media)
#   HIGH   -> DeepFace cada 2 s, ~30 FPS   (GPU dedicada)
VISION_PERF_PROFILE = os.getenv("VISION_PERF_PROFILE", "auto").strip()
# Overrides finos opcionales (0 = usar lo del perfil, sin tocar).
VISION_EMOTION_INTERVAL = _env_float("VISION_EMOTION_INTERVAL", "0")
VISION_TARGET_FPS = _env_float("VISION_TARGET_FPS", "0")

# Emociones -> comportamiento. Persona que comenta lo que ve: YUE | KAI.
VISION_EMOTION_PERSONA = os.getenv("VISION_EMOTION_PERSONA", "YUE").strip()
# Tope de frases por emoción a la hora (para que no hable de más).
MAX_EMOTION_RESPONSES_PER_HOUR = _env_int("MAX_EMOTION_RESPONSES_PER_HOUR", "4")
# Confianza mínima para hacer caso a una emoción.
# ESCALA UNIFICADA: se acepta escribirla como fracción (0.45) o como porcentaje
# (45) en el .env; ambas formas significan lo mismo. Se publican las DOS
# lecturas para que cada sistema use la suya sin contradecirse:
#   VISION_EMOTION_MIN_CONFIDENCE      -> 0..1   (paquete vision/)
#   VISION_EMOTION_MIN_CONFIDENCE_PCT  -> 0..100 (core/vision, percepción V3)
VISION_EMOTION_MIN_CONFIDENCE = _env_conf01("VISION_EMOTION_MIN_CONFIDENCE", "0.45")
VISION_EMOTION_MIN_CONFIDENCE_PCT = _env_conf_pct("VISION_EMOTION_MIN_CONFIDENCE", "0.45")
# Enfriamiento (s): no repetir la misma emoción hablada tan seguido.
VISION_EMOTION_COOLDOWN = _env_float("VISION_EMOTION_COOLDOWN", "120")
# Anti-spam del gesto empático del avatar (s).
VISION_AVATAR_MIN_GAP = _env_float("VISION_AVATAR_MIN_GAP", "6")

# Atención: segundos de ausencia continua para avisar (10 min por defecto) y
# semiancho de la "zona central" para considerar que mira de frente (0..0.5).
VISION_ABSENCE_SECONDS = _env_float("VISION_ABSENCE_SECONDS", "600")
VISION_ATTENTION_CENTER_BAND = _env_float("VISION_ATTENTION_CENTER_BAND", "0.22")


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
CAMERA_TARGET_FPS = _env_float("CAMERA_TARGET_FPS", "0")

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
VISION_FACE_DETECTOR_FPS = _env_float("VISION_FACE_DETECTOR_FPS", "0")
VISION_FACE_LANDMARKER_FPS = _env_float("VISION_FACE_LANDMARKER_FPS", "0")
VISION_POSE_FPS = _env_float("VISION_POSE_FPS", "0")
VISION_GESTURE_FPS = _env_float("VISION_GESTURE_FPS", "0")
VISION_OBJECT_FPS = _env_float("VISION_OBJECT_FPS", "0")
VISION_CLASSIFIER_FPS = _env_float("VISION_CLASSIFIER_FPS", "0")

# Límites de detección.
VISION_MAX_FACES = _env_int("VISION_MAX_FACES", "3")
VISION_MAX_HANDS = _env_int("VISION_MAX_HANDS", "2")

# Confianzas mínimas (0..1).
VISION_FACE_MIN_CONFIDENCE = _env_float("VISION_FACE_MIN_CONFIDENCE", "0.5")
VISION_POSE_MIN_CONFIDENCE = _env_float("VISION_POSE_MIN_CONFIDENCE", "0.5")
VISION_GESTURE_MIN_CONFIDENCE = _env_float("VISION_GESTURE_MIN_CONFIDENCE", "0.6")
VISION_OBJECT_MIN_CONFIDENCE = _env_float("VISION_OBJECT_MIN_CONFIDENCE", "0.5")

# Privacidad y depuración. Por diseño NO se guardan ni se envían imágenes.
VISION_SAVE_FRAMES = _truthy(os.getenv("VISION_SAVE_FRAMES", "false"))
VISION_EXTERNAL_UPLOAD = _truthy(os.getenv("VISION_EXTERNAL_UPLOAD", "false"))
VISION_DEBUG_OVERLAY = _truthy(os.getenv("VISION_DEBUG_OVERLAY", "false"))

# ===========================================================================
# VISIÓN AVANZADA — OCR, escena, acciones, emociones y privacidad  [ADITIVO]
# ---------------------------------------------------------------------------
# Bloque agregado por la integración del motor de percepción. Todo vive DENTRO
# de VISION_MP_ENABLED: si ese interruptor está en false, nada de esto se
# ejecuta ni consume recursos. El paquete vision/ tiene sus propios valores por
# defecto, así que este bloque es para descubribilidad y para sobrescribir.
# ===========================================================================

# Motor de percepción (OCR, habitación, acciones, emociones). Con false, el
# paquete vision/ se comporta exactamente como antes de esta integración.
VISION_PERCEPTION_ENABLED = _truthy(os.getenv("VISION_PERCEPTION_ENABLED", "true"))

# Interruptor de cámara PROPIO del motor nuevo, separado del clásico.
# CAMERA_ENABLED gobierna el CameraObserver de siempre. Al migrar hay que
# apagarlo para que no pelee por la webcam, y antes eso dejaba también sin
# cámara al motor nuevo. Ahora:
#   - vacío + VISION_REPLACE_LEGACY=true  -> el motor nuevo enciende la cámara,
#   - vacío + sin migración               -> hereda CAMERA_ENABLED (como antes),
#   - true/false explícito                -> manda siempre.
VISION_CAMERA_ENABLED = os.getenv("VISION_CAMERA_ENABLED", "").strip()

# Seguimiento fino de manos y dedos (21 puntos por mano).
VISION_HANDS_ENABLED = _truthy(os.getenv("VISION_HANDS_ENABLED", "true"))
VISION_HAND_FPS = _env_float("VISION_HAND_FPS", "0")

# Límite de personas seguidas a la vez y dispositivo de inferencia.
VISION_MAX_PEOPLE = _env_int("VISION_MAX_PEOPLE", "4")
VISION_DEVICE = os.getenv("VISION_DEVICE", "auto").strip().lower()
VISION_DEBUG = _truthy(os.getenv("VISION_DEBUG", "false"))
VISION_AUTO_SEARCH_CAMERA = _truthy(os.getenv("VISION_AUTO_SEARCH_CAMERA", "true"))
# Backend de captura: vacío = automático (prueba MSMF, DSHOW y ANY y se queda
# con el que entregue flujo sostenido). Fuerza uno con msmf | dshow | v4l2 | any
# si el diagnóstico te lo recomienda.
VISION_CAMERA_BACKEND = os.getenv("VISION_CAMERA_BACKEND", "").strip().lower()
VISION_MAX_CAMERA_INDEX = _env_int("VISION_MAX_CAMERA_INDEX", "4")

# --- Lectura de textos por cámara (OCR) ---
# Motor: auto | paddleocr | easyocr | tesseract | ninguno. Todos son LOCALES.
VISION_OCR_ENABLED = _truthy(os.getenv("VISION_OCR_ENABLED", "true"))
VISION_OCR_ENGINE = os.getenv("VISION_OCR_ENGINE", "auto").strip().lower()
VISION_OCR_LANGUAGES = os.getenv("VISION_OCR_LANGUAGES", "es").strip()
# Por defecto YUE NO lee sola: espera a que le pidas "lee esto". Ponlo en false
# solo si quieres que lea automáticamente cualquier texto que aparezca.
VISION_OCR_ONLY_ON_REQUEST = _truthy(os.getenv("VISION_OCR_ONLY_ON_REQUEST", "true"))
# Fotogramas que deben coincidir antes de dar un texto por estable.
VISION_OCR_MIN_AGREEMENTS = _env_int("VISION_OCR_MIN_AGREEMENTS", "3")
VISION_OCR_MIN_CONFIDENCE = _env_float("VISION_OCR_MIN_CONFIDENCE", "0.35")
VISION_TEXT_WATCH_FPS = _env_float("VISION_TEXT_WATCH_FPS", "0.5")

# --- Descripción de la habitación ---
VISION_SCENE_DESCRIPTION_ENABLED = _truthy(os.getenv("VISION_SCENE_DESCRIPTION_ENABLED", "true"))
VISION_SCENE_FPS = _env_float("VISION_SCENE_FPS", "0.3")

# --- Reconocimiento de acciones (análisis temporal) ---
VISION_ACTIONS_ENABLED = _truthy(os.getenv("VISION_ACTIONS_ENABLED", "true"))
VISION_ACTION_FPS = _env_float("VISION_ACTION_FPS", "6")

# --- Estimación afectiva ---
# IMPORTANTE: es una ESTIMACIÓN probabilística, nunca un hecho psicológico.
# YUE jamás diagnostica ni afirma emociones como certezas.
VISION_EMOTIONS_ENABLED = _truthy(os.getenv("VISION_EMOTIONS_ENABLED", "true"))
# Se relee con el mismo criterio que arriba (0.45 y 45 son equivalentes), así
# la definición duplicada ya no cambia el significado del valor.
VISION_EMOTION_MIN_CONFIDENCE = _env_conf01("VISION_EMOTION_MIN_CONFIDENCE", "0.45")
VISION_EMOTION_MIN_CONFIDENCE_PCT = _env_conf_pct("VISION_EMOTION_MIN_CONFIDENCE", "0.45")
# El ritmo de voz solo se usa como señal afectiva con permiso EXPLÍCITO.
VISION_EMOTION_USE_VOICE = _truthy(os.getenv("VISION_EMOTION_USE_VOICE", "false"))

# --- Antirrebote de eventos visuales ---
VISION_EVENT_MIN_DURATION = _env_float("VISION_EVENT_MIN_DURATION", "0.45")
VISION_EVENT_COOLDOWN = _env_float("VISION_EVENT_COOLDOWN", "4")

# --- Privacidad (punto 13). Los valores por defecto son los más restrictivos ---
VISION_PROCESS_LOCAL = _truthy(os.getenv("VISION_PROCESS_LOCAL", "true"))
VISION_SAVE_EVENTS = _truthy(os.getenv("VISION_SAVE_EVENTS", "false"))
VISION_ALLOW_CLOUD = _truthy(os.getenv("VISION_ALLOW_CLOUD", "false"))
# Con true, YUE no analiza nada hasta que se lo pidas expresamente.
VISION_ONLY_ON_REQUEST = _truthy(os.getenv("VISION_ONLY_ON_REQUEST", "false"))

# --- Modelos ---
# variant: full | lite. Los modelos NUNCA se descargan solos.
VISION_MODEL_VARIANT = os.getenv("VISION_MODEL_VARIANT", "full").strip().lower()
VISION_ALLOW_DOWNLOAD = _truthy(os.getenv("VISION_ALLOW_DOWNLOAD", "false"))

# --- Migración desde el CameraObserver clásico (punto 16) ---
# Con true, self.camera pasa a ser el adaptador que habla con el motor nuevo.
# Se deja en false hasta validar el sistema nuevo en tu equipo.
VISION_REPLACE_LEGACY = _truthy(os.getenv("VISION_REPLACE_LEGACY", "false"))
# FPS de los landmarks que alimentan el control del cursor por cabeza. Van a más
# cadencia que el resto porque el cursor se nota enseguida si va lento. Solo se
# ejecutan mientras el control por cabeza está activo.
VISION_HEAD_CONTROL_FPS = _env_float("VISION_HEAD_CONTROL_FPS", "0")

# ==========================================================================
# COMPRENSIÓN EMOCIONAL v2 (core.affect + core.support)
# ==========================================================================
# Sistema híbrido: las reglas locales resuelven la mayoría de los mensajes sin
# red ni coste; solo lo ambiguo se consulta al modelo.

# Permite la segunda opinión del LLM ante mensajes ambiguos. Con false, YUE
# funciona 100% offline con reglas locales (algo menos fina, igual de segura).
AFFECT_SEMANTIC_ENABLED = _truthy(os.getenv("AFFECT_SEMANTIC_ENABLED", "true"))
# Por debajo de esta confianza local, se pide la segunda opinión.
AFFECT_CONFIDENCE_THRESHOLD = _env_float("AFFECT_CONFIDENCE_THRESHOLD", "0.62")
# Techo de llamadas semánticas por sesión (control de coste y latencia).
AFFECT_SEMANTIC_MAX_CALLS = _env_int("AFFECT_SEMANTIC_MAX_CALLS", "40")
# Segundos máximos para la consulta semántica. Corto: si tarda, mejor reglas.
AFFECT_SEMANTIC_TIMEOUT = _env_int("AFFECT_SEMANTIC_TIMEOUT", "12")
# Cuántos intercambios recientes se pasan como contexto (2-4 recomendado).
AFFECT_CONTEXT_TURNS = _env_int("AFFECT_CONTEXT_TURNS", "3")
# Imprime en consola la lectura afectiva de cada mensaje. Solo para depurar.
AFFECT_DEBUG = _truthy(os.getenv("AFFECT_DEBUG", "false"))

# ---------------------------------------------------------------------------
# CEREBRO CENTRAL (core/state) — arbitraje del comportamiento de YUE
# ---------------------------------------------------------------------------
# Cada cuánto late el gestor de estado, en milisegundos. Es lo que caduca las
# propuestas vencidas y recalcula quién gobierna el avatar. 250 ms es cómodo:
# lo bastante fino para que no se note el retardo y lo bastante espaciado para
# no costar nada. Por debajo de 100 ms se ignora.
STATE_TICK_MS = _env_int("STATE_TICK_MS", "250")

# Registra en el log las decisiones del arbitraje: qué observó cada sensor, qué
# propuso cada módulo y cuál ganó. Es la forma de entender POR QUÉ YUE puso una
# cara concreta. Sale por `logging` (nivel INFO), no por print, así que no
# ensucia la consola salvo que se configure un handler.
STATE_LOG_DECISIONS = _truthy(os.getenv("STATE_LOG_DECISIONS", "true"))

# ===========================================================================
# MEMORIA EPISÓDICA EMOCIONAL (core/episodic_memory.py)
# ===========================================================================
# Capa ADITIVA sobre la memoria existente. facts/goals/mood_log/affect_log
# siguen exactamente igual; esto añade el recuerdo de ACONTECIMIENTOS concretos
# ligados a una emoción («mañana tiene una entrevista y está nervioso porque la
# anterior salió mal»), su seguimiento y su desenlace.

# Interruptor general. Con false, YUE se comporta exactamente como antes.
EPISODIC_MEMORY_ENABLED = _truthy(os.getenv("EPISODIC_MEMORY_ENABLED", "true"))

# Importancia mínima (0-1) para que un acontecimiento merezca ser un recuerdo.
# Por debajo, se descarta: no queremos "mañana compraré papel higiénico" en la
# memoria emocional. Subirlo hace a YUE más selectiva; bajarlo, más acumuladora.
EPISODIC_MIN_IMPORTANCE = _env_float("EPISODIC_MIN_IMPORTANCE", "0.55")

# Cuántos recuerdos concretos entran como MÁXIMO en el prompt. Pocos y bien
# elegidos: con más, YUE empieza a hablar como si leyera un expediente.
EPISODIC_MAX_CONTEXT = _env_int("EPISODIC_MAX_CONTEXT", "3")

# Cuántas veces puede YUE preguntar POR SU CUENTA por el mismo episodio. 1 es
# lo correcto: preguntar una vez es cariño, insistir es acoso.
EPISODIC_FOLLOWUP_MAX = _env_int("EPISODIC_FOLLOWUP_MAX", "1")

# Días que se conserva un episodio abierto antes de caducar (no se borra: pasa
# a 'expired'). Los triviales y ya cerrados se purgan pasado ese plazo.
EPISODIC_RETENTION_DAYS = _env_int("EPISODIC_RETENTION_DAYS", "90")

# --- Detección -------------------------------------------------------------
# Puntuación mínima del filtro LOCAL para considerar que hay un candidato y
# merecer una extracción estructurada. Es el freno de coste del sistema.
EPISODIC_CANDIDATE_THRESHOLD = _env_float("EPISODIC_CANDIDATE_THRESHOLD", "0.45")
# Permite pedir al modelo la extracción estructurada del acontecimiento. Con
# false, todo funciona con el extractor local (offline, algo menos fino).
EPISODIC_USE_LLM = _truthy(os.getenv("EPISODIC_USE_LLM", "true"))
# Techo de extracciones por sesión y timeout de cada una.
EPISODIC_MAX_LLM_CALLS = _env_int("EPISODIC_MAX_LLM_CALLS", "25")
EPISODIC_LLM_TIMEOUT = _env_int("EPISODIC_LLM_TIMEOUT", "12")
# La extracción corre en un hilo aparte para no congelar la interfaz mientras
# YUE responde. Ponlo en false solo para depurar de forma determinista.
EPISODIC_ASYNC = _truthy(os.getenv("EPISODIC_ASYNC", "true"))
# Log detallado [EPISODE] en consola.
EPISODIC_DEBUG = _truthy(os.getenv("EPISODIC_DEBUG", "false"))

# --- Deduplicación ---------------------------------------------------------
# Ventana en la que se buscan episodios abiertos parecidos, y parecido mínimo
# para dar dos menciones por el mismo acontecimiento.
EPISODIC_DEDUP_WINDOW_DAYS = _env_float("EPISODIC_DEDUP_WINDOW_DAYS", "30")
EPISODIC_DEDUP_THRESHOLD = _env_float("EPISODIC_DEDUP_THRESHOLD", "0.45")
# Cuántas horas sigue valiendo un «esa entrevista» / «lo de mañana» para
# referirse a un episodio ya guardado.
EPISODIC_ANAPHORA_WINDOW_HOURS = _env_float("EPISODIC_ANAPHORA_WINDOW_HOURS", "72")

# --- Seguimiento -----------------------------------------------------------
# Hora del día a la que se pregunta cuando solo se conoce el DÍA del evento.
EPISODIC_FOLLOWUP_HOUR = _env_int("EPISODIC_FOLLOWUP_HOUR", "19")
# Margen tras un evento con hora exacta antes de preguntar cómo fue.
EPISODIC_FOLLOWUP_AFTER_HOURS = _env_float("EPISODIC_FOLLOWUP_AFTER_HOURS", "2")
# Si el episodio es muy importante pero no tiene fecha, se pregunta pasado este
# tiempo desde que se contó.
EPISODIC_FOLLOWUP_UNKNOWN_HOURS = _env_float("EPISODIC_FOLLOWUP_UNKNOWN_HOURS", "24")
# Minutos de SILENCIO proactivo después de que el usuario pida espacio o diga
# que no quiere preguntas. Ningún seguimiento episódico salta en ese rato.
EPISODIC_QUIET_AFTER_BOUNDARY_MIN = _env_float(
    "EPISODIC_QUIET_AFTER_BOUNDARY_MIN", "180")

# --- Seguridad -------------------------------------------------------------
# Nivel de core.safety_ext (0 ninguno … 4 crítico) a partir del cual NO se crea
# episodio ni se programa seguimiento. Ese terreno lo lleva el sistema de
# seguridad, y un "¿cómo te fue?" automático ahí sería insensible.
EPISODIC_SAFETY_BLOCK_LEVEL = _env_int("EPISODIC_SAFETY_BLOCK_LEVEL", "2")
# Días tras un evento de riesgo durante los cuales se suspenden los
# seguimientos episódicos ordinarios.
EPISODIC_RISK_BLOCK_DAYS = _env_int("EPISODIC_RISK_BLOCK_DAYS", "2")

# --- Contexto --------------------------------------------------------------
# Antigüedad máxima de los episodios que pueden volver al prompt.
EPISODIC_CONTEXT_DAYS = _env_float("EPISODIC_CONTEXT_DAYS", "21")
# Relevancia mínima para colarse en el prompt: sin esto, YUE sacaría un
# recuerdo en cada respuesta y la continuidad dejaría de sentirse natural.
EPISODIC_CONTEXT_MIN_SCORE = _env_float("EPISODIC_CONTEXT_MIN_SCORE", "0.35")
# Ventana (segundos) para completar `yue_action` tras responder YUE.
EPISODIC_ACTION_WINDOW_SEC = _env_float("EPISODIC_ACTION_WINDOW_SEC", "180")
# Días que mira la observación de patrones repetidos (consolidación).
EPISODIC_PATTERN_DAYS = _env_float("EPISODIC_PATTERN_DAYS", "180")
# Parecido mínimo para dar por hecho que un comentario suelto («es que la
# anterior me salió mal») CONTINÚA un episodio ya abierto. Más bajo que el de
# deduplicación: aquí no se crea nada nuevo, solo se añade contexto.
EPISODIC_CONTINUATION_THRESHOLD = _env_float("EPISODIC_CONTINUATION_THRESHOLD", "0.30")
# Minutos dentro de los cuales se considera que se sigue hablando de lo mismo.
EPISODIC_SAME_TALK_MIN = _env_float("EPISODIC_SAME_TALK_MIN", "45")
# Horas durante las cuales la respuesta del usuario se asocia a la pregunta de
# seguimiento que YUE acaba de hacer, aunque no repita el nombre del evento.
EPISODIC_ANSWER_WINDOW_HOURS = _env_float("EPISODIC_ANSWER_WINDOW_HOURS", "24")

# ===========================================================================
# MEMORIA HISTÓRICA RELEVANTE (core/memory_ext.py · search_history)
# ===========================================================================
# Tercera capa de memoria, ADITIVA. No sustituye a nada:
#
#   recent_messages(12)      -> continuidad inmediata de la charla
#   episodic_memory.py       -> acontecimientos importantes y su seguimiento
#   memory_ext.search_history-> pasado RELEVANTE para el mensaje de AHORA
#
# Lee la tabla `messages` de core/memory.py (la única fuente del historial); no
# duplica ni un mensaje, así que borrar el historial lo borra de verdad.

# Interruptor general. Con false, YUE se comporta exactamente como antes.
MEMORY_RELEVANCE_ENABLED = _truthy(os.getenv("MEMORY_RELEVANCE_ENABLED", "true"))

# Cuántos recuerdos antiguos entran como MÁXIMO en el prompt. Pocos: llenar el
# contexto de mensajes viejos hace que YUE hable como si leyera un expediente.
MEMORY_RELEVANCE_TOP_K = _env_int("MEMORY_RELEVANCE_TOP_K", "3")

# Relevancia mínima (0..1) para que un recuerdo se envíe al modelo. Si NADA la
# supera, no se manda nada: es preferible no recordar a recordar cualquier cosa.
# Subirlo hace a YUE más prudente; bajarlo, más propensa a sacar temas viejos.
MEMORY_RELEVANCE_MIN_SCORE = _env_float("MEMORY_RELEVANCE_MIN_SCORE", "0.20")

# Cuántos mensajes del final se consideran "recientes" y quedan FUERA de la
# búsqueda. Debe coincidir con el n de recent_messages() del flujo de chat (12):
# así se excluye por ID el mensaje actual —que si no sería el más parecido, con
# similitud ~1.0— y todo lo que ya viaja al modelo por la vía inmediata.
MEMORY_RELEVANCE_SKIP_RECENT = _env_int("MEMORY_RELEVANCE_SKIP_RECENT", "12")

# Rol del que se recuperan los recuerdos personales. "user" es lo correcto: una
# respuesta antigua de YUE ("quizás Andrea vive en Lima") NO puede reaparecer
# meses después convertida en un hecho sobre el usuario. Usa "any" bajo tu
# responsabilidad; el bloque del prompt marca esos recuerdos como suyos, no del
# usuario, pero el riesgo de que el modelo los tome por ciertos sube.
MEMORY_RELEVANCE_ROLE = os.getenv("MEMORY_RELEVANCE_ROLE", "user")

# Longitud máxima de cada recuerdo (se corta por frase o palabra, nunca a mitad
# de una). Evita que un mensaje kilométrico se coma el contexto del modelo.
MEMORY_RELEVANCE_MAX_CHARS = _env_int("MEMORY_RELEVANCE_MAX_CHARS", "320")

# Mensajes más cortos que esto no son recuerdos ("sí", "ok", "jaja").
MEMORY_RELEVANCE_MIN_CHARS = _env_int("MEMORY_RELEVANCE_MIN_CHARS", "12")

# Techo de mensajes antiguos que se examinan por búsqueda. 4000 se recorre en
# milisegundos gracias a la caché de tokenización; subirlo solo hace falta si
# YUE lleva años acumulando conversación.
MEMORY_RELEVANCE_MAX_CANDIDATES = _env_int("MEMORY_RELEVANCE_MAX_CANDIDATES", "4000")

# --- Composición de la puntuación -----------------------------------------
# La relevancia semántica MANDA; recencia e importancia solo desempatan. Con
# estos pesos, "Andrea me engañó" (hace 3 meses) gana a "hoy almorcé pollo"
# (ayer) cuando el usuario escribe "Andrea volvió a escribirme".
MEMORY_RELEVANCE_RECENCY_WEIGHT = _env_float("MEMORY_RELEVANCE_RECENCY_WEIGHT", "0.15")
MEMORY_RELEVANCE_IMPORTANCE_WEIGHT = _env_float(
    "MEMORY_RELEVANCE_IMPORTANCE_WEIGHT", "0.10")
# Días en los que el empujón por recencia cae a la mitad.
MEMORY_RELEVANCE_HALFLIFE_DAYS = _env_float("MEMORY_RELEVANCE_HALFLIFE_DAYS", "45")

# Parecido (0..1) por encima del cual dos recuerdos se consideran el mismo y
# solo pasa el mejor. Evita devolver "Andrea me llamó" tres veces en lugar de
# tres piezas complementarias de la historia.
MEMORY_RELEVANCE_DIVERSITY = _env_float("MEMORY_RELEVANCE_DIVERSITY", "0.6")

# --- Motor de recuperación (intercambiable) --------------------------------
# "tfidf" (por defecto): local, offline, sin dependencias. Es TF-IDF enriquecido
# con raíces del español, conceptos y n-gramas de letras.
# "embeddings": OPCIONAL. Solo se usa si sentence-transformers YA está instalado
# en la máquina; si no lo está, cae a tfidf en silencio. No descarga nada por su
# cuenta y YUE nunca depende de internet por esto.
MEMORY_RELEVANCE_BACKEND = os.getenv("MEMORY_RELEVANCE_BACKEND", "tfidf")
MEMORY_RELEVANCE_EMBEDDING_MODEL = os.getenv(
    "MEMORY_RELEVANCE_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

# Log [memory-ext] en consola (consulta, nº de candidatos y score de cada
# resultado). NUNCA llega al usuario: es solo para la terminal.
MEMORY_RELEVANCE_DEBUG = _truthy(os.getenv("MEMORY_RELEVANCE_DEBUG", "false"))

# ===========================================================================
# MEMORIA NARRATIVA · Story Memory (core/story_memory.py)
# ===========================================================================
# Cuarta capa de memoria, ADITIVA sobre las tres que ya existían. Convierte
# acontecimientos sueltos en HISTORIAS que evolucionan: una amistad, una meta,
# un proyecto, algo que quedó pendiente. No sustituye ni toca `memoria_larga`
# (resumen consolidado), `emotional_episodes` (acontecimientos) ni el retrieval
# histórico de memory_ext: se suma al final del stack.
#
# Reparto de papeles, para que ninguna capa pise a otra:
#   recent_messages(12) -> continuidad inmediata de la charla
#   episodic            -> QUÉ le pasó y cómo terminó
#   memory_ext          -> qué dijo hace tiempo que encaja con ESTE mensaje
#   story_memory        -> qué HILO de su vida seguís los dos
STORY_MEMORY_ENABLED = _truthy(os.getenv("STORY_MEMORY_ENABLED", "true"))

# Cuántas historias como MÁXIMO entran en el prompt. Deliberadamente bajo: si
# YUE mete seis historias en cada mensaje deja de conversar y empieza a
# recitar. Además una historia solo entra si el mensaje la TOCA de verdad.
STORY_MAX_CONTEXT = _env_int("STORY_MAX_CONTEXT", "2")

# Relevancia mínima para que una historia entre al contexto. Por debajo de
# esto, es preferible no recordar nada.
STORY_CONTEXT_MIN_SCORE = _env_float("STORY_CONTEXT_MIN_SCORE", "0.45")

# Techo de historias que se examinan al buscar. SQLite las recorre en
# milisegundos; subirlo solo hace falta tras años de uso.
STORY_MAX_CANDIDATES = _env_int("STORY_MAX_CANDIDATES", "60")

# --- Umbrales para NO sobre-memorizar --------------------------------------
# Longitud mínima del mensaje para molestarse en analizarlo.
STORY_MIN_CHARS = _env_int("STORY_MIN_CHARS", "8")
# Carga emocional mínima (0..1) para que un mensaje SIN relación explícita y
# SIN episodio detrás genere historia o acontecimiento. Es lo que impide que
# «hoy comí pizza» acabe siendo un capítulo de su vida.
STORY_MIN_INTENSITY = _env_float("STORY_MIN_INTENSITY", "0.50")
# Significancia y confianza mínimas para CREAR una historia nueva desde la
# consolidación con modelo. Las que ya existen se actualizan siempre.
STORY_MIN_SIGNIFICANCE = _env_float("STORY_MIN_SIGNIFICANCE", "0.35")
STORY_MIN_CONFIDENCE = _env_float("STORY_MIN_CONFIDENCE", "0.45")

# --- Confianza: lo dicho pesa más que lo deducido --------------------------
# Confianza de partida según de dónde salga la información.
STORY_CONF_EXPLICIT = _env_float("STORY_CONF_EXPLICIT", "0.90")   # «Andrea es mi amiga»
STORY_CONF_INFERRED = _env_float("STORY_CONF_INFERRED", "0.55")   # «discutí con Andrea»
STORY_CONF_HEDGED = _env_float("STORY_CONF_HEDGED", "0.40")       # «creo que Andrea…»
# Cuánto sube la confianza cada vez que algo se corrobora.
STORY_CONF_STEP = _env_float("STORY_CONF_STEP", "0.30")
# Techos. El de lo inferido es MÁS BAJO a propósito: una suposición repetida
# muchas veces sigue siendo una suposición, no asciende a hecho por insistir.
STORY_CONF_CAP = _env_float("STORY_CONF_CAP", "0.97")
STORY_CONF_INFERRED_CAP = _env_float("STORY_CONF_INFERRED_CAP", "0.75")
# Techo de confianza para lo que proponga el modelo en la consolidación.
STORY_LLM_CONF_CAP = _env_float("STORY_LLM_CONF_CAP", "0.80")

# --- Etiquetas legibles derivadas del número (solo para mostrar) -----------
STORY_SIGNIFICANCE_HIGH = _env_float("STORY_SIGNIFICANCE_HIGH", "0.70")
STORY_SIGNIFICANCE_MED = _env_float("STORY_SIGNIFICANCE_MED", "0.40")

# --- Cierre sin nombre («ya hablamos y nos reconciliamos») ------------------
# Días hacia atrás en los que se acepta cerrar por anáfora la historia de
# PERSONA tocada más recientemente que tuviera algo pendiente. Fuera de esa
# ventana no se cierra nada: es mejor seguir creyendo que sigue abierto que
# dar por resuelto lo que no consta.
STORY_ANAPHORA_DAYS = _env_float("STORY_ANAPHORA_DAYS", "21")

# --- Metas -----------------------------------------------------------------
# Abre un hilo narrativo para cada meta activa del sistema `goals` de siempre.
# NO reemplaza esa tabla: `goals` sigue mandando. El progreso se queda en NULL
# mientras no haya evidencia; un número inventado es peor que no saber.
STORY_GOALS_ENABLED = _truthy(os.getenv("STORY_GOALS_ENABLED", "true"))
STORY_GOALS_MAX = _env_int("STORY_GOALS_MAX", "10")

# --- Consolidación narrativa ------------------------------------------------
# Cada cuántos días, como MÍNIMO, se revisan las historias. La parte
# determinista (metas y recálculo de significancia) NO necesita modelo.
STORY_CONSOLIDATION_DAYS = _env_float("STORY_CONSOLIDATION_DAYS", "7")
# Capa OPCIONAL de enriquecimiento con LLM. En false, Story Memory sigue
# funcionando entera con la vía determinista (también sin conexión).
STORY_CONSOLIDATION_ENABLED = _truthy(
    os.getenv("STORY_CONSOLIDATION_ENABLED", "true"))
STORY_USE_LLM = _truthy(os.getenv("STORY_USE_LLM", "true"))
# Presupuesto de llamadas por sesión y tiempo de espera. Es una consolidación
# periódica, no un chat: pocas llamadas y sin prisa.
STORY_MAX_LLM_CALLS = _env_int("STORY_MAX_LLM_CALLS", "4")
STORY_LLM_TIMEOUT = _env_float("STORY_LLM_TIMEOUT", "25")
# Cuántas historias como mucho puede proponer el modelo de una vez.
STORY_MAX_PER_CONSOLIDATION = _env_int("STORY_MAX_PER_CONSOLIDATION", "4")

# --- Paquete de evidencia (lo que se le manda al modelo) -------------------
# Días de episodios que se incluyen y tope de caracteres de conversación. Se
# manda evidencia YA PROCESADA, no un volcado de cientos de mensajes crudos.
STORY_EVIDENCE_DAYS = _env_float("STORY_EVIDENCE_DAYS", "30")
STORY_EVIDENCE_MAX_CHARS = _env_int("STORY_EVIDENCE_MAX_CHARS", "4000")

# --- Mantenimiento ----------------------------------------------------------
# Días sin evidencia tras los cuales una historia pasa a 'dormant'. NO se
# borra: si él vuelve a nombrarla, sigue ahí entera.
STORY_DORMANT_DAYS = _env_float("STORY_DORMANT_DAYS", "120")

# En terreno de riesgo manda el sistema de seguridad: a partir de este nivel no
# se construyen historias ni se toma nota de nada. Mismo criterio que la
# memoria episódica.
STORY_SAFETY_BLOCK_LEVEL = _env_int("STORY_SAFETY_BLOCK_LEVEL", "2")

# Log [STORY] en consola. Nunca imprime el mensaje completo del usuario.
STORY_DEBUG = _truthy(os.getenv("STORY_DEBUG", "false"))

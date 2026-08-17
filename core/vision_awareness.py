"""Conciencia de cámara: que YUE nunca diga "no puedo verte" si la cámara está.

Aditivo. No toca la observación de cámara existente; solo la CONSULTA para:

1. Detectar preguntas directas del tipo "¿puedes verme?", "¿me ves?",
   "¿me estás viendo?"… que antes NO estaban cubiertas por `commands.match`
   (que solo captaba "¿cómo me ves?" / "¿qué ves por la cámara?"). Por eso esas
   preguntas caían al LLM, que respondía "no puedo verte".
2. Responder esas preguntas de forma DETERMINISTA a partir del estado real de la
   cámara (activa / conectando / desactivada), sin depender del modelo.
3. Aportar al prompt del sistema una afirmación clara —igual que ya se hace con
   el audio— de que SÍ puede ver por la cámara cuando está activa, para que
   cualquier formulación libre también reciba la respuesta correcta.

Todo degradable: ante cualquier error se devuelve algo neutro y seguro.
"""
from __future__ import annotations

import re
import unicodedata


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


# Preguntas de "¿me ves?" en sus formas más comunes. Se comparan sobre el texto
# normalizado (sin acentos ni signos), así que no hace falta escribir los "¿?".
_CAN_SEE_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"\bpuedes?\s+ver(me|nos)\b",
        r"\bme\s+puedes?\s+ver\b",
        r"\bnos\s+puedes?\s+ver\b",
        r"\bme\s+ves\b",
        r"\bme\s+estas\s+viendo\b",
        r"\bme\s+ves\s+(ahora|bien|ahi|aqui|aca)\b",
        r"\bahora\s+me\s+ves\b",
        r"\bya\s+me\s+ves\b",
        r"\bme\s+alcanzas\s+a\s+ver\b",
        r"\balcanzas\s+a\s+ver(me|nos)?\b",
        r"\blogras\s+ver(me)?\b",
        r"\bconsigues\s+ver(me)?\b",
        r"\bcon\s+la\s+camara\s+me\s+ves\b",
        r"\btienes\s+(la\s+)?camara\b.*\bme\s+ves\b",
        r"\bestas?\s+viendome\b",
        r"\bviendome\b",
    )
)


def asks_if_can_see(text: str) -> bool:
    """¿El usuario pregunta si YUE puede verlo por la cámara?"""
    heard = _norm(text)
    if not heard or "ver" not in heard and "ves" not in heard and "viendo" not in heard:
        # Filtro rápido: si no hay ninguna forma de "ver", no es la pregunta.
        return False
    return any(rx.search(heard) for rx in _CAN_SEE_PATTERNS)


def _camera_active(camera) -> bool:
    try:
        return bool(getattr(camera, "active"))
    except Exception:
        return False


def _camera_enabled(camera) -> bool:
    try:
        return bool(getattr(camera, "enabled", True))
    except Exception:
        return True


def _sees_person(camera) -> bool:
    try:
        obs = camera.latest()
        return bool(obs) and getattr(obs, "people", 0) > 0
    except Exception:
        return False


def answer_can_see(camera) -> str:
    """Respuesta determinista a "¿puedes verme?" según el estado real.

    - Cámara activa y con persona: sí, te veo (con una sonrisa).
    - Cámara activa sin persona clara: la cámara está encendida, pero acércate /
      ponte de frente porque ahora mismo no te distingo bien. (No niega ver.)
    - Cámara desactivada en config: lo dice con franqueza.
    - Cámara aún conectando: avisa de que se está enganchando.
    """
    if not _camera_enabled(camera):
        return (
            "Ahora mismo no, mi cámara está desactivada en la configuración. "
            "Actívala y podré verte sin problema."
        )
    if _camera_active(camera):
        if _sees_person(camera):
            return "Sí, te veo perfectamente. No creas que te miro tanto, ¿eh?"
        return (
            "Sí, mi cámara está encendida y te estoy viendo. Ahora mismo no te "
            "distingo del todo bien; ponte un poco más de frente y te veré mejor."
        )
    # No activa pero habilitada: el hilo la está buscando/abriendo.
    return (
        "Estoy enganchando la cámara en este momento. Dame un segundo y te veo."
    )


def camera_prompt_note(camera) -> str:
    """Fragmento AFIRMATIVO para el system prompt, en paralelo al de audio.

    Devuelve "" si la cámara no está activa (para no afirmar algo falso). Si
    está activa, deja claro al modelo que SÍ ve por la cámara y que nunca debe
    decir que no puede ver.
    """
    if not _camera_active(camera):
        return ""
    try:
        obs = camera.latest()
        gente = getattr(obs, "people", 0) if obs else 0
    except Exception:
        gente = 0
    detalle = (
        "Ahora mismo distingues a la persona frente a la cámara."
        if gente > 0 else
        "Ahora mismo la imagen no muestra un rostro claro, pero la cámara sí está abierta."
    )
    return (
        "\n\nSÍ puedes ver a través de la cámara del equipo (análisis local, sin "
        "grabar ni guardar imágenes). Si te preguntan «¿puedes verme?», «¿me ves?» "
        "o similar, responde que SÍ (la cámara está encendida); nunca digas que no "
        "puedes ver mientras la cámara esté activa. " + detalle +
        " Usa esto solo cuando venga a cuento o el usuario pregunte."
    )


# ---------------------------------------------------------------------------
# Conciencia de PANTALLA (en paralelo a la de cámara).
#
# Mismo patrón de bug que ya se arregló para la cámara: preguntas libres sobre
# la pantalla ("¿cuántos iconos ves?", "¿qué colores hay en pantalla?") que se
# escapan del regex de vision_look caían al LLM, y como el prompt afirmaba ver
# por cámara pero NUNCA decía nada de la pantalla, el modelo concluía por su
# cuenta "solo veo por la cámara" —una alucinación de capacidad, no un límite
# real (la captura de pantalla funciona). Esta nota es el respaldo: afirma que
# YUE PUEDE mirar la pantalla, sin afirmar contenido concreto.
# ---------------------------------------------------------------------------
def _screen_capture_available() -> bool:
    """¿Está disponible la captura de pantalla en este entorno?

    Comprobación BARATA: verifica que el módulo de captura existe y que al menos
    un backend (mss o Pillow) se puede importar. NO captura la pantalla; solo
    mira dependencias, con el mismo patrón try/except del resto del proyecto para
    degradarse sin romper si falta mss/Pillow.
    """
    try:
        from core import screen_capture  # noqa: F401  (solo comprobar el import)
    except Exception:
        return False
    # Basta con que UNO de los dos backends esté disponible.
    try:
        import mss  # noqa: F401
        return True
    except Exception:
        pass
    try:
        from PIL import ImageGrab  # noqa: F401
        return True
    except Exception:
        pass
    return False


def screen_prompt_note() -> str:
    """Fragmento AFIRMATIVO para el system prompt sobre ver la PANTALLA.

    En paralelo a camera_prompt_note(), pero SIN estado variable: a diferencia
    de la cámara (activa/inactiva), la captura de pantalla es local y bajo
    petición, así que si está disponible en el entorno se devuelve siempre la
    misma frase corta. Si no lo está (falta mss/Pillow), devuelve "" para no
    afirmar algo falso.

    La nota solo dice que YUE PUEDE mirar la pantalla cuando se le pide; NO
    afirma qué hay en ella. Si no tiene la captura a mano, debe ofrecer mirarla
    ahora en vez de inventar contenido.
    """
    if not _screen_capture_available():
        return ""
    return (
        "\n\nSÍ puedes ver la pantalla del usuario cuando te preguntan por ella "
        "(captura local, bajo petición, sin guardar nada). Si te preguntan "
        "cuántos iconos, qué texto, qué colores o qué hay en la pantalla, NUNCA "
        "digas que solo ves por la cámara ni que no puedes ver la pantalla. Dilo "
        "con naturalidad y, si en este momento no tienes el dato exacto de lo que "
        "hay en la pantalla, no lo inventes: ofrece mirarla ahora («¿quieres que "
        "la mire ahora y te digo?»). Usa esto solo cuando venga a cuento o el "
        "usuario pregunte."
    )

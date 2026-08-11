"""Emociones visuales de Yue.

La emoción se expresa en el avatar, no mediante etiquetas ni acotaciones en el
texto. El análisis es local, rápido y determinista.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class EmotionState:
    name: str
    intensity: float = 1.0
    duration_ms: int = 4800


_CUES = {
    "excited": ["increible", "excelente", "vamos", "lo logramos", "que emocion", "brutal", "genial", "🎉", "🤩", "🔥"],
    "love": ["te quiero", "me importas", "carino", "corazon", "contigo", "adoro", "amor", "💕", "❤️", "🥰"],
    "shy": ["no es que", "me da verguenza", "sonrojo", "que pena", "ejem", "n-no", "😳", "🫣"],
    "proud": ["orgullosa", "orgulloso", "bien hecho", "sabia que podias", "lo conseguiste", "gran trabajo", "👏"],
    "playful": ["jeje", "jaja", "traviesa", "bromita", "despistad", "te atrape", "obvio", "que novedad", "ay si", "no me digas", "menuda", "ironia", "😏", "😜", "🙄"],
    "happy": ["feliz", "me alegra", "que bien", "me encanta", "bonito", "sonrie", "😊", "😄", "✨"],
    "curious": ["cuentame", "por que", "como", "quiero saber", "interesante", "curios", "🤔"],
    "confused": ["no entiendo", "confund", "que quisiste", "no me queda claro", "raro", "eh", "😕", "❓"],
    "worried": ["preocupa", "cuidado", "estas bien", "riesgo", "peligro", "atencion", "inquiet", "😟", "⚠"],
    "sad": ["triste", "lo siento", "perdon", "me duele", "extrano", "lastima", "snif", "😔", "😢"],
    "angry": ["tsk", "grr", "basta", "no me molestes", "idiota", "tonto", "enoj", "molest", "hmph", "😠"],
    "surprised": ["no puede ser", "en serio", "sorprend", "vaya", "wow", "que?!", "¿¡", "😲", "😮"],
    "relaxed": ["tranquil", "calma", "descansa", "respira", "todo esta bien", "estoy aqui", "con calma", "😌"],
    "focused": ["vamos paso a paso", "concentr", "analicemos", "primero", "plan", "preciso", "objetivo", "🧠"],
    "bored": ["aburr", "otra vez", "que lata", "meh", "monotono", "😑"],
    "sleepy": ["sueno", "dormir", "descansar", "cansad", "bostezo", "zzz", "🥱", "😴"],
}

_PRIORITY = [
    "worried", "angry", "sad", "surprised", "excited", "love", "shy",
    "proud", "playful", "happy", "confused", "curious", "focused",
    "sleepy", "bored", "relaxed",
]

_EMOTION_WORDS = (
    "neutral|feliz|contenta|emocionada|enamorada|timida|tímida|orgullosa|"
    "juguetona|curiosa|confundida|preocupada|triste|enojada|sorprendida|"
    "relajada|concentrada|aburrida|cansada|somnolienta|sarcastica|sarcástica|"
    "burlona|molesta|celosa|nerviosa|avergonzada|apenada"
)

# Verbos con los que un modelo suele AUTO-etiquetar lo que siente
# ("estoy feliz", "me siento triste", "me pongo nerviosa"). Los cazamos para
# que esa auto-descripcion no se diga: la emocion vive en el avatar, no en el texto.
_SELF_LABEL = (
    r"(?:estoy|estaba|me\s+siento|me\s+sent[ií]a|me\s+pongo|me\s+puse|"
    r"me\s+encuentro)"
)

# Emocion expresada como SUSTANTIVO ("me da verguenza", "siento pena",
# "que rabia", "me muero de verguenza"). Se limpia igual que las etiquetas.
_FEELING_NOUNS = (
    r"verg[uü]enza|pena|rabia|celos|corte|nervios|ternura|coraje"
)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def clean_response(text: str) -> str:
    """Quita etiquetas/acotaciones de emoción para que SOLO las muestre el avatar.

    Ahora limpia tanto al INICIO como EN MEDIO del texto: etiquetas [feliz] /
    (curiosa), acotaciones entre *asteriscos*, y auto-descripciones como
    «estoy feliz», «me siento triste» o «me da vergüenza». Así lo que YUE dice
    (y habla) queda libre de emoción escrita y la emoción vive solo en su cara
    y su cuerpo. Al final se normalizan los restos (espacios y puntuación
    huérfana) para que nunca quede una frase partida a la mitad.
    """
    value = (text or "").strip()
    if not value:
        return value

    # NUEVO (red de seguridad): aunque el motor ya lo recorta, aquí se vuelve a
    # quitar el monólogo interno del modelo (<think>...</think>, canales de
    # gpt-oss, "Thinking: ..."). Es el último punto antes de que YUE HABLE, así
    # que si alguna ruta se saltara la limpieza, la voz no lo leería igual.
    try:
        from core import text_sanitizer
        value = text_sanitizer.quitar_razonamiento(value) or value
    except Exception:
        pass

    _INT = r"(?:muy\s+|un\s+poco\s+|algo\s+|tan\s+|bastante\s+|super\s+)?"

    # --- 1) Etiqueta inicial típica de modelos: [feliz], (curiosa), Emoción: triste.
    value = re.sub(
        rf"^\s*(?:\[|\()?\s*(?:emoci[oó]n|estado|tono)?\s*[:=-]?\s*"
        rf"(?:{_EMOTION_WORDS})\s*(?:\]|\))?\s*[:;,.!\-–—]*\s*",
        "",
        value,
        flags=re.I,
    )
    # --- 2) Auto-etiqueta al INICIO: «Estoy feliz.», «Me siento triste,».
    value = re.sub(
        rf"^\s*(?:yo\s+)?{_SELF_LABEL}\s+{_INT}(?:{_EMOTION_WORDS})"
        rf"\s*[:;,.!\-–—]*\s*",
        "",
        value,
        flags=re.I,
    )
    # --- 3) Acotación inicial entre *asteriscos*: «*se sonroja*».
    value = re.sub(r"^\s*\*[^*\n]{1,45}\*\s*", "", value)

    # === lo NUEVO: los MISMOS casos pero EN CUALQUIER PARTE del texto ========
    # 4) Etiquetas [feliz] / (curiosa) sueltas donde sea.
    value = re.sub(
        rf"[\[(]\s*(?:emoci[oó]n|estado|tono)?\s*[:=-]?\s*"
        rf"(?:{_EMOTION_WORDS})\s*[\])]",
        " ",
        value,
        flags=re.I,
    )
    # 5) Acotaciones entre *asteriscos* (cortas) en cualquier parte.
    value = re.sub(r"\*[^*\n]{1,45}\*", " ", value)
    # 6) Auto-descripción «estoy/me siento [muy] EMOCIÓN» con su puntuación de
    #    cierre, para no dejar la frase colgando.
    value = re.sub(
        rf"\b(?:yo\s+)?{_SELF_LABEL}\s+{_INT}(?:{_EMOTION_WORDS})\b\s*[:;,.!¡¿?\-–—]*",
        " ",
        value,
        flags=re.I,
    )
    # 7) Emoción como sustantivo: «me da vergüenza», «qué rabia», «siento pena».
    value = re.sub(
        rf"\b(?:me\s+da\s+{_INT}|siento\s+{_INT}|tengo\s+{_INT}|qu[eé]\s+{_INT}|"
        rf"me\s+muero\s+de\s+{_INT})(?:{_FEELING_NOUNS})\b\s*[:;,.!¡¿?\-–—]*",
        " ",
        value,
        flags=re.I,
    )

    # --- 8) Normaliza restos: puntuación pegada a espacio, signos repetidos,
    #        espacios dobles y cualquier signo suelto al principio.
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"([,.;:¡¿])\1+", r"\1", value)
    value = re.sub(r"\s{2,}", " ", value)
    value = re.sub(r"^[\s,.;:!?¡¿\-–—]+", "", value)
    value = value.strip()
    # Si el recorte dejó la frase empezando en minúscula, la levantamos para
    # que al hablar/leer no suene cortada.
    if value and value[0].islower():
        value = value[0].upper() + value[1:]
    return value or "..."


def infer_emotion_state(text: str) -> EmotionState:
    low = _norm(text)
    scores = {name: 0.0 for name in _CUES}

    for name, cues in _CUES.items():
        for cue in cues:
            if _norm(cue) in low:
                scores[name] += 1.0

    exclamations = low.count("!")
    questions = low.count("?")
    if exclamations >= 2:
        scores["excited"] += 0.8
    elif exclamations == 1:
        scores["happy"] += 0.25
    if questions >= 2:
        scores["confused"] += 0.5
    elif questions == 1:
        scores["curious"] += 0.2
    if "..." in low or "…" in (text or ""):
        scores["shy"] += 0.15
        scores["sad"] += 0.1

    best = max(_PRIORITY, key=lambda name: (scores[name], -_PRIORITY.index(name)))
    score = scores[best]
    if score <= 0:
        return EmotionState("neutral", 0.5, 3500)

    intensity = min(1.0, 0.52 + score * 0.22)
    duration = int(4200 + min(score, 3.0) * 700)
    return EmotionState(best, intensity, duration)


def infer_reaction_to_user(text: str) -> EmotionState:
    """Reacción visual inmediata al mensaje del usuario."""
    low = _norm(text)
    if not low:
        return EmotionState("neutral", 0.45, 2500)

    if re.search(r"\b(me siento mal|estoy triste|llor|me duele|tengo miedo|ayuda|peligro)\b", low):
        return EmotionState("worried", 0.86, 6500)
    if re.search(r"\b(gracias|te quiero|te adoro|linda|hermosa|me gustas)\b", low):
        return EmotionState("shy", 0.9, 6200)
    if re.search(r"\b(jaja|jeje|broma|chiste|diviert)\b", low):
        return EmotionState("playful", 0.82, 5200)
    if re.search(r"\b(abre|escribe|presiona|busca|controla|crea|organiza|revisa)\b", low):
        return EmotionState("focused", 0.78, 5500)
    if "?" in text or re.match(r"^(como|por que|que|cual|cuando|donde|quien)\b", low):
        return EmotionState("curious", 0.72, 4800)
    if text.count("!") >= 2 or (len(text) > 8 and text.isupper()):
        return EmotionState("surprised", 0.82, 4200)
    return EmotionState("curious", 0.58, 3600)


def infer_conversation_state(user_text: str, assistant_text: str) -> EmotionState:
    """Combina el contexto del usuario con el tono de la respuesta de Yue."""
    response_state = infer_emotion_state(assistant_text)
    reaction_state = infer_reaction_to_user(user_text)

    if response_state.name != "neutral":
        # Ante tristeza o peligro del usuario, la reacción empática domina sobre
        # palabras optimistas como "vamos" que podrían parecer entusiasmo.
        if reaction_state.name == "worried" and response_state.name != "angry":
            return EmotionState("worried", max(0.78, reaction_state.intensity), 6500)
        return response_state
    return reaction_state


def infer_emotion(text: str) -> str:
    return infer_emotion_state(text).name


# ===========================================================================
# PUENTE hacia la arquitectura nueva (core.affect + core.support)
# ===========================================================================
# Este módulo se conserva ENTERO y funcionando: `clean_response()` sigue siendo
# la limpieza canónica del texto de YUE (nada que ver con emociones, y se usa en
# varios sitios), y las tres funciones `infer_*` siguen respondiendo igual para
# no romper ninguna llamada existente.
#
# Lo que cambia es que ya NO son la forma recomendada de entender al usuario:
#
#   ANTES:  emotion.infer_reaction_to_user(texto)  -> etiqueta -> pet.set_emotion
#   AHORA:  CompanionBrain.process(texto)          -> afecto + necesidad +
#                                                     seguridad + decisión +
#                                                     expresión
#
# Las funciones de abajo quedan marcadas como DEPRECADAS para el análisis del
# usuario. Se mantienen porque:
#   - `infer_emotion_state()` sigue siendo útil para el texto que produce YUE
#     (no el usuario), donde no hace falta toda la maquinaria afectiva;
#   - eliminarlas rompería `main.py` y `tests/test_core.py` sin ganar nada.
#
# La migración se hace llamando a `analyze_user_message()` desde los puntos que
# analizan al USUARIO, y dejando las demás llamadas como estaban.

#: Traducción de la taxonomía nueva a las etiquetas que entiende el avatar VRM.
#: Solo se usa en el puente de compatibilidad; la ruta buena para la cara de YUE
#: es `core.support.expression`, que decide una RESPUESTA y no una imitación.
_AFFECT_TO_AVATAR = {
    "joy": "happy", "excitement": "excited", "pride": "proud",
    "relief": "relaxed", "affection": "love", "sadness": "sad",
    "disappointment": "sad", "loneliness": "sad", "anger": "angry",
    "frustration": "angry", "fear": "worried", "anxiety": "worried",
    "guilt": "sad", "embarrassment": "shy", "confusion": "confused",
    "tiredness": "sleepy", "neutral": "neutral",
}


def affect_to_avatar_name(emotion) -> str:
    """Convierte una emoción de `core.affect` en etiqueta del avatar VRM."""
    return _AFFECT_TO_AVATAR.get(str(emotion or "neutral").lower(), "neutral")


def analyze_user_message(text: str, brain=None):
    """Análisis COMPLETO de un mensaje del usuario (ruta recomendada).

    Sustituye a `infer_reaction_to_user()` + `infer_emotion_state()` cuando lo
    que se analiza es al USUARIO. Devuelve un `CompanionResult` con el estado
    afectivo, lo que necesita, el nivel de riesgo, la decisión de apoyo y la
    expresión que debería mostrar YUE.

    Si el sistema nuevo no estuviera disponible por lo que sea, devuelve None y
    quien llama puede seguir usando las funciones clásicas de este módulo. Nunca
    lanza excepciones.
    """
    try:
        if brain is None:
            from core.companion_brain import CompanionBrain
            brain = CompanionBrain(engine=None, use_semantic=False)
        return brain.process(text)
    except Exception as exc:  # pragma: no cover
        print("[emotion] el puente afectivo no está disponible:", exc)
        return None


def to_emotion_state(expression) -> EmotionState:
    """Convierte una `YueExpression` en el `EmotionState` clásico.

    Permite que el código antiguo que espera `EmotionState(name, intensity,
    duration_ms)` reciba el resultado del sistema nuevo sin cambiar su forma.
    """
    try:
        return EmotionState(
            str(getattr(expression, "name", "neutral")),
            float(getattr(expression, "intensity", 0.5)),
            int(getattr(expression, "duration_ms", 4500)),
        )
    except Exception:
        return EmotionState("neutral", 0.5, 3500)

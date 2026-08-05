"""Puente visión -> diálogo (paso 16).

Construye un contexto BREVE y estructurado a partir de `vision_state` para
inyectarlo al modelo de lenguaje, y responde preguntas visuales concretas
("¿puedes verme?", "¿qué gesto hago?", "¿qué objetos ves?").

Todo se basa en la fusión (`snapshot`), nunca en variables globales sueltas. Las
frases sobre emoción son PRUDENTES por diseño (nunca afirman certeza).

Es Python puro: se prueba con un dict `vision_state` de ejemplo, sin cámara.
"""
from __future__ import annotations

# Traducciones legibles al español para expresiones/estados/gestos/objetos.
_EXPR_ES = {
    "smiling": "sonrisa", "surprised": "sorpresa", "sad_expression": "gesto triste",
    "angry_expression": "gesto de enojo", "tense": "tensión", "sleepy": "cansancio",
    "confused": "confusión", "neutral": "neutral", "thinking": "concentración",
}
_EMO_ES = {
    "happy": "positiva", "sad": "baja", "surprised": "de sorpresa",
    "angry": "de enojo", "worried": "de inquietud", "tired": "de cansancio",
    "neutral": "neutral",
}
_POSE_ES = {
    "standing": "de pie", "sitting": "sentada", "crouching": "agachada",
    "leaning_left": "inclinada a la izquierda", "leaning_right": "inclinada a la derecha",
    "leaning_forward": "inclinada hacia adelante", "unknown": "no clara",
}
_GESTURE_ES = {
    "Thumb_Up": "pulgar arriba", "Thumb_Down": "pulgar abajo", "Victory": "señal de victoria",
    "Open_Palm": "palma abierta", "Closed_Fist": "puño cerrado", "Pointing_Up": "índice arriba",
    "ILoveYou": "gesto de cariño", "Wave": "saludo con la mano",
    "SwipeLeft": "deslizamiento a la izquierda", "SwipeRight": "deslizamiento a la derecha",
}
_OBJ_ES = {
    "laptop": "una laptop", "cell_phone": "un celular", "bottle": "una botella",
    "cup": "una taza", "keyboard": "un teclado", "mouse": "un mouse", "book": "un libro",
    "chair": "una silla", "person": "una persona", "dog": "un perro", "cat": "un gato",
    "backpack": "una mochila", "tv": "un televisor", "television": "un televisor",
}


def _hand_side_es(side: str) -> str:
    return {"left": "izquierda", "right": "derecha"}.get(side, "")


def build_visual_context(vision_state: dict) -> str:
    """Contexto corto para el LLM. Vacío si no hay nada visual útil."""
    if not vision_state:
        return ""
    cam = vision_state.get("camera", {})
    if not cam.get("active"):
        return ""
    pres = vision_state.get("presence", {})
    if not pres.get("present"):
        return "VISIÓN ACTUAL:\n- Cámara activa.\n- No se ve a nadie ahora mismo."

    lines = ["VISIÓN ACTUAL:", "- Cámara activa."]
    n = pres.get("person_count", 1)
    lines.append(f"- {'Una persona visible' if n == 1 else f'{n} personas visibles'}.")

    faces = vision_state.get("faces", [])
    if faces:
        f = faces[0]
        expr = _EXPR_ES.get(f.get("expression", "neutral"), f.get("expression", "neutral"))
        emo = _EMO_ES.get(f.get("emotion_estimate", "neutral"), "neutral")
        conf = f.get("emotion_confidence", 0.0)
        nivel = "alta" if conf >= 0.7 else "moderada" if conf >= 0.45 else "baja"
        lines.append(f"- Expresión observable: {expr}.")
        if f.get("emotion_estimate") != "neutral":
            lines.append(f"- Emoción estimada (aproximada): {emo}, confianza {nivel}.")
        if f.get("looking_at_camera"):
            lines.append("- Mira hacia la cámara.")

    pose = vision_state.get("pose", {})
    if pose.get("visible"):
        lines.append(f"- Postura: {_POSE_ES.get(pose.get('state', 'unknown'), 'no clara')}.")
        if pose.get("left_arm_raised") or pose.get("right_arm_raised"):
            lines.append("- Tiene un brazo levantado.")

    for ev in vision_state.get("events", []):
        if ev.get("type") == "gesture" and ev.get("is_new"):
            g = _GESTURE_ES.get(ev.get("value"), ev.get("value"))
            lines.append(f"- Gesto nuevo: {g}.")
            break

    objs = vision_state.get("objects", {})
    if objs:
        legibles = [_OBJ_ES.get(k, k.replace("_", " ")) for k in list(objs.keys())[:5]]
        lines.append("- Objetos: " + ", ".join(legibles) + ".")

    return "\n".join(lines)


# --- respuestas directas a preguntas visuales concretas ------------------
def can_see(vision_state: dict) -> str:
    cam = vision_state.get("camera", {})
    if not cam.get("active"):
        return "Ahora mismo no tengo la cámara activa, así que no puedo verte."
    if vision_state.get("presence", {}).get("present"):
        return "Sí, te veo por la cámara."
    return "La cámara está activa, pero ahora mismo no distingo a nadie frente a ella."


def count_people(vision_state: dict) -> str:
    n = vision_state.get("presence", {}).get("person_count", 0)
    if n == 0:
        return "No veo a nadie ahora mismo."
    if n == 1:
        return "Veo a una persona."
    return f"Veo a {n} personas."


def describe_expression(vision_state: dict) -> str:
    faces = vision_state.get("faces", [])
    if not faces:
        return "No alcanzo a ver tu cara con claridad ahora mismo."
    f = faces[0]
    expr = _EXPR_ES.get(f.get("expression", "neutral"), "neutral")
    if f.get("expression") == "smiling":
        return "Parece que estás sonriendo."
    if f.get("expression") == "neutral":
        return "Tu expresión se ve tranquila, más bien neutral."
    return f"Tu expresión se ve con {expr}, aunque puedo equivocarme."


def describe_gesture(vision_state: dict) -> str:
    hands = vision_state.get("hands", [])
    if not hands:
        return "No veo que estés haciendo ningún gesto ahora mismo."
    h = hands[0]
    g = _GESTURE_ES.get(h.get("gesture"), h.get("gesture"))
    side = _hand_side_es(h.get("side", ""))
    return f"Veo un gesto de {g}" + (f" con la mano {side}." if side else ".")


def describe_objects(vision_state: dict) -> str:
    objs = vision_state.get("objects", {})
    if not objs:
        return "No distingo objetos claros en el entorno ahora mismo."
    partes = []
    for k, c in objs.items():
        nombre = _OBJ_ES.get(k, k.replace("_", " "))
        partes.append(nombre if c == 1 else f"{c} {nombre}")
    return "Veo " + ", ".join(partes) + "."


def describe_pose(vision_state: dict) -> str:
    pose = vision_state.get("pose", {})
    if not pose.get("visible"):
        return "No alcanzo a ver tu cuerpo completo para saber tu postura."
    est = _POSE_ES.get(pose.get("state", "unknown"), "no clara")
    if pose.get("left_arm_raised") or pose.get("right_arm_raised"):
        return f"Te veo {est}, y con un brazo levantado."
    return f"Diría que estás {est}."

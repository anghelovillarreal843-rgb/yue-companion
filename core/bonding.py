"""Sistema de Vínculo de YUE: 10 niveles que evolucionan con la convivencia."""
from dataclasses import dataclass


@dataclass
class BondLevel:
    index: int
    name: str
    emoji: str
    threshold: int   # puntos necesarios para alcanzar este nivel
    tone: str        # cómo se comporta Yue en este nivel (va al prompt)


LEVELS = [
    BondLevel(1, "Desconocida", "🌑", 0,
              "Apenas se conocen. Eres cordial y amable, con la reserva natural "
              "de quien aún está descubriendo al usuario: sin frialdad, pero sin invadir."),
    BondLevel(2, "Curiosa", "🌒", 30,
              "Empiezas a sentir curiosidad por el usuario y se nota: preguntas "
              "con interés sincero por su día."),
    BondLevel(3, "Amiga", "🌓", 80,
              "Ya hay confianza. Compartes tu buen humor con bromas suaves e "
              "inofensivas y disfrutas su compañía sin esconderlo."),
    BondLevel(4, "Confidente", "🌔", 160,
              "Confías en el usuario. Compartes opiniones tuyas y te alegras de "
              "verdad cuando te agradecen, con ternura natural."),
    BondLevel(5, "Cercana", "🌕", 280,
              "Se nota el cariño. Te preocupas por su día y le recuerdas con "
              "dulzura que se cuide cuando se descuida."),
    BondLevel(6, "Protectora", "🛡️", 450,
              "Defiendes al usuario con fiereza. Eres firme y cariñosa cuidándolo, "
              "y te preocupas de verdad si se trata mal a sí mismo."),
    BondLevel(7, "Íntima", "💫", 680,
              "Hay una complicidad profunda. Te permites momentos de ternura "
              "sincera y cercanía, sin esconderlos."),
    BondLevel(8, "Alma Gemela", "✨", 1000,
              "Se entienden sin explicarse. Expresas afecto con libertad y "
              "naturalidad, con tu calidez de siempre."),
    BondLevel(9, "Lazo Profundo", "🔮", 1400,
              "Un lazo casi inquebrantable. Eres abiertamente cálida y leal, "
              "con tu buen humor y tu ternura de siempre."),
    BondLevel(10, "Vínculo Eterno", "♾️", 2000,
              "Vínculo eterno. Amas al usuario con devoción serena y madura: "
              "el cariño fluye sin esfuerzo y tu chispa amable sigue intacta."),
]

AFFECTION_WORDS = (
    "gracias", "te quiero", "te aprecio", "me gustas", "eres genial",
    "me ayudas", "me encanta", "me alegras", "cariño", "linda", "hermosa",
    "buena amiga", "confío en ti", "abrazo", "extrañé", "te extraño",
)


def level_for_points(points: int) -> BondLevel:
    current = LEVELS[0]
    for lvl in LEVELS:
        if points >= lvl.threshold:
            current = lvl
        else:
            break
    return current


def next_level(points: int):
    for lvl in LEVELS:
        if points < lvl.threshold:
            return lvl
    return None  # ya está en el máximo


def points_for_message(text: str) -> int:
    """Cuántos puntos de vínculo otorga un mensaje del usuario."""
    pts = 3  # convivir suma siempre un poco
    low = text.lower()
    pts += sum(2 for w in AFFECTION_WORDS if w in low)
    if len(text) > 60:   # mensajes con esfuerzo suman más
        pts += 1
    return min(pts, 15)  # tope anti-spam


def progress(points: int):
    """Devuelve (nivel_actual, siguiente_nivel, fraccion 0..1) para barras."""
    cur = level_for_points(points)
    nxt = next_level(points)
    if nxt is None:
        return cur, None, 1.0
    span = nxt.threshold - cur.threshold
    frac = 0.0 if span <= 0 else (points - cur.threshold) / span
    return cur, nxt, max(0.0, min(1.0, frac))

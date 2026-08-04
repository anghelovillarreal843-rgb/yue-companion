"""Lip-sync por VISEMAS para español (FASE 4).

El avatar ya mueve la boca según el volumen (envolvente RMS del audio). Eso abre
y cierra la mandíbula, pero la boca no toma FORMA: una "a" y una "o" se ven igual.
Este módulo añade la forma: convierte el TEXTO en una secuencia de visemas (formas
de boca) con su duración, para que la boca dibuje vocales y consonantes.

Un "visema" es la forma visible de la boca para un grupo de sonidos. Se usan pocos,
que es lo estándar para avatares:

    AA  -> boca muy abierta (a)
    E   -> boca media (e)
    I   -> boca estirada (i, y vocal)
    O   -> boca redonda (o)
    U   -> boca fruncida (u)
    MBP -> boca cerrada (m, b, p)
    FV  -> labio inferior bajo los dientes (f, v)
    CONS-> consonante neutra, boca entreabierta (resto)
    REST-> boca en reposo (silencio, espacios)

Ofrece dos vías, combinables:
  1) `text_to_visemes(texto)`  -> forma de la boca a partir de las letras.
  2) `visemes_from_envelope()` -> apertura de mandíbula a partir del volumen RMS
     (formaliza lo que ya hacía YUE) para sincronizar con el audio real.
Y `merge_shape_with_envelope()` une ambas: la FORMA la pone el texto y la APERTURA
la modula el volumen del audio.

Solo librería estándar; determinista y fácil de probar. Funciona offline.
"""
from __future__ import annotations

import unicodedata

# Conjunto de visemas que el avatar debería poder representar (blendshapes).
VISEMES = ("AA", "E", "I", "O", "U", "MBP", "FV", "CONS", "REST")

# Vocales -> visema. (Las tildes se quitan antes de mirar aquí.)
_VOCAL_VISEMA = {
    "a": "AA", "e": "E", "i": "I", "o": "O", "u": "U", "y": "I",
}

# Duración nominal por tipo de sonido, en segundos (ritmo de habla natural).
_DUR_VOCAL = 0.090
_DUR_CONS = 0.055
_DUR_REST = 0.070

# Letras mudas o que no aportan forma propia en español.
_MUDAS = {"h"}


def _strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c))


def _tokens(word: str):
    """Divide una palabra en sonidos, tratando dígrafos y letras mudas.

    Maneja: ch, ll, rr como una sola consonante; 'qu'/'gu' ante e/i con u muda;
    'gue/gui' con u muda; la 'h' muda se ignora.
    """
    w = _strip_accents(word.lower())
    i = 0
    n = len(w)
    while i < n:
        c = w[i]
        nxt = w[i + 1] if i + 1 < n else ""
        nxt2 = w[i + 2] if i + 2 < n else ""

        # Dígrafos consonánticos.
        if c == "c" and nxt == "h":
            yield ("cons", "CONS"); i += 2; continue
        if c == "l" and nxt == "l":
            yield ("cons", "CONS"); i += 2; continue
        if c == "r" and nxt == "r":
            yield ("cons", "CONS"); i += 2; continue

        # 'qu' + e/i  -> sonido /k/, la u es muda.
        if c == "q" and nxt == "u" and nxt2 in ("e", "i"):
            yield ("cons", "CONS"); i += 2; continue
        # 'gu' + e/i  -> /g/, la u es muda (salvo diéresis, ya quitada por accents).
        if c == "g" and nxt == "u" and nxt2 in ("e", "i"):
            yield ("cons", "CONS"); i += 2; continue

        if c in _MUDAS:
            i += 1; continue

        if c in _VOCAL_VISEMA:
            yield ("vocal", _VOCAL_VISEMA[c]); i += 1; continue

        if c in ("m", "b", "p"):
            yield ("cons", "MBP"); i += 1; continue
        if c in ("f", "v"):
            yield ("cons", "FV"); i += 1; continue
        if c.isalpha():
            yield ("cons", "CONS"); i += 1; continue

        # Cualquier otro carácter dentro de la "palabra" (dígitos, símbolos).
        yield ("rest", "REST"); i += 1


def text_to_visemes(text: str, target_duration: float | None = None) -> list[dict]:
    """Convierte texto en una secuencia [{viseme, dur}] con duraciones en segundos.

    Si se pasa `target_duration` (p. ej. la duración real del audio TTS), las
    duraciones se escalan para que la suma coincida exactamente. Así la boca
    termina justo cuando termina la voz.
    """
    seq: list[dict] = []
    for palabra in (text or "").split():
        for tipo, vis in _tokens(palabra):
            if tipo == "vocal":
                seq.append({"viseme": vis, "dur": _DUR_VOCAL})
            elif tipo == "cons":
                seq.append({"viseme": vis, "dur": _DUR_CONS})
            else:
                seq.append({"viseme": vis, "dur": _DUR_REST})
        # Pausa breve entre palabras (boca en reposo).
        seq.append({"viseme": "REST", "dur": _DUR_REST})

    if not seq:
        return []

    # Quitar el REST final sobrante.
    if seq and seq[-1]["viseme"] == "REST":
        seq.pop()

    if target_duration and target_duration > 0:
        total = sum(v["dur"] for v in seq) or 1.0
        factor = target_duration / total
        for v in seq:
            v["dur"] = round(v["dur"] * factor, 4)
    return seq


def visemes_from_envelope(envelope, fps: float = 30.0, threshold: float = 0.06) -> list[dict]:
    """Apertura de mandíbula a partir de la envolvente RMS (formaliza lo previo).

    `envelope` es una lista de amplitudes 0..1 (una por frame). Devuelve, por
    frame, {t, viseme, openness}: si el volumen supera el umbral, la boca se abre
    (AA) con apertura proporcional; si no, queda en reposo (REST).
    """
    frames = []
    if not envelope:
        return frames
    dt = 1.0 / float(fps) if fps > 0 else 0.033
    peak = max(envelope) or 1.0
    for k, amp in enumerate(envelope):
        norm = max(0.0, min(1.0, amp / peak))
        if norm >= threshold:
            frames.append({"t": round(k * dt, 4), "viseme": "AA",
                           "openness": round(norm, 3)})
        else:
            frames.append({"t": round(k * dt, 4), "viseme": "REST",
                           "openness": 0.0})
    return frames


def merge_shape_with_envelope(text: str, envelope, fps: float = 30.0) -> list[dict]:
    """Une FORMA (texto) y APERTURA (volumen).

    La forma de la boca la marca el texto; el volumen del audio modula cuánto se
    abre en cada instante. Devuelve frames [{t, viseme, openness}] alineados a la
    duración real del audio (len(envelope)/fps).
    """
    if not envelope:
        return []
    dt = 1.0 / float(fps) if fps > 0 else 0.033
    dur_total = len(envelope) * dt
    shape = text_to_visemes(text, target_duration=dur_total)
    if not shape:
        return visemes_from_envelope(envelope, fps=fps)

    peak = max(envelope) or 1.0

    # Índice de qué visema toca en cada tiempo t según las duraciones del texto.
    limites = []
    acumulado = 0.0
    for v in shape:
        acumulado += v["dur"]
        limites.append((acumulado, v["viseme"]))

    frames = []
    j = 0
    for k, amp in enumerate(envelope):
        t = k * dt
        while j < len(limites) - 1 and t > limites[j][0]:
            j += 1
        vis = limites[j][1]
        norm = max(0.0, min(1.0, amp / peak))
        # En silencio real, la boca se cierra aunque el texto marque una vocal.
        if norm < 0.05:
            vis = "REST"
        frames.append({"t": round(t, 4), "viseme": vis, "openness": round(norm, 3)})
    return frames

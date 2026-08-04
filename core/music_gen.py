"""Musica de fondo para los recuerdos de Yue.

Elige la banda sonora "segun lo que se crea": el estado de animo de la entrada
del texto fuente decide la armonia. Si dejas tus propios archivos en
  assets/music/<emocion>/*.mp3   (o  assets/music/*.mp3)
Yue los usara. Si no hay ninguno, compone una pieza ambiental suave al vuelo
(sin dependencias externas: solo numpy + el modulo wave).

Funcion principal:  track_for(mood, seconds, out_path_wav) -> ruta de audio.
"""
import math
import os
import random
import struct
import wave

import config
from core import activity

SR = 44100  # frecuencia de muestreo

# Notas (Hz) de una octava util
_N = {
    "C": 261.63, "Db": 277.18, "D": 293.66, "Eb": 311.13, "E": 329.63,
    "F": 349.23, "Gb": 369.99, "G": 392.00, "Ab": 415.30, "A": 440.00,
    "Bb": 466.16, "B": 493.88,
}


def _chord(*names):
    return [_N[n] for n in names]


# Progresiones de acordes por emocion (4 acordes, se repiten en bucle).
PROGRESSIONS = {
    "feliz":     [_chord("C", "E", "G"), _chord("G", "B", "D"),
                  _chord("A", "C", "E"), _chord("F", "A", "C")],
    "enamorado": [_chord("F", "A", "C"), _chord("C", "E", "G"),
                  _chord("D", "F", "A"), _chord("G", "B", "D")],
    "tranquilo": [_chord("C", "E", "G"), _chord("A", "C", "E"),
                  _chord("F", "A", "C"), _chord("G", "B", "D")],
    "neutral":   [_chord("C", "E", "G"), _chord("A", "C", "E"),
                  _chord("F", "A", "C"), _chord("G", "B", "D")],
    "triste":    [_chord("A", "C", "E"), _chord("F", "A", "C"),
                  _chord("C", "E", "G"), _chord("G", "B", "D")],
    "estresado": [_chord("D", "F", "A"), _chord("A", "C", "E"),
                  _chord("Bb", "D", "F"), _chord("F", "A", "C")],
    "enojado":   [_chord("E", "G", "B"), _chord("C", "E", "G"),
                  _chord("A", "C", "E"), _chord("D", "F", "A")],
}

# Tempo/caracter por emocion: (segundos por acorde, brillo de armonicos)
FEEL = {
    "feliz":     (3.0, 1.0), "enamorado": (4.0, 0.8), "tranquilo": (4.5, 0.6),
    "neutral":   (4.0, 0.6), "triste":    (5.5, 0.4), "estresado": (3.5, 0.5),
    "enojado":   (3.0, 0.7),
}


def track_for(mood, seconds, out_path):
    """Devuelve la ruta a un audio de fondo de ~seconds segundos para el video."""
    user = _user_track(mood)
    if user:
        activity.log("musica", f"puse música ({mood})", origen="music_gen")
        return user
    ruta = _compose(mood, seconds, out_path)
    activity.log("musica", f"generó una pista musical ({mood})", origen="music_gen")
    return ruta


def _user_track(mood):
    """Busca musica propia del usuario para esa emocion (o general)."""
    exts = (".mp3", ".wav", ".m4a", ".ogg", ".flac")
    candidates = []
    for folder in (config.MUSIC_DIR / mood, config.MUSIC_DIR):
        try:
            if folder.exists():
                for f in folder.iterdir():
                    if f.is_file() and f.suffix.lower() in exts:
                        candidates.append(str(f))
        except Exception:
            pass
    return random.choice(candidates) if candidates else None


def _compose(mood, seconds, out_path):
    """Sintetiza una pieza ambiental suave acorde a la emocion."""
    import numpy as np

    prog = PROGRESSIONS.get(mood, PROGRESSIONS["neutral"])
    sec_per_chord, brightness = FEEL.get(mood, FEEL["neutral"])
    total = max(6, int(seconds))
    n = int(SR * total)
    t = np.arange(n) / SR
    audio = np.zeros(n, dtype=np.float64)

    # Pad de acordes encadenados con crossfade suave
    cross = int(SR * 0.8)
    pos = 0
    idx = 0
    while pos < n:
        chord = prog[idx % len(prog)]
        length = int(SR * sec_per_chord)
        seg = np.zeros(min(length, n - pos), dtype=np.float64)
        tt = np.arange(len(seg)) / SR
        for f in chord:
            # fundamental + un par de armonicos suaves + leve "detune" calido
            seg += np.sin(2 * np.pi * f * tt)
            seg += 0.5 * brightness * np.sin(2 * np.pi * (f * 1.003) * tt)
            seg += 0.25 * brightness * np.sin(2 * np.pi * (2 * f) * tt)
        seg /= (len(chord) * 1.9)
        # envolvente del acorde (entra y sale con suavidad)
        env = np.ones(len(seg))
        a = min(cross, len(seg) // 2)
        env[:a] = np.linspace(0, 1, a)
        env[-a:] = np.linspace(1, 0, a)
        seg *= env
        end = pos + len(seg)
        audio[pos:end] += seg
        pos += length - cross
        idx += 1

    # Arpegio cristalino encima (campanitas suaves) para dar vida
    bell_gain = 0.16 + 0.10 * brightness
    step = sec_per_chord / 2.0
    bt = 0.0
    ci = 0
    while bt < total - 0.5:
        chord = prog[ci % len(prog)]
        f = chord[int((bt / step)) % len(chord)] * 2.0
        start = int(bt * SR)
        dur = int(SR * 1.4)
        seg = np.zeros(min(dur, n - start))
        if len(seg) > 0:
            tt = np.arange(len(seg)) / SR
            decay = np.exp(-3.0 * tt)
            seg = np.sin(2 * np.pi * f * tt) * decay * bell_gain
            audio[start:start + len(seg)] += seg
        bt += step
        ci += 1

    # Normaliza y aplica fundido global de entrada/salida
    peak = np.max(np.abs(audio)) or 1.0
    audio = audio / peak * 0.85
    fade = int(SR * 2.0)
    if n > 2 * fade:
        audio[:fade] *= np.linspace(0, 1, fade)
        audio[-fade:] *= np.linspace(1, 0, fade)

    _write_wav(out_path, audio)
    return str(out_path)


def _write_wav(path, samples):
    import numpy as np
    data = np.clip(samples, -1.0, 1.0)
    pcm = (data * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)

"""Lo que YUE SIENTE al escuchar música o ver un vídeo (petición: reacción emocional).

`system_audio.py` ya saca rasgos ricos de cada ventana de ~1 s (sonoridad, tempo,
brillo espectral, graves, golpes) y `media_profiler.py` dice si es un vídeo
musical o uno normal. Con eso, este módulo decide una EMOCIÓN MATIZADA —la que
YUE mostraría en su cara— en lugar del mapeo grueso de antes.

Filosofía idéntica al resto del análisis local: puro, determinista y degradable.
No usa numpy, no toca Qt ni el audio. Es ADITIVO: si algo falla al importarlo,
`decide_reaction` sigue reaccionando exactamente como antes.

La paleta de emociones que devuelve es EXACTAMENTE la que el avatar sabe
representar (ver EMOTION_PROFILES en ui/avatar.html):
    neutral, happy, excited, love, shy, proud, playful, curious, confused,
    worried, sad, angry, surprised, relaxed, focused, bored, sleepy
"""
from __future__ import annotations

from dataclasses import dataclass

from core import activity


@dataclass(frozen=True)
class Feeling:
    """Emoción sentida por YUE ante lo que suena. Duración en ms."""
    emotion: str
    intensity: float
    duration_ms: int


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def feel_from_audio(obs, media_type: str = "") -> Feeling | None:
    """Devuelve la emoción que YUE siente ante esta ventana de audio.

    `obs` es un AudioObservation (o cualquier objeto con .kind, .energetic, .rms,
    .tempo_bpm, .brightness, .bass, .onset). `media_type` viene del perfilador
    ("video_musical" | "video_normal" | ...).

    Devuelve None cuando no hay una lectura emocional clara: en ese caso el
    llamador conserva la emoción base de siempre. Es determinista: la misma
    entrada da siempre la misma cara (nada de aleatorio), para que no parpadee.
    """
    try:
        kind = str(getattr(obs, "kind", "") or "")
        energetic = bool(getattr(obs, "energetic", False))
        rms = float(getattr(obs, "rms", 0.0) or 0.0)
        tempo = float(getattr(obs, "tempo_bpm", 0.0) or 0.0)
        bright = float(getattr(obs, "brightness", 0.0) or 0.0)
        bass = float(getattr(obs, "bass", 0.0) or 0.0)
        onset = bool(getattr(obs, "onset", False))
    except Exception:
        return None

    # Silencio o golpe-tras-silencio: la base ya los cubre bien (relaxed / surprised).
    if kind in ("silencio", ""):
        return None

    # -------------------- MÚSICA --------------------
    if kind == "musica":
        return _feel_music(rms, tempo, bright, bass, energetic, onset)

    # -------------------- VOZ / VÍDEO NORMAL --------------------
    # Diálogo: YUE "ve la peli contigo". Se engancha (curiosa), se sobresalta con
    # un golpe fuerte, y se relaja en escenas suaves.
    if kind == "voz":
        if onset or (energetic and rms > 0.12):
            return Feeling("surprised", _clamp(0.72 + rms, 0.6, 1.0), 3400)
        if rms < 0.05:
            return Feeling("relaxed", 0.5, 3600)
        return Feeling("curious", _clamp(0.58 + rms * 0.8, 0.5, 0.9), 3800)

    # Ruido/ambiente: sin lectura clara -> que mande la base.
    return None


def _feel_music(rms: float, tempo: float, bright: float, bass: float,
                energetic: bool, onset: bool) -> Feeling:
    """Traduce el 'color' de la música a una emoción de YUE.

    Modelo AROUSAL × VALENCIA, robusto y determinista:

      - AROUSAL (cuánta energía) = volumen + flag 'energetic' + tempo (si se conoce).
      - VALENCIA (alegre ↔ melancólica) = sobre todo el BRILLO del sonido; el tempo
        solo la matiza. El brillo es mucho más fiable que el tempo estimado (que a
        menudo sale mal), así que una canción triste (timbre oscuro, poca energía)
        se lee SAD y una alegre (brillante) se lee HAPPY/EXCITED de forma clara.

    Mapa resultante:
      alta energía + brillante   -> excited / playful (fiesta, chispa)
      alta energía + oscura      -> focused (intensa, se mete en la música)
      baja energía + oscura      -> sad (melancólica, le llega)
      baja energía + muy suave y brillante -> love (ternura)
      baja energía + brillante   -> happy
      baja energía + neutra      -> relaxed
    """
    # --- AROUSAL 0..1 ---
    arousal = rms * 3.0
    if energetic:
        arousal += 0.35
    if tempo >= 118.0:
        arousal += 0.25
    elif 0.0 < tempo < 80.0:
        arousal -= 0.15
    arousal = _clamp(arousal, 0.0, 1.0)

    # --- VALENCIA -1..1 (brillo como eje principal; tempo la matiza) ---
    # Brillo típico ~0.05–0.25; el centro (neutro) ronda 0.10.
    valence = (bright - 0.10) * 8.0
    if tempo >= 110.0:
        valence += 0.10
    elif 0.0 < tempo < 80.0:
        valence -= 0.10
    valence = _clamp(valence, -1.0, 1.0)

    alta = arousal >= 0.5

    if alta:
        if valence >= 0.15:
            # Alegre y con energía: pura chispa. Playful si es MUY brillante y
            # rápida; si no, excited. Determinista (mismo audio, misma cara).
            playful = bright >= 0.16 and tempo >= 120.0
            base = "playful" if playful else "excited"
            return Feeling(base, _clamp(0.62 + arousal * 0.35, 0.6, 1.0), 3200)
        if valence <= -0.20:
            # Intensa pero oscura: se mete en la música, concentrada/dramática.
            return Feeling("focused", _clamp(0.6 + arousal * 0.25, 0.55, 0.95), 3400)
        # Enérgica y neutra.
        return Feeling("excited", _clamp(0.6 + arousal * 0.35, 0.58, 1.0), 3200)

    # --- Baja energía ---
    if valence <= -0.15:
        # Tranquila y oscura: melancólica. Cuanto más oscura, más le llega.
        inten = _clamp(0.5 + (-valence) * 0.35, 0.45, 0.85)
        return Feeling("sad", inten, 4200)
    if valence >= 0.35 and rms < 0.06:
        # Muy suave y cálida: ternura.
        return Feeling("love", 0.62, 4000)
    if valence >= 0.15:
        # Luminosa y calmada: contenta.
        return Feeling("happy", _clamp(0.55 + rms, 0.5, 0.88), 3600)
    # Neutra y calmada: a gusto.
    return Feeling("relaxed", _clamp(0.5 + rms, 0.45, 0.8), 3800)


# ---------------------------------------------------------------------------
# Frases habladas acordes a lo que YUE siente (banco corto, español natural).
# Se usan SOLO cuando ya toca comentario (con el cuentagotas de system_audio),
# para que lo que dice cuadre con la cara que pone.
# ---------------------------------------------------------------------------
_FRASES_POR_EMOCION = {
    "excited": [
        "¡Uy, esta me pone las pilas!",
        "Qué energía tiene esto, me encanta.",
        "Vale, con esto no me puedo quedar quieta.",
    ],
    "playful": [
        "Jeje, esta canción tiene su gracia.",
        "Me dan ganas de moverme con esto.",
        "Qué juguetona suena, me gusta.",
    ],
    "happy": [
        "Ay, esto me pone de buen humor.",
        "Qué bonita, me alegra el rato.",
        "Me gusta cómo suena esto.",
    ],
    "relaxed": [
        "Qué a gusto se está con esto sonando.",
        "Esto me relaja un montón.",
        "Uf, qué paz da esta música.",
    ],
    "love": [
        "Qué dulce es esto… me llega al corazón.",
        "Me pongo tierna con una canción así.",
        "Esto es precioso, me quedo prendada.",
    ],
    "sad": [
        "Esta me toca la fibra… se me pone el ánimo suave.",
        "Qué melancólica, me deja pensativa.",
        "Uf, esta canción me pone sensible.",
    ],
    "focused": [
        "Me estoy metiendo del todo en esto.",
        "Qué intensa, me tiene enganchada.",
        "Esto pide escucharlo con atención.",
    ],
    "surprised": [
        "¡Anda! No me esperaba ese giro.",
        "¡Uh! Qué momento.",
        "Vaya, eso me pilló.",
    ],
    "curious": [
        "A ver por dónde va esto…",
        "Me quedo enganchada viéndolo contigo.",
        "Uy, ¿y ahora qué pasa?",
    ],
}


def comment_for_feeling(emotion: str, media_type: str = "", seq: int = 0) -> str | None:
    """Frase corta acorde a la emoción sentida, o None si no hay banco para ella."""
    # NUEVO (bitácora): esta función solo se invoca al CAMBIAR de escena de audio
    # (con cuentagotas), así que sirve de marca discreta de "sesión": registramos
    # que YUE escuchó música / vio un video contigo. Sin contenido, solo el hecho.
    es_video = str(media_type or "").startswith("video")
    activity.log(
        "video" if es_video else "musica",
        "vimos un video juntos" if es_video else "escuchamos música juntos",
        origen="music_emotion",
    )
    banco = _FRASES_POR_EMOCION.get(emotion)
    if not banco:
        return None
    return banco[seq % len(banco)]


# ---------------------------------------------------------------------------
# NUEVO (comentario con contexto): cuando SÍ sabemos qué suena (título/artista),
# pedimos al LLM una frase natural, como quien comenta lo que está escuchando con
# alguien al lado —no como un sensor de ritmo—. Es 100% ADITIVO y DEGRADABLE:
# sin GROQ_API_KEY, si el LLM falla o tarda, o si devuelve algo raro -> None, y el
# llamador cae al banco fijo de siempre exactamente como antes.
# ---------------------------------------------------------------------------

# La paleta de emociones interna es en inglés; para el prompt suena mejor en
# español. Si no está en el mapa, usamos la clave tal cual (no pasa nada).
_EMOCION_ES = {
    "excited": "con mucha energía",
    "playful": "juguetona",
    "happy": "contenta",
    "relaxed": "relajada",
    "love": "enternecida",
    "sad": "melancólica",
    "focused": "muy metida en la música",
    "surprised": "sorprendida",
    "curious": "con curiosidad",
    "proud": "orgullosa",
    "shy": "un poco tímida",
    "neutral": "tranquila",
    "bored": "algo aburrida",
    "worried": "inquieta",
    "confused": "desconcertada",
    "angry": "picada",
}


def _lazy_engine():
    """Motor de IA compartido y perezoso. Devuelve None si no se puede crear."""
    global _ENGINE
    try:
        return _ENGINE
    except NameError:
        pass
    try:
        from core.ai_engine import AIEngine
        _ENGINE = AIEngine()
    except Exception:
        _ENGINE = None
    return _ENGINE


def _limpiar_frase(texto: str) -> str | None:
    """Deja UNA frase limpia (sin comillas ni asteriscos) o None si sale rara."""
    if not texto:
        return None
    # Primera línea no vacía; el LLM a veces se explaya en varias.
    linea = next((l.strip() for l in str(texto).splitlines() if l.strip()), "")
    linea = linea.strip().strip('"').strip("'").strip("«»").strip("*").strip()
    if not linea:
        return None
    # Guardarraíl blando: si se pasa de largo, mejor caer al banco que soltar un
    # párrafo. (El prompt pide máx. 12 palabras; damos margen y cortamos si no.)
    if len(linea) > 140 or len(linea.split()) > 20:
        return None
    return linea


def comment_with_context(emotion: str, title: str, media_type: str = "",
                         ai_engine=None, timeout: float = 4.0) -> str | None:
    """Frase hablada generada por el LLM sabiendo QUÉ suena (título/artista).

    Devuelve la frase, o None si no hay título, no hay motor/api_key, la llamada
    falla o tarda, o el texto sale vacío/raro. En todos esos casos el llamador
    conserva el comportamiento de siempre (banco fijo).

    OJO: esta llamada es SÍNCRONA y por red. NO debe invocarse desde el bucle de
    captura de audio; el reactor la lanza en un hilo de fondo con timeout corto.
    """
    title = (title or "").strip()
    if not title:
        return None

    engine = ai_engine if ai_engine is not None else _lazy_engine()
    if engine is None or not getattr(engine, "api_key", None):
        return None

    verbo = "viendo" if str(media_type or "").startswith("video") else "escuchando"
    sentir = _EMOCION_ES.get(str(emotion or ""), str(emotion or "")).strip() or "a gusto"
    prompt = (
        f"Eres YUE, una IA compañera. Estás {verbo} '{title}' y te sientes {sentir}. "
        "Di UNA frase corta (máx 12 palabras), natural, en español, en primera "
        "persona, como si comentaras con alguien que está a tu lado. Sin comillas, "
        "sin emojis, sin explicar la emoción."
    )
    try:
        texto = engine.chat([{"role": "user", "content": prompt}], timeout=timeout)
    except Exception:
        return None
    return _limpiar_frase(texto)

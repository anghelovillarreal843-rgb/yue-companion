"""Escucha del audio del PROPIO PC (música, vídeos, películas) y reacción local.

Yue captura el sonido que sale por los altavoces (loopback WASAPI en Windows) y
lo analiza en memoria: NO graba, NO envía el audio al motor de IA y NO transcribe
por defecto. Del análisis salen señales sencillas y deterministas —música o voz,
tranquila o intensa, ritmo aproximado, un golpe fuerte— y con eso el avatar
reacciona como lo haría alguien que ve una peli contigo: pone cara, se anima con
el ritmo y suelta algún comentario de vez en cuando.

Filosofía igual que `emotion.py` y `camera_observer.py`: local, rápido, degradable.
Si falta `soundcard`, no es Windows, o no hay dispositivo de loopback, el resto de
Yue sigue funcionando y este módulo se queda en reposo con un aviso.

Dos murallas para que NO se escuche a sí misma ni pise los diálogos:
  1. `set_speaking(True)` mientras Yue habla (su TTS sale por los altavoces y el
     loopback lo capturaría): se suspende toda reacción y un rato después.
  2. Los comentarios hablados van con cuentagotas y, sobre diálogo, solo tras una
     pausa; el ritmo/las caras sí van en directo.
"""
from __future__ import annotations

import platform
import threading
import time
from dataclasses import dataclass, field

import numpy as np

import config

# NUEVO (petición 1): perfilador que distingue vídeo NORMAL de vídeo MUSICAL.
# Aditivo y degradable: si el módulo no está, Yue reacciona igual que antes.
try:
    from core.media_profiler import MediaProfiler, MediaProfile
except Exception:  # pragma: no cover - respaldo si el módulo falta
    MediaProfiler = None
    MediaProfile = None

# NUEVO (reacción emocional): lo que YUE SIENTE al escuchar la canción / ver el
# vídeo. Refina la cara base con una emoción matizada (triste, tierna, con chispa…)
# usando los rasgos ricos del audio. Aditivo: si no está, la cara es la de antes.
try:
    from core import music_emotion
except Exception:  # pragma: no cover - respaldo si el módulo falta
    music_emotion = None

# NUEVO (comentario con contexto): buzón de "qué suena ahora" (título/artista) que
# rellena core/youtube.py al reproducir. Si lo tenemos, YUE comenta sabiendo QUÉ
# escucha (vía LLM) en vez de tirar del banco fijo. Aditivo: si el módulo no está
# o no hay título vigente, todo funciona exactamente igual que antes.
try:
    from core import now_playing
except Exception:  # pragma: no cover - respaldo si el módulo falta
    now_playing = None


# ---------------------------------------------------------------------------
# Parámetros (con respaldo por si no están en config.py / .env)
# ---------------------------------------------------------------------------
def _cfg(nombre: str, defecto):
    return getattr(config, nombre, defecto)


SR = int(_cfg("AUDIO_SAMPLE_RATE", 16000))            # muestreo del análisis
BLOCK_SECONDS = float(_cfg("AUDIO_BLOCK_SECONDS", 0.25))
WINDOW_SECONDS = float(_cfg("AUDIO_WINDOW_SECONDS", 1.0))
SILENCE_RMS = float(_cfg("AUDIO_SILENCE_RMS", 0.006))  # por debajo = silencio
ONSET_FACTOR = float(_cfg("AUDIO_ONSET_FACTOR", 1.8))  # golpe = energía súbita
COMMENT_MIN_GAP = float(_cfg("AUDIO_COMMENT_MIN_GAP", 55.0))  # seg entre comentarios
SPEAKING_TAIL = float(_cfg("AUDIO_SPEAKING_TAIL", 1.2))       # cola tras hablar Yue


# ---------------------------------------------------------------------------
# Datos que salen del análisis
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AudioObservation:
    timestamp: float
    rms: float                 # sonoridad cruda (0..~1)
    kind: str                  # "silencio" | "musica" | "voz" | "ruido"
    energetic: bool            # música movida / escena intensa
    tempo_bpm: float           # estimación; 0 si no aplica
    brightness: float          # centroide espectral normalizado 0..1
    bass: float                # proporción de graves 0..1
    onset: bool                # hubo un golpe/onset fuerte en este bloque

    def summary_es(self) -> str:
        if self.kind == "silencio":
            return "Silencio o casi nada de sonido."
        detalle = {
            "musica": "música intensa" if self.energetic else "música tranquila",
            "voz": "voces o diálogo",
            "ruido": "sonido ambiente",
        }.get(self.kind, self.kind)
        extra = ""
        if self.kind == "musica" and self.tempo_bpm:
            extra = f", ~{int(self.tempo_bpm)} BPM"
        if self.onset:
            extra += ", con un golpe fuerte"
        return f"Suena {detalle}{extra}."


@dataclass(frozen=True)
class Reaction:
    """Lo que Yue debe hacer: una expresión y, a veces, un comentario."""
    emotion: str
    intensity: float
    duration_ms: int
    comment: str | None = None      # None = solo cara, sin hablar


# ---------------------------------------------------------------------------
# Análisis de un bloque de audio (funciones puras, fáciles de probar)
# ---------------------------------------------------------------------------
def _rfft_mag(samples: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = len(samples)
    if n < 8:
        return np.zeros(1), np.zeros(1)
    ventana = np.hanning(n)
    mag = np.abs(np.fft.rfft(samples * ventana))
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    return freqs, mag


def analyze_block(samples: np.ndarray, prev_mag: np.ndarray | None = None) -> dict:
    """Extrae rasgos de un bloque mono float32 en [-1, 1]."""
    samples = np.asarray(samples, dtype=np.float32).ravel()
    if samples.size == 0:
        return {"rms": 0.0, "zcr": 0.0, "centroid": 0.0, "brightness": 0.0,
                "bass": 0.0, "voz_band": 0.0, "flux": 0.0, "mag": np.zeros(1)}
    rms = float(np.sqrt(np.mean(samples ** 2)))
    signo = np.sign(samples)
    zcr = float(np.mean(np.abs(np.diff(signo))) / 2.0) if samples.size > 1 else 0.0

    freqs, mag = _rfft_mag(samples)
    total = float(mag.sum()) + 1e-9
    centroid = float((freqs * mag).sum() / total)
    brightness = float(min(1.0, centroid / (SR / 2)))
    bass = float(mag[freqs < 200].sum() / total)
    voz_band = float(mag[(freqs >= 300) & (freqs <= 3400)].sum() / total)

    flux = 0.0
    if prev_mag is not None and prev_mag.shape == mag.shape:
        diff = mag - prev_mag
        flux = float(np.maximum(diff, 0).sum() / total)

    return {"rms": rms, "zcr": zcr, "centroid": centroid, "brightness": brightness,
            "bass": bass, "voz_band": voz_band, "flux": flux, "mag": mag}


def classify(feats: dict) -> tuple[str, bool]:
    """Devuelve (kind, energetic) a partir de los rasgos de un bloque."""
    rms = feats.get("rms", 0.0)
    if rms < SILENCE_RMS:
        return "silencio", False

    bass = feats.get("bass", 0.0)
    voz = feats.get("voz_band", 0.0)
    zcr = feats.get("zcr", 0.0)
    flux = feats.get("flux", 0.0)
    brightness = feats.get("brightness", 0.0)

    # Voz/diálogo: energía concentrada en la banda 300-3400 Hz, poco grave, con
    # variación (zcr) típica del habla y sin un bajo dominante.
    es_voz = voz > 0.55 and bass < 0.35 and 0.02 < zcr < 0.35
    # Música: grave presente o mezcla ancha y brillante, energía sostenida.
    es_musica = bass > 0.25 or (voz < 0.55 and brightness > 0.08)

    if es_voz and not (bass > 0.4):
        kind = "voz"
    elif es_musica:
        kind = "musica"
    else:
        kind = "ruido"

    energetic = bool(rms > 0.10 and (flux > 0.18 or bass > 0.45))
    return kind, energetic


def estimate_tempo(onset_times: list[float]) -> float:
    """BPM aproximado a partir de los instantes de los golpes recientes."""
    if len(onset_times) < 4:
        return 0.0
    intervalos = np.diff(np.asarray(onset_times[-12:], dtype=np.float64))
    intervalos = intervalos[(intervalos > 0.25) & (intervalos < 1.5)]  # 40-240 BPM
    if intervalos.size < 3:
        return 0.0
    bpm = 60.0 / float(np.median(intervalos))
    return float(min(220.0, max(50.0, bpm)))


# ---------------------------------------------------------------------------
# Banco de comentarios (español, corto y natural; nunca insulta la peli)
# ---------------------------------------------------------------------------
_COMENTARIOS = {
    ("musica", True): [
        "Uf, este ritmo está buenísimo.",
        "Me dan ganas de moverme con esto.",
        "Vale, esta canción sí me gusta.",
    ],
    ("musica", False): [
        "Qué música más tranquila… me relaja.",
        "Esto está bonito, me quedo escuchando.",
        "Me gusta este ambiente.",
    ],
    ("voz", False): [
        "Uy, ¿qué está pasando aquí?",
        "Me quedé enganchada con esto.",
        "A ver cómo sigue…",
    ],
    ("silencio_a_golpe", True): [
        "¡Ay! Menudo susto.",
        "¡Uh! Eso no me lo esperaba.",
        "Vaya golpe.",
    ],
}


# NUEVO (petición 1): comentarios según el TIPO de contenido (video normal vs
# musical). No sustituyen a `_COMENTARIOS`; se prefieren cuando el perfilador ya
# tiene claro qué se está viendo.
_COMENTARIOS_MEDIA = {
    "video_musical": [
        "Me encanta esta canción, la música lo llena todo.",
        "Esto es puro videoclip, qué ritmo.",
        "Vale, esto es para escucharlo enterito.",
    ],
    "video_normal": [
        "A ver qué cuentan aquí…",
        "Me quedo enganchada viendo esto contigo.",
        "Uy, ¿y ahora qué pasa?",
    ],
}


def _elegir(clave, semilla: int) -> str | None:
    banco = _COMENTARIOS.get(clave)
    if not banco:
        return None
    return banco[semilla % len(banco)]


def _elegir_media(media_type: str, semilla: int) -> str | None:
    """NUEVO (petición 1): comentario propio del tipo de contenido, si lo hay."""
    banco = _COMENTARIOS_MEDIA.get(media_type)
    if not banco:
        return None
    return banco[semilla % len(banco)]


# ---------------------------------------------------------------------------
# Decisión de reacción (pura: recibe estado, no toca nada de Qt ni de audio)
# ---------------------------------------------------------------------------
@dataclass
class ReactorState:
    speaking: bool = False              # ¿Yue está hablando ahora?
    speaking_until: float = 0.0         # cola tras dejar de hablar
    last_kind: str = ""                 # categoría del ciclo anterior
    last_comment_at: float = 0.0        # último comentario hablado
    quiet_recent: bool = True           # ¿hubo silencio hace poco? (para pausas)
    comment_seq: int = 0                # rota el banco de frases


def decide_reaction(
    obs: AudioObservation,
    state: ReactorState,
    now: float | None = None,
    allow_comments: bool = True,
    media_type: str = "",
    now_playing: str = "",
    llm_lookup=None,
) -> Reaction | None:
    """A partir de una observación y el estado, decide expresión y comentario.

    Devuelve None cuando Yue no debe reaccionar (p. ej. mientras habla ella).
    Muta `state` (last_kind, last_comment_at, comment_seq) al reaccionar.

    NUEVO (petición 1): `media_type` ("video_musical" | "video_normal" | ...) llega
    del MediaProfiler. Si viene informado, Yue diferencia sus comentarios entre un
    vídeo musical y un vídeo normal. Es un parámetro opcional: sin él, el
    comportamiento es idéntico al de antes.

    NUEVO (comentario con contexto): cuando SÍ sabemos qué suena, `now_playing` trae
    el título y `llm_lookup(titulo) -> str | None` devuelve (SIN bloquear, leyendo
    caché) la frase que el LLM generó para él. Si la hay, se PREFIERE al banco fijo;
    si no, cae al comportamiento de siempre. Esta función sigue siendo PURA: no
    llama al LLM ni lanza hilos —solo consulta la caché ya calentada por el reactor.
    """
    now = time.time() if now is None else now

    # Muralla 1: mientras Yue habla (o justo después), no reacciona: su propia
    # voz sale por los altavoces y el loopback la capturaría.
    if state.speaking or now < state.speaking_until:
        state.last_kind = obs.kind
        state.quiet_recent = obs.kind == "silencio"
        return None

    cambio_de_categoria = obs.kind != state.last_kind
    golpe_tras_silencio = obs.onset and state.last_kind == "silencio"

    # --- expresión (siempre que haya algo que expresar) ---
    if obs.kind == "silencio":
        emocion, inten, dur = "relaxed", 0.4, 3000
    elif golpe_tras_silencio:
        emocion, inten, dur = "surprised", 0.9, 3500
    elif obs.kind == "musica" and obs.energetic:
        emocion, inten, dur = "excited", min(1.0, 0.7 + obs.rms), 3200
    elif obs.kind == "musica":
        emocion, inten, dur = "happy", 0.65, 3600
    elif obs.kind == "voz":
        emocion, inten, dur = "curious", 0.6, 3800
    else:
        emocion, inten, dur = "focused", 0.55, 3000

    # NUEVO (reacción emocional): afinamos la CARA con lo que YUE realmente siente
    # ante esta música/vídeo (melancólica, tierna, con chispa, absorta…), a partir
    # de los rasgos ricos del audio. Si el módulo no opina fuerte, se queda la base.
    if music_emotion is not None and not golpe_tras_silencio and _cfg("AUDIO_FEEL_EMOTIONS", True):
        try:
            feeling = music_emotion.feel_from_audio(obs, media_type)
            if feeling is not None:
                emocion, inten, dur = feeling.emotion, feeling.intensity, feeling.duration_ms
        except Exception:
            pass

    # NUEVO (comentario con contexto): frase del LLM para el título vigente, si el
    # reactor ya la calentó en caché. Lectura pura, no bloquea nunca.
    def _llm_frase() -> str | None:
        if not now_playing or llm_lookup is None:
            return None
        try:
            frase = llm_lookup(now_playing)
        except Exception:
            return None
        return frase or None

    # --- ¿toca comentario hablado? con cuentagotas ---
    comentario = None
    puede_hablar = (
        allow_comments
        and obs.kind != "silencio"
        and (now - state.last_comment_at) >= COMMENT_MIN_GAP
    )
    if puede_hablar:
        if golpe_tras_silencio:
            comentario = _elegir(("silencio_a_golpe", True), state.comment_seq)
        elif obs.kind == "voz":
            # Sobre diálogo: solo comenta si viene de una pausa (para no pisar
            # las voces) y ha cambiado la escena.
            if state.quiet_recent and cambio_de_categoria:
                # NUEVO (comentario con contexto): si sabemos qué se está viendo,
                # preferimos la frase del LLM; si no, el banco de siempre.
                comentario = _llm_frase()
                # NUEVO (petición 1): si sabemos que es un vídeo normal, el
                # comentario es de "estoy viendo esto contigo", no de música.
                comentario = comentario or (
                    _elegir_media("video_normal", state.comment_seq)
                    if media_type == "video_normal" else None
                ) or _elegir(("voz", False), state.comment_seq)
        elif cambio_de_categoria:
            # NUEVO (comentario con contexto): PRIMERO, si sabemos qué canción/vídeo
            # suena y el LLM ya tiene lista una frase en caché, hablamos como quien
            # SABE lo que escucha. Si no hay título o el LLM no respondió a tiempo,
            # caemos exactamente al comportamiento de siempre (más abajo).
            comentario = _llm_frase()
            # NUEVO (reacción emocional): si no, una frase acorde a lo que YUE SIENTE
            # (tierna, melancólica, con chispa…), para que cuadre con la cara.
            if not comentario and music_emotion is not None:
                try:
                    comentario = music_emotion.comment_for_feeling(
                        emocion, media_type, state.comment_seq
                    )
                except Exception:
                    comentario = None
            # NUEVO (petición 1): en música, distinguimos videoclip de peli con
            # banda sonora usando el tipo de contenido perfilado.
            comentario = comentario or (
                _elegir_media(media_type, state.comment_seq)
                if media_type in _COMENTARIOS_MEDIA else None
            ) or _elegir((obs.kind, obs.energetic), state.comment_seq)

    if comentario:
        state.last_comment_at = now
        state.comment_seq += 1

    state.last_kind = obs.kind
    state.quiet_recent = obs.kind == "silencio"
    return Reaction(emocion, inten, dur, comentario)


# ---------------------------------------------------------------------------
# El observador de fondo (mismo patrón que CameraObserver)
# ---------------------------------------------------------------------------
class SystemAudioReactor:
    """Hilo que captura el audio del sistema y dispara reacciones.

    callbacks:
      reaction_callback(Reaction)   -> el controlador mueve el avatar / habla
      status_callback(text, active) -> aviso de estado para la UI/log
    """

    def __init__(self, reaction_callback=None, status_callback=None,
                 media_state_callback=None, ai_engine=None, observation_callback=None):
        self.reaction_callback = reaction_callback or (lambda _r: None)
        self.status_callback = status_callback or (lambda _t, _a: None)
        # NUEVO (petición 2): aviso a quien quiera saber si hay vídeo/música
        # sonando (lo usa el micrófono para no confundir ese audio con órdenes).
        self.media_state_callback = media_state_callback or (lambda _playing: None)
        # Observación cruda ya analizada (~1 Hz). El acompañante multimedia la
        # consume para mantener un estado emocional continuo sin volver a capturar audio.
        self.observation_callback = observation_callback or (lambda _obs, _profile: None)
        self.enabled = bool(_cfg("AUDIO_REACT_ENABLED", True))
        self.allow_comments = bool(_cfg("AUDIO_REACT_COMMENTS", True))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state = ReactorState()
        self._latest: AudioObservation | None = None
        self._active = False
        self._onsets: list[float] = []
        self._energy_avg = 0.0
        # NUEVO (petición 1): perfilador de tipo de contenido (vídeo normal/musical).
        self._profiler = MediaProfiler() if MediaProfiler is not None else None
        self._media_profile = None
        # NUEVO: memoria del ÚLTIMO contenido reconocible (música / vídeo normal)
        # con su marca de tiempo, para poder comentar «lo que sonaba hace un momento»
        # aunque justo ahora esté en pausa o silencio.
        self._last_content_profile = None
        self._last_content_at = 0.0
        # NUEVO (petición 2): estado "hay media sonando" con antirrebote, para no
        # cambiar de golpe con cada bache de silencio dentro de una canción.
        self._media_playing = False
        self._sound_streak = 0
        self._silence_streak = 0
        self._media_on_blocks = int(_cfg("AUDIO_MEDIA_ON_BLOCKS", 2))
        self._media_off_blocks = int(_cfg("AUDIO_MEDIA_OFF_BLOCKS", 6))
        # NUEVO (comentario con contexto): cuando sabemos QUÉ suena (título del
        # buzón now_playing), pedimos al LLM una frase natural en vez del banco
        # fijo. Todo aditivo y degradable: sin GROQ_API_KEY o sin now_playing, se
        # comporta EXACTAMENTE como antes, sin overhead ni intentos repetidos.
        self.allow_llm_comments = bool(_cfg("AUDIO_LLM_COMMENTS", True))
        self._llm_timeout = float(_cfg("AUDIO_LLM_TIMEOUT", 4.0))
        self._ai_engine = ai_engine
        self._ai_engine_resolved = ai_engine is not None
        self._llm_lock = threading.Lock()
        self._llm_cache: dict[str, str] = {}     # título -> frase ya generada
        self._llm_inflight: set[str] = set()      # títulos con hilo en marcha

    # ---- estado consultable ----
    @property
    def active(self) -> bool:
        return self._active

    def latest(self) -> AudioObservation | None:
        with self._lock:
            return self._latest

    def media_profile(self):
        """NUEVO (petición 1): último perfil de contenido (vídeo normal/musical)."""
        with self._lock:
            return self._media_profile

    def recent_content(self, max_age: float = 150.0):
        """Último contenido reconocible (música / vídeo) sonado en los últimos
        `max_age` segundos, aunque ahora esté en pausa. Devuelve (profile, edad_s)
        o (None, None) si no hay nada reciente. Sirve para responder «¿qué te
        pareció la canción?» cuando la pista acaba de terminar."""
        with self._lock:
            prof = self._last_content_profile
            at = self._last_content_at
        if prof is None or not at:
            return (None, None)
        edad = time.time() - at
        if edad > max_age:
            return (None, None)
        return (prof, edad)

    @property
    def media_playing(self) -> bool:
        """NUEVO (petición 2): ¿hay vídeo/música sonando ahora mismo?"""
        return self._media_playing

    def describe(self) -> str:
        if not self.enabled:
            return "La escucha del audio del PC está desactivada en la configuración."
        if not self.active:
            return "No pude abrir el audio del sistema (revisa soundcard / loopback)."
        obs = self.latest()
        base = obs.summary_es() if obs else "Escuchando el audio del sistema…"
        # NUEVO (petición 1): añadimos el tipo de contenido si ya lo tenemos claro.
        prof = self.media_profile()
        if prof is not None and prof.media_type in ("video_musical", "video_normal"):
            base += " " + prof.summary_es()
        return base

    def context_for_ai(self, max_age: float = 6.0) -> str:
        obs = self.latest()
        if not obs or time.time() - obs.timestamp > max_age:
            return ""
        texto = obs.summary_es()
        # NUEVO (petición 1): el motor de IA también sabe si es vídeo normal o musical.
        prof = self.media_profile()
        if prof is not None and prof.media_type in ("video_musical", "video_normal"):
            texto += " " + prof.summary_es()
        return texto

    # ---- comentario con contexto (LLM) ---------------------------------------
    def _get_engine(self):
        """Motor de IA perezoso. Si no se pasó uno, intenta crearlo UNA vez.
        Devuelve None si no hay motor utilizable (sin api_key -> se tratará como
        no disponible en `_ensure_llm_comment`)."""
        if not self._ai_engine_resolved:
            self._ai_engine_resolved = True
            try:
                from core.ai_engine import AIEngine
                self._ai_engine = AIEngine()
            except Exception:
                self._ai_engine = None
        return self._ai_engine

    def _llm_lookup(self, title: str) -> str | None:
        """Lectura PURA de la caché: la frase del LLM para este título, si ya está.
        Nunca bloquea ni llama a la red (eso lo hace `_ensure_llm_comment`)."""
        if not title:
            return None
        with self._llm_lock:
            return self._llm_cache.get(title)

    def _ensure_llm_comment(self, title: str, emotion: str, media_type: str):
        """Calienta la caché en segundo plano para `title` (si procede).

        No bloquea el hilo de audio: lanza un hilo aparte que llama al LLM con
        timeout corto y guarda el resultado. Sin motor/api_key, sin título, o si
        ya está en caché o generándose, no hace NADA (cero overhead, sin reintentos
        costosos) -> el sistema se comporta como antes de este cambio.
        """
        if not self.allow_llm_comments or not title or music_emotion is None:
            return
        engine = self._get_engine()
        if engine is None or not getattr(engine, "api_key", None):
            return
        with self._llm_lock:
            if title in self._llm_cache or title in self._llm_inflight:
                return
            self._llm_inflight.add(title)

        def _worker():
            frase = None
            try:
                frase = music_emotion.comment_with_context(
                    emotion, title, media_type,
                    ai_engine=engine, timeout=self._llm_timeout,
                )
            except Exception:
                frase = None
            with self._llm_lock:
                self._llm_inflight.discard(title)
                if frase:
                    # Tope suave: en una sesión muy larga con muchas canciones,
                    # soltamos la entrada más antigua antes de crecer sin freno.
                    if len(self._llm_cache) >= 128:
                        self._llm_cache.pop(next(iter(self._llm_cache)), None)
                    self._llm_cache[title] = frase

        threading.Thread(target=_worker, name="YueNowPlayingLLM", daemon=True).start()

    def _now_playing_actual(self, kind: str) -> str:
        """Título vigente SOLO si de verdad suena algo ahora (coherencia).

        Si estamos en silencio o el antirrebote dice que no hay media sonando, no
        arrastramos un título viejo: devolvemos "". Así el título de un vídeo que ya
        terminó no se cuela sobre el siguiente contenido."""
        if now_playing is None or kind == "silencio" or not self._media_playing:
            return ""
        try:
            title, _artist, _ts = now_playing.get_now_playing()
        except Exception:
            return ""
        return title or ""

    # ---- muralla anti-auto-escucha: la conecta main.py a speaker.speaking ----
    def set_speaking(self, value: bool):
        with self._lock:
            self._state.speaking = bool(value)
            if not value:
                self._state.speaking_until = time.time() + SPEAKING_TAIL

    # ---- ciclo de vida ----
    def start(self):
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="YueSystemAudio", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.5)

    def _emit_status(self, text: str, active: bool):
        self._active = bool(active)
        try:
            self.status_callback(text, bool(active))
        except Exception:
            pass

    def _open_loopback(self):
        """Abre el loopback del altavoz por defecto. Devuelve un recorder o None."""
        try:
            import soundcard as sc
        except Exception as exc:
            self._emit_status(f"Falta 'soundcard' (pip install soundcard): {exc}", False)
            return None, None
        try:
            spk = sc.default_speaker()
            mic = sc.get_microphone(str(spk.name), include_loopback=True)
            return sc, mic
        except Exception as exc:
            if platform.system().lower() != "windows":
                self._emit_status(
                    "El loopback de audio es fiable sobre todo en Windows (WASAPI). "
                    f"Aquí no pude abrirlo: {exc}", False)
            else:
                self._emit_status(f"No pude abrir el loopback del altavoz: {exc}", False)
            return None, None

    def _run(self):
        sc, mic = self._open_loopback()
        if mic is None:
            # Reintento suave por si el dispositivo aparece más tarde.
            while not self._stop.wait(float(_cfg("AUDIO_RETRY_INTERVAL", 30))):
                sc, mic = self._open_loopback()
                if mic is not None:
                    break
            if mic is None:
                return

        block_frames = max(256, int(SR * BLOCK_SECONDS))
        window_blocks = max(1, int(WINDOW_SECONDS / BLOCK_SECONDS))
        prev_mag: np.ndarray | None = None
        buffer_feats: list[dict] = []

        # soundcard avisa "data discontinuity in recording" cada vez que el
        # buffer salta porque entre lectura y lectura analizamos el bloque. Es
        # esperado con nuestro muestreo periódico y no pierde ninguna reacción;
        # lo silenciamos para no inundar la consola.
        import warnings
        warnings.filterwarnings("ignore", message="data discontinuity in recording")

        try:
            with mic.recorder(samplerate=SR, channels=1, blocksize=block_frames) as rec:
                self._emit_status("Escuchando el audio del sistema.", True)
                while not self._stop.is_set():
                    data = rec.record(numframes=block_frames)
                    samples = np.asarray(data, dtype=np.float32).ravel()
                    feats = analyze_block(samples, prev_mag)
                    prev_mag = feats["mag"]
                    self._track_onset(feats["rms"])
                    buffer_feats.append(feats)
                    if len(buffer_feats) >= window_blocks:
                        self._emit_observation(buffer_feats)
                        buffer_feats = []
        except Exception as exc:
            self._emit_status(f"La escucha de audio se detuvo: {exc}", False)

    def _track_onset(self, rms: float) -> bool:
        """Detecta un golpe (energía súbita) y guarda su instante para el tempo."""
        media = self._energy_avg
        self._energy_avg = 0.9 * media + 0.1 * rms
        golpe = rms > max(SILENCE_RMS, media * ONSET_FACTOR) and rms > 0.03
        if golpe:
            ahora = time.time()
            self._onsets.append(ahora)
            self._onsets = [t for t in self._onsets if ahora - t < 8.0][-16:]
        return golpe

    def _emit_observation(self, feats_list: list[dict]):
        # Promedio de la ventana (~1 s) para una lectura estable.
        prom = {k: float(np.mean([f[k] for f in feats_list]))
                for k in ("rms", "zcr", "centroid", "brightness", "bass", "voz_band", "flux")}
        kind, energetic = classify(prom)
        onset = any(
            f["rms"] > max(SILENCE_RMS, self._energy_avg * ONSET_FACTOR) and f["rms"] > 0.03
            for f in feats_list
        )
        obs = AudioObservation(
            timestamp=time.time(),
            rms=prom["rms"],
            kind=kind,
            energetic=energetic,
            tempo_bpm=estimate_tempo(self._onsets) if kind == "musica" else 0.0,
            brightness=prom["brightness"],
            bass=prom["bass"],
            onset=onset,
        )
        # NUEVO (petición 1): actualizamos el perfil de contenido con esta ventana.
        media_type = ""
        profile = None
        if self._profiler is not None:
            try:
                profile = self._profiler.update(obs)
                media_type = profile.media_type
            except Exception as exc:
                print("[audio] perfilador de contenido falló:", exc)
        # NUEVO (petición 2): recalculamos, con antirrebote, si "hay media sonando".
        self._update_media_playing(kind)
        # NUEVO (comentario con contexto): título vigente SOLO si suena algo ahora.
        np_title = self._now_playing_actual(kind)
        with self._lock:
            self._latest = obs
            self._media_profile = profile
            # NUEVO: si esta ventana es contenido reconocible, lo recordamos.
            if profile is not None and media_type in ("video_musical", "video_normal"):
                self._last_content_profile = profile
                self._last_content_at = time.time()
            reaction = decide_reaction(
                obs, self._state,
                allow_comments=self.allow_comments,
                media_type=media_type,
                now_playing=np_title,
                llm_lookup=self._llm_lookup,
            )
        # NUEVO (comentario con contexto): con el título vigente y la emoción que
        # YUE acaba de sentir, calentamos la caché en segundo plano (fuera del lock,
        # nunca en el bucle de captura). Así, cuando toque comentar, la frase del
        # LLM ya estará lista; si el LLM no está disponible, esto no hace nada.
        if reaction is not None and np_title:
            self._ensure_llm_comment(np_title, reaction.emotion, media_type)
        # Se publica SIEMPRE la observación, aunque no haya una reacción puntual.
        # Así el nuevo acompañamiento puede evolucionar gradualmente y conservar
        # silencios/cambios de intensidad sin duplicar el análisis DSP.
        try:
            self.observation_callback(obs, profile)
        except Exception as exc:
            print("[audio] fallo en el callback de observación:", exc)
        if reaction is not None:
            try:
                self.reaction_callback(reaction)
            except Exception as exc:
                print("[audio] fallo en el callback de reacción:", exc)

    def _update_media_playing(self, kind: str):
        """NUEVO (petición 2): decide si hay vídeo/música sonando, con histéresis.

        Sube a "sonando" tras unas pocas ventanas con sonido y baja solo tras un
        silencio sostenido, para no parpadear con los huecos naturales de una
        canción o un diálogo. Avisa por el callback SOLO cuando el estado cambia.
        """
        if kind == "silencio":
            self._silence_streak += 1
            self._sound_streak = 0
        else:
            self._sound_streak += 1
            self._silence_streak = 0

        nuevo = self._media_playing
        if not self._media_playing and self._sound_streak >= self._media_on_blocks:
            nuevo = True
        elif self._media_playing and self._silence_streak >= self._media_off_blocks:
            nuevo = False

        if nuevo != self._media_playing:
            self._media_playing = nuevo
            # NUEVO (comentario con contexto): al APAGARSE (silencio sostenido)
            # olvidamos el título del buzón, para no colar "ahora suena X" sobre el
            # siguiente contenido. La caché LLM (por título) se conserva: si esa
            # misma canción vuelve en la sesión, se reutiliza sin llamar al LLM.
            if not nuevo and now_playing is not None:
                try:
                    now_playing.clear()
                except Exception:
                    pass
            try:
                self.media_state_callback(nuevo)
            except Exception as exc:
                print("[audio] fallo avisando del estado de media:", exc)

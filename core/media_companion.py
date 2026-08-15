"""Acompañamiento multimedia continuo para YUE.

Coordina el audio del sistema, la visión de pantalla, la memoria y el avatar sin
reemplazar ninguno de esos subsistemas. El módulo no graba vídeo ni audio: recibe
ventanas acústicas ya analizadas por :mod:`core.system_audio` y captura frames
individuales que se descartan inmediatamente después del análisis.

Diseño:
* dos hilos daemon (orquestación visual/emocional y visión remota);
* cola de visión de tamaño uno (si llega una escena nueva, la vieja deja de ser
  relevante y no se acumula RAM);
* estado emocional vectorial con subida/caída amortiguadas;
* comentarios con cuentagotas y una segunda barrera en ``main.py``;
* persistencia opcional a través de ``core.memory.Memory``.
"""
from __future__ import annotations

import base64
import io
import json
import math
import queue
import random
import re
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import config

try:
    from core import now_playing, screen_capture, screen_diff
except Exception:  # pragma: no cover - degradación en tests mínimos
    now_playing = None
    screen_capture = None
    screen_diff = None


EMOTIONS = (
    "neutral", "happy", "sad", "love", "relaxed", "excited", "focused",
    "surprised", "worried", "angry", "curious", "shy", "proud", "playful",
)


def _cfg(name: str, default):
    return getattr(config, name, default)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFD", str(text or "").lower())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True)
class MusicAnalysis:
    timestamp: float
    energy: float
    rhythm: float
    tempo_bpm: float
    speed: float
    intensity: float
    category: str
    atmosphere: str
    dominant_emotion: str
    emotion_weights: dict[str, float]
    silence: bool = False
    important_silence: bool = False
    onset: bool = False
    media_type: str = "indefinido"


@dataclass(frozen=True)
class VisualAnalysis:
    timestamp: float
    summary: str = ""
    people: tuple[str, ...] = ()
    expressions: tuple[str, ...] = ()
    emotions: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    visible_text: str = ""
    environment: str = ""
    content_type: str = ""
    scene_change: float = 0.0
    importance: float = 0.0
    emotion_weights: dict[str, float] = field(default_factory=dict)
    # NUEVO: qué cambió respecto a los frames anteriores ("antes estaba sentado,
    # ahora está de pie"). Vacío o "sin evidencia suficiente" cuando no se puede
    # afirmar nada: NUNCA se inventa continuidad.
    temporal_change: str = ""


@dataclass(frozen=True)
class MediaSource:
    app: str = ""
    source: str = "audio_sistema"
    title: str = ""
    process: str = ""
    window_title: str = ""


@dataclass(frozen=True)
class CompanionReaction:
    emotion: str
    intensity: float
    duration_ms: int = 2600
    comment: str | None = None
    gesture: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class AvatarMediaState:
    active: bool
    rhythm: float = 0.0
    tempo_bpm: float = 0.0
    energy: float = 0.0
    calm: float = 0.0
    look_to_screen: bool = True


# ---------------------------------------------------------------------------
# Audio -> lectura musical rica
# ---------------------------------------------------------------------------

def analyze_music(obs, media_type: str = "") -> MusicAnalysis:
    """Convierte una ``AudioObservation`` existente en una lectura musical.

    No pretende reconocer tonalidad/armonía sin una librería DSP pesada. Estima
    energía, ritmo, velocidad, intensidad y ambiente usando los rasgos que el
    proyecto ya calcula (RMS, BPM, brillo, graves y onsets).
    """
    now = float(getattr(obs, "timestamp", time.time()) or time.time())
    kind = str(getattr(obs, "kind", "silencio") or "silencio")
    rms = _clamp(float(getattr(obs, "rms", 0.0) or 0.0) * 5.0)
    tempo = max(0.0, float(getattr(obs, "tempo_bpm", 0.0) or 0.0))
    brightness = _clamp(float(getattr(obs, "brightness", 0.0) or 0.0) * 4.0)
    bass = _clamp(float(getattr(obs, "bass", 0.0) or 0.0) * 2.2)
    energetic = bool(getattr(obs, "energetic", False))
    onset = bool(getattr(obs, "onset", False))
    silence = kind in ("", "silencio")

    rhythm = 0.0
    if tempo > 0:
        rhythm = _clamp(0.28 + min(tempo, 180.0) / 240.0)
    if onset:
        rhythm = _clamp(rhythm + 0.22)
    if kind == "musica":
        rhythm = max(rhythm, 0.35)

    speed = _clamp((tempo - 55.0) / 100.0) if tempo else (0.62 if energetic else 0.28)
    energy = _clamp(rms * 0.62 + rhythm * 0.20 + (0.22 if energetic else 0.0))
    intensity = _clamp(energy * 0.64 + bass * 0.20 + (0.18 if onset else 0.0))

    if silence:
        return MusicAnalysis(
            now, 0.0, 0.0, 0.0, 0.0, 0.0, "silencio", "silencio", "neutral",
            {"neutral": 1.0}, silence=True, important_silence=onset,
            onset=onset, media_type=media_type or "silencio",
        )

    weights: dict[str, float] = {}
    category = "relajante"
    atmosphere = "calmada"

    # Diálogo/ambiente audiovisual: no se fuerza una categoría musical falsa.
    if kind == "voz":
        category = "suspenso" if intensity > 0.75 else "narrativa"
        atmosphere = "intensa" if intensity > 0.7 else "conversacional"
        weights = {"curious": 0.64, "focused": 0.32}
        if onset or intensity > 0.82:
            weights.update({"surprised": 0.82, "excited": 0.35})
    elif kind == "ruido":
        category = "acción" if intensity > 0.68 else "ambiente"
        atmosphere = "agitada" if intensity > 0.68 else "ambiental"
        weights = {"focused": 0.58, "curious": 0.32}
        if onset:
            weights["surprised"] = 0.75
    else:
        # Eje aproximado arousal × valencia. El brillo ayuda a separar luminoso
        # de oscuro; el grave y la baja velocidad aportan dramatismo/nostalgia.
        valence = _clamp(0.50 + (brightness - 0.36) * 0.95 - bass * 0.12, 0.0, 1.0)
        dark = 1.0 - valence
        slow = 1.0 - speed

        if dark >= 0.82 and intensity >= 0.62 and speed < 0.58:
            category, atmosphere = "terror", "amenazante"
            weights = {"worried": 0.86, "focused": 0.58, "surprised": 0.28}
        elif dark >= 0.72 and slow >= 0.52 and intensity < 0.72:
            category, atmosphere = "triste", "melancólica"
            weights = {"sad": 0.82, "relaxed": 0.20}
        elif intensity >= 0.78 and rhythm >= 0.58:
            category, atmosphere = "épica", "dramática"
            weights = {"excited": 0.70, "focused": 0.62, "proud": 0.35}
        elif intensity >= 0.72 and speed >= 0.62:
            category, atmosphere = "acción", "enérgica"
            weights = {"excited": 0.88, "playful": 0.42, "happy": 0.35}
        elif dark >= 0.68 and intensity >= 0.48:
            category, atmosphere = "suspenso", "tensa"
            weights = {"worried": 0.55, "focused": 0.64, "curious": 0.36}
        elif dark >= 0.57 and slow >= 0.66:
            category, atmosphere = "nostálgica", "evocadora"
            weights = {"sad": 0.48, "love": 0.42, "relaxed": 0.38}
        elif valence >= 0.70 and intensity < 0.58 and slow >= 0.42:
            category, atmosphere = "romántica", "cálida"
            weights = {"love": 0.76, "happy": 0.42, "relaxed": 0.45}
        elif valence >= 0.64 and intensity >= 0.55:
            category, atmosphere = "alegre", "luminosa"
            weights = {"happy": 0.80, "excited": 0.52, "playful": 0.30}
        elif intensity <= 0.40:
            category, atmosphere = "relajante", "serena"
            weights = {"relaxed": 0.82, "love": 0.20}
        elif intensity >= 0.58 and valence >= 0.52:
            category, atmosphere = "motivacional", "ascendente"
            weights = {"proud": 0.62, "excited": 0.55, "happy": 0.42}
        else:
            category, atmosphere = "relajante", "contemplativa"
            weights = {"relaxed": 0.58, "curious": 0.32}

        if onset:
            weights["surprised"] = max(weights.get("surprised", 0.0), 0.55)

    dominant = max(weights, key=weights.get) if weights else "neutral"
    return MusicAnalysis(
        timestamp=now,
        energy=energy,
        rhythm=rhythm,
        tempo_bpm=tempo,
        speed=speed,
        intensity=intensity,
        category=category,
        atmosphere=atmosphere,
        dominant_emotion=dominant,
        emotion_weights={k: _clamp(v) for k, v in weights.items()},
        silence=False,
        important_silence=False,
        onset=onset,
        media_type=media_type or "indefinido",
    )


# ---------------------------------------------------------------------------
# Visión -> JSON estructurado
# ---------------------------------------------------------------------------

_VISUAL_EMOTION_MAP = {
    "feliz": "happy", "alegre": "happy", "sonrisa": "happy",
    "triste": "sad", "llorando": "sad", "llanto": "sad",
    "enojado": "angry", "enojada": "angry", "furia": "angry",
    "sorprendido": "surprised", "sorprendida": "surprised",
    "miedo": "worried", "terror": "worried", "tenso": "worried",
    "romantico": "love", "romantica": "love", "ternura": "love",
    "calma": "relaxed", "tranquilo": "relaxed", "tranquila": "relaxed",
    "curioso": "curious", "curiosa": "curious", "expectativa": "curious",
    "concentrado": "focused", "concentrada": "focused",
}


def _list_text(value: Any, limit: int = 8) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [p.strip() for p in re.split(r"[,;|]", value) if p.strip()]
    elif isinstance(value, (list, tuple)):
        items = [str(p).strip() for p in value if str(p).strip()]
    else:
        items = []
    return tuple(items[:limit])


def _json_object(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if not value:
        return {}
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    start, end = value.find("{"), value.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(value[start:end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def parse_visual_response(text: str, change_score: float = 0.0) -> VisualAnalysis:
    data = _json_object(text)
    now = time.time()
    if not data:
        summary = re.sub(r"\s+", " ", str(text or "")).strip()[:300]
        weights = _weights_from_visual_text(summary)
        return VisualAnalysis(
            timestamp=now,
            summary=summary,
            scene_change=_clamp(change_score),
            importance=_clamp(0.25 + change_score * 0.6),
            emotion_weights=weights,
        )

    summary = str(data.get("resumen") or data.get("summary") or "").strip()[:320]
    people = _list_text(data.get("personas") or data.get("people"))
    expressions = _list_text(data.get("expresiones") or data.get("expressions"))
    emotions = _list_text(data.get("emociones") or data.get("emotions"))
    colors = _list_text(data.get("colores") or data.get("colors"), 6)
    actions = _list_text(data.get("acciones") or data.get("actions"))
    objects = _list_text(data.get("objetos") or data.get("objects"))
    visible_text = str(data.get("texto_visible") or data.get("visible_text") or "").strip()[:500]
    environment = str(data.get("ambiente") or data.get("environment") or "").strip()[:160]
    content_type = str(data.get("tipo_contenido") or data.get("content_type") or "").strip()[:80]
    importance = _clamp(data.get("importancia", data.get("importance", 0.4)))
    declared_change = _clamp(data.get("cambio_escena", data.get("scene_change", change_score)))
    temporal_change = str(
        data.get("cambio_respecto_antes") or data.get("temporal_change") or "").strip()[:200]
    # Si el modelo admite que no tiene evidencia, no lo guardamos como si fuera
    # una observación: así la capa conversacional no lo repite como un hecho.
    if temporal_change.lower().startswith("sin evidencia"):
        temporal_change = ""
    combined = " ".join((summary, " ".join(expressions), " ".join(emotions), environment))
    weights = _weights_from_visual_text(combined)
    return VisualAnalysis(
        timestamp=now,
        summary=summary,
        people=people,
        expressions=expressions,
        emotions=emotions,
        colors=colors,
        actions=actions,
        objects=objects,
        visible_text=visible_text,
        environment=environment,
        content_type=content_type,
        scene_change=max(_clamp(change_score), declared_change),
        importance=importance,
        emotion_weights=weights,
        temporal_change=temporal_change,
    )


def _weights_from_visual_text(text: str) -> dict[str, float]:
    normalized = _norm(text)
    weights: dict[str, float] = {}
    for cue, emo in _VISUAL_EMOTION_MAP.items():
        if cue in normalized:
            weights[emo] = max(weights.get(emo, 0.0), 0.72)
    if any(c in normalized for c in ("batalla", "explosion", "persecucion", "pelea")):
        weights.update({"excited": 0.72, "focused": 0.62})
    if any(c in normalized for c in ("paisaje", "atardecer", "naturaleza", "cielo")):
        weights.update({"relaxed": 0.58, "love": 0.38})
    if any(c in normalized for c in ("abrazo", "caricia", "bebe", "mascota")):
        weights["love"] = max(weights.get("love", 0.0), 0.68)
    return weights


# ---------------------------------------------------------------------------
# Fusión emocional continua
# ---------------------------------------------------------------------------


def fuse_emotions(audio: MusicAnalysis | None, visual: VisualAnalysis | None,
                  audio_sensitivity: float = 1.0,
                  visual_sensitivity: float = 1.0) -> dict[str, float]:
    result = {emotion: 0.0 for emotion in EMOTIONS}
    if audio is not None:
        for emotion, value in audio.emotion_weights.items():
            if emotion in result:
                result[emotion] += _clamp(value) * max(0.0, audio_sensitivity) * 0.62
    if visual is not None:
        visual_weight = 0.50 + 0.35 * _clamp(visual.importance)
        for emotion, value in visual.emotion_weights.items():
            if emotion in result:
                result[emotion] += _clamp(value) * max(0.0, visual_sensitivity) * visual_weight

    # Sinergias explícitas audio + vídeo.
    if audio and visual:
        if audio.dominant_emotion == "sad" and visual.emotion_weights.get("sad", 0) > 0.4:
            result["sad"] += 0.40
        if audio.category in ("épica", "acción") and (
            visual.emotion_weights.get("excited", 0) > 0.35
            or any("batalla" in _norm(a) or "pelea" in _norm(a) for a in visual.actions)
        ):
            result["excited"] += 0.35
            result["proud"] += 0.18
            result["curious"] += 0.12
        if audio.category == "romántica" and visual.emotion_weights.get("love", 0) > 0.3:
            result["love"] += 0.36
        if audio.onset and visual.scene_change > 0.65:
            result["surprised"] += 0.32

    peak = max(result.values()) if result else 0.0
    result["neutral"] = _clamp(0.55 - peak * 0.48)
    return {k: _clamp(v) for k, v in result.items()}


class ContinuousEmotionState:
    """Vector emocional amortiguado; nunca salta directamente al objetivo."""

    def __init__(self, rise_seconds: float = 3.2, fall_seconds: float = 6.5,
                 intensity_gain: float = 1.0):
        self.rise_seconds = max(0.25, float(rise_seconds))
        self.fall_seconds = max(self.rise_seconds, float(fall_seconds))
        self.intensity_gain = max(0.1, float(intensity_gain))
        self.levels = {emotion: 0.0 for emotion in EMOTIONS}
        self.levels["neutral"] = 1.0
        self.target = dict(self.levels)
        self._lock = threading.RLock()

    def set_target(self, target: dict[str, float]):
        with self._lock:
            for emotion in EMOTIONS:
                self.target[emotion] = _clamp(target.get(emotion, 0.0) * self.intensity_gain)

    def neutralize(self):
        self.set_target({"neutral": 1.0})

    def step(self, dt: float) -> tuple[str, float, dict[str, float]]:
        dt = max(0.001, min(float(dt), 2.0))
        with self._lock:
            for emotion in EMOTIONS:
                current = self.levels[emotion]
                target = self.target[emotion]
                tau = self.rise_seconds if target > current else self.fall_seconds
                alpha = 1.0 - math.exp(-dt / tau)
                self.levels[emotion] = _clamp(current + (target - current) * alpha)
            dominant = max(EMOTIONS, key=lambda e: self.levels[e])
            intensity = self.levels[dominant]
            # Neutral solo gana cuando el resto realmente se apagó.
            non_neutral = max((self.levels[e] for e in EMOTIONS if e != "neutral"), default=0.0)
            if dominant == "neutral" and non_neutral > 0.30:
                dominant = max((e for e in EMOTIONS if e != "neutral"), key=lambda e: self.levels[e])
                intensity = self.levels[dominant]
            return dominant, _clamp(max(0.28, intensity)), dict(self.levels)

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self.levels)


# ---------------------------------------------------------------------------
# Detección ligera de aplicación/fuente
# ---------------------------------------------------------------------------

_BROWSER_PROCESSES = ("chrome", "msedge", "firefox", "brave", "opera", "vivaldi")
_PLAYER_PROCESSES = ("vlc", "mpv", "wmplayer", "potplayer", "foobar", "musicbee")


def detect_media_source() -> MediaSource:
    window_title = ""
    process = ""
    try:
        import pygetwindow as gw
        active = gw.getActiveWindow()
        window_title = str(getattr(active, "title", "") or "").strip()
    except Exception:
        pass

    if _is_windows():
        try:
            from platforms import get_platform_controller
            _titulo_fg, _proceso_fg = get_platform_controller().foreground_app()
            process = process or _proceso_fg
        except Exception:
            pass

    # Si el audio viene de una app en segundo plano (Spotify mientras el usuario
    # mira otra ventana), el proceso foreground no basta. Como fallback barato,
    # busca reproductores conocidos ya abiertos; el modo solo llama a esta función
    # cuando el detector acústico confirmó que realmente hay sonido.
    if not any(name in _norm(process) for name in ("spotify",) + _BROWSER_PROCESSES + _PLAYER_PROCESSES):
        try:
            import psutil
            running = {str(p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])}
            for candidate in ("spotify.exe", "vlc.exe", "mpv.exe", "wmplayer.exe",
                              "musicbee.exe", "foobar2000.exe", "chrome.exe",
                              "msedge.exe", "firefox.exe", "brave.exe"):
                if candidate in running:
                    process = candidate
                    break
        except Exception:
            pass

    title_low = _norm(window_title)
    proc_low = _norm(process)
    source, app = "audio_sistema", process or ""
    if "spotify" in title_low or "spotify" in proc_low:
        source, app = "spotify", "Spotify"
    elif "youtube" in title_low:
        source, app = "youtube", "YouTube"
    elif any(name in proc_low for name in _BROWSER_PROCESSES):
        source, app = "navegador", _friendly_app(process)
    elif any(name in proc_low for name in _PLAYER_PROCESSES):
        source, app = "reproductor", _friendly_app(process)
    elif window_title:
        source, app = "aplicacion", _friendly_app(process) or window_title[:60]

    title = ""
    if now_playing is not None:
        try:
            title, _artist, _ts = now_playing.get_now_playing()
        except Exception:
            title = ""
    if not title:
        title = _clean_window_title(window_title, source)
    return MediaSource(app=app, source=source, title=title[:180], process=process,
                       window_title=window_title[:240])


def _is_windows() -> bool:
    import sys
    return sys.platform == "win32"


def _friendly_app(process: str) -> str:
    value = re.sub(r"\.exe$", "", str(process or ""), flags=re.I)
    names = {
        "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox",
        "brave": "Brave", "opera": "Opera", "vlc": "VLC",
        "wmplayer": "Windows Media Player", "mpv": "MPV",
    }
    return names.get(value.lower(), value.replace("_", " ").title())


def _clean_window_title(title: str, source: str) -> str:
    value = str(title or "").strip()
    if not value:
        return ""
    suffixes = (
        " - YouTube", " — Mozilla Firefox", " - Google Chrome",
        " - Microsoft Edge", " - Brave", " | Spotify",
    )
    for suffix in suffixes:
        if value.endswith(suffix):
            value = value[:-len(suffix)].strip()
    # Títulos genéricos no sirven como memoria de contenido.
    if _norm(value) in ("youtube", "spotify", "nueva pestana", "new tab"):
        return ""
    return value[:180]


# ---------------------------------------------------------------------------
# Coordinador
# ---------------------------------------------------------------------------

class MediaCompanion:
    """Coordina acompañamiento multimedia sin bloquear la conversación principal."""

    def __init__(
        self,
        ai_engine=None,
        memory=None,
        reaction_callback: Callable[[CompanionReaction], None] | None = None,
        avatar_callback: Callable[[AvatarMediaState], None] | None = None,
        status_callback: Callable[[str, bool], None] | None = None,
        context_callback: Callable[[], dict[str, Any]] | None = None,
    ):
        self.enabled = bool(_cfg("MEDIA_COMPANION_ENABLED", True))
        self.visual_enabled = bool(_cfg("MEDIA_COMPANION_VISUAL_ENABLED", True))
        self.comments_enabled = bool(_cfg("MEDIA_COMPANION_COMMENTS_ENABLED", True))
        self.avatar_enabled = bool(_cfg("MEDIA_COMPANION_AVATAR_ENABLED", True))
        self.ai_engine = ai_engine
        self.memory = memory
        self.reaction_callback = reaction_callback or (lambda _r: None)
        self.avatar_callback = avatar_callback or (lambda _s: None)
        self.status_callback = status_callback or (lambda _t, _a: None)
        self.context_callback = context_callback or (lambda: {})

        self.visual_min = _clamp(_cfg("MEDIA_COMPANION_VISUAL_MIN_INTERVAL", 0.5), 0.5, 2.0)
        self.visual_max = _clamp(_cfg("MEDIA_COMPANION_VISUAL_MAX_INTERVAL", 2.0), self.visual_min, 2.0)
        self.visual_interval = _clamp(
            _cfg("MEDIA_COMPANION_VISUAL_INTERVAL", 1.0), self.visual_min, self.visual_max
        )
        self.visual_change_threshold = _clamp(_cfg("MEDIA_COMPANION_SCENE_CHANGE_THRESHOLD", 0.025), 0.001, 0.8)
        self.visual_stale_seconds = max(3.0, float(_cfg("MEDIA_COMPANION_VISUAL_STALE_SECONDS", 12.0)))
        self.comment_min_gap = max(8.0, float(_cfg("MEDIA_COMPANION_COMMENT_MIN_GAP", 45.0)))
        self.comment_max_gap = max(self.comment_min_gap, float(_cfg("MEDIA_COMPANION_COMMENT_MAX_GAP", 150.0)))
        self.spontaneity = _clamp(_cfg("MEDIA_COMPANION_SPONTANEITY", 0.48))
        self.music_sensitivity = max(0.0, float(_cfg("MEDIA_COMPANION_MUSIC_SENSITIVITY", 1.0)))
        self.visual_sensitivity = max(0.0, float(_cfg("MEDIA_COMPANION_VISUAL_SENSITIVITY", 1.0)))
        self.avatar_motion = _clamp(_cfg("MEDIA_COMPANION_AVATAR_MOTION", 0.72))

        self._emotion = ContinuousEmotionState(
            rise_seconds=float(_cfg("MEDIA_COMPANION_EMOTION_RISE_SECONDS", 3.2)),
            fall_seconds=float(_cfg("MEDIA_COMPANION_EMOTION_FALL_SECONDS", 7.0)),
            intensity_gain=float(_cfg("MEDIA_COMPANION_EMOTION_INTENSITY", 1.0)),
        )
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._playing = False
        self._speaking = False
        self._started = False
        self._thread: threading.Thread | None = None
        self._vision_thread: threading.Thread | None = None
        self._vision_queue: queue.Queue = queue.Queue(maxsize=1)
        self._audio: MusicAnalysis | None = None
        self._visual: VisualAnalysis | None = None
        self._source = MediaSource()
        self._session_id: int | None = None
        self._session_started = 0.0
        self._last_frame_signature = None
        # NUEVO (memoria temporal): últimos frames observados (t-4 … t) con su
        # resumen. Sirve para entender CAMBIOS ("antes estaba sentado, ahora se
        # levantó") sin mandar los cinco frames en cada petición.
        self._frame_memory: deque = deque(
            maxlen=max(2, int(_cfg("MEDIA_COMPANION_TEMPORAL_FRAMES", 5))))
        self._last_keyframe_b64 = ""
        # Detección de VÍDEO SIN AUDIO: movimiento sostenido en pantalla.
        self._silent_video_score = 0.0
        self._silent_video_since = 0.0
        self._last_visual_submit = 0.0
        self._last_visual_result = 0.0
        self._vision_retry_at = 0.0
        self._last_emotion_emit = 0.0
        self._last_avatar_emit = 0.0
        self._last_comment_at = 0.0
        self._next_comment_check_at = 0.0
        self._pending_comment: tuple[str, str] | None = None
        self._recent_comments: deque[str] = deque(maxlen=12)
        self._comment_seq = 0
        self._last_memory_title = ""
        self._last_memory_at = 0.0
        self._last_source_scan_at = 0.0
        self._last_scene_key = ""
        self._rng = random.Random()

    @property
    def active(self) -> bool:
        with self._lock:
            return bool(self.enabled and self._playing)

    def start(self):
        if not self.enabled or self._started:
            return
        self._started = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="YueMediaCompanion", daemon=True)
        self._vision_thread = threading.Thread(target=self._vision_loop, name="YueMediaVision", daemon=True)
        self._thread.start()
        self._vision_thread.start()
        self._emit_status("Acompañamiento multimedia preparado.", False)

    def stop(self):
        self._stop.set()
        self._wake.set()
        self._offer_vision(None)
        for thread in (self._thread, self._vision_thread):
            if thread and thread.is_alive():
                thread.join(timeout=2.5)
        self._finish_session()
        if self.avatar_enabled:
            self._emit_avatar(AvatarMediaState(active=False))

    def set_speaking(self, value: bool):
        with self._lock:
            self._speaking = bool(value)

    def set_media_playing(self, value: bool):
        value = bool(value)
        with self._lock:
            changed = value != self._playing
            self._playing = value
        if not changed:
            return
        if value:
            self._source = self._scan_source(force=True)
            self._session_started = time.time()
            self._last_comment_at = self._session_started
            self._next_comment_check_at = self._session_started + self.comment_min_gap
            self._last_frame_signature = None
            self._last_scene_key = ""
            with self._lock:
                self._visual = None
            self._start_session()
            self._refresh_target()
            self._emit_status("Modo acompañamiento multimedia activo.", True)
            self._wake.set()
        else:
            self._emotion.neutralize()
            self._pending_comment = None
            self._finish_session()
            self._emit_status("Modo acompañamiento multimedia en reposo.", False)
            if self.avatar_enabled:
                self._emit_avatar(AvatarMediaState(active=False))

    def on_audio_observation(self, obs, profile=None):
        if not self.enabled:
            return
        media_type = str(getattr(profile, "media_type", "") or "")
        analysis = analyze_music(obs, media_type)
        with self._lock:
            self._audio = analysis
        self._refresh_target()
        self._remember_source_if_needed()
        self._wake.set()

    def context_for_ai(self, max_age: float = 180.0) -> str:
        with self._lock:
            audio = self._audio
            visual = self._visual
            source = self._source
            playing = self._playing
        pieces = []
        if source.title:
            pieces.append(f"contenido: {source.title}")
        if source.app:
            pieces.append(f"aplicación: {source.app}")
        if audio and (time.time() - audio.timestamp) <= max_age and not audio.silence:
            pieces.append(
                f"audio {audio.category}, ambiente {audio.atmosphere}, "
                f"energía {int(audio.energy * 100)}%"
            )
        if visual and (time.time() - visual.timestamp) <= max_age and visual.summary:
            pieces.append("escena: " + visual.summary[:180])
            # NUEVO: el cambio respecto a lo anterior, solo si el modelo tuvo
            # evidencia suficiente (si no, el parser lo deja vacío).
            if getattr(visual, "temporal_change", ""):
                pieces.append("cambio reciente: " + visual.temporal_change[:140])
        # NUEVO: vídeo SILENCIADO. Aunque no haya audio, si hay movimiento
        # sostenido en pantalla YUE debe saber que está viendo algo.
        if not playing and self.silent_video_detected():
            pieces.append("hay vídeo en pantalla sin sonido (detectado por movimiento)")
        if not pieces:
            return ""
        state = "reproduciéndose" if playing else "reproducido hace poco"
        if not playing and self.silent_video_detected():
            state = "en pantalla (sin audio)"
        return state + "; " + "; ".join(pieces) + "."

    def emotional_snapshot(self) -> dict[str, float]:
        return self._emotion.snapshot()

    # ---- bucle principal -------------------------------------------------
    def _run(self):
        last = time.monotonic()
        next_capture = 0.0
        next_silent_probe = 0.0
        while not self._stop.is_set():
            now_mono = time.monotonic()
            dt = max(0.01, min(now_mono - last, 0.5))
            last = now_mono
            with self._lock:
                playing = self._playing
                audio = self._audio
            emotion_name, intensity, levels = self._emotion.step(dt)

            if playing:
                if self.visual_enabled and now_mono >= next_capture:
                    interval = self._capture_and_schedule_vision()
                    next_capture = now_mono + interval
                self._emit_continuous_avatar(audio, now_mono)
                self._emit_continuous_emotion(emotion_name, intensity, now_mono)
                self._maybe_comment(emotion_name, intensity, levels)
                self._wake.wait(timeout=0.12)
                self._wake.clear()
            else:
                # Continúa el decaimiento unos segundos para volver suavemente a neutral.
                self._emit_continuous_emotion(emotion_name, intensity, now_mono)
                # NUEVO (vídeo sin audio): sondeo BARATO de movimiento aunque no
                # suene nada. Antes el modo multimedia solo despertaba cuando el
                # detector acústico oía algo, así que un vídeo silenciado pasaba
                # totalmente desapercibido. Esto NO llama a ningún modelo: solo
                # compara dos miniaturas cada pocos segundos.
                if self.visual_enabled and now_mono >= next_silent_probe:
                    self._probe_silent_video()
                    next_silent_probe = now_mono + max(
                        1.0, float(_cfg("MEDIA_COMPANION_SILENT_PROBE_INTERVAL", 2.5)))
                self._wake.wait(timeout=0.35)
                self._wake.clear()

    def _emit_continuous_emotion(self, emotion_name: str, intensity: float, now_mono: float):
        if (now_mono - self._last_emotion_emit) < 0.42:
            return
        self._last_emotion_emit = now_mono
        try:
            self.reaction_callback(CompanionReaction(
                emotion=emotion_name,
                intensity=_clamp(intensity),
                duration_ms=2600,
                reason="estado_continuo",
            ))
        except Exception:
            pass

    def _emit_continuous_avatar(self, audio: MusicAnalysis | None, now_mono: float):
        if not self.avatar_enabled or (now_mono - self._last_avatar_emit) < 0.32:
            return
        self._last_avatar_emit = now_mono
        rhythm = (audio.rhythm if audio and not audio.silence else 0.0) * self.avatar_motion
        energy = (audio.energy if audio and not audio.silence else 0.0) * self.avatar_motion
        calm = 0.0
        if audio and audio.category in ("relajante", "romántica", "nostálgica"):
            calm = _clamp((1.0 - audio.energy) * self.avatar_motion)
        self._emit_avatar(AvatarMediaState(
            active=True,
            rhythm=_clamp(rhythm),
            tempo_bpm=audio.tempo_bpm if audio else 0.0,
            energy=_clamp(energy),
            calm=calm,
            look_to_screen=True,
        ))

    # ---- visión ----------------------------------------------------------
    def _capture_and_schedule_vision(self) -> float:
        if screen_capture is None or screen_diff is None:
            return self.visual_max
        # Si el proveedor de visión falló, no seguimos capturando/encolando frames
        # durante el periodo de reintento. El audio y el avatar siguen activos.
        if time.time() < self._vision_retry_at:
            return self.visual_max
        try:
            image = screen_capture._grab()
            image = self._mask_yue_region(image)
            signature = screen_diff.signature_from_image(image)
            change = self._change_score(self._last_frame_signature, signature)
            self._last_frame_signature = signature
            now = time.time()
            stale = (now - self._last_visual_submit) >= self.visual_stale_seconds
            # Movimiento sostenido = hay vídeo aunque esté SILENCIADO.
            self._track_silent_video(change, now)
            if change >= self.visual_change_threshold or stale:
                b64 = self._encode_frame(image)
                self._source = self._scan_source()
                # Se manda también un resumen corto de lo ya observado, para que
                # el modelo pueda comparar en vez de describir cada frame como
                # una fotografía independiente.
                self._offer_vision((b64, change, self._source, self._temporal_brief()))
                self._last_visual_submit = now
            return self._adaptive_interval(change)
        except Exception as exc:
            self._emit_status(f"Visión multimedia temporalmente no disponible: {exc}", self.active)
            return self.visual_max

    def _probe_silent_video(self):
        """Mide el movimiento de pantalla SIN llamar a ningún modelo.

        Solo captura, enmascara el avatar y compara firmas perceptuales. Es lo
        que permite que YUE se dé cuenta de que hay un vídeo aunque esté
        silenciado (o con el volumen a cero).
        """
        if screen_capture is None or screen_diff is None:
            return
        try:
            image = screen_capture._grab()
            image = self._mask_yue_region(image)
            signature = screen_diff.signature_from_image(image)
            previa = self._last_frame_signature
            change = self._change_score(previa, signature)
            self._last_frame_signature = signature
            # La PRIMERA muestra no tiene con qué compararse (_change_score
            # devuelve 1.0 ante la duda) y dispararía un falso "hay vídeo".
            if previa is None:
                return
            self._track_silent_video(change, time.time())
        except Exception:
            # Un fallo de captura aquí no debe molestar: es un sondeo opcional.
            pass

    def _mask_yue_region(self, image):
        """Evita que el propio avatar provoque falsos cambios de escena."""
        try:
            from PIL import ImageDraw
            copy = image.copy()
            draw = ImageDraw.Draw(copy)
            width, height = copy.size
            avatar_w = int(_cfg("AVATAR_WIDTH", 320)) + 80
            avatar_h = int(_cfg("AVATAR_HEIGHT", 520)) + 80
            draw.rectangle(
                (max(0, width - avatar_w), max(0, height - avatar_h), width, height),
                fill=(0, 0, 0),
            )
            return copy
        except Exception:
            return image

    @staticmethod
    def _encode_frame(image, max_width: int = 960, quality: int = 62) -> str:
        if image.width > max_width:
            image = image.resize((max_width, int(image.height * max_width / image.width)))
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def _change_score(before, after) -> float:
        if before is None or after is None:
            return 1.0
        try:
            ham = min(1.0, screen_diff.hamming(before.hash, after.hash) / 20.0)
            pixel = min(1.0, screen_diff.pixel_diff_ratio(before.thumb, after.thumb) * 8.0)
            tile = min(1.0, screen_diff.tile_diff_max_ratio(before.thumb, after.thumb) * 2.5)
            return _clamp(max(ham, pixel, tile))
        except Exception:
            return 1.0

    def _adaptive_interval(self, change: float) -> float:
        if change >= 0.38:
            return self.visual_min
        if change >= 0.14:
            return max(self.visual_min, min(self.visual_max, 0.72))
        if change <= 0.015:
            return self.visual_max
        span = self.visual_max - self.visual_min
        return _clamp(self.visual_max - change * span * 2.5, self.visual_min, self.visual_max)

    def _offer_vision(self, item):
        try:
            self._vision_queue.put_nowait(item)
            return
        except queue.Full:
            pass
        try:
            self._vision_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._vision_queue.put_nowait(item)
        except queue.Full:
            pass

    def _vision_loop(self):
        while not self._stop.is_set():
            try:
                item = self._vision_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                continue
            # Compatibilidad: los items antiguos venían con 3 campos.
            if len(item) == 4:
                b64, change, source, historial = item
            else:
                b64, change, source = item
                historial = ""
            if time.time() < self._vision_retry_at:
                continue
            try:
                if self.ai_engine is None:
                    raise RuntimeError("motor de visión no configurado")
                text = self.ai_engine.look(
                    "Eres la percepción visual privada de YUE. Analiza solo la escena "
                    "multimedia visible; no identifiques personas reales ni guardes datos. "
                    "Devuelve únicamente JSON válido y breve.",
                    b64,
                    self._vision_instruction(change, source, historial),
                    timeout=float(_cfg("MEDIA_COMPANION_VISION_TIMEOUT", 18.0)),
                )
                visual = parse_visual_response(text, change)
                with self._lock:
                    self._visual = visual
                    self._source = source
                self._last_visual_result = time.time()
                self._remember_frame(visual, change)
                self._last_keyframe_b64 = b64
                self._refresh_target()
                self._remember_visual_event(visual)
                self._wake.set()
            except Exception as exc:
                retry = max(15.0, float(_cfg("MEDIA_COMPANION_VISION_RETRY_SECONDS", 60.0)))
                self._vision_retry_at = time.time() + retry
                self._emit_status(f"Análisis visual multimedia pausado: {exc}", self.active)

    @staticmethod
    def _vision_instruction(change: float, source: MediaSource,
                            historial: str = "") -> str:
        """Instrucción del frame actual, con memoria temporal corta opcional.

        MEJORADO: antes cada frame se analizaba como una fotografía totalmente
        independiente ("no inventes continuidad"), así que YUE no podía notar
        cambios básicos. Ahora recibe un RESUMEN de lo observado en los frames
        anteriores (no las imágenes: sale casi gratis) y puede decir "antes
        estaba sentado y ahora se levantó". La regla de no inventar sigue en pie:
        si la evidencia temporal no basta, debe decirlo en `cambio_respecto_antes`.
        """
        base = (
            "Analiza el frame ACTUAL. No inventes spoilers ni des por hecho nada "
            "que no se vea. Responde con este JSON exacto: "
            '{"resumen":"máx. 18 palabras","personas":[],"expresiones":[],"emociones":[],'
            '"colores":[],"acciones":[],"objetos":[],"texto_visible":"",'
            '"ambiente":"","tipo_contenido":"","cambio_escena":0.0,"importancia":0.0,'
            '"cambio_respecto_antes":""}. '
            "cambio_escena e importancia van de 0 a 1. "
            f"Cambio visual local estimado={change:.2f}; fuente={source.source}; "
            f"aplicación={source.app or 'desconocida'}."
        )
        if historial:
            base += (
                "\n\nObservaciones ANTERIORES (resumen, de más antigua a más "
                f"reciente):\n{historial}\n"
                "En `cambio_respecto_antes` di en pocas palabras QUÉ CAMBIÓ "
                "respecto a eso (por ejemplo: «antes estaba sentado, ahora está de "
                "pie»). Si no hay evidencia suficiente para afirmar un cambio, "
                "escribe exactamente «sin evidencia suficiente». NO inventes "
                "continuidad."
            )
        return base

    # ---- memoria temporal de frames --------------------------------------
    def _remember_frame(self, visual, change: float):
        """Guarda un resumen del frame recién analizado (t-4 … t)."""
        try:
            resumen = str(getattr(visual, "summary", "") or "").strip()
            if not resumen:
                acciones = list(getattr(visual, "actions", []) or [])
                resumen = ", ".join(str(a) for a in acciones[:3])
            if not resumen:
                return
            self._frame_memory.append({
                "t": time.time(),
                "resumen": resumen[:140],
                "cambio": round(float(change), 3),
            })
        except Exception:
            pass

    def _temporal_brief(self, max_items: int = 4) -> str:
        """Resumen textual de los frames anteriores para el prompt.

        Es texto, no imágenes: mantiene el coste prácticamente igual que antes.
        """
        if not self._frame_memory:
            return ""
        ahora = time.time()
        lineas = []
        for entrada in list(self._frame_memory)[-int(max_items):]:
            edad = max(0, int(ahora - float(entrada.get("t", ahora))))
            lineas.append(f"- hace {edad}s: {entrada.get('resumen', '')}")
        return "\n".join(lineas)

    # ---- vídeo SIN audio --------------------------------------------------
    def _track_silent_video(self, change: float, now: float):
        """Detecta vídeo por MOVIMIENTO, sin depender del audio del sistema.

        El modo multimedia se encendía solo cuando el detector acústico oía algo.
        Con un vídeo silenciado (o con el volumen a cero) YUE no se enteraba de
        que había vídeo. Aquí se mantiene una media móvil del cambio visual: si
        se sostiene por encima del umbral, hay vídeo aunque no suene nada.
        """
        umbral = float(_cfg("MEDIA_COMPANION_SILENT_VIDEO_MOTION", 0.12))
        piso = float(_cfg("MEDIA_COMPANION_SILENT_VIDEO_FLOOR", 0.02))
        # Media móvil suave: un cambio puntual (abrir un menú) no cuenta.
        self._silent_video_score = (self._silent_video_score * 0.72) + (float(change) * 0.28)
        # La media sola NO basta: un único cambio brusco la deja alta varios
        # segundos mientras decae, y eso bastaba para declarar "hay vídeo" con la
        # pantalla ya quieta. Exigimos además que el frame ACTUAL siga moviéndose.
        if float(change) < piso:
            self._silent_video_since = 0.0
            return
        if self._silent_video_score >= umbral:
            if not self._silent_video_since:
                self._silent_video_since = now
        else:
            self._silent_video_since = 0.0

    def silent_video_detected(self, min_seconds: float = None,
                              now: float = None) -> bool:
        """True si hay movimiento sostenido compatible con un vídeo silenciado.

        `now` es inyectable para poder probarlo sin depender del reloj real.
        """
        if not self._silent_video_since:
            return False
        minimo = float(min_seconds if min_seconds is not None
                       else _cfg("MEDIA_COMPANION_SILENT_VIDEO_SECONDS", 4.0))
        ahora = float(now if now is not None else time.time())
        return (ahora - self._silent_video_since) >= minimo

    def visual_activity(self) -> dict:
        """Estado visual para diagnóstico y para la capa conversacional."""
        return {
            "movimiento": round(self._silent_video_score, 3),
            "video_silencioso": self.silent_video_detected(),
            "frames_recordados": len(self._frame_memory),
            "ultimo_resumen": (self._frame_memory[-1].get("resumen", "")
                               if self._frame_memory else ""),
        }

    # ---- objetivo emocional ---------------------------------------------
    def _refresh_target(self):
        with self._lock:
            audio = self._audio
            visual = self._visual
            playing = self._playing
        if not playing:
            self._emotion.neutralize()
            return
        target = fuse_emotions(audio, visual, self.music_sensitivity, self.visual_sensitivity)
        self._emotion.set_target(target)

    # ---- comentarios -----------------------------------------------------
    def _maybe_comment(self, emotion: str, intensity: float, levels: dict[str, float]):
        if not self.comments_enabled:
            return
        now = time.time()
        with self._lock:
            audio = self._audio
            visual = self._visual
            speaking = self._speaking
        if speaking or audio is None or audio.silence:
            return
        context = self._safe_context()
        if context.get("pc_busy") or context.get("vision_busy") or context.get("teacher_mode"):
            return

        gap = now - self._last_comment_at
        important = bool(
            (visual and (visual.importance >= 0.76 or visual.scene_change >= 0.78))
            or audio.onset
            or intensity >= 0.82
        )
        # No pisar diálogo. Conservamos la observación como comentario pendiente y
        # esperamos una ventana musical/ambiental o una pausa.
        dialogue = str(getattr(audio, "media_type", "")) == "video_normal" and audio.category in ("narrativa", "suspenso")
        if dialogue:
            if important:
                candidate = self._build_comment(emotion, audio, visual)
                if candidate:
                    self._pending_comment = (candidate, "escena_importante")
            return

        if self._pending_comment and gap >= self.comment_min_gap:
            text, reason = self._pending_comment
            self._pending_comment = None
            self._speak_comment(text, emotion, intensity, reason)
            return

        if gap < self.comment_min_gap:
            return
        # El bucle emocional corre varias veces por segundo, pero la decisión de
        # hablar no. Sin esta compuerta, varias tiradas seguidas volverían casi
        # seguro cualquier probabilidad pequeña.
        if now < self._next_comment_check_at:
            return
        self._next_comment_check_at = now + self._rng.uniform(8.0, 18.0)
        probability = self.spontaneity
        if important:
            probability = min(1.0, probability + 0.38)
        elif gap >= self.comment_max_gap:
            probability = min(0.95, probability + 0.30)
        else:
            probability *= 0.45
        # Usuario recién activo = probablemente está concentrado o interactuando.
        idle = float(context.get("user_idle_seconds", 999.0) or 999.0)
        if idle < 12.0:
            probability *= 0.25
        if visual and visual.visible_text and visual.importance < 0.72:
            probability *= 0.55
        if self._rng.random() > probability:
            return
        candidate = self._build_comment(emotion, audio, visual)
        if candidate:
            self._speak_comment(candidate, emotion, intensity,
                                "escena_importante" if important else "espontaneo")

    def _build_comment(self, emotion: str, audio: MusicAnalysis,
                       visual: VisualAnalysis | None) -> str | None:
        banks: dict[str, list[str]] = {
            "sad": ["Esa parte sí llegó hondo…", "Qué escena tan triste…", "Esta melodía se siente mucho."],
            "love": ["Qué momento tan bonito…", "Esa parte tuvo mucha ternura.", "Qué bonita melodía…"],
            "surprised": ["¡No esperaba eso!", "Vaya giro… eso sí me sorprendió.", "Uy, esa parte me tomó desprevenida."],
            "excited": ["Esto se puso buenísimo.", "Qué energía tiene esta parte.", "Ahora sí se puso emocionante."],
            "happy": ["Me gustó mucho esa parte.", "Esto da gusto escucharlo contigo.", "Qué bonito se siente este momento."],
            "relaxed": ["Qué paz da esta parte…", "Esta melodía se disfruta en silencio.", "Qué agradable ambiente."],
            "curious": ["Esto se está poniendo interesante.", "A ver qué pasa ahora…", "No quiero hacer spoiler, pero esto se está poniendo interesante."],
            "focused": ["Esta parte merece atención.", "Me tiene completamente concentrada.", "Qué intensa se volvió la escena."],
            "worried": ["Esto se está poniendo tenso…", "No me gusta nada cómo pinta esto.", "Qué momento tan inquietante."],
            "proud": ["Eso sí fue impresionante.", "Qué gran momento.", "Esa parte estuvo increíble."],
            "playful": ["Je, esa parte tuvo su gracia.", "Me gustó ese detalle.", "Qué ritmo tan juguetón."],
        }
        candidates = list(banks.get(emotion, ()))
        if visual and visual.summary:
            low = _norm(visual.summary)
            if any(k in low for k in ("paisaje", "montana", "cielo", "mar", "bosque")):
                candidates.insert(0, "Qué hermoso paisaje…")
            if any(k in low for k in ("llora", "llorando", "triste")):
                candidates.insert(0, "Esa escena fue muy triste…")
            if any(k in low for k in ("batalla", "pelea", "explosion")):
                candidates.insert(0, "Esa escena estuvo intensa.")
        if audio.category == "épica":
            candidates.insert(0, "Esta parte suena realmente épica.")
        elif audio.category == "nostálgica":
            candidates.insert(0, "Esta melodía tiene algo muy nostálgico…")
        elif audio.category == "romántica":
            candidates.insert(0, "Qué bonita melodía…")

        if not candidates:
            return None
        start = self._comment_seq % len(candidates)
        self._comment_seq += 1
        for offset in range(len(candidates)):
            candidate = candidates[(start + offset) % len(candidates)]
            if _norm(candidate) not in {_norm(c) for c in self._recent_comments}:
                return candidate
        return None

    def _speak_comment(self, text: str, emotion: str, intensity: float, reason: str):
        self._last_comment_at = time.time()
        self._next_comment_check_at = self._last_comment_at + self.comment_min_gap
        self._recent_comments.append(text)
        gesture = {
            "surprised": "sobresalto", "happy": "asentir", "excited": "asomarse",
            "sad": "suspiro", "love": "ladear", "curious": "pensar",
            "focused": "asomarse", "relaxed": "suspiro", "worried": "encoger",
            "proud": "celebrar", "playful": "reir",
        }.get(emotion)
        try:
            self.reaction_callback(CompanionReaction(
                emotion=emotion,
                intensity=_clamp(max(0.45, intensity)),
                duration_ms=4200,
                comment=text,
                gesture=gesture,
                reason=reason,
            ))
        except Exception:
            pass
        # La UI confirma la entrega mediante mark_comment_delivered().

    def _safe_context(self) -> dict[str, Any]:
        try:
            value = self.context_callback() or {}
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _scan_source(self, force: bool = False) -> MediaSource:
        now = time.time()
        if not force and (now - self._last_source_scan_at) < 3.0:
            return self._source
        self._last_source_scan_at = now
        try:
            return detect_media_source()
        except Exception:
            return self._source

    def mark_comment_delivered(self, text: str, emotion: str, intensity: float):
        """Registra un comentario solo después de que la UI decidió mostrarlo/hablarlo."""
        self._remember_comment(text, emotion, intensity)

    # ---- memoria ---------------------------------------------------------
    def _start_session(self):
        if self.memory is None or self._session_id is not None:
            return
        try:
            self._session_id = self.memory.start_multimedia_session(
                source=self._source.source,
                app=self._source.app,
                title=self._source.title,
                media_type="indefinido",
            )
        except Exception as exc:
            print("[media-companion] no pude iniciar memoria multimedia:", exc)
            self._session_id = None

    def _finish_session(self):
        if self.memory is None or self._session_id is None:
            self._session_id = None
            return
        try:
            levels = self._emotion.snapshot()
            non_neutral = max((e for e in EMOTIONS if e != "neutral"), key=lambda e: levels[e])
            dominant = non_neutral if levels[non_neutral] >= 0.18 else "neutral"
            peak = levels[non_neutral] if dominant != "neutral" else levels.get("neutral", 1.0)
            with self._lock:
                audio = self._audio
                visual = self._visual
            summary = ""
            if visual and visual.summary:
                summary = visual.summary
            elif audio:
                summary = f"Contenido {audio.category}, ambiente {audio.atmosphere}."
            self.memory.end_multimedia_session(
                self._session_id,
                dominant_emotion=dominant,
                peak_intensity=peak,
                summary=summary,
            )
        except Exception as exc:
            print("[media-companion] no pude cerrar memoria multimedia:", exc)
        finally:
            self._session_id = None
            self._session_started = 0.0

    def _remember_source_if_needed(self):
        if self.memory is None or not self.active:
            return
        source = self._scan_source()
        if source.title or source.app:
            self._source = source
        now = time.time()
        title_key = _norm(self._source.title)
        if not title_key or (title_key == self._last_memory_title and now - self._last_memory_at < 45.0):
            return
        self._last_memory_title = title_key
        self._last_memory_at = now
        try:
            with self._lock:
                audio = self._audio
            kind = "video" if (audio and audio.media_type == "video_normal") else "musica"
            self.memory.remember_multimedia_item(
                media_kind=kind,
                title=self._source.title,
                source=self._source.source,
                app=self._source.app,
                emotion=audio.dominant_emotion if audio else "neutral",
                intensity=audio.intensity if audio else 0.0,
                session_id=self._session_id,
            )
            if self._session_id is not None:
                self.memory.update_multimedia_session(
                    self._session_id,
                    source=self._source.source,
                    app=self._source.app,
                    title=self._source.title,
                    media_type=audio.media_type if audio else "indefinido",
                )
        except Exception as exc:
            print("[media-companion] no pude recordar el título:", exc)

    def _remember_visual_event(self, visual: VisualAnalysis):
        if self.memory is None or self._session_id is None:
            return
        scene_key = _norm(visual.summary)
        if not scene_key or scene_key == self._last_scene_key:
            return
        if visual.importance < 0.55 and visual.scene_change < 0.55:
            return
        self._last_scene_key = scene_key
        levels = visual.emotion_weights
        emotion = max(levels, key=levels.get) if levels else "neutral"
        favorite = bool(visual.importance >= 0.80 and emotion in ("happy", "love", "excited", "surprised"))
        try:
            self.memory.add_multimedia_event(
                session_id=self._session_id,
                event_kind="escena",
                label=visual.content_type or "escena",
                emotion=emotion,
                intensity=max(levels.values()) if levels else visual.importance,
                detail=visual.summary,
                favorite=favorite,
            )
        except Exception:
            pass

    def _remember_comment(self, text: str, emotion: str, intensity: float):
        if self.memory is None or self._session_id is None:
            return
        try:
            self.memory.add_multimedia_event(
                session_id=self._session_id,
                event_kind="comentario",
                label="reacción de YUE",
                emotion=emotion,
                intensity=intensity,
                detail="",
                comment=text,
                favorite=False,
            )
        except Exception:
            pass

    # ---- callbacks -------------------------------------------------------
    def _emit_avatar(self, state: AvatarMediaState):
        try:
            self.avatar_callback(state)
        except Exception:
            pass

    def _emit_status(self, text: str, active: bool):
        try:
            self.status_callback(text, bool(active))
        except Exception:
            pass

"""Perfilador de contenido: distingue un VÍDEO NORMAL de un VÍDEO MUSICAL.

`system_audio.py` clasifica cada ventana de ~1 s en silencio/música/voz/ruido.
Eso sirve para reaccionar en directo, pero no dice de qué TIPO de contenido se
trata: una canción suena a música sostenida y con un ritmo estable, mientras que
un vídeo normal (peli, vlog, gameplay, tutorial) mezcla diálogo, ambiente, algún
tramo de música y silencios.

Este módulo acumula las observaciones de los últimos segundos y decide, con
histéresis para no dar tumbos, si lo que se está viendo es:

  - "video_musical"  -> canción / videoclip: música dominante y ritmo estable.
  - "video_normal"   -> peli, vlog, tutorial…: hay diálogo o mezcla variada.
  - "silencio"       -> nada relevante sonando.
  - "indefinido"     -> aún no hay datos suficientes para decidir.

Es puro y determinista (solo listas y medias), igual que el resto del análisis
de audio, así que es fácil de probar y no depende de Qt ni de la tarjeta de
sonido. Es ADITIVO: si algo falla al importarlo, `system_audio.py` sigue
funcionando exactamente como antes.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

import config


def _cfg(nombre: str, defecto):
    return getattr(config, nombre, defecto)


# ---------------------------------------------------------------------------
# Parámetros (con respaldo si no están en config.py / .env)
# ---------------------------------------------------------------------------
WINDOW_SECONDS = float(_cfg("MEDIA_PROFILE_WINDOW", 12.0))     # memoria del perfil
MIN_SAMPLES = int(_cfg("MEDIA_PROFILE_MIN_SAMPLES", 6))        # nº mínimo para decidir
HYSTERESIS = int(_cfg("MEDIA_PROFILE_HYSTERESIS", 3))          # repeticiones para cambiar
SILENCE_DOMINANT = float(_cfg("MEDIA_PROFILE_SILENCE_RATIO", 0.75))
MUSIC_DOMINANT = float(_cfg("MEDIA_PROFILE_MUSIC_RATIO", 0.62))
VOICE_PRESENT = float(_cfg("MEDIA_PROFILE_VOICE_RATIO", 0.30))
TEMPO_CV_STABLE = float(_cfg("MEDIA_PROFILE_TEMPO_CV", 0.18))  # coef. variación del BPM


@dataclass(frozen=True)
class MediaProfile:
    media_type: str            # "video_musical" | "video_normal" | "silencio" | "indefinido"
    confidence: float          # 0..1
    music_ratio: float         # proporción de ventanas con música (sobre las sonoras)
    voice_ratio: float         # proporción con voz/diálogo (sobre las sonoras)
    noise_ratio: float         # proporción con ruido/ambiente (sobre las sonoras)
    silence_ratio: float       # proporción de silencio (sobre el total)
    tempo_bpm: float           # BPM típico si aplica (0 si no)
    tempo_stable: bool         # ¿el ritmo se mantiene estable?
    samples: int               # nº de ventanas consideradas

    def is_music_video(self) -> bool:
        return self.media_type == "video_musical"

    def is_normal_video(self) -> bool:
        return self.media_type == "video_normal"

    def summary_es(self) -> str:
        if self.media_type == "silencio":
            return "Ahora mismo no suena nada en concreto."
        if self.media_type == "video_musical":
            extra = f" (~{int(self.tempo_bpm)} BPM)" if self.tempo_bpm else ""
            estable = " y bien marcado" if self.tempo_stable else ""
            return f"Parece un vídeo musical: la música manda{estable}{extra}."
        if self.media_type == "video_normal":
            return "Parece un vídeo normal: hay diálogo y sonidos variados, no solo música."
        return "Aún no tengo claro qué tipo de contenido es."


class MediaProfiler:
    """Acumula observaciones y entrega un `MediaProfile` estable con histéresis."""

    def __init__(self):
        # Guardamos (timestamp, kind, energetic, tempo_bpm) por ventana.
        self._hist: deque[tuple[float, str, bool, float]] = deque()
        self._committed = "indefinido"
        self._pending = "indefinido"
        self._pending_count = 0
        self._last_profile: MediaProfile | None = None

    # ---- API pública ----
    def update(self, obs, now: float | None = None) -> MediaProfile:
        """Registra una observación (AudioObservation o similar) y recalcula el perfil."""
        now = time.time() if now is None else now
        kind = getattr(obs, "kind", "silencio")
        energetic = bool(getattr(obs, "energetic", False))
        tempo = float(getattr(obs, "tempo_bpm", 0.0) or 0.0)
        self._hist.append((now, kind, energetic, tempo))
        self._prune(now)
        profile = self._evaluate(now)
        self._last_profile = profile
        return profile

    def latest(self) -> MediaProfile | None:
        return self._last_profile

    # ---- interno ----
    def _prune(self, now: float):
        limite = now - WINDOW_SECONDS
        while self._hist and self._hist[0][0] < limite:
            self._hist.popleft()

    def _evaluate(self, now: float) -> MediaProfile:
        total = len(self._hist)
        if total == 0:
            return self._make("indefinido", 0.0, 0, 0, 0, 0, 0.0, False, 0)

        kinds = [k for _, k, _, _ in self._hist]
        n_sil = kinds.count("silencio")
        sound = [(k, e, t) for _, k, e, t in self._hist if k != "silencio"]
        n_sound = len(sound)

        silence_ratio = n_sil / total
        n_music = sum(1 for k, _, _ in sound if k == "musica")
        n_voice = sum(1 for k, _, _ in sound if k == "voz")
        n_noise = sum(1 for k, _, _ in sound if k == "ruido")
        base = max(1, n_sound)
        music_ratio = n_music / base
        voice_ratio = n_voice / base
        noise_ratio = n_noise / base

        tempos = [t for _, _, t in sound if t > 0]
        tempo_bpm, tempo_stable = self._tempo_stats(tempos)

        # Datos insuficientes: mantenemos lo ya comprometido sin arriesgar.
        if total < MIN_SAMPLES:
            return self._make(self._committed, 0.3, music_ratio, voice_ratio,
                              noise_ratio, silence_ratio, tempo_bpm, tempo_stable, total)

        candidato, confianza = self._decide(
            silence_ratio, music_ratio, voice_ratio, noise_ratio, tempo_stable
        )
        estable_final = self._commit(candidato)
        return self._make(estable_final, confianza, music_ratio, voice_ratio,
                          noise_ratio, silence_ratio, tempo_bpm, tempo_stable, total)

    @staticmethod
    def _tempo_stats(tempos: list[float]) -> tuple[float, bool]:
        if len(tempos) < 4:
            media = sum(tempos) / len(tempos) if tempos else 0.0
            return float(media), False
        media = sum(tempos) / len(tempos)
        if media <= 0:
            return 0.0, False
        var = sum((t - media) ** 2 for t in tempos) / len(tempos)
        cv = (var ** 0.5) / media
        return float(media), bool(cv < TEMPO_CV_STABLE)

    @staticmethod
    def _decide(silence_ratio, music_ratio, voice_ratio, noise_ratio, tempo_stable):
        # 1) Predomina el silencio -> no hay contenido que perfilar.
        if silence_ratio >= SILENCE_DOMINANT:
            return "silencio", 0.6

        # 2) Vídeo musical: la música domina y apenas hay diálogo.
        if music_ratio >= MUSIC_DOMINANT and voice_ratio <= 0.25:
            conf = min(1.0, 0.55 + 0.35 * music_ratio + (0.1 if tempo_stable else 0.0))
            return "video_musical", conf

        # 3) Vídeo normal: hay diálogo apreciable, o mezcla variada sin música dominante.
        if voice_ratio >= VOICE_PRESENT or (voice_ratio >= 0.18 and music_ratio < 0.55):
            conf = min(1.0, 0.5 + 0.4 * voice_ratio + 0.2 * noise_ratio)
            return "video_normal", conf

        # 4) Casi todo música pero con algo de voz/ruido: sigue siendo musical, con menos confianza.
        if music_ratio >= 0.45 and voice_ratio < 0.18:
            return "video_musical", min(1.0, 0.45 + 0.3 * music_ratio)

        # 5) Mezcla ambigua -> lo tratamos como vídeo normal (lo más común).
        return "video_normal", 0.4

    def _commit(self, candidato: str) -> str:
        """Histéresis: solo cambia el tipo si el candidato se repite HYSTERESIS veces."""
        if candidato == self._committed:
            self._pending = candidato
            self._pending_count = 0
            return self._committed
        if candidato == self._pending:
            self._pending_count += 1
        else:
            self._pending = candidato
            self._pending_count = 1
        if self._pending_count >= HYSTERESIS:
            self._committed = candidato
            self._pending_count = 0
        return self._committed

    @staticmethod
    def _make(media_type, confidence, music_ratio, voice_ratio, noise_ratio,
              silence_ratio, tempo_bpm, tempo_stable, samples) -> MediaProfile:
        return MediaProfile(
            media_type=media_type,
            confidence=float(round(confidence, 3)),
            music_ratio=float(round(music_ratio, 3)),
            voice_ratio=float(round(voice_ratio, 3)),
            noise_ratio=float(round(noise_ratio, 3)),
            silence_ratio=float(round(silence_ratio, 3)),
            tempo_bpm=float(round(tempo_bpm, 1)),
            tempo_stable=bool(tempo_stable),
            samples=int(samples),
        )

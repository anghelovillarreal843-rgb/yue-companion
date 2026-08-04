"""Perfiles de rendimiento por hardware (gama baja / media / alta).

Traduce el equipo del usuario a parámetros concretos de la visión: a qué FPS
capturar y —lo más caro— cada cuántos segundos correr DeepFace. Cumple el punto
8 del pedido:

    LOW    -> DeepFace cada 8 s, captura 10-15 FPS
    MEDIUM -> DeepFace cada 5 s, captura 20 FPS
    HIGH   -> DeepFace cada 2 s, captura 30 FPS

El perfil se elige por config (`VISION_PERF_PROFILE`). Si vale "auto", se estima
con psutil (núcleos + RAM). Todo es degradable: si psutil no está, cae a MEDIUM.
Es puro y determinista salvo la detección automática, así que se prueba fácil.
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    import config  # type: ignore
except Exception:  # pragma: no cover - permite importar el módulo suelto en tests
    config = None  # type: ignore


def _cfg(name: str, default):
    return getattr(config, name, default) if config is not None else default


@dataclass(frozen=True)
class PerfProfile:
    name: str                 # "LOW" | "MEDIUM" | "HIGH"
    target_fps: float         # FPS de captura objetivo
    emotion_interval: float   # cada cuántos segundos corre DeepFace
    capture_width: int        # ancho de captura
    capture_height: int       # alto de captura
    analysis_scale: float     # reescalado del recorte facial antes de DeepFace (0..1)
    run_emotion: bool         # ¿se ejecuta el motor de emociones?

    def frame_pause(self) -> float:
        """Pausa entre fotogramas (segundos) para acercarse a `target_fps`."""
        return 1.0 / max(1.0, self.target_fps)


# Perfiles base EXACTOS según el documento. Los valores de captura/escala son
# conservadores para no ahogar equipos modestos.
_PROFILES = {
    "LOW": PerfProfile(
        name="LOW", target_fps=12.0, emotion_interval=8.0,
        capture_width=480, capture_height=360, analysis_scale=0.6, run_emotion=True,
    ),
    "MEDIUM": PerfProfile(
        name="MEDIUM", target_fps=20.0, emotion_interval=5.0,
        capture_width=640, capture_height=480, analysis_scale=0.8, run_emotion=True,
    ),
    "HIGH": PerfProfile(
        name="HIGH", target_fps=30.0, emotion_interval=2.0,
        capture_width=1280, capture_height=720, analysis_scale=1.0, run_emotion=True,
    ),
}


def _auto_detect() -> str:
    """Estima la gama del equipo. Sin psutil o ante cualquier duda -> MEDIUM."""
    try:
        import os
        import psutil  # type: ignore

        ram_gb = psutil.virtual_memory().total / (1024 ** 3)
        cores = psutil.cpu_count(logical=False) or os.cpu_count() or 2

        if ram_gb <= 8.5 or cores <= 2:
            return "LOW"
        if ram_gb >= 15.5 and cores >= 6:
            return "HIGH"
        return "MEDIUM"
    except Exception:
        return "MEDIUM"


def resolve_profile(override: str | None = None) -> PerfProfile:
    """Devuelve el `PerfProfile` a usar.

    Prioridad: `override` explícito > config `VISION_PERF_PROFILE` > "auto".
    Permite además ajustar la cadencia de DeepFace por config sin cambiar de
    perfil (`VISION_EMOTION_INTERVAL`), útil para afinar en un equipo concreto.
    """
    choice = (override or str(_cfg("VISION_PERF_PROFILE", "auto"))).strip().upper()
    if choice not in _PROFILES:
        choice = _auto_detect() if choice in ("", "AUTO") else "MEDIUM"

    base = _PROFILES[choice]

    # Overrides finos opcionales (0 o vacío = no tocar). No cambian el nombre.
    interval = _cfg("VISION_EMOTION_INTERVAL", 0.0)
    fps = _cfg("VISION_TARGET_FPS", 0.0)
    try:
        interval = float(interval or 0.0)
        fps = float(fps or 0.0)
    except (TypeError, ValueError):
        interval = fps = 0.0

    if interval > 0 or fps > 0:
        base = PerfProfile(
            name=base.name,
            target_fps=fps if fps > 0 else base.target_fps,
            emotion_interval=interval if interval > 0 else base.emotion_interval,
            capture_width=base.capture_width,
            capture_height=base.capture_height,
            analysis_scale=base.analysis_scale,
            run_emotion=base.run_emotion,
        )
    return base


def describe(profile: PerfProfile) -> str:
    return (
        f"Perfil {profile.name}: {profile.target_fps:.0f} FPS de captura, "
        f"emociones cada {profile.emotion_interval:.0f} s, "
        f"captura {profile.capture_width}x{profile.capture_height}."
    )

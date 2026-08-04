"""Construye el video del recuerdo con FFmpeg.

Toma la imagen generada por FLUX y la convierte en un video con:
  - efecto Ken Burns (acercamiento/paneo lento),
  - fundido de entrada y salida,
  - una sutil viñeta cinematografica,
  - musica de fondo (la que eligio music_gen segun la emocion),
  - y, si hay una fuente disponible, un pie de pagina con fecha y emocion.

Funcion principal:  make(image, audio, out_mp4, caption=None) -> ruta del mp4.
"""
import glob
import os
import subprocess

import config
from core import activity


def make(image_path, audio_path, out_path, caption=None):
    dur = _audio_duration(audio_path)
    if not dur or dur <= 0:
        dur = config.VIDEO_SECONDS
    dur = max(6.0, min(45.0, float(dur)))
    fps = config.VIDEO_FPS
    frames = int(dur * fps)

    w = _even(config.IMAGE_WIDTH)
    h = _even(config.IMAGE_HEIGHT)

    # Ken Burns: ampliamos primero para tener margen y luego hacemos zoom lento.
    chain = (
        f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,"
        f"crop={w*2}:{h*2},"
        f"zoompan=z='min(zoom+0.0007,1.30)':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={w}x{h}:fps={fps},"
        f"vignette=PI/5,"
        f"fade=t=in:st=0:d=1.2,fade=t=out:st={dur-1.4:.2f}:d=1.4"
    )

    cap = _drawtext(caption, w, h)
    if cap:
        chain += "," + cap

    chain += "[v]"

    cmd = [
        config.FFMPEG_BIN, "-y",
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-filter_complex", f"[0:v]{chain}",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{dur:.2f}", "-r", str(fps),
        "-shortest",
        str(out_path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        # Reintento sin pie de pagina (por si la fuente dio problemas)
        if cap:
            return _make_no_caption(image_path, audio_path, out_path, dur, fps, w, h)
        raise RuntimeError(
            "FFmpeg falló al crear el video. ¿Está instalado ffmpeg?\n"
            + proc.stderr.decode("utf-8", "ignore")[-600:]
        )
    activity.log("video", "armó un video", origen="video_gen")
    return str(out_path)


def _make_no_caption(image_path, audio_path, out_path, dur, fps, w, h):
    chain = (
        f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,"
        f"crop={w*2}:{h*2},"
        f"zoompan=z='min(zoom+0.0007,1.30)':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={int(dur*fps)}:s={w}x{h}:fps={fps},"
        f"vignette=PI/5,"
        f"fade=t=in:st=0:d=1.2,fade=t=out:st={dur-1.4:.2f}:d=1.4[v]"
    )
    cmd = [
        config.FFMPEG_BIN, "-y",
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-filter_complex", f"[0:v]{chain}",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{dur:.2f}", "-r", str(fps), "-shortest",
        str(out_path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(
            "FFmpeg falló al crear el video. ¿Está instalado ffmpeg?\n"
            + proc.stderr.decode("utf-8", "ignore")[-600:]
        )
    activity.log("video", "armó un video", origen="video_gen")
    return str(out_path)


def _drawtext(caption, w, h):
    """Genera el filtro drawtext si encontramos una fuente del sistema."""
    if not caption:
        return None
    font = _find_font()
    if not font:
        return None
    safe = _escape(caption)
    fontfile = font.replace("\\", "/").replace(":", "\\:")
    fsize = max(20, w // 26)
    return (
        f"drawtext=fontfile='{fontfile}':text='{safe}':"
        f"fontcolor=white@0.92:fontsize={fsize}:"
        f"box=1:boxcolor=black@0.45:boxborderw=18:"
        f"x=(w-text_w)/2:y=h-(text_h)-{max(28, h//22)}"
    )


def _escape(text):
    text = (text or "").replace("\n", " ").strip()
    if len(text) > 70:
        text = text[:67] + "…"
    # caracteres especiales para drawtext
    for a, b in (("\\", "\\\\"), (":", "\\:"), ("'", "\u2019"),
                 ("%", "\\%"), ('"', "")):
        text = text.replace(a, b)
    return text


_FONT_CACHE = "__unset__"


def _find_font():
    global _FONT_CACHE
    if _FONT_CACHE != "__unset__":
        return _FONT_CACHE
    candidates = [
        # Windows
        "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        # macOS
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            _FONT_CACHE = c
            return c
    # busqueda generica en Linux
    for pat in ("/usr/share/fonts/**/*.ttf", "/usr/share/fonts/**/*.otf"):
        hits = glob.glob(pat, recursive=True)
        if hits:
            _FONT_CACHE = hits[0]
            return hits[0]
    _FONT_CACHE = None
    return None


def _even(x):
    x = int(x)
    return x if x % 2 == 0 else x + 1


def _audio_duration(path):
    try:
        out = subprocess.run(
            [config.FFPROBE_BIN, "-v", "error", "-show_entries",
             "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return float(out.stdout.decode().strip())
    except Exception:
        return None

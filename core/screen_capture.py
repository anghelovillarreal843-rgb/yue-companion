"""Captura de pantalla para que Yue 'vea' lo que haces.

Devuelve la imagen como JPEG en base64, reducida para no pasar el límite de
4 MB de Groq ni gastar demasiados tokens.
"""
import base64
import io


def capture_b64(max_width=1280, quality=70) -> str:
    img = _grab()
    w, h = img.size
    if w > max_width:
        img = img.resize((max_width, int(h * max_width / w)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _grab():
    """Devuelve la pantalla principal como imagen PIL RGB."""
    errores = []
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            shot = sct.grab(mon)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception as exc:
        errores.append(f"mss: {exc}")
    # respaldo (Windows / macOS)
    try:
        from PIL import ImageGrab
        return ImageGrab.grab().convert("RGB")
    except Exception as exc:
        errores.append(f"PIL.ImageGrab: {exc}")
    # Si llegamos aquí, ningún método de captura funcionó: mensaje claro.
    raise RuntimeError(
        "No pude capturar la pantalla. Prueba: pip install mss pillow. "
        "Detalle -> " + " | ".join(errores)
    )


def capture_info(max_width=1280, quality=70) -> dict:
    """Autodiagnóstico de la CAPTURA (sin llamar al modelo).

    Devuelve {ok, width, height, bytes, note}. No lanza excepción: informa.
    """
    try:
        img = _grab()
        w, h = img.size
        if w > max_width:
            img = img.resize((max_width, int(h * max_width / w)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        raw = buf.getvalue()
        return {"ok": True, "width": img.size[0], "height": img.size[1],
                "bytes": len(raw), "note": "captura correcta"}
    except Exception as exc:
        return {"ok": False, "width": 0, "height": 0, "bytes": 0, "note": str(exc)}

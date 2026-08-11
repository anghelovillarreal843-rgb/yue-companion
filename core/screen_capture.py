"""Captura de pantalla para que Yue 'vea' lo que haces.

Devuelve la imagen como JPEG en base64, reducida para no pasar el limite de
4 MB de los proveedores ni gastar demasiados tokens.

AMPLIADO (aditivo, sin romper la API anterior):
  * `_grab()`, `capture_b64()` y `capture_info()` siguen existiendo con la MISMA
    firma que antes (solo se les anadio el parametro opcional `monitor`).
  * Multi-monitor: `list_monitors()`, `capture_b64(monitor=N)`, `grab(monitor=N)`.
  * `grab_frame()` devuelve un `ScreenFrame` con `capture_timestamp`,
    `frame_age`, `monitor`, `width`, `height` y validaciones (negro / vacio /
    dimensiones).
  * Nunca se reutiliza una captura vieja sin querer: el frame lleva su edad y
    quien lo consume decide si le sirve (`ScreenFrame.is_fresh(limite)`).
"""
import base64
import io
import time

# Ultima captura hecha (solo para diagnostico y para poder medir edades).
_ultimo_frame = None


class CaptureError(RuntimeError):
    """No se pudo capturar la pantalla por ningun metodo."""


class ScreenFrame:
    """Una captura con su metadata. `image` es un PIL.Image RGB."""

    def __init__(self, image, monitor: int = 1, method: str = "",
                 timestamp: float | None = None, monitors_total: int = 1):
        self.image = image
        self.monitor = int(monitor)
        self.method = method
        self.capture_timestamp = float(timestamp if timestamp is not None else time.time())
        self.monitors_total = int(monitors_total)
        try:
            self.width, self.height = image.size
        except Exception:
            self.width = self.height = 0

    # ---- edad ------------------------------------------------------------
    @property
    def frame_age(self) -> float:
        """Segundos transcurridos desde que se tomo la captura."""
        return max(0.0, time.time() - self.capture_timestamp)

    def is_fresh(self, max_age: float = 2.0) -> bool:
        return self.frame_age <= float(max_age)

    # ---- validaciones ----------------------------------------------------
    def is_empty(self) -> bool:
        """Sin imagen o con dimensiones imposibles."""
        return self.image is None or self.width < 16 or self.height < 16

    def brightness(self) -> float:
        """Brillo medio 0..255 sobre una miniatura (barato)."""
        try:
            small = self.image.convert("L").resize((32, 18))
            datos = list(small.getdata())
            return sum(datos) / max(1, len(datos))
        except Exception:
            return -1.0

    def is_black(self, umbral: float = 3.0) -> bool:
        """True si la captura es practicamente negra (fallo tipico de DRM/GPU)."""
        brillo = self.brightness()
        if brillo < 0 or brillo > umbral:
            return False
        # Confirmacion: ademas de oscura, casi sin variacion. Una foto nocturna
        # real si tiene variacion y no debe marcarse como captura fallida.
        try:
            small = self.image.convert("L").resize((32, 18))
            datos = list(small.getdata())
            return (max(datos) - min(datos)) <= 4
        except Exception:
            return True

    def validate(self, max_age: float | None = None) -> tuple[bool, str]:
        """(ok, motivo). Motivo vacio si la captura sirve."""
        if self.is_empty():
            return False, "captura vacia o de dimensiones invalidas"
        if self.is_black():
            return False, "captura completamente negra (proteccion de contenido o GPU)"
        if max_age is not None and not self.is_fresh(max_age):
            return False, f"captura antigua ({self.frame_age:.2f}s > {float(max_age):.2f}s)"
        return True, ""

    # ---- salida ----------------------------------------------------------
    def to_b64(self, max_width: int = 1280, quality: int = 70) -> str:
        img = self.image
        w, h = img.size
        if w > max_width:
            img = img.resize((max_width, max(1, int(h * max_width / w))))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def as_dict(self) -> dict:
        return {
            "monitor": self.monitor,
            "monitors_total": self.monitors_total,
            "width": self.width,
            "height": self.height,
            "capture_timestamp": round(self.capture_timestamp, 3),
            "frame_age": round(self.frame_age, 3),
            "method": self.method,
        }


# --------------------------------------------------------------- monitores
def list_monitors() -> list:
    """Monitores disponibles: [{index, width, height, left, top, primary}].

    El indice 1 es el primero real (mss reserva el 0 para "todos juntos").
    Si mss no esta, devuelve un unico monitor deducido de PIL.
    """
    try:
        import mss
        with mss.mss() as sct:
            salida = []
            for i, mon in enumerate(sct.monitors):
                if i == 0:
                    continue  # el 0 es el "escritorio virtual" completo
                salida.append({
                    "index": i,
                    "width": int(mon.get("width", 0)),
                    "height": int(mon.get("height", 0)),
                    "left": int(mon.get("left", 0)),
                    "top": int(mon.get("top", 0)),
                    "primary": i == 1,
                })
            if salida:
                return salida
    except Exception:
        pass
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        return [{"index": 1, "width": img.size[0], "height": img.size[1],
                 "left": 0, "top": 0, "primary": True}]
    except Exception:
        return []


def monitor_count() -> int:
    return len(list_monitors())


# ----------------------------------------------------------------- captura
def grab_frame(monitor: int = 1) -> ScreenFrame:
    """Captura el monitor indicado y devuelve un ScreenFrame con metadata.

    Cadena: mss (monitor concreto) -> mss (monitor 1) -> PIL.ImageGrab.
    Lanza CaptureError solo si NINGUN metodo funciono.
    """
    global _ultimo_frame
    errores = []
    monitor = max(1, int(monitor or 1))
    total = 1

    # 1) mss con el monitor pedido.
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            monitores = sct.monitors
            total = max(1, len(monitores) - 1)
            if monitor >= len(monitores) and len(monitores) > 1:
                errores.append(
                    f"mss: el monitor {monitor} no existe (hay {total}); uso el 1")
                monitor = 1
            indice = monitor if len(monitores) > 1 else 0
            mon = monitores[indice]
            ts = time.time()
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            frame = ScreenFrame(img, monitor=monitor, method="mss",
                                timestamp=ts, monitors_total=total)
            if not frame.is_empty():
                _ultimo_frame = frame
                return frame
            errores.append("mss: devolvio una imagen vacia")
    except Exception as exc:
        errores.append(f"mss: {exc}")

    # 2) Respaldo PIL (Windows / macOS). Solo puede dar la pantalla principal.
    try:
        from PIL import ImageGrab
        ts = time.time()
        img = ImageGrab.grab().convert("RGB")
        frame = ScreenFrame(img, monitor=1, method="PIL.ImageGrab",
                            timestamp=ts, monitors_total=total)
        if not frame.is_empty():
            _ultimo_frame = frame
            return frame
        errores.append("PIL.ImageGrab: imagen vacia")
    except Exception as exc:
        errores.append(f"PIL.ImageGrab: {exc}")

    raise CaptureError(
        "No pude capturar la pantalla. Prueba: pip install mss pillow. "
        "Detalle -> " + " | ".join(errores)
    )


def grab(monitor: int = 1):
    """Imagen PIL RGB del monitor indicado (compatibilidad comoda)."""
    return grab_frame(monitor=monitor).image


def _grab():
    """COMPATIBILIDAD: pantalla principal como imagen PIL RGB.

    Se mantiene tal cual porque lo usan `core/media_companion.py`,
    `core/ai_engine.py` y varios diagnosticos.
    """
    try:
        return grab_frame(monitor=1).image
    except CaptureError as exc:
        # Mismo tipo de error que antes para no romper a quien lo captura.
        raise RuntimeError(str(exc)) from exc


def last_frame():
    """Ultimo frame capturado (con su edad). Nunca fuerza una captura nueva."""
    return _ultimo_frame


def capture_b64(max_width=1280, quality=70, monitor: int = 1) -> str:
    """JPEG en base64 del monitor indicado. Firma compatible con la anterior."""
    return grab_frame(monitor=monitor).to_b64(max_width=max_width, quality=quality)


def capture_info(max_width=1280, quality=70, monitor: int = 1) -> dict:
    """Autodiagnostico de la CAPTURA (sin llamar al modelo).

    Devuelve {ok, width, height, bytes, note, monitor, monitors, frame_age,
    capture_timestamp, method}. No lanza excepcion: informa.
    """
    try:
        frame = grab_frame(monitor=monitor)
        ok, motivo = frame.validate()
        raw = base64.b64decode(frame.to_b64(max_width=max_width, quality=quality))
        return {
            "ok": bool(ok),
            "width": frame.width,
            "height": frame.height,
            "bytes": len(raw),
            "note": motivo or "captura correcta",
            "monitor": frame.monitor,
            "monitors": frame.monitors_total,
            "frame_age": round(frame.frame_age, 3),
            "capture_timestamp": round(frame.capture_timestamp, 3),
            "method": frame.method,
        }
    except Exception as exc:
        return {"ok": False, "width": 0, "height": 0, "bytes": 0, "note": str(exc),
                "monitor": monitor, "monitors": 0, "frame_age": 0.0,
                "capture_timestamp": 0.0, "method": ""}

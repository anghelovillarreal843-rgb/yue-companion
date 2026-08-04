"""Motor de cámara del sistema de percepción V3.

Responsabilidades (punto 1 del pedido):
  - detectar una webcam disponible,
  - iniciar la captura en un hilo propio,
  - entregar cada fotograma a un callback (procesamiento en memoria),
  - liberar los recursos correctamente,
  - manejar errores y reconectar sola si la cámara desaparece.

FPS dinámico según el perfil de hardware (LOW/MEDIUM/HIGH).

PRIVACIDAD (punto 9): los fotogramas se procesan en memoria y JAMÁS se guardan
en disco ni se envían fuera. Este módulo no tiene ninguna ruta de escritura de
imágenes a propósito. `CAMERA_PRIVACY_MODE` deja constancia explícita de ello.

DISEÑO ADITIVO / SIN CONFLICTOS: por defecto abre la cámara con OpenCV igual que
el observador clásico, pero como el V3 está APAGADO por defecto, no compite con
`core/camera_observer.py`. Para pruebas se puede inyectar `capture_factory`, así
todo el motor se ejercita sin webcam real.
"""
from __future__ import annotations

import platform
import threading
import time
from typing import Callable

try:
    import config  # type: ignore
except Exception:  # pragma: no cover
    config = None  # type: ignore

from core.vision.perf_profile import PerfProfile, resolve_profile


def _cfg(name: str, default):
    return getattr(config, name, default) if config is not None else default


def _default_capture_factory(profile: PerfProfile):
    """Abre la primera webcam disponible con OpenCV. Devuelve (cap, index) o (None, -1).

    Reproduce la estrategia robusta del observador clásico: en Windows prueba
    primero DSHOW; fija ancho/alto del perfil y buffer 1 para baja latencia.
    """
    try:
        import cv2
    except Exception as exc:  # pragma: no cover - sin OpenCV no hay cámara
        print("[vision.camera] falta opencv-python:", exc)
        return None, -1

    index_pref = int(_cfg("CAMERA_INDEX", _cfg("VISION_CAMERA_INDEX", 0)))
    max_index = max(index_pref, int(_cfg("CAMERA_MAX_INDEX", 3)))
    width = int(profile.capture_width)
    height = int(profile.capture_height)

    backends = []
    if platform.system().lower() == "windows" and hasattr(cv2, "CAP_DSHOW"):
        backends.append(cv2.CAP_DSHOW)
    backends.append(getattr(cv2, "CAP_ANY", 0))

    # Probamos primero el índice preferido y luego el resto.
    order = [index_pref] + [i for i in range(max_index + 1) if i != index_pref]
    for index in order:
        for backend in backends:
            cap = None
            try:
                cap = cv2.VideoCapture(index, backend)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                ok, frame = cap.read()
                if cap.isOpened() and ok and frame is not None:
                    return cap, index
                cap.release()
            except Exception:
                try:
                    if cap is not None:
                        cap.release()
                except Exception:
                    pass
    return None, -1


class CameraEngine:
    """Hilo de captura con estado consultable y reconexión automática."""

    def __init__(
        self,
        frame_callback: Callable[[object, int], None],
        status_callback: Callable[[str, bool], None] | None = None,
        profile: PerfProfile | None = None,
        capture_factory: Callable[[PerfProfile], tuple] | None = None,
    ) -> None:
        self.frame_callback = frame_callback
        self.status_callback = status_callback or (lambda _t, _a: None)
        self.profile = profile or resolve_profile()
        # Inyectable para pruebas; por defecto abre OpenCV real.
        self._factory = capture_factory or _default_capture_factory
        self.privacy_mode = bool(_cfg("CAMERA_PRIVACY_MODE", True))

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active = False
        self._camera_index = -1
        self._search_interval = max(0.5, float(_cfg("CAMERA_SEARCH_INTERVAL", 3.0)))

    @property
    def active(self) -> bool:
        return self._active

    @property
    def camera_index(self) -> int:
        return self._camera_index

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="YueVisionCamera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.5)
        self._active = False

    def _emit_status(self, text: str, active: bool) -> None:
        self._active = bool(active)
        try:
            self.status_callback(text, bool(active))
        except Exception:
            pass

    def _run(self) -> None:
        # Aviso claro de privacidad la primera vez (sin frames a disco jamás).
        if self.privacy_mode:
            print("[vision.camera] modo privado: análisis local, sin guardar ni enviar imágenes.")

        pause = self.profile.frame_pause()
        while not self._stop.is_set():
            cap, index = self._safe_open()
            if cap is None:
                self._emit_status(
                    "Busco una cámara disponible… (se conectará sola en cuanto haya una).",
                    False,
                )
                self._stop.wait(self._search_interval)
                continue

            self._camera_index = index
            self._emit_status(
                f"Cámara {index} activa ({self.profile.name}); análisis local, sin guardar imágenes.",
                True,
            )
            failures = 0
            try:
                while not self._stop.is_set():
                    ok, frame = self._safe_read(cap)
                    if not ok or frame is None:
                        failures += 1
                        if failures >= 5:
                            break
                        self._stop.wait(0.25)
                        continue
                    failures = 0
                    try:
                        self.frame_callback(frame, index)
                    except Exception as exc:  # pragma: no cover - defensivo
                        print("[vision.camera] callback de fotograma falló:", exc)
                    self._stop.wait(pause)
            finally:
                self._safe_release(cap)
                self._emit_status("La cámara se desconectó; volveré a buscarla.", False)

            if not self._stop.is_set():
                self._stop.wait(self._search_interval)

        self._emit_status("Cámara detenida.", False)

    # -- envoltorios defensivos: nunca dejan que un fallo tumbe el hilo --
    def _safe_open(self):
        try:
            return self._factory(self.profile)
        except Exception as exc:
            print("[vision.camera] no pude abrir la cámara:", exc)
            return None, -1

    @staticmethod
    def _safe_read(cap):
        try:
            return cap.read()
        except Exception:
            return False, None

    @staticmethod
    def _safe_release(cap):
        try:
            cap.release()
        except Exception:
            pass

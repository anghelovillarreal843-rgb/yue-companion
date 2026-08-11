"""Servicio ÚNICO de cámara (paso 2 del pedido).

Regla de oro de la arquitectura: existe UNA sola `cv2.VideoCapture`. Ningún
detector abre la webcam por su cuenta; todos leen el último fotograma desde el
`FrameHub` que alimenta este servicio.

Características:
  - apertura con OpenCV (DSHOW en Windows) por índice configurable,
  - captura en un hilo propio (no bloquea la interfaz),
  - reconexión automática si la cámara desaparece,
  - bloqueo seguro entre hilos y conservación del último fotograma válido,
  - conversión BGR->RGB perezosa (solo cuando alguien pide el RGB),
  - control de FPS objetivo y redimensionado configurable,
  - liberación correcta al cerrar YUE.

PRIVACIDAD: los fotogramas viven en memoria; este módulo no tiene ninguna ruta
de escritura a disco a propósito.

Para pruebas sin webcam se inyecta `capture_factory`, que devuelve un objeto con
la interfaz mínima de `cv2.VideoCapture` (isOpened/read/release/set).
"""
from __future__ import annotations

import logging
import platform
import threading
import time
from typing import Callable, Optional

from vision.frame_hub import FrameHub

log = logging.getLogger("vision.camera")


def _default_capture_factory(index: int, width: int, height: int):
    """Abre la webcam real probando y VALIDANDO backends.

    CORRECCIÓN: antes se daba la cámara por buena tras UNA sola lectura, no se
    probaba MSMF (el backend nativo de Windows 10/11) y se aplicaba BUFFERSIZE
    sobre DSHOW. Resultado en Windows: la cámara entregaba un fotograma y se
    quedaba muda. Ahora `CaptureOpener` exige varias lecturas consecutivas y
    recuerda el backend que de verdad funciona. Ver vision/camera_backend.py.
    """
    from vision.camera_backend import CaptureOpener
    return CaptureOpener().open(index, width, height)


class CameraService:
    def __init__(
        self,
        frame_hub: FrameHub,
        index: int = 0,
        width: int = 640,
        height: int = 480,
        target_fps: float = 15.0,
        privacy_mode: bool = True,
        status_callback: Optional[Callable[[str, bool], None]] = None,
        capture_factory: Optional[Callable[[int, int, int], object]] = None,
        reconnect_interval: float = 3.0,
        read_timeout: float = 2.5,
    ) -> None:
        self.hub = frame_hub
        self.index = int(index)
        self.width = int(width)
        self.height = int(height)
        self.target_fps = max(1.0, float(target_fps))
        self.privacy_mode = bool(privacy_mode)
        self._status_cb = status_callback or (lambda _t, _a: None)
        # Sin fábrica inyectada se usa el abridor con validación de backend, que
        # además recuerda cuál funciona y se puede avisar si deja de servir.
        if capture_factory is None:
            from vision.camera_backend import CaptureOpener
            self._factory = CaptureOpener()
        else:
            self._factory = capture_factory
        self._reconnect = max(0.5, float(reconnect_interval))
        self._read_timeout = max(0.5, float(read_timeout))

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False

    # -- API pública ----------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="YueCameraService", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.5)
        self._running = False
        self._emit("Cámara detenida.", False)

    def is_running(self) -> bool:
        return self._running

    def get_latest_frame(self):
        """Último fotograma BGR (sin copia). None si aún no hay."""
        return self.hub.latest_bgr()

    def get_latest_rgb_frame(self):
        """Último fotograma RGB (conversión perezosa y cacheada)."""
        return self.hub.latest_rgb()

    def get_frame_timestamp(self) -> float:
        return self.hub.timestamp()

    # -- interno --------------------------------------------------------
    def _emit(self, text: str, active: bool) -> None:
        self._running = bool(active)
        try:
            self._status_cb(text, bool(active))
        except Exception:
            pass

    def _run(self) -> None:
        if self.privacy_mode:
            log.info("Modo privado: análisis local, sin guardar ni enviar imágenes.")
        pause = 1.0 / self.target_fps

        while not self._stop.is_set():
            cap = self._safe_open()
            if cap is None:
                self._emit("Busco una cámara disponible…", False)
                self._stop.wait(self._reconnect)
                continue

            self._emit(f"Cámara {self.index} activa (análisis local, sin guardar imágenes).", True)
            log.info("Cámara %s abierta (%sx%s @ %.0f FPS objetivo).",
                     self.index, self.width, self.height, self.target_fps)
            # CORRECCIÓN: la tolerancia a fallos es TEMPORAL, no por contador.
            # Un contador de 5 fallos con esperas de 0,2 s tumbaba la cámara en
            # un segundo ante cualquier microcorte, y provocaba un ciclo de
            # reconexión de 3 s en el que apenas llegaba un fotograma.
            frames_ok = 0
            primer_fallo = 0.0
            try:
                while not self._stop.is_set():
                    inicio = time.monotonic()
                    ok, frame = self._safe_read(cap)
                    if not ok or frame is None:
                        ahora = time.monotonic()
                        if primer_fallo == 0.0:
                            primer_fallo = ahora
                        elif (ahora - primer_fallo) >= self._read_timeout:
                            log.warning(
                                "La cámara %s lleva %.1f s sin entregar fotogramas "
                                "(%d recibidos); reconecto.",
                                self.index, ahora - primer_fallo, frames_ok)
                            # Si murió casi sin dar nada, el backend es el
                            # culpable: que el abridor pruebe otro distinto.
                            if frames_ok < 5:
                                self._report_backend_failure()
                            break
                        self._stop.wait(0.05)
                        continue
                    primer_fallo = 0.0
                    frames_ok += 1
                    self.hub.publish(frame)
                    # CORRECCIÓN: antes se esperaba el periodo COMPLETO después
                    # de leer, así que el ritmo real era 1/(lectura + periodo) y
                    # nunca se llegaba al objetivo. Con una webcam que tarda
                    # ~60 ms por fotograma y un objetivo de 15 FPS salían ~8 FPS
                    # reales: la mitad. Ahora se descuenta lo que costó la
                    # lectura, que es lo que hace un bucle a ritmo fijo.
                    restante = pause - (time.monotonic() - inicio)
                    if restante > 0:
                        self._stop.wait(restante)
            finally:
                self._safe_release(cap)
                self._emit("La cámara se desconectó; volveré a buscarla.", False)

            if not self._stop.is_set():
                self._stop.wait(self._reconnect)

    def _report_backend_failure(self) -> None:
        """Avisa al abridor de que su backend preferido dejó de servir."""
        reporter = getattr(self._factory, "report_failure", None)
        if callable(reporter):
            try:
                reporter()
            except Exception:
                pass

    def _safe_open(self):
        try:
            return self._factory(self.index, self.width, self.height)
        except Exception as exc:
            log.error("No pude abrir la cámara %s: %s", self.index, exc)
            return None

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

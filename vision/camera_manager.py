"""Administrador ÚNICO de cámara (punto 4 del pedido).

`CameraService` ya abre una sola `cv2.VideoCapture` dentro del paquete `vision/`.
Lo que faltaba —y es lo que añade este módulo— es impedir que DOS SISTEMAS
DISTINTOS de YUE (el `CameraObserver` clásico, `core.vision` V3 y este paquete)
abran la MISMA webcam a la vez, que es la causa típica de fotogramas negros o
de que la cámara "no responda".

Aporta:

  - un cerrojo global por índice de cámara (`acquire` / `release`), de modo que
    el segundo sistema que lo intente reciba un "no" claro en vez de pelear,
  - búsqueda automática de cámaras disponibles (`scan`),
  - configuración de índice, resolución y FPS desde el .env,
  - reconexión automática (la hereda de `CameraService`),
  - encendido y apagado inmediatos, con liberación correcta al cerrar,
  - estado consultable: activa, índice, FPS reales, último error.

Todo error se registra sin cerrar la aplicación.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from vision.camera_service import CameraService
from vision.frame_hub import FrameHub

log = logging.getLogger("vision.camera_manager")

# Cerrojo de proceso: qué índice de cámara tiene tomado quién.
_GLOBAL_LOCK = threading.RLock()
_OWNERS: dict[int, str] = {}


def acquire_camera(index: int, owner: str) -> bool:
    """Reserva la webcam `index` para `owner`. False si ya la tiene otro."""
    with _GLOBAL_LOCK:
        current = _OWNERS.get(int(index))
        if current is not None and current != owner:
            log.warning("La cámara %s ya está en uso por '%s'; '%s' no la abrirá.",
                        index, current, owner)
            return False
        _OWNERS[int(index)] = owner
        return True


def release_camera(index: int, owner: str) -> None:
    with _GLOBAL_LOCK:
        if _OWNERS.get(int(index)) == owner:
            _OWNERS.pop(int(index), None)


def camera_owner(index: int) -> str | None:
    with _GLOBAL_LOCK:
        return _OWNERS.get(int(index))


def owners() -> dict[int, str]:
    with _GLOBAL_LOCK:
        return dict(_OWNERS)


def scan(max_index: int = 4, probe=None) -> list[int]:
    """Índices de cámara que entregan flujo SOSTENIDO. No toca los ya tomados.

    CORRECCIÓN: antes bastaba con que `read()` devolviera un fotograma. Muchas
    webcams de Windows dan el primero y luego se quedan mudas si el backend no
    encaja, así que aparecían como disponibles y luego no servían. Ahora se
    exige que superen la validación de `vision.camera_backend`.
    """
    found: list[int] = []
    if probe is None:
        def probe(idx: int) -> bool:
            try:
                from vision.camera_backend import backend_candidates
                from vision.camera_backend import probe as probe_backend
            except Exception:
                return False
            for backend in backend_candidates():
                if probe_backend(idx, backend, timeout=2.0).usable:
                    return True
            return False

    for idx in range(max(1, int(max_index))):
        if camera_owner(idx) is not None:
            continue
        try:
            if probe(idx):
                found.append(idx)
        except Exception as exc:
            log.debug("fallo probando la cámara %s: %s", idx, exc)
    return found


@dataclass
class CameraStatus:
    active: bool = False
    index: int = -1
    width: int = 0
    height: int = 0
    target_fps: float = 0.0
    real_fps: float = 0.0
    owner: str = ""
    last_error: str = ""
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "activa": self.active,
            "indice": self.index,
            "resolucion": f"{self.width}x{self.height}",
            "fps_objetivo": round(self.target_fps, 1),
            "fps_real": round(self.real_fps, 1),
            "propietario": self.owner,
            "ultimo_error": self.last_error,
        }


class CameraManager:
    """Fachada única sobre `CameraService` con cerrojo y autobúsqueda."""

    def __init__(
        self,
        frame_hub: FrameHub | None = None,
        *,
        index: int = 0,
        width: int = 1280,
        height: int = 720,
        target_fps: float = 20.0,
        privacy_mode: bool = True,
        owner: str = "vision",
        auto_search: bool = True,
        max_index: int = 4,
        status_callback: Callable[[str, bool], None] | None = None,
        capture_factory=None,
    ) -> None:
        self.hub = frame_hub or FrameHub()
        self.index = int(index)
        self.width = int(width)
        self.height = int(height)
        self.target_fps = float(target_fps)
        self.privacy_mode = bool(privacy_mode)
        self.owner = str(owner)
        self.auto_search = bool(auto_search)
        self.max_index = int(max_index)
        self._status_cb = status_callback or (lambda _t, _a: None)
        self._capture_factory = capture_factory

        self._service: CameraService | None = None
        self._lock = threading.RLock()
        self._status = CameraStatus(index=self.index, width=self.width,
                                    height=self.height, target_fps=self.target_fps,
                                    owner=self.owner)
        self._frames_seen = 0
        self._fps_t0 = 0.0
        self._last_frame_id = -1

    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Enciende la cámara. False si no se pudo (y deja el motivo en el estado)."""
        with self._lock:
            if self._service is not None and self._service.is_running():
                return True

            index = self.index
            if not acquire_camera(index, self.owner):
                other = camera_owner(index)
                self._status.last_error = (
                    f"la cámara {index} ya la está usando '{other}'")
                if self.auto_search:
                    alternatives = [i for i in scan(self.max_index) if i != index]
                    if alternatives:
                        index = alternatives[0]
                        if acquire_camera(index, self.owner):
                            log.info("Uso la cámara %s porque la %s está ocupada.",
                                     index, self.index)
                            self.index = index
                        else:
                            self._emit("No hay ninguna cámara libre.", False)
                            return False
                    else:
                        self._emit("No hay ninguna cámara libre.", False)
                        return False
                else:
                    self._emit(self._status.last_error, False)
                    return False

            self._service = CameraService(
                frame_hub=self.hub,
                index=index,
                width=self.width,
                height=self.height,
                target_fps=self.target_fps,
                privacy_mode=self.privacy_mode,
                status_callback=self._on_service_status,
                capture_factory=self._capture_factory,
            )
            self._fps_t0 = time.monotonic()
            self._frames_seen = 0
            try:
                self._service.start()
            except Exception as exc:
                self._status.last_error = str(exc)
                log.error("No pude iniciar la cámara %s: %s", index, exc)
                release_camera(index, self.owner)
                self._service = None
                return False
            self._status.index = index
            self._status.owner = self.owner
            return True

    def stop(self) -> None:
        """Apagado INMEDIATO y liberación del dispositivo."""
        with self._lock:
            service = self._service
            self._service = None
        if service is not None:
            try:
                service.stop()
            except Exception as exc:
                log.debug("fallo deteniendo la cámara: %s", exc)
        release_camera(self._status.index, self.owner)
        self._status.active = False
        self._emit("Cámara apagada y liberada.", False)

    def restart(self) -> bool:
        self.stop()
        time.sleep(0.2)
        return self.start()

    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        service = self._service
        return bool(service is not None and service.is_running())

    def latest_bgr(self):
        return self.hub.latest_bgr()

    def latest_rgb(self):
        return self.hub.latest_rgb()

    def frame_id(self) -> int:
        return self.hub.frame_id()

    def has_new_frame(self) -> bool:
        fid = self.hub.frame_id()
        if fid != self._last_frame_id:
            self._last_frame_id = fid
            return True
        return False

    def frame_age(self) -> float:
        ts = self.hub.timestamp()
        return float("inf") if ts <= 0 else (time.monotonic() - ts)

    # ------------------------------------------------------------------
    def _on_service_status(self, text: str, active: bool) -> None:
        self._status.active = bool(active)
        self._status.message = text
        if not active and "desconect" in text.lower():
            self._status.last_error = text
        self._emit(text, active)

    def _emit(self, text: str, active: bool) -> None:
        try:
            self._status_cb(text, bool(active))
        except Exception:
            pass

    def tick_fps(self) -> float:
        """Actualiza y devuelve los FPS reales de CAPTURA.

        CORRECCIÓN (fallo silencioso, y de los que engañan): esto solo lo
        llamaba `PerceptionEngine._job_face`. O sea, los FPS reales medían la
        cadencia del DETECTOR DE ROSTROS, no la de la cámara, y si ese módulo
        estaba apagado (o aún cargando su modelo) `real_fps` se quedaba en 0.0
        para siempre. Un 0 ahí es peligroso porque es justo el síntoma de "la
        cámara no entrega nada", así que llevaba a diagnosticar un problema de
        webcam que no existía.

        Ahora se mide contra el contador de fotogramas del `FrameHub`, que lo
        incrementa el hilo de captura. Es independiente de qué detectores estén
        registrados y de a qué ritmo vayan, que es lo que un FPS de captura
        tiene que significar. Se sigue pudiendo llamar desde cualquier sitio y
        tantas veces como se quiera: ya no cuenta llamadas, cuenta fotogramas.
        """
        now = time.monotonic()
        try:
            frame_id = int(self.hub.frame_id())
        except Exception:
            return self._status.real_fps

        if self._last_frame_id < 0 or self._fps_t0 <= 0.0:
            self._last_frame_id = frame_id
            self._fps_t0 = now
            return self._status.real_fps

        elapsed = now - self._fps_t0
        if elapsed >= 1.0:
            nuevos = max(0, frame_id - self._last_frame_id)
            self._status.real_fps = nuevos / elapsed
            self._last_frame_id = frame_id
            self._fps_t0 = now
        return self._status.real_fps

    def status(self) -> CameraStatus:
        # Se refresca aquí para que el dato sea correcto lo consulte quien lo
        # consulte, sin depender de que algún detector se acuerde de llamar a
        # tick_fps(). `status()` se pide varias veces por segundo desde la capa
        # reactiva, así que el muestreo de 1 s siempre se cumple.
        self._status.active = self.is_running()
        self._status.owner = camera_owner(self._status.index) or ""
        if self._status.active:
            self.tick_fps()
        else:
            self._status.real_fps = 0.0
            self._last_frame_id = -1
            self._fps_t0 = 0.0
        return self._status

    def available_cameras(self) -> list[int]:
        return scan(self.max_index)

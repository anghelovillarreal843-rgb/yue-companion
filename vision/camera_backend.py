"""Apertura robusta de la webcam: elección y VALIDACIÓN de backend.

Problema que resuelve (visto en Windows 11 con el diagnóstico real):

    Cámara abierta. Midiendo…
    Fotogramas recibidos: 1  ->  0.2 FPS reales

La cámara abría, entregaba UN fotograma y se quedaba muda. Causas típicas, todas
reproducibles en Windows:

  1. se validaba la apertura con UNA sola lectura: muchas webcams devuelven el
     primer fotograma y fallan a partir del segundo si el backend no encaja,
  2. no se probaba MSMF, que es el backend nativo de Windows 10/11 y el que
     funciona cuando DSHOW avisa «can't be used to capture by index»,
  3. se aplicaba `CAP_PROP_BUFFERSIZE` sobre DSHOW, que no lo soporta y puede
     dejar la captura en mal estado,
  4. no había calentamiento: varias cámaras (sobre todo las de portátil con
     autoexposición) tardan entre 3 y 10 fotogramas en estabilizarse.

La solución es probar cada backend de verdad: abrir, calentar y exigir VARIAS
lecturas consecutivas correctas antes de dar la cámara por buena. El backend que
funciona se recuerda para no repetir la búsqueda en cada reconexión.

Se puede forzar uno concreto con `VISION_CAMERA_BACKEND=msmf|dshow|v4l2|any`.
"""
from __future__ import annotations

import logging
import os
import platform
import time
from dataclasses import dataclass, field

log = logging.getLogger("vision.camera.backend")


def _cv2():
    try:
        import cv2
        return cv2
    except Exception as exc:  # pragma: no cover
        log.error("Falta opencv-python: %s", exc)
        return None


def backend_candidates() -> list[tuple[str, int]]:
    """Backends a probar, en el orden más prometedor para este sistema."""
    cv2 = _cv2()
    if cv2 is None:
        return []

    def const(name: str) -> int | None:
        value = getattr(cv2, name, None)
        return int(value) if value is not None else None

    sistema = platform.system().lower()
    orden: list[tuple[str, int | None]] = []

    forzado = (os.environ.get("VISION_CAMERA_BACKEND") or "").strip().lower()
    mapa = {"msmf": "CAP_MSMF", "dshow": "CAP_DSHOW", "v4l2": "CAP_V4L2",
            "avfoundation": "CAP_AVFOUNDATION", "gstreamer": "CAP_GSTREAMER",
            "any": "CAP_ANY"}
    if forzado in mapa:
        orden.append((forzado.upper(), const(mapa[forzado])))

    if sistema == "windows":
        # MSMF primero: es el nativo de Windows 10/11 y el que suele entregar
        # flujo sostenido cuando DSHOW solo da el primer fotograma.
        orden += [("MSMF", const("CAP_MSMF")),
                  ("DSHOW", const("CAP_DSHOW"))]
    elif sistema == "darwin":
        orden += [("AVFOUNDATION", const("CAP_AVFOUNDATION"))]
    else:
        orden += [("V4L2", const("CAP_V4L2"))]
    orden.append(("ANY", const("CAP_ANY") or 0))

    vistos: set[int] = set()
    salida: list[tuple[str, int]] = []
    for nombre, valor in orden:
        if valor is None or valor in vistos:
            continue
        vistos.add(valor)
        salida.append((nombre, valor))
    return salida


@dataclass
class ProbeResult:
    """Resultado de probar un backend concreto sobre un índice concreto."""

    index: int
    backend: str
    opened: bool = False
    frames: int = 0
    attempts: int = 0
    seconds: float = 0.0
    width: int = 0
    height: int = 0
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.opened and self.frames >= 3

    @property
    def fps(self) -> float:
        return self.frames / self.seconds if self.seconds > 0 else 0.0

    def as_line(self) -> str:
        if self.usable:
            return (f"[ OK ] índice {self.index} + {self.backend}: "
                    f"{self.frames}/{self.attempts} lecturas, "
                    f"{self.width}x{self.height}, ~{self.fps:.0f} FPS")
        motivo = self.error or (
            "abre pero no entrega flujo sostenido" if self.opened else "no abre")
        return f"[FALLA] índice {self.index} + {self.backend}: {motivo}"


def configure(cap, width: int, height: int, backend_name: str = "") -> None:
    """Aplica resolución y ajustes, saltando los que rompen cada backend."""
    cv2 = _cv2()
    if cv2 is None or cap is None:
        return
    try:
        if width > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        if height > 0:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
    except Exception as exc:
        log.debug("no pude fijar la resolución: %s", exc)
    # BUFFERSIZE baja la latencia, pero DSHOW no lo soporta y puede dejar la
    # captura muda tras el primer fotograma. Ahí se omite a propósito.
    if backend_name.upper() != "DSHOW" and hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass


def validate(cap, *, warmup: int = 3, needed: int = 3, max_attempts: int = 25,
             timeout: float = 3.0) -> tuple[int, int, tuple[int, int]]:
    """Comprueba que la cámara entrega flujo SOSTENIDO, no un fotograma suelto.

    Devuelve (lecturas_correctas, intentos, (ancho, alto)).
    """
    if cap is None:
        return 0, 0, (0, 0)
    inicio = time.monotonic()
    intentos = correctas = 0
    tamano = (0, 0)

    # Calentamiento: se descartan los primeros fotogramas (autoexposición).
    for _ in range(max(0, warmup)):
        try:
            cap.read()
        except Exception:
            break
        if time.monotonic() - inicio > timeout:
            break

    while intentos < max_attempts and correctas < needed:
        if time.monotonic() - inicio > timeout:
            break
        intentos += 1
        try:
            ok, frame = cap.read()
        except Exception:
            ok, frame = False, None
        if ok and frame is not None and getattr(frame, "size", 1) != 0:
            correctas += 1
            try:
                tamano = (int(frame.shape[1]), int(frame.shape[0]))
            except Exception:
                pass
        else:
            # Un fallo suelto no descalifica: se da un respiro y se reintenta.
            time.sleep(0.05)
    return correctas, intentos, tamano


def probe(index: int, backend: tuple[str, int], width: int = 640,
          height: int = 480, timeout: float = 3.0) -> ProbeResult:
    """Prueba un índice con un backend y libera siempre el dispositivo."""
    nombre, valor = backend
    resultado = ProbeResult(index=index, backend=nombre)
    cv2 = _cv2()
    if cv2 is None:
        resultado.error = "sin opencv-python"
        return resultado

    cap = None
    inicio = time.monotonic()
    try:
        cap = cv2.VideoCapture(index, valor)
        if not cap.isOpened():
            resultado.error = "no abre"
            return resultado
        resultado.opened = True
        configure(cap, width, height, nombre)
        correctas, intentos, tamano = validate(cap, timeout=timeout)
        resultado.frames = correctas
        resultado.attempts = intentos
        resultado.width, resultado.height = tamano
    except Exception as exc:
        resultado.error = str(exc)[:120]
    finally:
        resultado.seconds = time.monotonic() - inicio
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass
        # Windows necesita un instante para soltar el dispositivo de verdad.
        time.sleep(0.15)
    return resultado


def probe_all(indices=range(4), width: int = 640, height: int = 480) -> list[ProbeResult]:
    """Prueba todas las combinaciones índice × backend. Para diagnóstico."""
    salida: list[ProbeResult] = []
    for idx in indices:
        for backend in backend_candidates():
            resultado = probe(idx, backend, width, height)
            salida.append(resultado)
            if resultado.usable:
                break        # ese índice ya tiene un backend que funciona
    return salida


class CaptureOpener:
    """Abre la cámara probando backends y RECUERDA el que funcionó.

    Es lo que `CameraService` usa como `capture_factory`. En la primera apertura
    busca; en las reconexiones va directo al backend bueno. Si ese backend
    empieza a fallar, `report_failure()` lo descarta y vuelve a buscar.
    """

    def __init__(self, *, warmup: int = 3, needed: int = 3) -> None:
        self.warmup = int(warmup)
        self.needed = int(needed)
        self._preferido: tuple[str, int] | None = None
        self._descartados: set[str] = set()
        self.last_backend: str = ""
        self.last_error: str = ""

    def __call__(self, index: int, width: int, height: int):
        return self.open(index, width, height)

    # ------------------------------------------------------------------
    def open(self, index: int, width: int, height: int):
        cv2 = _cv2()
        if cv2 is None:
            self.last_error = "falta opencv-python"
            return None

        candidatos: list[tuple[str, int]] = []
        if self._preferido is not None:
            candidatos.append(self._preferido)
        for backend in backend_candidates():
            if backend[0] in self._descartados:
                continue
            if self._preferido is not None and backend[0] == self._preferido[0]:
                continue
            candidatos.append(backend)

        if not candidatos:
            # Todos descartados: se les da otra oportunidad antes de rendirse.
            self._descartados.clear()
            candidatos = backend_candidates()

        for nombre, valor in candidatos:
            cap = None
            try:
                cap = cv2.VideoCapture(index, valor)
                if not cap.isOpened():
                    self._cerrar(cap)
                    continue
                configure(cap, width, height, nombre)
                correctas, intentos, tamano = validate(
                    cap, warmup=self.warmup, needed=self.needed)
                if correctas >= self.needed:
                    self._preferido = (nombre, valor)
                    self.last_backend = nombre
                    self.last_error = ""
                    log.info("Cámara %s abierta con %s (%sx%s, %d/%d lecturas).",
                             index, nombre, tamano[0], tamano[1], correctas, intentos)
                    return cap
                log.info("La cámara %s con %s abre pero solo dio %d/%d lecturas; "
                         "pruebo otro backend.", index, nombre, correctas, intentos)
                self.last_error = (f"{nombre}: abre pero no entrega flujo "
                                   f"({correctas}/{intentos} lecturas)")
            except Exception as exc:
                self.last_error = f"{nombre}: {exc}"
                log.debug("fallo abriendo %s con %s: %s", index, nombre, exc)
            finally:
                if cap is not None and self.last_backend != nombre:
                    self._cerrar(cap)
                    time.sleep(0.15)
        log.warning("No conseguí abrir la cámara %s con ningún backend. %s",
                    index, self.last_error)
        return None

    def report_failure(self) -> None:
        """El backend en uso dejó de entregar: se descarta y se busca otro."""
        if self._preferido is not None:
            nombre = self._preferido[0]
            self._descartados.add(nombre)
            log.info("El backend %s dejó de entregar fotogramas; probaré otro.", nombre)
            self._preferido = None
            self.last_backend = ""

    @staticmethod
    def _cerrar(cap) -> None:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass

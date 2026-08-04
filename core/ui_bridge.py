"""Puente para tocar las ventanas de Yue desde el hilo correcto.

POR QUÉ EXISTE (esto colgó la app, no es teoría):

`PCWorker` es un QThread. Las ventanas de Yue (avatar, chat, burbujas) las creó
el hilo principal de Qt. Cuando el worker llamaba a `SetWindowLong(hwnd,
GWL_EXSTYLE, ...)` sobre una de esas ventanas, Windows le manda `WM_STYLECHANGING`
y `WM_STYLECHANGED` al hilo DUEÑO usando SendMessage, y **bloquea al worker hasta
que ese hilo conteste**. Con ventanas layered (las de Yue lo son) eso además
dispara un repintado. Si el hilo principal está ocupado en ese instante:
abrazo mortal, «Python no responde», y ni siquiera llega a imprimirse la orden.

La regla de Win32 es simple: los estilos de una ventana se cambian desde el hilo
que la creó. Este módulo hace justo eso.

  * `install(objeto_del_hilo_ui)` lo llama main.py una vez, al arrancar.
  * `run_on_ui_thread(fn, timeout)` encola la función en el hilo de Qt y espera
    con un LÍMITE. Si el hilo de la interfaz está atascado, se devuelve el
    control igualmente: preferimos perder el efecto antes que colgar a Yue.

Si nadie instaló el puente (por ejemplo en las pruebas), todo devuelve None sin
romper nada.
"""
from __future__ import annotations

import threading

_bridge = None


class _UiBridge:
    """Vive en el hilo de la interfaz y ejecuta lo que le encolen."""

    def __init__(self, owner):
        from PyQt5.QtCore import QObject, pyqtSignal

        class _Runner(QObject):
            pedido = pyqtSignal(object)

            def __init__(self):
                super().__init__()
                self.pedido.connect(self._ejecutar)

            def _ejecutar(self, tarea):
                # Esto YA corre en el hilo de la interfaz.
                try:
                    tarea()
                except Exception as exc:
                    print("[ui-bridge] la tarea falló:", exc)

        self._runner = _Runner()
        # Nos aseguramos de que el runner viva en el hilo de la UI.
        try:
            self._runner.moveToThread(owner.thread())
        except Exception:
            pass

    def enviar(self, tarea):
        self._runner.pedido.emit(tarea)


def install(owner) -> bool:
    """Lo llama main.py desde el hilo principal. `owner` es cualquier QObject."""
    global _bridge
    try:
        _bridge = _UiBridge(owner)
        return True
    except Exception as exc:
        print("[ui-bridge] no pude instalarme:", exc)
        _bridge = None
        return False


def disponible() -> bool:
    return _bridge is not None


def post_to_ui_thread(fn) -> bool:
    """Encola `fn` sin esperar: ideal para progreso/bitácoras frecuentes.

    A diferencia de run_on_ui_thread, nunca frena al PCWorker mientras la UI
    pinta. La tarea sigue ejecutándose en el hilo correcto de Qt.
    """
    if _bridge is None:
        return False
    try:
        _bridge.enviar(fn)
        return True
    except Exception as exc:
        print("[ui-bridge] no pude publicar en la interfaz:", exc)
        return False


def run_on_ui_thread(fn, timeout: float = 1.0):
    """Ejecuta `fn` en el hilo de la interfaz y devuelve su resultado.

    Si el puente no está instalado -> None.
    Si el hilo de la interfaz no contesta a tiempo -> None (pero NUNCA cuelga).
    """
    if _bridge is None:
        return None
    listo = threading.Event()
    caja = {}

    def _tarea():
        try:
            caja["valor"] = fn()
        except Exception as exc:
            caja["error"] = exc
        finally:
            listo.set()

    try:
        _bridge.enviar(_tarea)
    except Exception as exc:
        print("[ui-bridge] no pude encolar:", exc)
        return None

    if not listo.wait(timeout):
        print("[ui-bridge] la interfaz no contestó a tiempo; sigo sin ese efecto")
        return None
    if "error" in caja:
        print("[ui-bridge] la tarea dio error:", caja["error"])
        return None
    return caja.get("valor")

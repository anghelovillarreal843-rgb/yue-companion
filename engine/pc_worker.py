"""Workers Qt del control de PC (extraídos de main.py, PR 5, paso 4)."""
from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal


class PCWorker(QThread):
    progress = pyqtSignal(str)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, controller, engine, instruction):
        super().__init__()
        self.controller = controller
        self.engine = engine
        self.instruction = instruction

    def run(self):
        # --- aprendizaje: si ya sé hacer esto, lo repito sin gastar visión ---
        try:
            from core.learning import integration as learning
            replay = learning.try_replay(
                self.controller, self.instruction, progress=self.progress.emit
            )
            if replay is not None:
                self.done.emit(replay)
                return
        except Exception as exc:
            if "cancelada" in str(exc).lower():
                self.failed.emit(str(exc))
                return
            print("[aprendizaje] no pude repetir la receta:", exc)

        try:
            self.done.emit(self.controller.execute(
                self.instruction,
                engine=self.engine,
                progress=self.progress.emit,
            ))
        except Exception as exc:
            self.failed.emit(str(exc))


class PCRecoveryWorker(QThread):
    """Ejecuta 'deshacer' o 'repetir' fuera del hilo de la interfaz.

    Ambos actúan sobre teclado/ratón (Ctrl+Z o reejecutar un bloque), así que
    conviene no bloquear la UI. Los métodos del controlador ya devuelven un dict
    con el resultado (no lanzan), por eso 'failed' casi nunca se usa.
    """
    progress = pyqtSignal(str)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, controller, mode, actions=None, label=""):
        super().__init__()
        self.controller = controller
        self.mode = mode
        self.actions = actions          # solo para mode == "routine"
        self.label = label

    def run(self):
        try:
            if self.mode == "undo":
                self.done.emit(self.controller.undo_last())
            elif self.mode == "routine":
                self.done.emit(self.controller.run_actions(
                    self.actions, progress=self.progress.emit, label=self.label
                ))
            else:
                self.done.emit(self.controller.repeat_last(progress=self.progress.emit))
        except Exception as exc:
            self.failed.emit(str(exc))
"""AiWorker: hilo Qt para una llamada al LLM (extraído de main.py, PR 5, paso 3)."""
from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal


class AiWorker(QThread):
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, engine, messages):
        super().__init__()
        self.engine = engine
        self.messages = messages

    def run(self):
        try:
            self.done.emit(self.engine.chat(self.messages))
        except Exception as exc:
            self.failed.emit(str(exc))
from PyQt5.QtCore import QThread, pyqtSignal


class AutonomyWorker(QThread):
    """Genera el aporte autónomo fuera del hilo de la interfaz."""

    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, engine, messages):
        super().__init__()
        self.engine = engine
        self.messages = messages

    def run(self):
        try:
            self.done.emit(self.engine.chat(self.messages, timeout=55))
        except Exception as exc:
            self.failed.emit(str(exc))
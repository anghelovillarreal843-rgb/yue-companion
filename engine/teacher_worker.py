"""TeacherWorker (PR 5, paso 6).

QThread del Modo Profesora que vive en engine/ (mismo patrón que
engine/pc_worker.py): las clases pesadas de la profe van aquí para no acoplar
directores con QThreads definidos en main.py.
"""
from PyQt5.QtCore import QThread, pyqtSignal


class PdfPageVisionWorker(QThread):
    """NUEVO: hace que YUE MIRA una página de PDF rasterizada (PNG) y la explique.

    A diferencia de VisionWorker (que captura la pantalla), aquí la imagen viene de
    un archivo: la codificamos a base64 y la enviamos al modelo de visión. Se usa
    para páginas de puras imágenes/diagramas de un PDF escaneado.
    """
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, engine, system, instruction, image_path):
        super().__init__()
        self.engine = engine
        self.system = system
        self.instruction = instruction
        self.image_path = image_path

    def run(self):
        try:
            import base64
            with open(self.image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            self.done.emit(self.engine.look(self.system, b64, self.instruction))
        except Exception as exc:
            self.failed.emit(str(exc))
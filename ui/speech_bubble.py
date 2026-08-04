"""Globo de diálogo de Yue: aparece junto a su cabeza, SIGUE al personaje
mientras este se mueve, sube lentamente, pierde opacidad y desaparece."""
from PyQt5.QtCore import Qt, QPoint, QRectF, QTimer, QElapsedTimer
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QFont
from PyQt5.QtWidgets import QWidget, QLabel, QVBoxLayout


def _ease_out_cubic(t):
    return 1 - (1 - t) ** 3


class SpeechBubble(QWidget):
    def __init__(self, text, anchor_provider, parent=None):
        """anchor_provider: funcion SIN argumentos que devuelve el QPoint global
        actual junto a la cabeza de Yue (se consulta en cada frame para seguirla)."""
        super().__init__(parent)
        # Titulo para que Yue reconozca sus propias ventanas (verificacion de pantalla).
        self.setWindowTitle("YUE · burbuja")
        self._anchor = anchor_provider
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setFont(QFont("Segoe UI", 11))
        self.label.setStyleSheet("color: #eafcff; background:transparent;")
        self.label.setMaximumWidth(240)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 22)  # margen extra abajo para la colita
        lay.addWidget(self.label)
        self.adjustSize()

        # parametros de animacion
        self._max_rise = 100
        self._duration = max(4200, min(11000, 2600 + len(text) * 60))

        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self._reposition(0.0)  # colocar antes de mostrar

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        body = QRectF(0, 0, w, h - 10)

        path = QPainterPath()
        path.addRoundedRect(body, 16, 16)
        tail = QPainterPath()                 # colita apuntando hacia el personaje
        tail.moveTo(28, h - 12)
        tail.lineTo(16, h - 1)
        tail.lineTo(46, h - 12)
        path = path.united(tail)

        p.setPen(QColor(125, 249, 255, 200))
        p.setBrush(QColor(14, 11, 30, 235))
        p.drawPath(path)

    def _reposition(self, rise):
        a = self._anchor()  # posicion ACTUAL de Yue (la sigue al moverse/balancearse)
        self.move(a.x(), int(a.y() - self.height() - rise))

    def _tick(self):
        t = min(1.0, self._clock.elapsed() / self._duration)
        rise = self._max_rise * _ease_out_cubic(t)
        self._reposition(rise)

        # opacidad: legible un rato y luego se desvanece
        if t < 0.45:
            self.setWindowOpacity(1.0)
        else:
            self.setWindowOpacity(max(0.0, 1.0 - (t - 0.45) / 0.55))

        if t >= 1.0:
            self._timer.stop()
            self.close()

    def start(self):
        self.setWindowOpacity(1.0)
        self._reposition(0.0)
        self.show()
        self._clock.start()
        self._timer.start(30)

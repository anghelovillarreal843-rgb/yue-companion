"""Globo de diálogo de Yue: aparece junto a su cabeza, SIGUE al personaje
mientras este se mueve, sube lentamente, pierde opacidad y desaparece."""
from PyQt5.QtCore import Qt, QPoint, QRectF, QTimer, QElapsedTimer
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QFont
from PyQt5.QtWidgets import QWidget, QLabel, QVBoxLayout, QApplication

from ui import theme


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
        self.label.setFont(QFont(theme.FONT_UI, 11))
        self.label.setStyleSheet(f"color: {theme.TEXT}; background:transparent;")
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

        p.setPen(QColor(theme.ACCENT))
        p.setBrush(QColor(theme.GLASS))
        p.drawPath(path)

    def _reposition(self, rise):
        a = self._anchor()  # posicion ACTUAL de Yue (la sigue al moverse/balancearse)
        screen = self.screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        geom = screen.availableGeometry() if screen else QRectF(0, 0, 1920, 1080)
        # Mantiene el globo DENTRO de la pantalla: arriba no se corta (sale
        # justo sobre la cabeza de Yue) y abajo no se superpone al avatar.
        x = max(geom.left() + 4, min(a.x(), geom.right() - self.width() - 4))
        y = max(geom.top() + 4, min(int(a.y() - self.height() - rise),
                                    geom.bottom() - self.height() - 40))
        self.move(int(x), int(y))

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
        # Que el globo quede por encima del avatar y el chat, nunca "trasero".
        self.raise_()
        self._clock.start()
        self._timer.start(30)

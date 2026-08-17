"""Texto que aparece cuando el usuario escribe: sube lentamente y desaparece.
Estilizado con la paleta sobria de YUE (sin transparencias ni neones)."""
from PyQt5.QtCore import (Qt, QPoint, QRectF, QPropertyAnimation,
                          QParallelAnimationGroup, QEasingCurve)
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QWidget, QLabel, QVBoxLayout, QApplication

from ui import theme


class FloatingText(QWidget):
    """Mensaje del usuario que flota hacia arriba y se desvanece.

    anchor_global: punto (esquina inferior-izquierda) desde donde nace el texto.
    """

    def __init__(self, text, anchor_global: QPoint, parent=None):
        super().__init__(parent)
        # Titulo para que Yue reconozca sus propias ventanas (verificacion de pantalla).
        self.setWindowTitle("YUE · nota")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setStyleSheet(
            f"color:{theme.TEXT}; font-size:13px; font-weight:600; background:transparent;"
        )
        self.label.setMaximumWidth(250)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 9, 14, 9)
        lay.addWidget(self.label)
        self.adjustSize()

        # Nace justo por encima del punto ancla, pero SIEMPRE dentro de la
        # pantalla visible: antes podia quedar cortado (por arriba o por los
        # lados) cuando el chat estaba pegado al borde del escritorio.
        screen = self.screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        geom = screen.availableGeometry() if screen else QRectF(0, 0, 1920, 1080)
        x = anchor_global.x()
        y = anchor_global.y() - self.height()
        x = max(geom.left() + 4, min(x, geom.right() - self.width() - 4))
        y = max(geom.top() + 4, min(y, geom.bottom() - self.height() - 4))
        self.move(QPoint(int(x), int(y)))
        self._build_animation()

    def paintEvent(self, _):
        # Fondo solido morado oscuro con borde suave: legible sobre cualquier
        # fondo, sin translucidez ni desenfoque.
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor(theme.LINE))
        p.setBrush(QColor(theme.GLASS))
        p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 14, 14)

    def _build_animation(self):
        dur = max(3500, min(9000, 2200 + len(self.label.text()) * 55))
        start = self.pos()
        end = QPoint(start.x(), start.y() - 120)  # sube

        move = QPropertyAnimation(self, b"pos", self)
        move.setStartValue(start)
        move.setEndValue(end)
        move.setEasingCurve(QEasingCurve.OutCubic)
        move.setDuration(dur)

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setStartValue(1.0)
        fade.setKeyValueAt(0.35, 1.0)   # legible un instante
        fade.setEndValue(0.0)           # luego se desvanece
        fade.setDuration(dur)

        self._group = QParallelAnimationGroup(self)
        self._group.addAnimation(move)
        self._group.addAnimation(fade)
        self._group.finished.connect(self.close)

    def start(self):
        self.setWindowOpacity(1.0)
        self.show()
        # Asegura que la nota quede POR ENCIMA del chat y el avatar, no
        # "trasera" (antes podia aparecer tapada por otras ventanas de YUE).
        self.raise_()
        self._group.start()
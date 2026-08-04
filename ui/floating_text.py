"""Texto que aparece a los pies de Yue cuando el usuario escribe:
sube lentamente, pierde opacidad y desaparece."""
from PyQt5.QtCore import (Qt, QPoint, QRectF, QPropertyAnimation,
                          QParallelAnimationGroup, QEasingCurve)
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QWidget, QLabel, QVBoxLayout


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
            "color:#ffffff; font-size:13px; font-weight:600; background:transparent;"
        )
        self.label.setMaximumWidth(250)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.addWidget(self.label)
        self.adjustSize()

        # nace justo por encima del punto ancla (a los pies, lado izquierdo)
        self.move(anchor_global.x(), anchor_global.y() - self.height())
        self._build_animation()

    def paintEvent(self, _):
        # fondo oscuro translúcido para que el texto sea legible sobre cualquier fondo
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(28, 22, 34, 200))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 14, 14)

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
        self._group.start()

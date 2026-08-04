"""Chat flotante de Yue, optimizado para escritura continua y sin bloqueos."""
from PyQt5.QtCore import (
    Qt, QPoint, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal,
)
from PyQt5.QtGui import QFont, QColor, QKeyEvent
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QFrame, QPushButton,
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
)

from ui import theme


class ReliableLineEdit(QLineEdit):
    """Entrada resistente a pérdidas de foco y pulsaciones rápidas de Enter."""
    submit = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.isAutoRepeat():
            self.submit.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class FootChat(QWidget):
    send_message = pyqtSignal(str)
    # NUEVO: emite las rutas de los archivos soltados sobre la ventanita del chat.
    files_dropped = pyqtSignal(list)

    WIDTH = 340
    AUTO_FADE_MS = 15000

    # El usuario pidió que la ACTIVIDAD de Yue (estado + acciones del PC)
    # NO aparezca en el chat: esos avisos reajustaban y reposicionaban la
    # ventanita sin parar durante las tareas, y a veces trababan la escritura.
    # Las respuestas normales de Yue se siguen mostrando igual.
    # Pon esto en True si algún día quieres volver a ver esa actividad.
    MOSTRAR_ACTIVIDAD = False

    # Extensiones que YUE sabe leer en Modo Profesora (documentos e imágenes).
    DROP_EXTS = (".pdf", ".docx", ".pptx", ".epub", ".txt", ".md",
                 ".png", ".jpg", ".jpeg", ".bmp", ".webp")

    def __init__(self):
        super().__init__()
        # Titulo para que Yue reconozca sus propias ventanas (verificacion de pantalla).
        self.setWindowTitle("YUE · chat")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # NUEVO: permite soltar PDF/documentos sobre el chat para que YUE los lea.
        self.setAcceptDrops(True)
        self.setFixedWidth(self.WIDTH)
        # Windows fuerza un alto mínimo ligeramente mayor por el marco invisible.
        # Declararlo evita los avisos repetidos de setGeometry del registro.
        self.setMinimumHeight(180)
        self._pet_ref = None
        self._layout_pending = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        self.card = QFrame()
        self.card.setObjectName("card")
        self.card.setStyleSheet(
            f"QFrame#card{{background:{theme.GLASS};border:1px solid {theme.LINE};"
            "border-radius:18px;}"
        )
        # Guardamos el estilo normal para poder resaltar el borde al arrastrar.
        self._card_style_normal = self.card.styleSheet()
        self._card_style_drag = (
            f"QFrame#card{{background:{theme.GLASS};border:2px dashed {theme.CYAN};"
            "border-radius:18px;}"
        )
        glow = QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(28)
        glow.setOffset(0, 0)
        glow.setColor(QColor(125, 249, 255, 70))
        self.card.setGraphicsEffect(glow)
        outer.addWidget(self.card)

        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(7)

        self.bond_label = QLabel("🌑 Vínculo · nivel 1/10")
        self.bond_label.setStyleSheet(f"color:{theme.VIOLET};background:transparent;")
        self.bond_label.setFont(QFont(theme.FONT_MONO, 9, QFont.Bold))
        lay.addWidget(self.bond_label)

        # NUEVO: indicador del MODO actual (🤍 Compañera / 📚 Profesora).
        self.mode_label = QLabel("🤍 Compañera")
        self.mode_label.setStyleSheet(f"color:{theme.CYAN};background:transparent;")
        self.mode_label.setFont(QFont(theme.FONT_MONO, 9, QFont.Bold))
        self.mode_label.setToolTip("Modo actual de YUE")
        lay.addWidget(self.mode_label)

        self.reply = QLabel("")
        self.reply.setWordWrap(True)
        self.reply.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.reply.setStyleSheet(
            f"color:{theme.TEXT};background:rgba(125,249,255,16);"
            "border:1px solid rgba(125,249,255,70);border-radius:11px;padding:8px 11px;"
        )
        self.reply.setFont(QFont(theme.FONT_UI, 10))
        self.reply.hide()
        lay.addWidget(self.reply)

        self._reply_opacity = QGraphicsOpacityEffect(self.reply)
        self._reply_opacity.setOpacity(1.0)
        self.reply.setGraphicsEffect(self._reply_opacity)
        self._reply_fade = QPropertyAnimation(self._reply_opacity, b"opacity", self)
        self._reply_fade.setDuration(320)
        self._reply_fade.setEasingCurve(QEasingCurve.OutCubic)
        self._reply_fade.finished.connect(self._hide_reply_if_transparent)
        self._reply_timer = QTimer(self)
        self._reply_timer.setSingleShot(True)
        self._reply_timer.timeout.connect(self._fade_reply)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{theme.CYAN};background:transparent;font-style:italic;")
        self.status.setFont(QFont(theme.FONT_UI, 9))
        self.status.hide()
        lay.addWidget(self.status)

        self.pc_actions = QLabel("")
        self.pc_actions.setWordWrap(True)
        self.pc_actions.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.pc_actions.setStyleSheet(
            f"color:{theme.TEXT};background:rgba(8,10,24,180);"
            "border:1px solid rgba(125,249,255,45);border-radius:8px;padding:6px 8px;"
        )
        self.pc_actions.setFont(QFont(theme.FONT_MONO, 8))
        self.pc_actions.setToolTip("Últimas acciones de control del PC")
        self.pc_actions.hide()
        lay.addWidget(self.pc_actions)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(7)

        self.input = ReliableLineEdit()
        self.input.setPlaceholderText("Escríbele o dale una orden a Yue…")
        self.input.setToolTip("Escribe, o arrastra un PDF/documento aquí para que Yue lo lea")
        self.input.setFont(QFont(theme.FONT_UI, 11))
        self.input.setClearButtonEnabled(True)
        self.input.setFocusPolicy(Qt.StrongFocus)
        self.input.setStyleSheet(
            f"QLineEdit{{background:rgba(10,8,24,235);color:{theme.TEXT};"
            f"border:1px solid {theme.LINE};border-radius:13px;padding:9px 13px;}}"
            f"QLineEdit:focus{{border:1px solid {theme.CYAN};}}"
        )
        self.input.textEdited.connect(self._on_typing)
        self.input.submit.connect(self._on_send)
        input_row.addWidget(self.input, 1)

        self.send_btn = QPushButton("➤")
        self.send_btn.setFixedSize(38, 38)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setToolTip("Enviar")
        self.send_btn.setStyleSheet(
            f"QPushButton{{background:rgba(125,249,255,28);color:{theme.CYAN};"
            f"border:1px solid {theme.LINE};border-radius:12px;font-size:16px;}}"
            f"QPushButton:hover{{border:1px solid {theme.CYAN};background:rgba(125,249,255,45);}}"
            "QPushButton:pressed{padding-top:2px;}"
        )
        self.send_btn.clicked.connect(self._on_send)
        input_row.addWidget(self.send_btn)
        lay.addLayout(input_row)

        self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.ensure_input_ready)

    # ---------------- Arrastrar y soltar documentos (NUEVO) ----------------
    def _drop_paths(self, mime) -> list:
        """Extrae rutas locales de documentos soportados desde un evento de arrastre."""
        rutas = []
        if mime is None or not mime.hasUrls():
            return rutas
        for url in mime.urls():
            ruta = url.toLocalFile()
            if ruta and ruta.lower().endswith(self.DROP_EXTS):
                rutas.append(ruta)
        return rutas

    def _set_drag_highlight(self, on: bool):
        self.card.setStyleSheet(self._card_style_drag if on else self._card_style_normal)

    def dragEnterEvent(self, event):
        # Aceptamos el arrastre solo si trae al menos un documento legible.
        if self._drop_paths(event.mimeData()):
            self._set_drag_highlight(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drag_highlight(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_drag_highlight(False)
        rutas = self._drop_paths(event.mimeData())
        if rutas:
            event.acceptProposedAction()
            self.files_dropped.emit(rutas)
            QTimer.singleShot(0, lambda: self.ensure_input_ready(focus=False))
        else:
            event.ignore()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            QTimer.singleShot(0, self.ensure_input_ready)

    def ensure_input_ready(self, focus=True):
        """Mantiene la caja habilitada sin robar foco a las apps controladas."""
        if not self.input.isEnabled():
            self.input.setEnabled(True)
        if focus and self.isVisible():
            self.raise_()
            self.activateWindow()
            self.input.setFocus(Qt.OtherFocusReason)

    def is_user_composing(self) -> bool:
        return bool(self.input.text().strip())

    def _on_typing(self, text):
        if not self.reply.isVisible() or not text.strip():
            return
        self._reply_timer.stop()
        # Una sola animación ligera. Evita recalcular tamaño y efectos en cada tecla.
        if self._reply_fade.state() != QPropertyAnimation.Running:
            self._reply_fade.stop()
            self._reply_fade.setStartValue(self._reply_opacity.opacity())
            self._reply_fade.setEndValue(0.0)
            self._reply_fade.setDuration(220)
            self._reply_fade.start()

    def _on_send(self):
        text = self.input.text().strip()
        if not text:
            self.ensure_input_ready(focus=True)
            return
        self._reply_timer.stop()
        self._reply_fade.stop()
        self.reply.hide()
        self._reply_opacity.setOpacity(1.0)
        self.input.clear()
        self._schedule_layout()
        self.send_message.emit(text)
        QTimer.singleShot(0, lambda: self.ensure_input_ready(focus=False))

    def set_bond(self, level):
        # No mostramos nombres que parezcan emociones (p. ej. "Curiosa").
        self.bond_label.setText(f"{level.emoji} Vínculo · nivel {level.index}/10")
        self.bond_label.setToolTip("Progreso del vínculo con Yue")

    def set_mode(self, emoji, label):
        """NUEVO: actualiza el indicador de modo (🤍 Compañera / 📚 Profesora)."""
        emoji = (emoji or "").strip()
        label = (label or "").strip()
        texto = f"{emoji} {label}".strip() if (emoji or label) else "🤍 Compañera"
        self.mode_label.setText(texto)

    def show_reply(self, text):
        text = (text or "").strip()
        self._reply_fade.stop()
        self._reply_timer.stop()
        if not text:
            self.reply.hide()
        else:
            self.reply.setText("Yue: " + text)
            self._reply_opacity.setOpacity(1.0)
            self.reply.show()
            self._reply_timer.start(self.AUTO_FADE_MS)
        self._schedule_layout()
        QTimer.singleShot(0, lambda: self.ensure_input_ready(focus=False))

    def _fade_reply(self):
        if not self.reply.isVisible():
            return
        self._reply_fade.stop()
        self._reply_fade.setStartValue(self._reply_opacity.opacity())
        self._reply_fade.setEndValue(0.0)
        self._reply_fade.setDuration(650)
        self._reply_fade.start()

    def _hide_reply_if_transparent(self):
        if self._reply_opacity.opacity() <= 0.03:
            self.reply.hide()
            self._reply_opacity.setOpacity(1.0)
            self._schedule_layout()

    def set_status(self, text):
        if not self.MOSTRAR_ACTIVIDAD:
            # Actividad oculta a pedido del usuario: sin reajustes ni foco,
            # así la caja de escritura no se traba mientras Yue trabaja.
            if not self.status.isHidden():
                self.status.hide()
            return
        if text:
            self.status.setText(text)
            self.status.show()
        else:
            self.status.hide()
        self._schedule_layout()
        QTimer.singleShot(0, lambda: self.ensure_input_ready(focus=False))

    def set_pc_actions(self, actions):
        """Muestra una bitácora compacta de las últimas acciones del PC."""
        if not self.MOSTRAR_ACTIVIDAD:
            # A pedido del usuario, la bitácora de acciones no aparece en el
            # chat (evita reajustes de tamaño que trababan la escritura).
            if not self.pc_actions.isHidden():
                self.pc_actions.hide()
            return
        rows = []
        for item in list(actions or [])[-8:]:
            mark = "✓" if item.get("ok") else "✗"
            detail = str(item.get("detail", "")).replace("\n", " ")[:92]
            rows.append(f"{mark} {detail}")
        if rows:
            self.pc_actions.setText("PC · últimas acciones\n" + "\n".join(rows))
            self.pc_actions.show()
        else:
            self.pc_actions.hide()
        self._schedule_layout()

    def _schedule_layout(self):
        if self._layout_pending:
            return
        self._layout_pending = True

        def apply_layout():
            self._layout_pending = False
            self.adjustSize()
            self._reposition_if_needed()

        QTimer.singleShot(0, apply_layout)

    def anchor_for_floating(self) -> QPoint:
        top_left = self.input.mapToGlobal(QPoint(0, 0))
        return QPoint(top_left.x(), top_left.y() - 6)

    def _reposition_if_needed(self):
        if self._pet_ref is not None:
            self.reposition(self._pet_ref)

    def reposition(self, pet):
        self._pet_ref = pet
        self.adjustSize()
        g = pet.frameGeometry()
        screen = pet.screen().availableGeometry() if pet.screen() else None
        left = screen.left() if screen else 0
        top = screen.top() if screen else 0
        right = screen.right() if screen else 1920
        bottom = screen.bottom() if screen else 1080

        x = g.left() - self.width() - 8
        if x < left + 4:
            x = min(g.right() + 8, right - self.width() - 4)
        y = g.bottom() - self.height() + 6
        y = max(top + 4, min(y, bottom - self.height() - 4))
        self.move(x, y)

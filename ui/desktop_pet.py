"""Personaje de escritorio.

Modo VRM (3D): renderiza el avatar yue.vrm con three-vrm (ojos, labios,
expresiones y físicas) dentro de una ventana transparente, sin marco y siempre
encima. Si PyQtWebEngine no está disponible, cae al modo PNG (imagen estática).
"""
import json
import math
import sys

from PyQt5.QtCore import Qt, QPoint, QUrl, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap, QColor, QCursor
from PyQt5.QtWidgets import QWidget, QLabel, QMenu, QApplication

import config
from ui import theme

# ¿Tenemos motor web para el VRM 3D?
try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
    _HAS_WEB = True
except Exception:
    _HAS_WEB = False


class DesktopPet(QWidget):
    clicked = pyqtSignal()        # clic simple -> mostrar/ocultar chat
    moved = pyqtSignal()          # se emite al arrastrarla
    request_quit = pyqtSignal()
    toggle_voice = pyqtSignal()
    toggle_mic = pyqtSignal()     # escucharme por voz on/off
    toggle_autonomy = pyqtSignal()  # autonomia segura on/off
    look_screen = pyqtSignal()    # que YUE mire la pantalla ahora mismo

    def __init__(self):
        super().__init__()
        # Titulo para que Yue reconozca sus propias ventanas (verificacion de pantalla).
        self.setWindowTitle("YUE")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.mode = "vrm" if (_HAS_WEB and config.USE_VRM and config.VRM.exists()) else "png"
        print(f"[avatar] PyQtWebEngine disponible={_HAS_WEB}  USE_VRM={config.USE_VRM}  "
              f"-> modo={self.mode}")
        self._drag_offset = None
        self._moved = False
        self._talking = False
        self._emotion = "neutral"
        self._media_state = {
            "active": False, "rhythm": 0.0, "tempo_bpm": 0.0,
            "energy": 0.0, "calm": 0.0, "look_to_screen": True,
        }
        self._ready = False
        self._t = 0.0
        self._base_y = None

        if self.mode == "vrm":
            self._build_vrm()
        else:
            self._build_png()
        self._build_camera_badge()

        # temporizador: idle (png) + seguimiento de ojos (vrm)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

        self._place_bottom_right()

    # ================= VRM 3D =================
    def _build_vrm(self):
        print(f"[avatar] modo=VRM  archivo={config.VRM}  existe={config.VRM.exists()}")
        w = config.AVATAR_WIDTH
        h = config.AVATAR_HEIGHT
        self.resize(w, h)

        self.view = QWebEngineView(self)
        self.view.resize(w, h)
        self.view.setAttribute(Qt.WA_TranslucentBackground)
        self.view.setStyleSheet("background:transparent")
        # que el ratón lo maneje la ventana (para arrastrar / menú)
        self.view.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        page = self.view.page()
        page.setBackgroundColor(QColor(0, 0, 0, 0))

        s = self.view.settings()
        for attr in ("LocalContentCanAccessFileUrls", "LocalContentCanAccessRemoteUrls",
                     "WebGLEnabled", "Accelerated2dCanvasEnabled"):
            if hasattr(QWebEngineSettings, attr):
                s.setAttribute(getattr(QWebEngineSettings, attr), True)

        # Servimos por http local: Chromium NO permite fetch() sobre file://.
        from core import localserver
        port = localserver.start()
        turn = math.radians(config.AVATAR_TURN)
        debug = "1" if config.AVATAR_DEBUG else "0"
        url = (f"http://127.0.0.1:{port}/ui/avatar.html?turn={turn}&debug={debug}"
               f"&armDown={config.AVATAR_ARM_DOWN}"
               f"&armFwd={config.AVATAR_ARM_FWD}"
               f"&armIn={config.AVATAR_ARM_IN}"
               f"&camDist={config.AVATAR_CAM_DIST}"
               f"&camY={config.AVATAR_CAM_Y}"
               f"&gest={getattr(config, 'AVATAR_GESTURE_GAIN', 1.0)}")
        self._vrm_ok = False
        self.view.loadFinished.connect(self._on_loaded)
        self.view.load(QUrl(url))

        # Aviso si se usa el Python de la Microsoft Store (QtWebEngine no suele
        # funcionar ahi: su proceso interno no arranca y el 3D nunca carga).
        exe = sys.executable or ""
        if "WindowsApps" in exe or "PythonSoftwareFoundation" in exe:
            print("[avatar] AVISO: estas usando el Python de la Microsoft Store. "
                  "QtWebEngine (el motor 3D) casi nunca funciona ahi. "
                  "Instala Python desde python.org para ver el avatar 3D.")

        # Respaldo: si el 3D no carga a tiempo, mostrar el PNG para no quedarte sin Yue.
        # Damos margen amplio: el .vrm pesa y las librerias se bajan de internet.
        self._png_switched = False
        QTimer.singleShot(50000, self._vrm_timeout)

    def _on_loaded(self, ok):
        self._ready = True
        print(f"[avatar] pagina cargada ok={ok}; esperando a que el 3D termine de cargar…")
        self._js(f"setEmotion('{self._emotion}', 1.0, 4000)")
        self._js("setTalking(%s)" % ("true" if self._talking else "false"))
        self._send_media_state()
        self._vrm_polls = 0
        QTimer.singleShot(1500, self._poll_vrm)

    def _poll_vrm(self):
        # Revisa el estado del visor 3D cada 1.5s hasta que cargue, falle o se agote.
        if self.mode != "vrm" or self._vrm_ok or getattr(self, "view", None) is None:
            return

        def _got(t):
            t = (t or "").strip()
            if t == "VRM_OK":
                self._vrm_ok = True
                print("[avatar] ✓ 3D cargado correctamente (VRM_OK).")
                return
            if t == "VRM_ERROR":
                # leer el mensaje de error visible en la pagina para saber la causa real
                try:
                    self.view.page().runJavaScript(
                        "(document.getElementById('msg')||{}).textContent||''",
                        lambda m: print(f"[avatar] ✗ el visor 3D reporto un ERROR: {m}"))
                except Exception:
                    pass
                self._switch_to_png()
                return
            # sigue cargando (titulo vacio / 'Cargando…'): reintentar
            self._vrm_polls += 1
            if self._vrm_polls < 32:   # ~48s
                if self._vrm_polls in (4, 12, 20):
                    print(f"[avatar] el 3D sigue cargando… ({self._vrm_polls*1.5:.0f}s)")
                QTimer.singleShot(1500, self._poll_vrm)
            else:
                print("[avatar] el 3D tardo demasiado en cargar; uso PNG de respaldo.")
                self._switch_to_png()

        try:
            self.view.page().runJavaScript("document.title", _got)
        except Exception:
            QTimer.singleShot(1500, self._poll_vrm)

    def _vrm_timeout(self):
        if self.mode == "vrm" and not self._vrm_ok:
            print("[avatar] el 3D no cargo a tiempo. Cambiando al PNG de respaldo.")
            self._switch_to_png()

    def _switch_to_png(self):
        if getattr(self, "_png_switched", False):
            return
        self._png_switched = True
        try:
            if getattr(self, "view", None) is not None:
                self.view.hide()
                self.view.deleteLater()
                self.view = None
        except Exception:
            pass
        self.mode = "png"
        self._build_png()
        self.label.show()
        self.resize(self._base.size())
        self._place_bottom_right()
        if getattr(self, "camera_badge", None) is not None:
            self.camera_badge.raise_()

    # Gestos que el avatar puede hacer bien (sin IK, no atraviesan el cuerpo):
    GESTOS = (
        "asentir", "negar", "ladear", "sobresalto", "encoger", "suspiro",
        "reir", "asomarse", "retroceder", "mirar_lejos", "estirarse",
        "celebrar", "pensar", "desanimo",
        # NUEVO: lo usa la percepción visual cuando te ve saludar con la mano.
        "saludar",
    )

    def play_gesture(self, nombre: str, gain: float = 1.0):
        """Dispara un gesto puntual. Los de la emocion salen solos; esto es
        para momentos concretos (un «asentir» al confirmar una orden)."""
        if nombre not in self.GESTOS:
            print(f"[avatar] gesto desconocido: {nombre}")
            return False
        gain = max(0.0, min(1.5, float(gain)))
        self._js(f"playGestureExt('{nombre}', {gain:.2f})")
        return True

    def _js(self, code):
        if self.mode == "vrm" and self._ready:
            self.view.page().runJavaScript(code)

    # ================= PNG (respaldo) =================
    def _build_png(self):
        pix = QPixmap(str(config.ASSET))
        if pix.isNull():
            raise FileNotFoundError(f"No se encontró la imagen: {config.ASSET}")
        self._base = pix.scaledToHeight(config.PET_HEIGHT, Qt.SmoothTransformation)
        self.label = QLabel(self)
        self.label.setPixmap(self._base)
        self.resize(self._base.size())
        self.label.resize(self._base.size())

    # ================= indicador de cámara =================
    def _build_camera_badge(self):
        self.camera_badge = QLabel("●  CÁMARA", self)
        self.camera_badge.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.camera_badge.setStyleSheet(
            "QLabel{background:rgba(14,20,24,205);color:#72f1b8;"
            "border:1px solid rgba(114,241,184,150);border-radius:8px;"
            "padding:3px 7px;font-size:9px;font-weight:bold;}"
        )
        self.camera_badge.adjustSize()
        self.camera_badge.move(8, 8)
        self.camera_badge.hide()
        self.camera_badge.raise_()

    def set_camera_active(self, active: bool):
        badge = getattr(self, "camera_badge", None)
        if badge is None:
            return
        badge.setVisible(bool(active))
        if active:
            badge.raise_()

    # ================= común =================
    def _place_bottom_right(self):
        geo = QApplication.primaryScreen().availableGeometry()
        x = geo.right() - self.width() - 30
        y = geo.bottom() - self.height() - 10
        self.move(x, y)
        self._base_y = y

    def bubble_anchor(self) -> QPoint:
        g = self.mapToGlobal(QPoint(0, 0))
        y = g.y() + (int(self.height() * 0.12) if self.mode == "vrm" else 30)
        return QPoint(g.x() + int(self.width() * 0.6), y)

    def set_talking(self, value: bool):
        self._talking = bool(value)
        self._js("setTalking(%s)" % ("true" if value else "false"))

    def set_lipsync(self, payload):
        """NUEVO: envía a avatar.html el envelope de amplitud del audio para el
        lip-sync real. `payload` es {"values":[...], "block_ms":N} o None. Con
        None (o si algo falla) la UI vuelve a su seno de respaldo. El guard
        `if(window.setLipsync)` evita errores si el visor aún no la definió."""
        if self.mode != "vrm":
            return
        if not payload:
            self._js("if(window.setLipsync)setLipsync(null)")
            return
        try:
            data = json.dumps({
                "values": list(payload.get("values", [])),
                "block_ms": int(payload.get("block_ms", 40)),
            }, separators=(",", ":"))
        except Exception:
            self._js("if(window.setLipsync)setLipsync(null)")
            return
        self._js("if(window.setLipsync)setLipsync(%s)" % data)

    def set_emotion(self, name: str, intensity: float = 1.0, duration_ms: int = 4800):
        self._emotion = name or "neutral"
        intensity = max(0.25, min(1.0, float(intensity)))
        duration_ms = max(1200, int(duration_ms))
        self._js(f"setEmotion('{self._emotion}', {intensity:.3f}, {duration_ms})")

    def set_media_companion(self, state):
        """Actualiza la animación ambiental durante música o vídeo.

        Acepta ``AvatarMediaState`` o un diccionario para mantener este widget
        desacoplado de ``core.media_companion``.
        """
        def read(name, default):
            if isinstance(state, dict):
                return state.get(name, default)
            return getattr(state, name, default)

        self._media_state = {
            "active": bool(read("active", False)),
            "rhythm": max(0.0, min(1.0, float(read("rhythm", 0.0) or 0.0))),
            "tempo_bpm": max(0.0, min(240.0, float(read("tempo_bpm", 0.0) or 0.0))),
            "energy": max(0.0, min(1.0, float(read("energy", 0.0) or 0.0))),
            "calm": max(0.0, min(1.0, float(read("calm", 0.0) or 0.0))),
            "look_to_screen": bool(read("look_to_screen", True)),
        }
        self._send_media_state()

    def _send_media_state(self):
        if self.mode != "vrm":
            return
        payload = json.dumps(self._media_state, separators=(",", ":"))
        self._js("if(window.setMediaCompanion)setMediaCompanion(%s)" % payload)

    def _tick(self):
        self._t += 0.04
        if self.mode == "png":
            if self._base_y is None:
                return
            bob = math.sin(self._t) * 3
            if self._talking:
                bob += math.sin(self._t * 14) * 2
            media = self._media_state
            if media.get("active") and media.get("rhythm", 0.0) > 0.05:
                hz = max(0.55, min(3.0, media.get("tempo_bpm", 78.0) / 60.0))
                bob += math.sin(self._t * math.pi * 2.0 * hz) * 1.5 * media["rhythm"]
            self.move(self.x(), int(self._base_y + bob))
        else:
            media = self._media_state
            if media.get("active") and media.get("look_to_screen", True):
                # YUE vive en la esquina inferior derecha: una mirada leve hacia
                # arriba/izquierda se siente como observar el contenido con el usuario.
                dx = -0.42
                dy = 0.10
            else:
                c = QCursor.pos()
                center = self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2))
                dx = (c.x() - center.x()) / 400.0
                dy = (center.y() - c.y()) / 400.0
            self._js(f"lookAt({dx:.3f},{dy:.3f})")

    # ---------- ratón ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_offset = e.globalPos() - self.frameGeometry().topLeft()
            self._moved = False
        elif e.button() == Qt.RightButton:
            self._context_menu(e.globalPos())

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag_offset)
            self._base_y = self.y()
            self._moved = True
            self.moved.emit()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            if not self._moved:
                self.clicked.emit()
            self._drag_offset = None

    def _context_menu(self, pos):
        m = QMenu()
        # Rediseno sobrio con la paleta de YUE: superficie solida morada,
        # sin transparencias, sin blur ni neones. Misma familia que el chat.
        m.setStyleSheet(
            "QMenu{"
            f"background:{theme.GLASS};color:{theme.TEXT};"
            f"border:1px solid {theme.LINE};border-radius:12px;"
            "padding:6px 4px;"
            "}"
            "QMenu::item{"
            f"color:{theme.TEXT};background:transparent;border-radius:8px;"
            "padding:8px 18px 8px 12px;margin:1px 6px;"
            "}"
            "QMenu::item:selected{"
            f"background:{theme.BG2};color:{theme.ACCENT};"
            "}"
            "QMenu::item:disabled{color:" + theme.TEXT_DIM + ";}"
            "QMenu::separator{"
            f"height:1px;background:{theme.LINE};margin:5px 10px;"
            "}"
        )
        m.addAction("💬  Mostrar / ocultar chat", self.clicked.emit)
        m.addAction("🔊  Activar / silenciar voz", self.toggle_voice.emit)
        m.addAction("🎤  Escucharme por voz (activar/desactivar)", self.toggle_mic.emit)
        m.addAction("👁  Ver mi pantalla ahora", self.look_screen.emit)
        m.addAction("🧠  Activar / pausar autonomía segura", self.toggle_autonomy.emit)
        m.addSeparator()
        m.addAction("✖  Salir", self.request_quit.emit)
        m.exec_(pos)

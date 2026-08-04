"""Control del cursor con la cabeza (accesibilidad).

Reutiliza los MISMOS landmarks faciales que camera_observer.py ya obtiene con
MediaPipe (la misma fuente que usa face_emotion.py). NO abre otra cámara ni otro
pipeline: recibe los puntos por fotograma y los traduce a movimiento del ratón.

Diseño pensado para personas con dificultad de motricidad fina en las manos (no
necesariamente en el cuello):
- Control por VELOCIDAD (rate control): cuánto giras la cabeza respecto al frente
  marca la velocidad del cursor, no una posición fija. Es lo más cómodo y
  tolerante para este público.
- ZONA MUERTA central: mirar de frente (y los microtemblores naturales) NO mueve
  el cursor.
- DWELL-CLIC: si el cursor se queda quieto dentro de un radio pequeño durante N
  ms, se dispara un ÚNICO clic izquierdo (con refractario para no repetir).
- SEGURIDAD: el clic pasa por el mismo camino seguro de pc_control (botones
  permitidos, coordenadas recortadas a la pantalla, FAILSAFE). Solo mueve el
  ratón y hace clic izquierdo: no puede escribir, usar atajos ni disparar
  ninguna de las acciones bloqueadas de pc_control.
"""
from __future__ import annotations

import math
import time

import config

# Índices del malla facial canónica de MediaPipe FaceLandmarker (478 puntos).
_NOSE_TIP = 1
_EYE_OUTER_A = 33      # comisura externa de un ojo
_EYE_OUTER_B = 263     # comisura externa del otro ojo
_FACE_SIDE_A = 234     # contorno lateral de la cara (una mejilla/oreja)
_FACE_SIDE_B = 454     # contorno lateral opuesto
_CHIN = 152            # mentón
_MIN_LANDMARKS = 468   # necesitamos al menos la malla facial completa


def _xy(landmark):
    """Devuelve (x, y) tanto si el punto es una tupla como un objeto .x/.y."""
    if landmark is None:
        return None
    if isinstance(landmark, (tuple, list)):
        return float(landmark[0]), float(landmark[1])
    return float(getattr(landmark, "x")), float(getattr(landmark, "y"))


class HeadCursorController:
    """Mueve el cursor del sistema según la orientación de la cabeza.

    Parámetros inyectables (para no acoplar ni romper pruebas):
      click_fn(): hace un clic izquierdo seguro (por defecto, el de pc_control).
      is_blocked(): si devuelve True, el control se abstiene (p. ej. mientras
                    Yue ejecuta una orden de PC, para no pelear por el ratón).
      notify(texto): para avisos opcionales (voz/chat).
    """

    def __init__(self, click_fn=None, is_blocked=None, notify=None):
        self._click_fn = click_fn
        self._is_blocked = is_blocked
        self._notify = notify
        self._active = False

        # Estado de orientación / movimiento.
        self._baseline = None          # (yaw, pitch) de "mirar de frente"
        self._calibrating = 0          # nº de fotogramas de calibración restantes
        self._calib_acc = [0.0, 0.0]   # acumulador para promediar el frente
        self._vx = 0.0                 # velocidad suavizada (px/frame)
        self._vy = 0.0

        # Estado del dwell-clic.
        self._dwell_anchor = None      # (x, y) donde empezó a quedarse quieto
        self._dwell_since = 0.0
        self._dwell_fired = False
        self._last_click = 0.0

        self._load_params()

    # ------------------------------------------------------------------
    def _load_params(self):
        """Lee los parámetros de config (se relee al activar, por si cambian)."""
        self.deadzone = float(getattr(config, "HEAD_CONTROL_DEADZONE", 0.06))
        self.gain = float(getattr(config, "HEAD_CONTROL_GAIN", 55.0))
        self.max_speed = float(getattr(config, "HEAD_CONTROL_MAX_SPEED", 38.0))
        self.smoothing = min(0.95, max(0.0, float(getattr(config, "HEAD_CONTROL_SMOOTHING", 0.5))))
        self.dwell_ms = int(getattr(config, "HEAD_CONTROL_DWELL_MS", 900))
        self.dwell_radius = int(getattr(config, "HEAD_CONTROL_DWELL_RADIUS", 22))
        self.dwell_cooldown_ms = int(getattr(config, "HEAD_CONTROL_DWELL_COOLDOWN_MS", 700))
        self.margin = int(getattr(config, "HEAD_CONTROL_MARGIN", 3))
        self.invert_x = bool(getattr(config, "HEAD_CONTROL_INVERT_X", False))
        self.invert_y = bool(getattr(config, "HEAD_CONTROL_INVERT_Y", False))

    # ------------------------------------------------------------------
    @property
    def is_active(self) -> bool:
        return self._active

    def enable(self) -> bool:
        self._load_params()
        self._reset_motion()
        # Al activar, calibramos "el frente" con los próximos fotogramas para que
        # la zona muerta se centre en la postura real del usuario.
        self._baseline = None
        self._calibrating = 8
        self._calib_acc = [0.0, 0.0]
        self._active = True
        return True

    def disable(self) -> bool:
        self._active = False
        self._reset_motion()
        return False

    def toggle(self) -> bool:
        return self.disable() if self._active else self.enable()

    def recenter(self):
        """Vuelve a tomar la postura actual como 'mirar de frente'."""
        self._baseline = None
        self._calibrating = 8
        self._calib_acc = [0.0, 0.0]

    def _reset_motion(self):
        self._vx = self._vy = 0.0
        self._dwell_anchor = None
        self._dwell_fired = False
        self._dwell_since = 0.0

    # ------------------------------------------------------------------
    def _estimate_orientation(self, landmarks):
        """Calcula (yaw, pitch) aproximados a partir de nariz/ojos/mejillas.

        Valores normalizados por el tamaño de la cara, así que no dependen de la
        distancia a la cámara. Devuelve None si faltan puntos.
        """
        if not landmarks or len(landmarks) < _MIN_LANDMARKS:
            return None
        try:
            nose = _xy(landmarks[_NOSE_TIP])
            eye_a = _xy(landmarks[_EYE_OUTER_A])
            eye_b = _xy(landmarks[_EYE_OUTER_B])
            side_a = _xy(landmarks[_FACE_SIDE_A])
            side_b = _xy(landmarks[_FACE_SIDE_B])
            chin = _xy(landmarks[_CHIN])
        except (IndexError, TypeError, ValueError):
            return None
        if None in (nose, eye_a, eye_b, side_a, side_b, chin):
            return None

        eye_mid_x = (eye_a[0] + eye_b[0]) / 2.0
        eye_mid_y = (eye_a[1] + eye_b[1]) / 2.0
        face_width = abs(side_b[0] - side_a[0])
        face_height = abs(chin[1] - eye_mid_y)
        if face_width < 1e-4 or face_height < 1e-4:
            return None

        # Yaw: desplazamiento horizontal de la nariz respecto al centro de los
        # ojos, escalado por medio ancho de cara -> ~[-1, 1].
        yaw = (nose[0] - eye_mid_x) / (0.5 * face_width)
        # Pitch: posición vertical de la nariz entre ojos y mentón.
        pitch = (nose[1] - eye_mid_y) / face_height
        return yaw, pitch

    def _deadzoned(self, value: float) -> float:
        """Aplica la zona muerta: por debajo del umbral, cero; luego arranca
        desde cero en el borde (sin salto)."""
        if abs(value) < self.deadzone:
            return 0.0
        return value - math.copysign(self.deadzone, value)

    # ------------------------------------------------------------------
    def process_landmarks(self, landmarks, frame_shape=None):
        """Punto de entrada por fotograma. Seguro de llamar siempre.

        Si el control no está activo, o no hay rostro, o algo falla, no hace
        nada (y nunca lanza excepciones hacia el hilo de la cámara).
        """
        if not self._active:
            return
        try:
            if self._is_blocked is not None and self._is_blocked():
                # Alguien más está usando el ratón (p. ej. una orden de PC):
                # nos apartamos para no pelear por el cursor.
                self._reset_motion()
                return

            orient = self._estimate_orientation(landmarks)
            if orient is None:
                # Sin rostro fiable: frenamos en seco pero mantenemos el dwell.
                self._vx = self._vy = 0.0
                return
            yaw, pitch = orient

            # Calibración del "frente" durante los primeros fotogramas.
            if self._calibrating > 0:
                self._calib_acc[0] += yaw
                self._calib_acc[1] += pitch
                self._calibrating -= 1
                if self._calibrating == 0:
                    n = 8.0
                    self._baseline = (self._calib_acc[0] / n, self._calib_acc[1] / n)
                return
            if self._baseline is None:
                self._baseline = (yaw, pitch)
                return

            off_yaw = self._deadzoned(yaw - self._baseline[0])
            off_pitch = self._deadzoned(pitch - self._baseline[1])

            dx = self.gain * off_yaw
            dy = self.gain * off_pitch
            if self.invert_x:
                dx = -dx
            if self.invert_y:
                dy = -dy

            # Suavizado exponencial para atenuar el temblor y los saltos.
            self._vx = self.smoothing * self._vx + (1.0 - self.smoothing) * dx
            self._vy = self.smoothing * self._vy + (1.0 - self.smoothing) * dy

            vx, vy = self._clamp_speed(self._vx, self._vy)
            self._apply(vx, vy)
        except Exception as exc:
            # Nunca dejamos que un fallo del control tumbe el hilo de la cámara.
            print("[head] fallo procesando el fotograma:", exc)

    def _clamp_speed(self, vx: float, vy: float):
        mag = math.hypot(vx, vy)
        if mag > self.max_speed and mag > 0:
            factor = self.max_speed / mag
            vx *= factor
            vy *= factor
        return vx, vy

    def _apply(self, vx: float, vy: float):
        """Mueve el cursor de forma RELATIVA y evalúa el dwell-clic."""
        try:
            import pyautogui
        except Exception:
            return  # sin pyautogui no hay control; se calla en vez de romper

        try:
            width, height = pyautogui.size()
            pos = pyautogui.position()
            cx, cy = int(pos[0]), int(pos[1])
        except Exception:
            return

        lo_x, hi_x = self.margin, max(self.margin, width - 1 - self.margin)
        lo_y, hi_y = self.margin, max(self.margin, height - 1 - self.margin)

        moved = abs(vx) >= 0.5 or abs(vy) >= 0.5
        if moved:
            nx = int(min(max(lo_x, cx + vx), hi_x))
            ny = int(min(max(lo_y, cy + vy), hi_y))
            if nx != cx or ny != cy:
                try:
                    pyautogui.FAILSAFE = True
                    pyautogui.moveTo(nx, ny)
                except Exception:
                    # p. ej. FAILSAFE si el cursor ya estaba en una esquina:
                    # saltamos este fotograma sin más.
                    return
                cx, cy = nx, ny

        self._update_dwell(cx, cy)

    def _update_dwell(self, x: int, y: int):
        """Dispara un clic izquierdo si el cursor lleva quieto lo suficiente."""
        now = time.time()
        if self._dwell_anchor is None:
            self._dwell_anchor = (x, y)
            self._dwell_since = now
            self._dwell_fired = False
            return

        dist = math.hypot(x - self._dwell_anchor[0], y - self._dwell_anchor[1])
        if dist > self.dwell_radius:
            # Se movió: reiniciamos el conteo con el nuevo ancla.
            self._dwell_anchor = (x, y)
            self._dwell_since = now
            self._dwell_fired = False
            return

        if self._dwell_fired:
            return  # ya clicó en esta parada; espera a que se mueva otra vez
        held_ms = (now - self._dwell_since) * 1000.0
        cooled = (now - self._last_click) * 1000.0 >= self.dwell_cooldown_ms
        if held_ms >= self.dwell_ms and cooled:
            self._do_click()
            self._dwell_fired = True
            self._last_click = now

    def _do_click(self):
        """Clic izquierdo por el camino SEGURO (mismos límites que pc_control)."""
        try:
            if self._click_fn is not None:
                self._click_fn()  # p. ej. PCController.safe_click (solo izquierdo)
            else:
                import pyautogui
                pyautogui.FAILSAFE = True
                pyautogui.click(button="left")
        except Exception as exc:
            print("[head] no pude hacer el clic por dwell:", exc)

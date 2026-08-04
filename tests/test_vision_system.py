"""Pruebas del YUE V3 PERCEPTION SYSTEM.

No requieren webcam ni DeepFace: la cámara, el detector de rostro y el motor de
emociones se INYECTAN con dobles de prueba. Cubren el punto 11 del pedido:

  - cámara disponible / fallo de cámara / liberación de recursos,
  - rostro detectado y presencia,
  - emoción procesada (con throttle por perfil),
  - modo de bajo rendimiento (perfiles LOW/MEDIUM/HIGH),
  - atención: presente / ausente / rostro perdido / ausencia larga,
  - gestor de emociones con límites y personajes YUE/KAI,
  - avatar reactivo (espejo empático),
  - bus de eventos,
  - integración de VisionController (opt-in) de punta a punta.

Ejecuta:  python -m unittest tests.test_vision_system    (desde yue_companion/)
o bien:   python tests/test_vision_system.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest

# Permite ejecutar el archivo directamente (python tests/test_vision_system.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.vision.attention_detector import AttentionDetector
from core.vision import avatar_emotion_controller as avatar_ctl
from core.vision.camera import CameraEngine
from core.vision.emotion_ai import EmotionAI
from core.vision.emotion_manager import EmotionManager
from core.vision.events import EventBus, VisionEvent, EVENT_EMOTION, EVENT_ABSENCE
from core.vision.face_detector import FaceDetector
from core.vision import perf_profile
from core.vision.vision_controller import VisionController


# --------------------------------------------------------------------------
# Dobles de prueba
# --------------------------------------------------------------------------
class FakeCapture:
    """Simula cv2.VideoCapture: entrega N fotogramas y luego 'se desconecta'."""

    def __init__(self, frames=5, fail=False):
        self._left = frames
        self._fail = fail
        self.released = False

    def read(self):
        if self._fail or self._left <= 0:
            return False, None
        self._left -= 1
        # Un "fotograma" con .shape y slicing (para que el recorte del ROI no
        # salga vacío). Nadie lo decodifica: es un doble de prueba.
        return True, _Frame(640, 480)

    def isOpened(self):
        return not self._fail

    def set(self, *_a):
        return True

    def release(self):
        self.released = True


def face_provider_center(_frame):
    """Rostro grande y centrado (mirando de frente)."""
    return [(220, 140, 200, 200, 0.9)]  # sobre un cuadro ~640x480


def face_provider_side(_frame):
    """Rostro presente pero desplazado a un lado (no atento)."""
    return [(10, 20, 120, 120, 0.9)]


def face_provider_none(_frame):
    return []


def analyzer_happy(_roi):
    return {"emotion": "happy", "confidence": 92}


def analyzer_sad(_roi):
    return {"emotion": "sad", "confidence": 88}


# --------------------------------------------------------------------------
class TestPerfProfiles(unittest.TestCase):
    def test_valores_exactos_del_documento(self):
        low = perf_profile.resolve_profile("LOW")
        med = perf_profile.resolve_profile("MEDIUM")
        high = perf_profile.resolve_profile("HIGH")
        self.assertEqual(low.emotion_interval, 8.0)
        self.assertEqual(med.emotion_interval, 5.0)
        self.assertEqual(high.emotion_interval, 2.0)
        self.assertTrue(10 <= low.target_fps <= 15)
        self.assertEqual(med.target_fps, 20.0)
        self.assertEqual(high.target_fps, 30.0)

    def test_desconocido_cae_a_algo_valido(self):
        p = perf_profile.resolve_profile("NO_EXISTE")
        self.assertIn(p.name, ("LOW", "MEDIUM", "HIGH"))


class TestCameraEngine(unittest.TestCase):
    def test_entrega_frames_y_libera(self):
        recibido = []
        cap = FakeCapture(frames=3)
        done = threading.Event()

        def factory(_profile):
            return cap, 0

        def on_frame(_frame, index):
            recibido.append(index)
            if len(recibido) >= 3:
                done.set()

        eng = CameraEngine(frame_callback=on_frame, capture_factory=factory,
                           profile=perf_profile.resolve_profile("HIGH"))
        eng.start()
        done.wait(timeout=3.0)
        eng.stop()
        self.assertGreaterEqual(len(recibido), 3)
        self.assertTrue(cap.released, "la cámara debe liberarse al parar")

    def test_fallo_de_camara_no_rompe(self):
        estados = []
        eng = CameraEngine(
            frame_callback=lambda *_a: None,
            status_callback=lambda t, a: estados.append(a),
            capture_factory=lambda _p: (None, -1),
        )
        eng.start()
        time.sleep(0.4)
        eng.stop()
        self.assertFalse(eng.active)
        self.assertIn(False, estados)  # informó "sin cámara"


class TestFaceDetector(unittest.TestCase):
    def test_presencia_y_posicion(self):
        det = FaceDetector(box_provider=face_provider_center)
        # frame ndarray-like con shape: usamos un objeto con .shape
        res = det.detect(_Frame(640, 480))
        self.assertTrue(res.user_present)
        self.assertEqual(res.as_dict()["user_present"], True)
        self.assertIn("face_position", res.as_dict())
        self.assertAlmostEqual(res.face_position["x"], (220 + 100) / 640, places=2)

    def test_sin_rostro(self):
        det = FaceDetector(box_provider=face_provider_none)
        res = det.detect(_Frame(640, 480))
        self.assertFalse(res.user_present)
        self.assertEqual(res.as_dict(), {"user_present": False})


class TestEmotionAI(unittest.TestCase):
    def test_lectura_y_formato(self):
        ai = EmotionAI(emotion_interval=5.0, analyzer=analyzer_happy)
        r = ai.analyze(roi=object(), now=100.0, force=True)
        self.assertIsNotNone(r)
        self.assertEqual(r.as_dict(), {"emotion": "happy", "confidence": 92})

    def test_throttle_por_perfil(self):
        llamadas = {"n": 0}

        def contador(_roi):
            llamadas["n"] += 1
            return {"emotion": "sad", "confidence": 70}

        ai = EmotionAI(emotion_interval=8.0, analyzer=contador)
        ai.analyze(object(), now=0.0)       # corre
        ai.analyze(object(), now=3.0)       # dentro del intervalo -> caché
        ai.analyze(object(), now=7.9)       # aún dentro -> caché
        ai.analyze(object(), now=8.1)       # pasó el intervalo -> corre
        self.assertEqual(llamadas["n"], 2)

    def test_emocion_invalida_se_descarta(self):
        ai = EmotionAI(analyzer=lambda _r: {"emotion": "aburrido", "confidence": 99})
        self.assertIsNone(ai.analyze(object(), now=0.0, force=True))


class TestAttentionDetector(unittest.TestCase):
    def test_transiciones_basicas(self):
        det = AttentionDetector(absence_seconds=600, center_band=0.22)
        u1 = det.update(True, {"x": 0.5, "y": 0.5}, now=0.0)
        self.assertTrue(u1.presence_changed)
        self.assertEqual(u1.state, "atento")

        u2 = det.update(True, {"x": 0.05, "y": 0.5}, now=1.0)  # a un lado
        self.assertEqual(u2.state, "presente")
        self.assertTrue(u2.attention_changed)

        u3 = det.update(False, None, now=2.0)   # se va
        self.assertTrue(u3.face_lost)
        self.assertEqual(u3.state, "ausente")

    def test_ausencia_larga_una_sola_vez(self):
        det = AttentionDetector(absence_seconds=600)
        det.update(True, {"x": 0.5, "y": 0.5}, now=0.0)
        det.update(False, None, now=1.0)
        antes = det.update(False, None, now=599.0)
        self.assertFalse(antes.long_absence)
        cruza = det.update(False, None, now=602.0)
        self.assertTrue(cruza.long_absence)
        otra = det.update(False, None, now=900.0)
        self.assertFalse(otra.long_absence)  # no se repite


class TestEmotionManager(unittest.TestCase):
    def test_limite_por_hora(self):
        m = EmotionManager(persona="YUE")
        m.max_per_hour = 2
        m.cooldown = 0.0
        r1 = m.maybe_react("happy", 90, now=0.0)
        r2 = m.maybe_react("sad", 90, now=1.0)
        r3 = m.maybe_react("angry", 90, now=2.0)  # excede el tope
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)
        self.assertIsNone(r3)

    def test_neutral_y_baja_confianza_callan(self):
        m = EmotionManager(persona="YUE")
        self.assertIsNone(m.maybe_react("neutral", 99, now=0.0))
        self.assertIsNone(m.maybe_react("happy", 10, now=1.0))

    def test_enfriamiento_misma_emocion(self):
        m = EmotionManager(persona="YUE")
        m.cooldown = 120.0
        self.assertIsNotNone(m.maybe_react("sad", 90, now=0.0))
        self.assertIsNone(m.maybe_react("sad", 90, now=30.0))     # muy pronto
        self.assertIsNotNone(m.maybe_react("sad", 90, now=200.0))  # ya pasó

    def test_personaje_kai(self):
        m = EmotionManager(persona="KAI")
        r = m.maybe_react("sad", 90, now=0.0)
        self.assertEqual(r.persona, "KAI")
        self.assertIn("escucho", r.text.lower())


class TestAvatarController(unittest.TestCase):
    def test_espejo_empatico(self):
        # triste/enojo -> preocupación (no imita); feliz -> feliz.
        self.assertEqual(avatar_ctl.empathic_avatar_emotion("sad")[0], "worried")
        self.assertEqual(avatar_ctl.empathic_avatar_emotion("angry")[0], "worried")
        self.assertEqual(avatar_ctl.empathic_avatar_emotion("happy")[0], "happy")
        self.assertIsNone(avatar_ctl.empathic_avatar_emotion("neutral"))

    def test_estados_con_nombre(self):
        self.assertEqual(avatar_ctl.state("FELIZ")[0], "happy")
        self.assertEqual(avatar_ctl.state("SORPRENDIDO")[0], "surprised")
        self.assertEqual(avatar_ctl.state("NORMAL")[0], "neutral")


class TestEventBus(unittest.TestCase):
    def test_suscripcion_por_tipo(self):
        bus = EventBus()
        recibidos = []
        bus.subscribe(lambda e: recibidos.append(e), EVENT_EMOTION)
        bus.emit(VisionEvent.emotion("happy", 90))
        bus.emit(VisionEvent.presence(True))     # otro tipo: no llega
        self.assertEqual(len(recibidos), 1)
        self.assertEqual(recibidos[0].data, {"emotion": "happy", "confidence": 90})

    def test_suscriptor_que_falla_no_tumba_al_resto(self):
        bus = EventBus()
        ok = []
        bus.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
        bus.subscribe(lambda e: ok.append(e))
        bus.emit(VisionEvent.face_lost())
        self.assertEqual(len(ok), 1)


class TestVisionControllerIntegration(unittest.TestCase):
    def test_desactivado_no_arranca(self):
        vc = VisionController(camera_factory=lambda _p: (FakeCapture(3), 0),
                              analyzer=analyzer_happy,
                              face_provider=face_provider_center)
        vc.enabled = False
        vc.start()  # no debe abrir cámara
        self.assertFalse(vc.active)

    def test_flujo_completo_emocion(self):
        eventos = []
        gestos = []
        dichos = []

        vc = VisionController(
            on_avatar_emotion=lambda n, i, d: gestos.append(n),
            on_speak=lambda t: dichos.append(t),
            face_provider=face_provider_center,
            analyzer=analyzer_sad,
            camera_factory=lambda _p: (FakeCapture(frames=50), 0),
            profile=perf_profile.resolve_profile("HIGH"),
            persona="YUE",
        )
        vc.enabled = True
        vc.bus.subscribe(lambda e: eventos.append(e.type))
        vc.start()
        # Esperamos a que llegue al menos un evento de emoción.
        deadline = time.time() + 4.0
        while time.time() < deadline and EVENT_EMOTION not in eventos:
            time.sleep(0.05)
        vc.stop()

        self.assertIn(EVENT_EMOTION, eventos, "debió emitir emotion_detected")
        self.assertIn("worried", gestos, "tristeza -> avatar preocupado")
        self.assertTrue(dichos, "YUE debió comentar la tristeza al menos una vez")
        snap = vc.snapshot()
        self.assertTrue(snap["user_present"])
        self.assertEqual(snap["emotion"]["emotion"], "sad")

    def test_ausencia_dispara_evento_y_frase(self):
        # Cámara que solo ve 'nada' -> tras cruzar el umbral corto, ausencia larga.
        eventos = []
        dichos = []
        vc = VisionController(
            on_speak=lambda t: dichos.append(t),
            face_provider=face_provider_none,
            camera_factory=lambda _p: (FakeCapture(frames=100), 0),
            profile=perf_profile.resolve_profile("HIGH"),
        )
        vc.enabled = True
        # Umbral de ausencia diminuto para la prueba.
        vc.attention.absence_seconds = 0.2
        vc.bus.subscribe(lambda e: eventos.append(e.type), EVENT_ABSENCE)
        vc.start()
        deadline = time.time() + 4.0
        while time.time() < deadline and EVENT_ABSENCE not in eventos:
            time.sleep(0.05)
        vc.stop()
        self.assertIn(EVENT_ABSENCE, eventos)
        self.assertTrue(any("esperando" in d.lower() or "sigo" in d.lower() for d in dichos))


class _Frame:
    """Objeto mínimo con .shape y slicing, para el FaceDetector sin numpy."""

    def __init__(self, w, h):
        self.shape = (h, w, 3)
        self.size = w * h

    def __getitem__(self, _slices):
        return self  # el ROII no se decodifica en las pruebas


if __name__ == "__main__":
    unittest.main(verbosity=2)

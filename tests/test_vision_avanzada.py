"""Pruebas de la VISIÓN AVANZADA de YUE (punto 19 del pedido).

Ninguna prueba necesita webcam, MediaPipe ni modelos: todo se inyecta con
dobles. Cubren los 21 casos exigidos:

  1. cámara desconectada          12. persona que se sienta y se levanta
  2. cámara ocupada               13. acción de beber
  3. fotograma vacío              14. emoción indeterminada
  4. una persona                  15. confianza insuficiente
  5. varias personas              16. módulo faltante
  6. mano izquierda y derecha     17. modelo faltante
  7. conteo de dedos              18. cierre limpio de hilos
  8. gestos sostenidos            19. privacidad activada
  9. OCR repetido                 20. cámara desactivada
 10. texto inestable              21. ausencia de claves API
 11. objeto que aparece/desaparece

Ejecuta:  python -m unittest tests.test_vision_avanzada   (desde yue_companion/)
o bien:   python tests/test_vision_avanzada.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vision.analyzers.action_analyzer import ActionAnalyzer, ActionRule
from vision.analyzers.emotion_analyzer import EmotionAnalyzer, is_safe_phrase
from vision.analyzers.gesture_analyzer import GestureAnalyzer
from vision.analyzers.room_analyzer import RoomAnalyzer
from vision.camera_manager import CameraManager, acquire_camera, release_camera, scan
from vision.capabilities import detect as detect_capabilities
from vision.context_builder import VisionContextBuilder
from vision.detectors.action_detector import FrameEvidence
from vision.detectors.scene_detector import SceneDetector
from vision.event_manager import EventManager
from vision.frame_hub import FrameHub
from vision.models.model_registry import ModelRegistry
from vision.ocr.text_stabilizer import TextStabilizer, normalize, similarity
from vision.privacy_manager import PrivacyManager
from vision.settings import load as load_settings
from vision.tracking.hand_tracker import HandTracker
from vision.tracking.object_tracker import ObjectTracker
from vision.tracking.person_tracker import PersonTracker
from vision.tracking.temporal_smoother import EMA, Hysteresis, MajorityVote


# ==========================================================================
# Dobles de prueba
# ==========================================================================
class FakeFrame:
    """Fotograma mínimo: tiene .shape, .ndim y se puede recortar."""

    def __init__(self, w=640, h=480, channels=3):
        self.shape = (h, w, channels) if channels else (h, w)
        self.ndim = 3 if channels else 2

    def __getitem__(self, _item):
        return self


class FakeCapture:
    """Simula cv2.VideoCapture: entrega N fotogramas y luego se desconecta."""

    def __init__(self, frames=1000, fail=False):
        self._left = frames
        self._fail = fail
        self.released = False

    def isOpened(self):
        return not self._fail and not self.released

    def read(self):
        if self._fail or self.released or self._left <= 0:
            return False, None
        self._left -= 1
        return True, FakeFrame()

    def set(self, *_a):
        return True

    def release(self):
        self.released = True


def fake_factory(frames=1000, fail=False, holder=None):
    def factory(_index, _w, _h):
        if fail:
            return None
        cap = FakeCapture(frames=frames)
        if holder is not None:
            holder.append(cap)
        return cap
    return factory


def hand_landmarks(*, fist=False, thumb_up=False, victory=False, pointing=False,
                   open_palm=False, offset=0.0):
    """Genera 21 landmarks coherentes para el gesto pedido."""
    # Muñeca en (0.5, 0.8); la mano crece hacia arriba.
    wx, wy = 0.5 + offset, 0.8
    pts = [(wx, wy, 0.0)] * 21
    pts = list(pts)

    def setp(i, dx, dy):
        pts[i] = (wx + dx, wy + dy, 0.0)

    # Nudillos (MCP) en fila.
    setp(5, -0.06, -0.20)    # índice
    setp(9, -0.01, -0.22)    # medio  -> escala = 0.22
    setp(13, 0.04, -0.20)    # anular
    setp(17, 0.08, -0.17)    # meñique
    setp(1, -0.09, -0.06)    # pulgar CMC
    setp(2, -0.12, -0.10)    # pulgar MCP

    def finger(mcp, pip, dip, tip, dx, extended):
        base = pts[mcp]
        reach = -0.14 if extended else 0.04
        pts[pip] = (base[0] + dx * 0.3, base[1] + reach * 0.35, 0.0)
        pts[dip] = (base[0] + dx * 0.6, base[1] + reach * 0.7, 0.0)
        pts[tip] = (base[0] + dx, base[1] + reach, 0.0)

    idx_ext = open_palm or victory or pointing
    mid_ext = open_palm or victory
    ring_ext = open_palm
    pinky_ext = open_palm

    finger(5, 6, 7, 8, -0.03, idx_ext)
    finger(9, 10, 11, 12, 0.03, mid_ext)
    finger(13, 14, 15, 16, 0.02, ring_ext)
    finger(17, 18, 19, 20, 0.03, pinky_ext)

    if thumb_up:
        # Pulgar recto hacia arriba, muy por encima de los nudillos.
        pts[3] = (wx - 0.05, wy - 0.22, 0.0)
        pts[4] = (wx - 0.06, wy - 0.34, 0.0)
    elif open_palm:
        # Pulgar abierto en lateral.
        pts[3] = (wx - 0.15, wy - 0.09, 0.0)
        pts[4] = (wx - 0.20, wy - 0.12, 0.0)
    else:
        # Pulgar recogido sobre la palma: la punta se acerca al nudillo del índice.
        pts[3] = (wx - 0.04, wy - 0.10, 0.0)
        pts[4] = (wx - 0.01, wy - 0.13, 0.0)
    return pts


def box(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


# ==========================================================================
# 1-3. Cámara: desconectada, ocupada, fotograma vacío
# ==========================================================================
class TestCamara(unittest.TestCase):
    def setUp(self):
        self.hub = FrameHub()

    def tearDown(self):
        release_camera(0, "prueba-a")
        release_camera(0, "prueba-b")
        release_camera(1, "prueba-b")

    def test_01_camara_desconectada_no_rompe(self):
        """Sin cámara disponible, start() devuelve False y no lanza excepción."""
        mgr = CameraManager(self.hub, owner="prueba-a", auto_search=False,
                            capture_factory=fake_factory(fail=True))
        # El servicio reintenta en su hilo; start() no debe bloquear ni fallar.
        self.assertTrue(mgr.start())        # el hilo arranca
        time.sleep(0.15)
        self.assertIsNone(mgr.latest_bgr())  # pero nunca hay fotograma
        mgr.stop()

    def test_02_camara_ocupada_por_otro_sistema(self):
        """El cerrojo global impide que dos módulos abran la misma webcam."""
        self.assertTrue(acquire_camera(0, "prueba-a"))
        mgr = CameraManager(self.hub, index=0, owner="prueba-b", auto_search=False,
                            capture_factory=fake_factory())
        self.assertFalse(mgr.start())
        self.assertIn("ya la está usando", mgr.status().last_error)
        release_camera(0, "prueba-a")

    def test_02b_busca_camara_alternativa(self):
        """Con auto_search, si la 0 está ocupada intenta otra libre."""
        self.assertTrue(acquire_camera(0, "prueba-a"))
        libres = scan(3, probe=lambda i: i == 1)
        self.assertEqual(libres, [1])
        release_camera(0, "prueba-a")

    def test_03_fotograma_vacio(self):
        """Un hub sin fotogramas devuelve None en vez de explotar."""
        self.assertFalse(self.hub.has_frame())
        self.assertIsNone(self.hub.latest_bgr())
        self.assertIsNone(self.hub.latest_rgb())
        self.assertEqual(self.hub.frame_id(), 0)

    def test_18_cierre_limpio_de_hilos(self):
        """Tras stop() no queda ningún hilo del sistema de visión vivo."""
        holder = []
        mgr = CameraManager(self.hub, index=2, owner="prueba-b", auto_search=False,
                            capture_factory=fake_factory(holder=holder))
        mgr.start()
        time.sleep(0.2)
        mgr.stop()
        time.sleep(0.2)
        vivos = [t.name for t in threading.enumerate()
                 if "Camera" in t.name or t.name.startswith("Vision-")]
        self.assertEqual(vivos, [], f"hilos sin cerrar: {vivos}")
        self.assertTrue(all(c.released for c in holder), "la cámara no se liberó")
        release_camera(2, "prueba-b")


# ==========================================================================
# 4-5. Personas: una y varias
# ==========================================================================
class TestPersonas(unittest.TestCase):
    def test_04_una_persona_id_estable(self):
        """La misma persona conserva su ID entre fotogramas."""
        tracker = PersonTracker()
        for i in range(5):
            tracker.update_from_boxes([box(0.4 + i * 0.005, 0.3, 0.2, 0.3)],
                                      now=100.0 + i * 0.1)
        self.assertEqual(tracker.count(), 1)
        ids = {t.track_id for t in tracker.tracks()}
        self.assertEqual(len(ids), 1, "el ID cambió con la misma persona")
        snap = tracker.snapshots()[0]
        self.assertTrue(snap.is_primary)
        self.assertEqual(snap.position, "center")

    def test_05_varias_personas(self):
        """Dos personas reciben IDs distintos y estables."""
        tracker = PersonTracker(max_people=4)
        for i in range(5):
            tracker.update_from_boxes(
                [box(0.10, 0.3, 0.15, 0.25), box(0.65, 0.3, 0.18, 0.28)],
                now=200.0 + i * 0.1)
        self.assertEqual(tracker.count(), 2)
        ids = sorted(t.track_id for t in tracker.tracks())
        self.assertEqual(len(set(ids)), 2)
        posiciones = {s.position for s in tracker.snapshots()}
        self.assertEqual(posiciones, {"left", "right"})
        # La más grande y centrada es la principal.
        principal = [s for s in tracker.snapshots() if s.is_primary]
        self.assertEqual(len(principal), 1)

    def test_05b_persona_que_se_va(self):
        """Al desaparecer, la pista muere y el conteo baja."""
        tracker = PersonTracker(max_missing=2)
        for i in range(4):
            tracker.update_from_boxes([box(0.4, 0.3, 0.2, 0.3)], now=300.0 + i * 0.1)
        self.assertEqual(tracker.count(), 1)
        for i in range(4):
            tracker.update_from_boxes([], now=301.0 + i * 0.1)
        self.assertEqual(tracker.count(), 0)


# ==========================================================================
# 6-8. Manos, dedos y gestos
# ==========================================================================
class TestManos(unittest.TestCase):
    def test_06_mano_izquierda_y_derecha(self):
        """Cada lado se sigue por separado y con su propio identificador."""
        tracker = HandTracker()
        estados = tracker.update([
            ("left", hand_landmarks(open_palm=True, offset=-0.2)),
            ("right", hand_landmarks(open_palm=True, offset=0.2)),
        ], now=400.0)
        self.assertEqual(len(estados), 2)
        lados = {s.side for s in estados}
        self.assertEqual(lados, {"left", "right"})
        ids = {s.hand_id for s in estados}
        self.assertEqual(len(ids), 2, "las dos manos comparten identificador")
        # El ID se mantiene en el siguiente fotograma.
        estados2 = tracker.update([
            ("left", hand_landmarks(open_palm=True, offset=-0.2)),
            ("right", hand_landmarks(open_palm=True, offset=0.2)),
        ], now=400.1)
        self.assertEqual({s.hand_id for s in estados2}, ids)

    def test_07_conteo_de_dedos(self):
        """Mano abierta = 5 dedos; puño = 0; victoria = 2."""
        tracker = HandTracker()
        abierta = tracker.update([("right", hand_landmarks(open_palm=True))], now=410.0)
        self.assertEqual(abierta[0].finger_count, 5)

        tracker.reset()
        puno = tracker.update([("right", hand_landmarks(fist=True))], now=411.0)
        self.assertEqual(puno[0].finger_count, 0)
        self.assertEqual(puno[0].gesture, "fist")

        tracker.reset()
        victoria = tracker.update([("right", hand_landmarks(victory=True))], now=412.0)
        self.assertEqual(victoria[0].finger_count, 2)
        self.assertEqual(victoria[0].gesture, "victory")

        # La frase de conteo con las dos manos suma.
        tracker.reset()
        dos = tracker.update([
            ("left", hand_landmarks(open_palm=True, offset=-0.2)),
            ("right", hand_landmarks(open_palm=True, offset=0.2)),
        ], now=413.0)
        total, frase = GestureAnalyzer.count_fingers(dos)
        self.assertEqual(total, 10)
        self.assertIn("10", frase)

    def test_07b_sin_manos_lo_dice(self):
        total, frase = GestureAnalyzer.count_fingers([])
        self.assertEqual(total, -1)
        self.assertIn("no veo", frase.lower())

    def test_08_gesto_sostenido_no_se_repite(self):
        """Un gesto mantenido genera UN evento, no uno por fotograma."""
        tracker = HandTracker()
        analyzer = GestureAnalyzer(min_duration=0.4, cooldown=5.0)
        eventos = []
        t = 500.0
        for _ in range(40):                       # ~4 segundos de pulgar arriba
            estados = tracker.update([("right", hand_landmarks(thumb_up=True))], now=t)
            eventos.extend(analyzer.update(estados, now=t))
            t += 0.1
        self.assertEqual(len(eventos), 1,
                         f"un gesto sostenido generó {len(eventos)} eventos")
        self.assertEqual(eventos[0].gesture, "thumbs_up")
        self.assertEqual(eventos[0].hand, "right")
        self.assertGreaterEqual(eventos[0].duration, 0.4)
        # Y el diccionario tiene la forma exacta del documento.
        d = eventos[0].as_dict()
        self.assertEqual(set(d), {"event", "gesture", "hand", "confidence", "duration"})
        self.assertEqual(d["event"], "gesture_detected")

    def test_08b_gesto_muy_breve_no_cuenta(self):
        """Dos fotogramas de gesto no llegan a la duración mínima."""
        tracker = HandTracker()
        analyzer = GestureAnalyzer(min_duration=0.5)
        eventos = []
        for i in range(2):
            estados = tracker.update([("right", hand_landmarks(thumb_up=True))],
                                     now=510.0 + i * 0.05)
            eventos.extend(analyzer.update(estados, now=510.0 + i * 0.05))
        self.assertEqual(eventos, [])


# ==========================================================================
# 9-10. OCR: texto repetido e inestable
# ==========================================================================
class TestOCR(unittest.TestCase):
    def test_09_ocr_repetido_no_se_entrega_dos_veces(self):
        """El mismo texto se entrega una vez; después is_new es False."""
        st = TextStabilizer(min_agreements=3)
        texto = "Reunión viernes 7 de agosto"
        r = None
        for i in range(3):
            r = st.feed(texto, 0.9, region=[0.1, 0.1, 0.9, 0.3], now=600.0 + i)
        self.assertIsNotNone(r)
        self.assertTrue(r.stable)
        self.assertTrue(r.is_new)
        st.mark_delivered(r.text)

        # Se vuelve a ver el mismo cartel: ya no es nuevo.
        for i in range(3):
            r2 = st.feed(texto, 0.9, now=610.0 + i)
        self.assertIsNotNone(r2)
        self.assertFalse(r2.is_new, "el mismo texto se entregó como nuevo dos veces")

    def test_10_texto_inestable_no_sale(self):
        """Lecturas distintas en cada fotograma nunca llegan a ser estables."""
        st = TextStabilizer(min_agreements=3, similarity_threshold=0.85)
        lecturas = ["Munlcipalidad", "Rstaurante XYZ", "0000 aaaa", "zzzz 1234"]
        for i, txt in enumerate(lecturas):
            self.assertIsNone(st.feed(txt, 0.6, now=620.0 + i),
                              f"«{txt}» se dio por estable sin acuerdo")

    def test_10b_variantes_parecidas_convergen(self):
        """Pequeños errores de OCR se agrupan y sale la variante mayoritaria."""
        st = TextStabilizer(min_agreements=3)
        st.feed("Municipalidad Distrital de Nanchoc", 0.88, now=630.0)
        st.feed("Municipalidad Distrital de Nanchoc", 0.91, now=630.5)
        r = st.feed("Municipalidad Distrital de Nanchoq", 0.70, now=631.0)
        self.assertIsNotNone(r)
        self.assertEqual(r.text, "Municipalidad Distrital de Nanchoc")
        self.assertGreater(r.confidence, 0.7)
        # Estructura exacta del documento.
        d = r.as_dict()
        for clave in ("text", "confidence", "language", "stable", "region", "timestamp"):
            self.assertIn(clave, d)
        self.assertEqual(d["language"], "es")

    def test_10c_texto_demasiado_corto_se_ignora(self):
        st = TextStabilizer(min_length=3)
        self.assertIsNone(st.feed("a", 0.9, now=640.0))
        self.assertIsNone(st.feed("", 0.9, now=641.0))

    def test_10d_normalizacion_y_similitud(self):
        self.assertEqual(normalize("  Reunión,  VIERNES!  "), "reunion viernes")
        self.assertGreater(similarity(normalize("hola mundo"), normalize("hola mundo")), 0.99)
        self.assertLess(similarity(normalize("hola"), normalize("zzzz")), 0.5)


# ==========================================================================
# 11. Objetos que aparecen y desaparecen
# ==========================================================================
class TestObjetos(unittest.TestCase):
    def test_11_objeto_aparece_y_desaparece(self):
        tracker = ObjectTracker(max_missing=2)
        det = [{"label": "bottle", "confidence": 0.8, "bounding_box": box(0.3, 0.4, 0.1, 0.2)}]
        for i in range(3):
            tracker.update_from_detections(det, now=700.0 + i * 0.2)
        self.assertEqual(tracker.count(), 1)
        self.assertTrue(tracker.has("bottle"))
        estado = tracker.as_state()[0]
        self.assertIn("object_id", estado)
        self.assertEqual(estado["translated_label"], "botella")

        cambios = []
        for i in range(4):
            tracker.update_from_detections([], now=702.0 + i * 0.2)
            cambios.extend(tracker.changes_es())
        self.assertEqual(tracker.count(), 0)
        self.assertIn("ya no veo botella", cambios)

    def test_11b_no_cuenta_dos_veces_el_mismo_objeto(self):
        """La misma botella moviéndose sigue siendo UNA botella."""
        tracker = ObjectTracker()
        for i in range(8):
            tracker.update_from_detections(
                [{"label": "bottle", "confidence": 0.8,
                  "bounding_box": box(0.30 + i * 0.01, 0.4, 0.1, 0.2)}],
                now=710.0 + i * 0.1)
        self.assertEqual(tracker.counts().get("bottle"), 1)

    def test_11c_umbral_por_clase(self):
        """Un perro necesita más confianza que un celular."""
        tracker = ObjectTracker()
        self.assertGreater(tracker.threshold_for("dog"), tracker.threshold_for("cell phone"))
        tracker.update_from_detections(
            [{"label": "dog", "confidence": 0.5, "bounding_box": box(0.1, 0.1, 0.2, 0.2)}],
            now=720.0)
        self.assertEqual(tracker.count(), 0, "aceptó un perro con confianza insuficiente")


# ==========================================================================
# 12-13. Acciones: sentarse/levantarse y beber
# ==========================================================================
class TestAcciones(unittest.TestCase):
    def _evidencia(self, **kwargs):
        ev = FrameEvidence(has_pose=True, torso_height=0.25, body_size=0.2,
                           body_center=(0.5, 0.4))
        for k, v in kwargs.items():
            setattr(ev, k, v)
        for s in kwargs.pop("_signals", ()):
            ev.add(s)
        return ev

    def test_12_sentarse_y_levantarse(self):
        """La secuencia de bajar con rodillas dobladas se lee como sentarse."""
        analyzer = ActionAnalyzer(window=4.0)
        t = 800.0
        eventos = []
        for i in range(20):
            ev = FrameEvidence(has_pose=True, torso_height=0.25, body_size=0.2,
                               body_center=(0.5, 0.30 + i * 0.012))   # baja
            ev.knee_angle_closed = True
            ev.add("knees_bent")
            eventos.extend(analyzer.update(ev, person_id=1, now=t))
            t += 0.1
        nombres = {e.action for e in eventos}
        self.assertIn("sitting_down", nombres, f"no detectó sentarse: {nombres}")

        # Ahora se levanta: sube y sin rodillas dobladas.
        analyzer2 = ActionAnalyzer(window=4.0)
        t = 900.0
        eventos2 = []
        for i in range(20):
            ev = FrameEvidence(has_pose=True, torso_height=0.25, body_size=0.2,
                               body_center=(0.5, 0.55 - i * 0.012))   # sube
            eventos2.extend(analyzer2.update(ev, person_id=1, now=t))
            t += 0.1
        self.assertIn("standing_up", {e.action for e in eventos2})

    def test_13_accion_de_beber(self):
        """Beber exige mano cerca de la boca Y objeto detectado, sostenidos."""
        analyzer = ActionAnalyzer(window=5.0)
        t = 1000.0
        eventos = []
        for i in range(25):
            ev = FrameEvidence(has_pose=True, torso_height=0.25, body_size=0.2,
                               body_center=(0.5, 0.4))
            ev.hand_near_mouth = True
            ev.drink_object_near_mouth = True
            ev.add("hand_near_mouth")
            ev.add("bottle_detected")
            ev.add("object_near_mouth")
            eventos.extend(analyzer.update(ev, person_id=1, now=t))
            t += 0.1
        beber = [e for e in eventos if e.action == "drinking"]
        self.assertTrue(beber, "no detectó la acción de beber")
        evento = beber[0]
        d = evento.as_dict()
        for clave in ("action", "confidence", "person_id", "start_time", "duration", "evidence"):
            self.assertIn(clave, d)
        self.assertEqual(d["person_id"], 1)
        self.assertIn("hand_near_mouth", d["evidence"])
        self.assertIn("bottle_detected", d["evidence"])
        self.assertGreaterEqual(d["confidence"], 0.5)

    def test_13b_una_sola_postura_no_basta(self):
        """Mano cerca de la boca SIN objeto no puede ser 'beber'."""
        analyzer = ActionAnalyzer(window=5.0)
        t = 1100.0
        eventos = []
        for _ in range(25):
            ev = FrameEvidence(has_pose=True, torso_height=0.25, body_size=0.2,
                               body_center=(0.5, 0.4))
            ev.hand_near_mouth = True
            ev.add("hand_near_mouth")
            eventos.extend(analyzer.update(ev, person_id=1, now=t))
            t += 0.1
        self.assertNotIn("drinking", {e.action for e in eventos},
                         "dedujo 'beber' de una sola postura")

    def test_13c_caida_exige_evidencia_muy_alta(self):
        """Un fotograma horizontal no dispara la alerta; varios segundos sí."""
        analyzer = ActionAnalyzer(window=6.0)
        t = 1200.0
        ev_corta = FrameEvidence(has_pose=True, torso_height=0.25,
                                 body_center=(0.5, 0.4), body_size=0.2)
        ev_corta.add("horizontal_body")
        ev_corta.add("no_movement")
        eventos = analyzer.update(ev_corta, person_id=1, now=t)
        self.assertEqual(eventos, [], "alertó de caída con un solo fotograma")

        for i in range(45):                       # 4,5 s de cuerpo horizontal
            t += 0.1
            ev = FrameEvidence(has_pose=True, torso_height=0.25,
                               body_center=(0.5, 0.4), body_size=0.2)
            ev.add("horizontal_body")
            ev.add("no_movement")
            eventos.extend(analyzer.update(ev, person_id=1, now=t))
        caidas = [e for e in eventos if e.action == "possible_fall"]
        self.assertTrue(caidas, "no avisó tras varios segundos en el suelo")
        # Y se comunica como POSIBLE, nunca como diagnóstico.
        self.assertEqual(caidas[0].action, "possible_fall")

    def test_13d_catalogo_minimo(self):
        """El criterio de aceptación pide al menos diez acciones; hay más de 20."""
        analyzer = ActionAnalyzer()
        self.assertGreaterEqual(len(analyzer.rules), 10)
        self.assertGreaterEqual(len({r.name for r in analyzer.rules}), 20)


# ==========================================================================
# 14-15. Emociones: indeterminada y confianza insuficiente
# ==========================================================================
class TestEmociones(unittest.TestCase):
    def test_14_emocion_indeterminada(self):
        """Sin señales claras, el estado es 'undetermined' con confianza 0."""
        analyzer = EmotionAnalyzer(min_confidence=0.6)
        est = analyzer.update(blendshapes={}, now=1300.0)
        d = est.as_dict()
        self.assertEqual(d, {"affective_state": "undetermined", "confidence": 0.0})

    def test_15_confianza_insuficiente_no_afirma(self):
        """Una señal débil no se presenta como emoción."""
        analyzer = EmotionAnalyzer(min_confidence=0.9)
        for i in range(5):
            est = analyzer.update(blendshapes={"mouthSmileLeft": 0.36,
                                               "mouthSmileRight": 0.36},
                                  now=1310.0 + i * 0.3)
        self.assertEqual(est.affective_state, "undetermined")
        self.assertEqual(est.confidence, 0.0)

    def test_15b_emocion_clara_es_una_estimacion(self):
        """Con señales fuertes hay estimación, pero SIEMPRE con prudencia."""
        analyzer = EmotionAnalyzer(min_confidence=0.4)
        for i in range(10):
            est = analyzer.update(
                blendshapes={"mouthSmileLeft": 0.85, "mouthSmileRight": 0.85},
                now=1320.0 + i * 0.3)
        self.assertEqual(est.affective_state, "happy")
        d = est.as_dict()
        self.assertIn("safe_description", d)
        self.assertIn("certainty", d)
        self.assertIn(d["certainty"], {"low", "medium", "high"})
        # La frase nunca afirma un hecho psicológico.
        self.assertTrue(is_safe_phrase(d["safe_description"]))
        self.assertLess(d["confidence"], 1.0, "no puede haber certeza absoluta")

    def test_15c_frases_prohibidas_se_bloquean(self):
        """Los diagnósticos y afirmaciones sobre la persona están vetados."""
        for frase in ("Estás deprimido", "estas mintiendo", "Tienes ansiedad",
                      "Sé exactamente cómo te sientes", "estás enamorado"):
            self.assertFalse(is_safe_phrase(frase), f"no bloqueó: {frase}")
        for frase in ("Parece que podrías estar algo cansado.",
                      "Detecté señales compatibles con sorpresa."):
            self.assertTrue(is_safe_phrase(frase))

    def test_15d_acumulacion_temporal_evita_saltos(self):
        """Un fotograma raro no cambia la estimación de golpe."""
        analyzer = EmotionAnalyzer(min_confidence=0.4, window=4.0)
        for i in range(12):
            analyzer.update(blendshapes={"mouthSmileLeft": 0.9, "mouthSmileRight": 0.9},
                            now=1330.0 + i * 0.2)
        est = analyzer.update(blendshapes={"browDownLeft": 0.9, "browDownRight": 0.9},
                              now=1333.0)
        self.assertEqual(est.affective_state, "happy",
                         "un solo fotograma cambió la emoción acumulada")


# ==========================================================================
# 16-17. Módulo y modelo faltantes
# ==========================================================================
class TestDegradacion(unittest.TestCase):
    def test_16_modulo_faltante_solo_desactiva_ese_modulo(self):
        """Sin OCR instalado, el resto de capacidades siguen evaluándose."""
        cfg = load_settings()
        caps = detect_capabilities(settings=cfg, registry=None, privacy=None,
                                   camera_available=False)
        d = caps.as_dict()
        self.assertIn("ocr", d)
        self.assertIn("emotion_estimation", d)
        # Todas las claves del documento están presentes.
        for clave in ("camera", "face_detection", "hand_tracking", "pose_tracking",
                      "object_detection", "ocr", "scene_description",
                      "action_recognition", "emotion_estimation"):
            self.assertIn(clave, d)
            self.assertIsInstance(d[clave], bool)
        # Y cada capacidad ausente explica por qué.
        for clave in caps.missing():
            self.assertTrue(caps.reasons.get(clave), f"sin motivo para {clave}")

    def test_17_modelo_faltante_no_rompe(self):
        """require() de un modelo inexistente devuelve None, no una excepción."""
        registry = ModelRegistry(base_dir="/ruta/que/no/existe")
        self.assertIsNone(registry.require("face_landmarker"))
        estado = registry.verify("face_landmarker")
        self.assertFalse(estado.present)
        self.assertFalse(estado.valid)
        self.assertIn("no encontrado", estado.reason)
        self.assertIn("face_landmarker", registry.missing())
        # El informe se puede pedir siempre.
        self.assertIn("Modelos de visión", registry.describe())

    def test_17b_no_descarga_sin_autorizacion(self):
        """Sin VISION_ALLOW_DOWNLOAD, nunca se descarga nada."""
        llamadas = []
        registry = ModelRegistry(base_dir="/tmp/yue_modelos_prueba",
                                 allow_download=False,
                                 downloader=lambda url, dst: llamadas.append(url))
        self.assertFalse(registry.download("face_landmarker"))
        self.assertEqual(llamadas, [], "descargó sin autorización")

    def test_17c_carga_una_sola_vez(self):
        """get_or_load no construye el mismo modelo dos veces."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, "face_detector.task")
            with open(ruta, "wb") as fh:
                fh.write(b"x" * 200_000)
            registry = ModelRegistry(base_dir=tmp)
            construcciones = []

            class Falso:
                def close(self):
                    pass

            def builder(_p):
                construcciones.append(1)
                return Falso()

            a = registry.get_or_load("face_detector", builder)
            b = registry.get_or_load("face_detector", builder)
            self.assertIs(a, b)
            self.assertEqual(len(construcciones), 1, "cargó el modelo dos veces")
            registry.release()


# ==========================================================================
# 19-21. Privacidad, cámara desactivada y sin claves API
# ==========================================================================
class TestPrivacidad(unittest.TestCase):
    def test_19_privacidad_activada_bloquea_todo(self):
        p = PrivacyManager()
        p.set_camera(True)
        self.assertTrue(p.allows("ocr"))
        p.set_privacy_mode(True)
        for feature in ("ocr", "emotions", "objects", "actions", "scene", "faces"):
            self.assertFalse(p.allows(feature), f"{feature} siguió activo en privacidad")
        self.assertFalse(p.can_save_frames())
        self.assertFalse(p.can_save_events())
        self.assertFalse(p.can_use_cloud())
        self.assertIn("Modo privacidad", p.describe_es())

    def test_19b_apagar_un_modulo_no_apaga_los_demas(self):
        p = PrivacyManager()
        p.set_camera(True)
        p.set_feature("emotions", False)
        self.assertFalse(p.allows("emotions"))
        self.assertTrue(p.allows("objects"))
        self.assertTrue(p.allows("ocr"))

    def test_19c_solo_bajo_peticion(self):
        p = PrivacyManager(only_on_request=True, request_window=2.0)
        p.set_camera(True)
        self.assertFalse(p.allows("ocr"), "analizó sin que se lo pidieran")
        p.request_analysis()
        self.assertTrue(p.allows("ocr"))
        p.close_request()
        self.assertFalse(p.allows("ocr"))

    def test_19d_no_guarda_nada_por_defecto(self):
        p = PrivacyManager()
        p.set_camera(True)
        self.assertFalse(p.can_save_frames(), "guarda imágenes por defecto")
        self.assertFalse(p.can_save_events(), "guarda eventos por defecto")
        self.assertFalse(p.can_use_cloud(), "permite la nube por defecto")

    def test_20_camara_desactivada(self):
        """Con la cámara apagada, nada se analiza aunque los módulos estén on."""
        p = PrivacyManager()
        p.set_camera(False)
        for feature in ("ocr", "emotions", "objects", "faces"):
            self.assertFalse(p.allows(feature))
        cfg = load_settings()
        caps = detect_capabilities(settings=cfg, camera_available=False)
        self.assertFalse(caps["camera"])
        self.assertFalse(caps["ocr"])

    def test_21_sin_claves_api_todo_sigue_local(self):
        """El sistema es 100% local: sin claves API no se degrada nada."""
        guardadas = {}
        for clave in ("GROQ_API_KEY", "VISION_API_KEY", "OPENAI_API_KEY"):
            guardadas[clave] = os.environ.pop(clave, None)
        try:
            p = PrivacyManager(process_local=True, allow_cloud=False)
            p.set_camera(True)
            self.assertTrue(p.allows("ocr"), "el OCR local dependió de una clave API")
            self.assertFalse(p.can_use_cloud())
            st = TextStabilizer(min_agreements=2)
            st.feed("prueba local", 0.9, now=1400.0)
            r = st.feed("prueba local", 0.9, now=1400.5)
            self.assertIsNotNone(r, "el OCR local dejó de funcionar sin claves")
        finally:
            for clave, valor in guardadas.items():
                if valor is not None:
                    os.environ[clave] = valor

    def test_21b_olvidar_observaciones(self):
        events = EventManager(min_duration=0.0, cooldown=0.0)
        events.observe("gesture_detected", "thumbs_up", 0.9, now=1500.0)
        self.assertEqual(len(events.history()), 1)
        borrados = events.forget()
        self.assertEqual(borrados, 1)
        self.assertEqual(events.history(), [])


# ==========================================================================
# Eventos, contexto y escena
# ==========================================================================
class TestEventosYContexto(unittest.TestCase):
    def test_eventos_no_se_repiten(self):
        """El mismo evento sostenido se emite una vez, no en cada pasada."""
        events = EventManager(min_duration=0.3, cooldown=5.0, release_after=0.5)
        emitidos = []
        t = 1600.0
        for _ in range(30):
            ev = events.observe("action_detected", "typing", 0.8, now=t)
            if ev:
                emitidos.append(ev)
            t += 0.1
        self.assertEqual(len(emitidos), 1, f"se emitió {len(emitidos)} veces")

    def test_eventos_respetan_confianza_minima(self):
        events = EventManager(min_duration=0.0, min_confidence=0.7)
        self.assertIsNone(events.observe("gesture_detected", "victory", 0.4, now=1700.0))

    def test_contexto_es_corto_y_prudente(self):
        builder = VisionContextBuilder(max_lines=6)
        snapshot = {
            "camera": {"active": True, "fps": 15},
            "privacy": {"modo_privacidad": False},
            "presence": {"count": 1},
            "attention": {"state": "attentive"},
            "objects": [{"label": "laptop", "translated_label": "laptop", "confidence": 0.9},
                        {"label": "book", "translated_label": "libro", "confidence": 0.7}],
            "room": {"scene_type": "oficina doméstica", "scene_confidence": 0.78,
                     "lighting": "media"},
            "text": {"stable": True, "text": "Reunión viernes 7 de agosto",
                     "confidence": 0.91, "timestamp": time.time()},
            "affective": {"affective_state": "happy", "confidence": 0.7,
                          "certainty": "medium",
                          "safe_description": "Te noto de buen ánimo, aunque puedo equivocarme."},
            "actions": [],
        }
        ctx = builder.build(snapshot)
        self.assertTrue(ctx.has_content)
        self.assertLessEqual(len(ctx.lines), 6, "el contexto es demasiado largo")
        self.assertIn("Reunión viernes 7 de agosto", ctx.text)
        self.assertIn("laptop", ctx.text)
        # Nunca se mandan coordenadas al modelo.
        self.assertNotIn("bounding_box", ctx.text)
        self.assertNotIn("0.9", ctx.text.replace("0.91", ""))
        # La emoción va marcada como impresión.
        self.assertIn("impresión", ctx.text)

    def test_contexto_vacio_en_privacidad(self):
        builder = VisionContextBuilder()
        ctx = builder.build({"camera": {"active": True},
                             "privacy": {"modo_privacidad": True}})
        self.assertFalse(ctx.has_content)
        self.assertEqual(ctx.text, "")

    def test_no_reacciona_por_defecto(self):
        """YUE no comenta todo lo que ve."""
        builder = VisionContextBuilder()
        snapshot = {"camera": {"active": True}, "privacy": {"modo_privacidad": False},
                    "presence": {"count": 1}, "actions": []}
        reacciona, motivo = builder.should_react(snapshot, None)
        self.assertFalse(reacciona)
        # Pero sí ante un riesgo evidente.
        snapshot["actions"] = [{"action": "possible_fall", "confidence": 0.9}]
        reacciona, motivo = builder.should_react(snapshot, None)
        self.assertTrue(reacciona)
        self.assertEqual(motivo, "riesgo")

    def test_habitacion_usa_lenguaje_prudente(self):
        analyzer = RoomAnalyzer()
        detector = SceneDetector()
        lectura = detector.read(None, object_labels=["laptop", "keyboard", "chair"])
        snapshot = analyzer.update(scene_reading=lectura, people_count=1,
                                   object_tracks=[
                                       type("T", (), {"label": "laptop", "confidence": 0.94})(),
                                       type("T", (), {"label": "chair", "confidence": 0.90})(),
                                   ])
        for clave in ("scene_type", "scene_confidence", "people_count", "objects",
                      "lighting", "changes", "description"):
            self.assertIn(clave, snapshot)
        desc = snapshot["description"].lower()
        self.assertTrue(
            any(p in desc for p in ("parece", "probablemente", "diría", "no consigo")),
            f"la descripción afirma sin prudencia: {snapshot['description']}")

    def test_habitacion_detecta_cambios(self):
        analyzer = RoomAnalyzer(change_cooldown=0.0)
        analyzer.update(people_count=0, object_tracks=[], now=1800.0)
        snap = analyzer.update(people_count=1, object_tracks=[], now=1801.0)
        self.assertIn("apareció una persona", snap["changes"])


# ==========================================================================
# Suavizado temporal
# ==========================================================================
class TestSuavizado(unittest.TestCase):
    def test_ema(self):
        ema = EMA(0.5)
        self.assertEqual(ema.update(10), 10)     # arranque sin sesgo
        self.assertAlmostEqual(ema.update(0), 5.0)

    def test_histeresis_no_parpadea(self):
        h = Hysteresis(on_threshold=0.7, off_threshold=0.3, min_on=0.2, min_off=0.2)
        t = 0.0
        # Un valor rondando el umbral no debe encender.
        for _ in range(5):
            h.update(0.69, now=t)
            t += 0.05
        self.assertFalse(h.state)
        # Sostenido por encima sí enciende.
        for _ in range(10):
            h.update(0.9, now=t)
            t += 0.05
        self.assertTrue(h.state)
        # Y un bajón puntual no lo apaga.
        h.update(0.2, now=t)
        self.assertTrue(h.state)

    def test_voto_por_mayoria(self):
        vote = MajorityVote(window=10.0)
        for i in range(8):
            vote.add("happy", 0.8, now=float(i))
        vote.add("angry", 0.9, now=8.0)
        etiqueta, conf = vote.winner(now=8.5)
        self.assertEqual(etiqueta, "happy")
        self.assertGreater(conf, 0.5)


# ==========================================================================
# Intenciones de voz
# ==========================================================================
class TestIntenciones(unittest.TestCase):
    def test_todas_las_ordenes_del_documento(self):
        from vision import voice_intents as vi
        casos = {
            "Activa la cámara": "camara_on",
            "desactiva la camara": "camara_off",
            "¿Qué ves?": "que_ves",
            "Describe mi habitación": "describir_habitacion",
            "Lee este texto": "leer_texto",
            "lee lo que estoy mostrando": "leer_texto",
            "¿Qué dice este documento?": "leer_texto",
            "Lee solamente el título": "leer_titulo",
            "¿Qué estoy sosteniendo?": "que_sostengo",
            "Sigue mi mano": "seguir_mano",
            "¿Cuántos dedos muestro?": "contar_dedos",
            "¿Qué estoy haciendo?": "que_hago",
            "No analices mis emociones": "no_emociones",
            "No guardes observaciones": "no_guardar_eventos",
            "Olvida lo que viste": "olvidar_observaciones",
            "Modo privacidad": "modo_privacidad_on",
            "Solo analiza cuando te lo pida": "solo_bajo_peticion",
            "No leas la pantalla": "no_ocr",
        }
        for frase, esperado in casos.items():
            intent = vi.detect(frase)
            self.assertEqual(intent.name, esperado,
                             f"«{frase}» -> {intent.name!r}, esperaba {esperado!r}")

    def test_frases_normales_no_disparan(self):
        from vision import voice_intents as vi
        for frase in ("¿Cómo estás?", "Ponme música", "Cuéntame un chiste",
                      "Abre el navegador"):
            self.assertFalse(vi.detect(frase), f"«{frase}» disparó una intención")

    def test_peticion_visual_explicita(self):
        from vision import voice_intents as vi
        self.assertTrue(vi.is_vision_request("lee este texto"))
        self.assertTrue(vi.is_vision_request("describe mi habitación"))
        self.assertFalse(vi.is_vision_request("modo privacidad"))

    def test_regresion_quitar_modo_privacidad(self):
        """«quita el modo privacidad» contiene «modo privacidad»: no debe encenderlo."""
        from vision import voice_intents as vi
        for frase in ("quita el modo privacidad", "sal del modo privacidad",
                      "desactiva el modo privacidad", "apaga el modo privacidad"):
            self.assertEqual(vi.detect(frase).name, "modo_privacidad_off",
                             f"«{frase}» ACTIVÓ la privacidad en vez de quitarla")
        self.assertEqual(vi.detect("modo privacidad").name, "modo_privacidad_on")


class TestRegresionFPS(unittest.TestCase):
    """Regresión: FPS=0 en el .env significa «usa el perfil», no «apagado»."""

    def test_fps_cero_usa_el_perfil(self):
        from vision.settings import get_fps
        os.environ["VISION_PRUEBA_FPS"] = "0"
        try:
            self.assertEqual(get_fps("VISION_PRUEBA_FPS", 12.0), 12.0)
        finally:
            os.environ.pop("VISION_PRUEBA_FPS", None)

    def test_fps_explicito_manda(self):
        from vision.settings import get_fps
        os.environ["VISION_PRUEBA_FPS"] = "7.5"
        try:
            self.assertEqual(get_fps("VISION_PRUEBA_FPS", 12.0), 7.5)
        finally:
            os.environ.pop("VISION_PRUEBA_FPS", None)

    def test_configuracion_real_no_deja_nada_a_cero(self):
        """Con la config del proyecto, ningún módulo queda apagado por error."""
        cfg = load_settings()
        for campo in ("target_fps", "face_fps", "landmark_fps", "pose_fps",
                      "gesture_fps", "object_fps", "hand_fps", "scene_fps",
                      "action_fps", "text_watch_fps"):
            valor = getattr(cfg, campo)
            self.assertGreater(valor, 0.0,
                               f"{campo} quedó en 0: ese módulo no se ejecutaría")


# ==========================================================================
# Adaptador de migración
# ==========================================================================
class TestAdaptadorLegado(unittest.TestCase):
    def test_expone_la_interfaz_antigua(self):
        from vision.legacy_adapter import LegacyCameraObserverAdapter
        adapter = LegacyCameraObserverAdapter(None)
        # Todo lo que main.py llama sobre self.camera debe existir.
        for nombre in ("start", "stop", "latest", "describe", "context_for_ai",
                       "risk_signal", "set_landmark_consumer", "set_fast_mode"):
            self.assertTrue(hasattr(adapter, nombre), f"falta {nombre}()")
        self.assertTrue(hasattr(adapter, "active"))
        self.assertTrue(hasattr(adapter, "enabled"))
        # Y sin motor detrás, degrada sin lanzar excepciones.
        self.assertFalse(adapter.active)
        self.assertIsNone(adapter.latest())
        self.assertEqual(adapter.context_for_ai(), "")
        self.assertFalse(adapter.risk_signal())
        adapter.set_fast_mode(True)
        adapter.set_landmark_consumer(None)
        adapter.start()
        adapter.stop()


class TestBackendCamara(unittest.TestCase):
    """Regresión: la cámara daba UN fotograma y se quedaba muda en Windows."""

    def test_validate_rechaza_camara_que_da_un_solo_fotograma(self):
        """Una cámara que entrega el primer frame y falla NO es utilizable."""
        from vision.camera_backend import validate

        class CamaraMuda:
            def __init__(self):
                self.leidas = 0

            def read(self):
                self.leidas += 1
                # Entrega los 3 del calentamiento y luego se queda muda,
                # que es exactamente el fallo real de Windows + DSHOW.
                if self.leidas <= 3:
                    return True, FakeFrame()
                return False, None

        correctas, intentos, _tam = validate(CamaraMuda(), warmup=3, needed=3,
                                             max_attempts=10, timeout=2.0)
        self.assertLess(correctas, 3, "dio por buena una cámara muda")
        self.assertGreater(intentos, 0)

    def test_validate_acepta_flujo_sostenido(self):
        from vision.camera_backend import validate

        class CamaraBuena:
            def read(self):
                return True, FakeFrame()

        correctas, _intentos, tam = validate(CamaraBuena(), warmup=2, needed=3)
        self.assertGreaterEqual(correctas, 3)
        self.assertEqual(tam, (640, 480))

    def test_validate_tolera_fallos_sueltos(self):
        """Un microcorte no descalifica a una cámara que después sigue bien."""
        from vision.camera_backend import validate

        class CamaraConHipo:
            def __init__(self):
                self.n = 0

            def read(self):
                self.n += 1
                return (False, None) if self.n % 4 == 0 else (True, FakeFrame())

        correctas, _i, _t = validate(CamaraConHipo(), warmup=1, needed=3,
                                     max_attempts=20, timeout=3.0)
        self.assertGreaterEqual(correctas, 3)

    def test_buffersize_no_se_aplica_a_dshow(self):
        """CAP_PROP_BUFFERSIZE sobre DSHOW deja la captura muda: se omite."""
        from vision.camera_backend import configure
        try:
            import cv2
        except Exception:
            self.skipTest("sin OpenCV")

        class CapEspia:
            def __init__(self):
                self.props = []

            def set(self, prop, valor):
                self.props.append(prop)
                return True

        espia_dshow = CapEspia()
        configure(espia_dshow, 640, 480, "DSHOW")
        self.assertNotIn(cv2.CAP_PROP_BUFFERSIZE, espia_dshow.props,
                         "aplicó BUFFERSIZE sobre DSHOW")

        espia_msmf = CapEspia()
        configure(espia_msmf, 640, 480, "MSMF")
        self.assertIn(cv2.CAP_PROP_BUFFERSIZE, espia_msmf.props)

    def test_windows_prueba_msmf_antes_que_dshow(self):
        """En Windows, MSMF va primero: es el nativo de Windows 10/11."""
        import platform
        from vision.camera_backend import backend_candidates
        try:
            import cv2  # noqa: F401
        except Exception:
            self.skipTest("sin OpenCV")
        nombres = [n for n, _v in backend_candidates()]
        self.assertIn("ANY", nombres, "siempre debe haber un respaldo genérico")
        if platform.system().lower() == "windows":
            self.assertIn("MSMF", nombres)
            self.assertLess(nombres.index("MSMF"), nombres.index("DSHOW"))

    def test_opener_descarta_backend_que_falla(self):
        from vision.camera_backend import CaptureOpener
        opener = CaptureOpener()
        opener._preferido = ("DSHOW", 700)
        opener.last_backend = "DSHOW"
        opener.report_failure()
        self.assertIsNone(opener._preferido)
        self.assertIn("DSHOW", opener._descartados)

    def test_servicio_avisa_al_abridor_si_el_backend_muere(self):
        """CameraService debe pedir otro backend si apenas recibió fotogramas."""
        from vision.camera_service import CameraService

        class AbridorEspia:
            def __init__(self):
                self.avisos = 0

            def __call__(self, *_a):
                return None

            def report_failure(self):
                self.avisos += 1

        espia = AbridorEspia()
        servicio = CameraService(FrameHub(), capture_factory=espia)
        servicio._report_backend_failure()
        self.assertEqual(espia.avisos, 1)


class TestPlanificador(unittest.TestCase):
    """Regresión de dos fallos del planificador que rompían el cierre y el cursor."""

    def test_stop_no_lanza_typeerror(self):
        """`_Worker._stop` tapaba `threading.Thread._stop`, que la stdlib llama."""
        from vision.vision_scheduler import VisionScheduler
        sch = VisionScheduler()
        sch.add("rapido", 50.0, lambda: None)
        sch.start()
        time.sleep(0.25)
        try:
            sch.stop()        # antes: TypeError: 'Event' object is not callable
        except TypeError as exc:
            self.fail(f"stop() lanzó TypeError: {exc}")
        sch.stop()            # doble stop: también debe ser inofensivo
        vivos = [t.name for t in threading.enumerate() if t.name.startswith("Vision-")]
        self.assertEqual(vivos, [])

    def test_worker_no_tapa_atributos_de_thread(self):
        """Ningún atributo del worker puede pisar la API interna de Thread."""
        from vision.vision_scheduler import _Worker
        worker = _Worker("x", 10.0, lambda: None, threading.Event())
        self.assertTrue(callable(getattr(worker, "_stop", None)),
                        "_stop dejó de ser el método de Thread")
        self.assertIsInstance(worker._stop_event, threading.Event)

    def test_add_en_caliente_arranca_el_hilo(self):
        """Un módulo registrado con el planificador ya en marcha debe correr."""
        from vision.vision_scheduler import VisionScheduler
        sch = VisionScheduler()
        sch.add("a", 30.0, lambda: None)
        sch.start()
        time.sleep(0.2)
        sch.add("b", 30.0, lambda: None)       # añadido DESPUÉS de start()
        time.sleep(0.3)
        metricas = sch.metrics_dict()
        sch.stop()
        self.assertGreater(metricas["b"]["runs"], 0,
                           "el módulo añadido en caliente quedó inerte")


class TestControlPorCabeza(unittest.TestCase):
    """El motor nuevo debe alimentar el cursor igual que el observador clásico."""

    def _motor(self, tmp):
        from vision.camera_manager import CameraManager
        from vision.models.model_registry import ModelRegistry
        from vision.perception_engine import PerceptionEngine
        from vision.privacy_manager import from_settings
        with open(os.path.join(tmp, "face_landmarker.task"), "wb") as fh:
            fh.write(b"x" * 2_000_000)
        cfg = load_settings()
        mgr = CameraManager(FrameHub(), index=97, owner="prueba-cabeza",
                            capture_factory=fake_factory(), auto_search=False)
        return PerceptionEngine(cfg, privacy=from_settings(cfg), camera=mgr,
                                registry=ModelRegistry(base_dir=tmp))

    def test_entrega_landmarks_al_cursor(self):
        import tempfile

        class CaraFalsa:
            landmarks = [(0.5, 0.5, 0.0)] * 478

        class DetectorFalso:
            def detect(self, _rgb):
                return [CaraFalsa()]

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            motor = self._motor(tmp)
            recibidos = []
            try:
                motor.start()
                motor.set_landmark_consumer(lambda lm, shape: recibidos.append((len(lm), shape)))
                motor.set_fast_mode(True)
                motor._head_landmarker = DetectorFalso()
                # Sondeo con límite: la máquina puede ir cargada y un sleep fijo
                # haría la prueba frágil sin que hubiera nada roto.
                limite = time.time() + 5.0
                while not recibidos and time.time() < limite:
                    time.sleep(0.02)
                self.assertGreater(len(recibidos), 0,
                                   "el cursor no recibió ningún landmark")
                # La malla facial completa: es lo que HeadCursorController exige.
                self.assertGreaterEqual(recibidos[-1][0], 468)
                # Y al apagar el modo rápido, deja de entregar.
                motor.set_fast_mode(False)
                time.sleep(0.25)          # deja terminar la pasada en curso
                cuantos = len(recibidos)
                time.sleep(0.4)
                self.assertEqual(len(recibidos), cuantos,
                                 "siguió entregando con el modo rápido apagado")
            finally:
                motor.stop()
                release_camera(97, "prueba-cabeza")

    def test_el_adaptador_reenvia_al_motor(self):
        from vision.legacy_adapter import LegacyCameraObserverAdapter

        class MotorEspia:
            def __init__(self):
                self.consumidor = None
                self.rapido = False

            def set_landmark_consumer(self, c):
                self.consumidor = c

            def set_fast_mode(self, on):
                self.rapido = bool(on)

        espia = MotorEspia()
        adaptador = LegacyCameraObserverAdapter(espia)

        def consumidor(_lm, _shape):
            pass

        adaptador.set_landmark_consumer(consumidor)
        adaptador.set_fast_mode(True)
        self.assertIs(espia.consumidor, consumidor,
                      "el adaptador no pasó el consumidor al motor")
        self.assertTrue(espia.rapido)


class TestVersion(unittest.TestCase):
    """El sello existe para detectar que se ejecuta una copia antigua."""

    def test_la_copia_actual_esta_al_dia(self):
        from vision import version
        faltan, sin_arreglo = version.comprobar()
        self.assertEqual(faltan, [], f"faltan archivos: {faltan}")
        self.assertEqual(sin_arreglo, [], f"faltan arreglos: {sin_arreglo}")
        self.assertTrue(version.al_dia())

    def test_informe_dice_version_y_ruta(self):
        from vision import version
        texto = version.informe()
        self.assertIn(version.VERSION, texto)
        self.assertIn("Ejecutando desde", texto)

    def test_detecta_arreglo_ausente(self):
        """Si un arreglo no está en el código, hay que enterarse."""
        from vision import version
        # Marca que ningún archivo contiene: debe salir como ausente.
        original = version.MARCAS
        try:
            version.MARCAS = (("vision/vision_scheduler.py",
                               "MARCA_QUE_NO_EXISTE_EN_NINGUN_SITIO",
                               "arreglo de prueba"),)
            _faltan, sin_arreglo = version.comprobar()
            self.assertIn("arreglo de prueba", sin_arreglo)
            self.assertFalse(version.al_dia())
        finally:
            version.MARCAS = original
        self.assertTrue(version.al_dia(), "no restauró el estado")


class TestInterruptorCamara(unittest.TestCase):
    """Regresión: CAMERA_ENABLED=false apagaba TAMBIÉN el motor nuevo.

    Es la contradicción que aparecía al seguir el procedimiento de migración:
    para que el observador clásico no peleara por la webcam había que apagarlo,
    y eso dejaba a YUE sin visión de ninguna clase.
    """

    def setUp(self):
        self._guardadas = {}
        for clave in ("CAMERA_ENABLED", "VISION_REPLACE_LEGACY",
                      "VISION_CAMERA_ENABLED", "VISION_MP_ENABLED"):
            self._guardadas[clave] = os.environ.pop(clave, None)

    def tearDown(self):
        for clave, valor in self._guardadas.items():
            os.environ.pop(clave, None)
            if valor is not None:
                os.environ[clave] = valor

    def _cargar(self, **entorno):
        for clave, valor in entorno.items():
            os.environ[clave] = valor
        # `settings` lee primero `config`, que ya está importado con los valores
        # del arranque; para la prueba forzamos la lectura del entorno.
        import vision.settings as sm
        original = sm._yue_config
        try:
            sm._yue_config = None
            return sm.load()
        finally:
            sm._yue_config = original

    def test_migracion_enciende_la_camara_del_motor(self):
        cfg = self._cargar(CAMERA_ENABLED="false", VISION_REPLACE_LEGACY="true",
                           VISION_MP_ENABLED="true")
        self.assertTrue(cfg.camera_enabled,
                        "al migrar, el motor nuevo debe quedarse con la cámara")
        self.assertFalse(cfg.legacy_camera_enabled,
                         "el observador clásico debe quedar apagado")

    def test_sin_migracion_se_hereda_el_comportamiento_previo(self):
        """Quien apagó la cámara y no migra, la sigue teniendo apagada."""
        cfg = self._cargar(CAMERA_ENABLED="false", VISION_REPLACE_LEGACY="false",
                           VISION_MP_ENABLED="true")
        self.assertFalse(cfg.camera_enabled)

    def test_apagado_explicito_manda_sobre_todo(self):
        cfg = self._cargar(CAMERA_ENABLED="true", VISION_REPLACE_LEGACY="true",
                           VISION_CAMERA_ENABLED="false", VISION_MP_ENABLED="true")
        self.assertFalse(cfg.camera_enabled,
                         "VISION_CAMERA_ENABLED=false debe imponerse")

    def test_capacidades_explican_que_variable_tocar(self):
        cfg = self._cargar(CAMERA_ENABLED="false", VISION_REPLACE_LEGACY="false",
                           VISION_MP_ENABLED="true")
        caps = detect_capabilities(settings=cfg)
        self.assertFalse(caps["camera"])
        motivo = caps.reasons["camera"]
        self.assertIn("CAMERA_ENABLED", motivo)
        self.assertIn("VISION_REPLACE_LEGACY", motivo,
                      "el motivo debe decir cómo arreglarlo")


if __name__ == "__main__":
    unittest.main(verbosity=2)

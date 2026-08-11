"""Pruebas de la PERCEPCIÓN VISUAL CONECTADA (capa reactiva + estado vivo).

Ninguna prueba necesita webcam, MediaPipe ni modelos: todo se inyecta con
dobles. Cubren los fallos que impedían que la visión moviera nada, y el
comportamiento nuevo:

  ARREGLOS
   1. get_bool("") ya no apaga la cámara       (era el fallo fatal)
   2. confianza en porcentaje se normaliza
   3. el EventManager tiene suscriptor
   4. el vocabulario de gestos coincide
   5. la postura entrega landmarks
   6. los FPS no dependen del detector de rostros

  COMPORTAMIENTO
   7. estado vivo con la forma exacta del pedido
   8. presencia: aparece / se va
   9. gesto de saludo -> avatar levanta la mano + "Hola."
  10. emoción -> espejo empático (no imitación)
  11. objeto nuevo -> comentario, una sola vez
  12. contacto visual sostenido -> inicia conversación
  13. enfriamientos y tope de frases por minuto
  14. modo privacidad: no reacciona a nada
  15. guardas de la app: si no puede hablar, calla
  16. postura: parado / sentado / acostado / brazos cruzados
  17. preguntas directas contestadas del estado
  18. cierre limpio de hilos

Ejecuta:  python -m unittest tests.test_vision_reactiva   (desde yue_companion/)
o bien:   python tests/test_vision_reactiva.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vision import settings as settings_mod
from vision import voice_intents
from vision.detectors.pose_analyzer import PoseAnalyzerModule
from vision.event_manager import EventManager
from vision.live_state import LiveVisionState, distancia_es
from vision.reactive_layer import (
    EMOCION_A_REACCION,
    GESTO_A_REACCION,
    ReactiveLayer,
)


# ===========================================================================
# Dobles de prueba
# ===========================================================================
def snapshot_base(**cambios) -> dict:
    base = {
        "camera": {"active": True, "index": 2, "fps": 12.0, "target_fps": 12.0},
        "presence": {"count": 0, "primary_id": None, "people": []},
        "attention": {"state": "unknown", "looking_at_camera": False,
                      "attention_ratio": 0.0, "present": False, "absent_for": 0.0},
        "pose": {"visible": False, "state": "unknown", "left_arm_raised": False,
                 "right_arm_raised": False, "movement": "still"},
        "hands": [], "objects": [], "room": {}, "actions": [],
        "affective": {"affective_state": "undetermined", "confidence": 0.0},
        "text": None, "privacy": {}, "capabilities": {},
    }
    base.update(cambios)
    return base


def persona(pos="center", size=0.07, primary=True, pid=1):
    return {"person_id": pid, "position": pos, "relative_size": size,
            "approaching": "stable", "visible_for": 3.0, "is_primary": primary,
            "bounding_box": [0.3, 0.2, 0.6, 0.7]}


class MotorFalso:
    """Sustituye al PerceptionEngine: solo hace falta que devuelva snapshots."""

    def __init__(self, snap=None):
        self.snap = snap or snapshot_base()

    def snapshot(self):
        return self.snap


class PrivacidadFalsa:
    def __init__(self, privado=False):
        self.privado = privado

    def is_private(self):
        return self.privado


class Espia:
    """Recoge todo lo que la capa reactiva manda al avatar y a la voz."""

    def __init__(self):
        self.avatar, self.gestos, self.voz = [], [], []

    def emocion(self, nombre, intensidad, ms):
        self.avatar.append(nombre)

    def gesto(self, nombre, ganancia=1.0):
        self.gestos.append(nombre)

    def hablar(self, texto):
        self.voz.append(texto)


def montar(motor=None, espia=None, privacy=None, **kw):
    """Construye una capa reactiva rápida y determinista para las pruebas."""
    espia = espia or Espia()
    capa = ReactiveLayer(
        motor or MotorFalso(),
        EventManager(min_duration=0.0, cooldown=0.0, min_confidence=0.3),
        privacy=privacy,
        state=LiveVisionState(),
        on_avatar_emotion=espia.emocion,
        on_avatar_gesture=espia.gesto,
        on_speak=espia.hablar,
        can_speak=kw.pop("can_speak", lambda: True),
        can_animate=kw.pop("can_animate", lambda: True),
    )
    for nombre, valor in kw.items():
        setattr(capa, nombre, valor)
    return capa, espia


def landmarks(puntos):
    """33 puntos de MediaPipe Pose; se sobrescriben los que interesen.

    Recibe un dict POSICIONAL porque las claves son índices enteros (11, 12,
    23...) y Python no admite kwargs numéricos.
    """
    class P:
        def __init__(s, x, y, v=1.0):
            s.x, s.y, s.z, s.visibility = x, y, 0.0, v
    lm = [P(0.5, 0.5) for _ in range(33)]
    for i, (x, y, v) in puntos.items():
        lm[i] = P(x, y, v)
    return lm


# ===========================================================================
# 1-6. Los arreglos
# ===========================================================================
class TestArreglos(unittest.TestCase):

    def test_01_cadena_vacia_no_apaga_la_camara(self):
        """El fallo fatal: config declara "" como «sin definir», no como false."""
        self.assertTrue(settings_mod.get_bool("__NO_EXISTE__", True))
        os.environ["__PRUEBA_VACIA__"] = ""
        try:
            # "" debe caer al valor por defecto, no valer False.
            self.assertTrue(settings_mod.get_bool("__PRUEBA_VACIA__", True))
            self.assertFalse(settings_mod.get_bool("__PRUEBA_VACIA__", False))
            # Los espacios en blanco cuentan igual que vacío.
            os.environ["__PRUEBA_VACIA__"] = "   "
            self.assertTrue(settings_mod.get_bool("__PRUEBA_VACIA__", True))
            # Y un valor de verdad sigue mandando.
            os.environ["__PRUEBA_VACIA__"] = "false"
            self.assertFalse(settings_mod.get_bool("__PRUEBA_VACIA__", True))
        finally:
            os.environ.pop("__PRUEBA_VACIA__", None)

    def test_02_confianza_en_porcentaje_se_normaliza(self):
        """VISION_EMOTION_MIN_CONFIDENCE=60 es 60%, no un umbral de 60.0."""
        os.environ["__PRUEBA_CONF__"] = "60"
        try:
            self.assertAlmostEqual(settings_mod.get_confidence("__PRUEBA_CONF__", 0.45), 0.6)
            os.environ["__PRUEBA_CONF__"] = "0.45"
            self.assertAlmostEqual(settings_mod.get_confidence("__PRUEBA_CONF__", 0.5), 0.45)
            os.environ["__PRUEBA_CONF__"] = "150"
            self.assertLessEqual(settings_mod.get_confidence("__PRUEBA_CONF__", 0.5), 1.0)
        finally:
            os.environ.pop("__PRUEBA_CONF__", None)

    def test_03_la_capa_se_suscribe_a_los_eventos(self):
        """Antes el EventManager no tenía NI UN suscriptor: todo moría ahí."""
        capa, _ = montar()
        self.assertEqual(len(capa.events._subs), 0)
        capa.start()
        try:
            self.assertEqual(len(capa.events._subs), 1)
        finally:
            capa.stop()
        self.assertEqual(len(capa.events._subs), 0)

    def test_04_vocabulario_de_gestos_coincide(self):
        """El puente viejo esperaba 'Thumb_Up'; el motor emite 'thumbs_up'."""
        from vision.analyzers.gesture_analyzer import REPORTABLE
        for gesto in REPORTABLE:
            self.assertIn(gesto, GESTO_A_REACCION,
                          f"el gesto {gesto} no tiene reacción definida")

    def test_05_la_postura_entrega_landmarks(self):
        """Sin landmarks el ActionDetector no puede deducir NADA."""
        m = PoseAnalyzerModule(None)
        obs = m._interpret(landmarks({
            11: (0.42, 0.30, 1.0), 12: (0.58, 0.30, 1.0),
            15: (0.40, 0.60, 1.0), 16: (0.60, 0.60, 1.0),
            23: (0.45, 0.55, 1.0), 24: (0.55, 0.55, 1.0),
            25: (0.45, 0.75, 1.0), 26: (0.55, 0.75, 1.0),
            27: (0.45, 0.95, 1.0), 28: (0.55, 0.95, 1.0)}))
        self.assertEqual(len(obs.landmarks), 33)
        self.assertEqual(len(obs.landmarks[0]), 4)   # x, y, z, visibilidad

    def test_06_fps_independientes_del_detector(self):
        """Los FPS medían la cadencia del detector de rostros, no la cámara."""
        from vision.camera_manager import CameraManager
        from vision.frame_hub import FrameHub
        hub = FrameHub()
        cm = CameraManager(hub, index=99, owner="test", auto_search=False)
        cm._status.active = True
        cm.is_running = lambda: True          # simula cámara viva
        for _ in range(30):
            hub.publish(object())
        cm.tick_fps()                          # primera llamada: fija el origen
        cm._fps_t0 -= 1.5                      # simula que pasó 1,5 s
        for _ in range(15):
            hub.publish(object())
        self.assertGreater(cm.tick_fps(), 0.0,
                           "los FPS siguen en 0 sin llamadas del detector")


# ===========================================================================
# 7-8. Estado vivo
# ===========================================================================
class TestEstadoVivo(unittest.TestCase):

    def test_07_forma_exacta_del_pedido(self):
        estado = LiveVisionState().get()
        for clave in ("personas", "emociones", "objetos", "gestos", "texto",
                      "postura", "mirada", "escena", "camara", "actualizado"):
            self.assertIn(clave, estado, f"falta la clave {clave}")

    def test_07b_nunca_devuelve_none_ni_falta_una_clave(self):
        """Se consulta desde toda YUE: no puede obligar a defensas."""
        estado = LiveVisionState().get()
        self.assertIsNotNone(estado["personas"]["count"])
        self.assertEqual(estado["personas"]["count"], 0)
        self.assertFalse(estado["mirada"]["mira_a_yue"])
        self.assertEqual(estado["objetos"], [])

    def test_07c_distancia_aproximada(self):
        self.assertEqual(distancia_es(0.20), "muy cerca")
        self.assertEqual(distancia_es(0.07), "cerca")
        self.assertEqual(distancia_es(0.03), "a distancia normal")
        self.assertEqual(distancia_es(0.005), "lejos")
        self.assertEqual(distancia_es(0.0), "desconocida")

    def test_08_traduce_persona_postura_y_objetos(self):
        st = LiveVisionState()
        st.update_from_snapshot(snapshot_base(
            presence={"count": 1, "primary_id": 1, "people": [persona()]},
            pose={"visible": True, "state": "sitting", "left_arm_raised": False,
                  "right_arm_raised": True, "movement": "still"},
            objects=[{"label": "book", "translated_label": "libro",
                      "confidence": 0.8, "object_id": 1}],
        ))
        estado = st.get()
        self.assertTrue(estado["personas"]["hay_persona"])
        self.assertEqual(estado["personas"]["principal"]["posicion"], "al centro")
        self.assertEqual(estado["personas"]["principal"]["distancia"], "cerca")
        self.assertEqual(estado["postura"]["estado"], "sentado")
        self.assertTrue(estado["postura"]["mano_levantada"])
        self.assertEqual(estado["objetos"][0]["etiqueta"], "libro")
        self.assertIn("libro", estado["objetos_nuevos"])

    def test_08b_objeto_solo_es_nuevo_una_vez(self):
        st = LiveVisionState()
        snap = snapshot_base(objects=[{"label": "book", "translated_label": "libro",
                                       "confidence": 0.8, "object_id": 1}])
        st.update_from_snapshot(snap)
        self.assertIn("libro", st.get()["objetos_nuevos"])
        st.update_from_snapshot(snap)
        self.assertEqual(st.get()["objetos_nuevos"], [])

    def test_08c_contacto_visual_acumula_segundos(self):
        st = LiveVisionState()
        mirando = snapshot_base(
            presence={"count": 1, "primary_id": 1, "people": [persona()]},
            attention={"state": "attentive", "looking_at_camera": True,
                       "attention_ratio": 0.9, "present": True, "absent_for": 0.0})
        ahora = time.time()
        st.update_from_snapshot(mirando, now=ahora)
        self.assertEqual(st.get()["mirada"]["segundos_mirando"], 0.0)
        st.update_from_snapshot(mirando, now=ahora + 4.0)
        self.assertAlmostEqual(st.get()["mirada"]["segundos_mirando"], 4.0, places=1)
        # Al apartar la vista, el contador se reinicia.
        st.update_from_snapshot(snapshot_base(), now=ahora + 5.0)
        self.assertEqual(st.get()["mirada"]["segundos_mirando"], 0.0)


# ===========================================================================
# 9-12. Reacciones
# ===========================================================================
class TestReacciones(unittest.TestCase):

    def test_09_saludo_levanta_la_mano_y_dice_hola(self):
        capa, espia = montar()
        capa._on_gesto("wave", {"hand": "right", "gesture_es": "saludo con la mano"})
        self.assertIn("saludar", espia.gestos)
        self.assertIn("happy", espia.avatar)
        self.assertIn("Hola.", espia.voz)

    def test_09b_pulgar_arriba_asiente_sin_hablar(self):
        capa, espia = montar()
        capa._on_gesto("thumbs_up", {"hand": "right"})
        self.assertIn("asentir", espia.gestos)
        self.assertEqual(espia.voz, [], "el pulgar arriba no debe hablar")

    def test_10_emocion_es_espejo_empatico_no_imitacion(self):
        """Si te ve triste, YUE se preocupa; no se pone triste."""
        self.assertEqual(EMOCION_A_REACCION["sad"][0], "worried")
        self.assertEqual(EMOCION_A_REACCION["happy"][0], "happy")
        self.assertEqual(EMOCION_A_REACCION["surprised"][0], "surprised")

        capa, espia = montar()
        capa._reaccionar_emocion({"emociones": {
            "emocion_raw": "sad", "emocion": "triste", "confianza": 0.8,
            "cambio": True, "desde_hace": 0.0}})
        self.assertIn("worried", espia.avatar)

    def test_10b_emocion_debil_o_neutral_no_reacciona(self):
        capa, espia = montar()
        capa._reaccionar_emocion({"emociones": {
            "emocion_raw": "sad", "confianza": 0.3, "cambio": True, "desde_hace": 0.0}})
        capa._reaccionar_emocion({"emociones": {
            "emocion_raw": "neutral", "confianza": 0.9, "cambio": True, "desde_hace": 0.0}})
        self.assertEqual(espia.avatar, [])

    def test_11_objeto_nuevo_se_comenta_una_sola_vez(self):
        capa, espia = montar()
        capa._reaccionar_objetos({"objetos_nuevos": ["libro"]})
        self.assertEqual(espia.voz, ["Veo que tienes un libro."])
        capa._reaccionar_objetos({"objetos_nuevos": ["libro"]})
        self.assertEqual(len(espia.voz), 1, "no debe repetir el mismo objeto")

    def test_11b_objeto_sin_frase_no_dice_nada(self):
        capa, espia = montar()
        capa._reaccionar_objetos({"objetos_nuevos": ["silla", "planta"]})
        self.assertEqual(espia.voz, [], "YUE no es un inventario parlante")

    def test_12_contacto_visual_sostenido_inicia_conversacion(self):
        capa, espia = montar(reactive_gaze_seconds=3.0)
        capa.gaze_seconds = 3.0
        # Poco rato: no dice nada.
        capa._reaccionar_mirada({"mirada": {"mira_a_yue": True, "segundos_mirando": 1.0}})
        self.assertEqual(espia.voz, [])
        # Rato largo: inicia.
        capa._reaccionar_mirada({"mirada": {"mira_a_yue": True, "segundos_mirando": 4.0}})
        self.assertEqual(len(espia.voz), 1)
        self.assertIn("ladear", espia.gestos)


# ===========================================================================
# 13-15. Silencio
# ===========================================================================
class TestSilencio(unittest.TestCase):

    def test_13_enfriamiento_del_saludo(self):
        capa, espia = montar()
        capa.cd_saludo = 30.0
        capa._on_gesto("wave", {"hand": "right"})
        capa._on_gesto("wave", {"hand": "right"})
        self.assertEqual(espia.voz.count("Hola."), 1, "saludó dos veces seguidas")

    def test_13b_tope_de_frases_por_minuto(self):
        capa, espia = montar()
        capa.max_frases_min = 2
        for i in range(6):
            capa._decir(f"frase {i}")
        self.assertEqual(len(espia.voz), 2, "el tope global no se respetó")

    def test_13c_lo_urgente_se_salta_el_tope(self):
        capa, espia = montar()
        capa.max_frases_min = 1
        capa._decir("normal")
        capa._decir("otra normal")
        capa._decir("¿estás bien?", urgente=True)
        self.assertIn("¿estás bien?", espia.voz)

    def test_14_modo_privacidad_no_reacciona_a_nada(self):
        capa, espia = montar(privacy=PrivacidadFalsa(privado=True))

        class Evt:
            event, key, data, confidence = "gesture_detected", "wave", {"hand": "right"}, 0.9

        capa.on_event(Evt())
        self.assertEqual(espia.avatar, [])
        self.assertEqual(espia.voz, [])
        self.assertEqual(espia.gestos, [])

    def test_15_si_la_app_dice_que_no_hable_calla(self):
        capa, espia = montar(can_speak=lambda: False)
        capa._on_gesto("wave", {"hand": "right"})
        self.assertEqual(espia.voz, [], "habló pese a la guarda de la app")
        self.assertIn("saludar", espia.gestos, "el gesto sí puede seguir saliendo")

    def test_15b_si_la_cara_esta_ocupada_no_anima(self):
        capa, espia = montar(can_animate=lambda: False)
        capa._on_gesto("wave", {"hand": "right"})
        self.assertEqual(espia.avatar, [])
        self.assertEqual(espia.gestos, [])

    def test_15c_una_guarda_que_falla_no_tumba_nada(self):
        def revienta():
            raise RuntimeError("la app se cayó")
        capa, espia = montar(can_speak=revienta, can_animate=revienta)
        capa._on_gesto("wave", {"hand": "right"})   # no debe lanzar
        self.assertEqual(espia.voz, [], "ante la duda, callar")


# ===========================================================================
# 16-17. Postura y preguntas directas
# ===========================================================================
class TestPostura(unittest.TestCase):

    def _estado(self, puntos):
        return PoseAnalyzerModule(None)._interpret(landmarks(puntos)).state

    def test_16_parado(self):
        self.assertEqual(self._estado({
            11: (0.42, 0.30, 1.0), 12: (0.58, 0.30, 1.0),
            15: (0.40, 0.60, 1.0), 16: (0.60, 0.60, 1.0),
            23: (0.45, 0.55, 1.0), 24: (0.55, 0.55, 1.0),
            25: (0.45, 0.75, 1.0), 26: (0.55, 0.75, 1.0),
            27: (0.45, 0.95, 1.0), 28: (0.55, 0.95, 1.0)}), "standing")

    def test_16b_sentado(self):
        self.assertEqual(self._estado({
            11: (0.42, 0.30, 1.0), 12: (0.58, 0.30, 1.0),
            15: (0.40, 0.60, 1.0), 16: (0.60, 0.60, 1.0),
            23: (0.45, 0.60, 1.0), 24: (0.55, 0.60, 1.0),
            25: (0.45, 0.70, 1.0), 26: (0.55, 0.70, 1.0),
            27: (0.45, 0.72, 0.1), 28: (0.55, 0.72, 0.1)}), "sitting")

    def test_16c_acostado(self):
        """Tronco horizontal: antes se confundía con inclinación lateral."""
        self.assertEqual(self._estado({
            11: (0.25, 0.50, 1.0), 12: (0.25, 0.60, 1.0),
            15: (0.15, 0.55, 1.0), 16: (0.15, 0.58, 1.0),
            23: (0.65, 0.52, 1.0), 24: (0.65, 0.60, 1.0),
            25: (0.80, 0.55, 1.0), 26: (0.80, 0.58, 1.0),
            27: (0.95, 0.55, 1.0), 28: (0.95, 0.58, 1.0)}), "lying")

    def test_16d_brazos_cruzados(self):
        self.assertEqual(self._estado({
            11: (0.40, 0.30, 1.0), 12: (0.60, 0.30, 1.0),
            15: (0.56, 0.44, 1.0), 16: (0.44, 0.44, 1.0),
            23: (0.45, 0.60, 1.0), 24: (0.55, 0.60, 1.0),
            25: (0.45, 0.72, 1.0), 26: (0.55, 0.72, 1.0),
            27: (0.45, 0.75, 0.1), 28: (0.55, 0.75, 0.1)}), "arms_crossed")

    def test_16e_mano_levantada(self):
        obs = PoseAnalyzerModule(None)._interpret(landmarks({
            11: (0.42, 0.30, 1.0), 12: (0.58, 0.30, 1.0),
            15: (0.40, 0.60, 1.0), 16: (0.62, 0.12, 1.0),
            23: (0.45, 0.60, 1.0), 24: (0.55, 0.60, 1.0),
            25: (0.45, 0.72, 1.0), 26: (0.55, 0.72, 1.0),
            27: (0.45, 0.75, 0.1), 28: (0.55, 0.75, 0.1)}))
        self.assertTrue(obs.right_arm_raised)


class TestPreguntasDirectas(unittest.TestCase):

    def setUp(self):
        class Cam:
            active, index, real_fps = True, 2, 12.0

        class Priv:
            def request_analysis(self, *a): pass
            def allows(self, f): return True
            def describe_es(self): return ""

        snap = snapshot_base(
            presence={"count": 2, "primary_id": 1,
                      "people": [persona(), persona("left", 0.03, False, 2)]},
            attention={"state": "attentive", "looking_at_camera": True,
                       "attention_ratio": 0.9, "present": True, "absent_for": 0.0},
            pose={"visible": True, "state": "sitting", "left_arm_raised": False,
                  "right_arm_raised": False, "movement": "still"},
            objects=[{"label": "book", "translated_label": "libro",
                      "confidence": 0.8, "object_id": 1},
                     {"label": "laptop", "translated_label": "laptop",
                      "confidence": 0.9, "object_id": 2}])

        class Motor:
            running, privacy, camera = True, Priv(), Cam()
            def snapshot(self): return snap

        self.motor = Motor()
        self.estado = LiveVisionState()
        self.estado.update_from_snapshot(snap)

    def responder(self, frase):
        return voice_intents.handle(self.motor, frase, state=self.estado)

    def test_17_cuantas_personas(self):
        self.assertIn("2", self.responder("¿cuántas personas hay?"))

    def test_17b_me_estas_mirando(self):
        self.assertIn("Sí", self.responder("¿me estás mirando?"))

    def test_17c_que_postura_tengo(self):
        self.assertIn("sentado", self.responder("¿estoy sentado?"))

    def test_17d_que_objetos_ves(self):
        respuesta = self.responder("¿qué objetos ves?")
        self.assertIn("libro", respuesta)
        self.assertIn("laptop", respuesta)

    def test_17e_frase_sin_intencion_devuelve_none(self):
        self.assertIsNone(self.responder("cuéntame un chiste"))


# ===========================================================================
# 18. Hilos
# ===========================================================================
class TestHilos(unittest.TestCase):

    def test_18_cierre_limpio(self):
        antes = threading.active_count()
        capa, _ = montar()
        capa.poll_hz = 20.0
        capa.start()
        time.sleep(0.3)
        self.assertTrue(capa.running)
        capa.stop()
        time.sleep(0.3)
        self.assertFalse(capa.running)
        self.assertLessEqual(threading.active_count(), antes,
                             "quedaron hilos vivos tras stop()")

    def test_18b_start_dos_veces_no_duplica_hilos(self):
        capa, _ = montar()
        capa.start()
        try:
            capa.start()
            self.assertEqual(len(capa.events._subs), 1)
        finally:
            capa.stop()

    def test_18c_un_snapshot_que_revienta_no_tumba_el_hilo(self):
        class MotorRoto:
            def snapshot(self):
                raise RuntimeError("el motor se cayó")

        capa, _ = montar(motor=MotorRoto())
        capa.poll_hz = 30.0
        capa.start()
        try:
            time.sleep(0.3)
            self.assertTrue(capa.running, "el hilo murió por una excepción")
        finally:
            capa.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)

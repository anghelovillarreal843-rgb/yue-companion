"""Pruebas de regresión del arranque: lectura numérica del .env.  [ADITIVO]

Fallo que cubren (era el que impedía arrancar YUE por completo):

    File "config.py", line 648, in <module>
      VISION_EMOTION_MIN_CONFIDENCE = int(os.getenv("VISION_EMOTION_MIN_CONFIDENCE", "60"))
    ValueError: invalid literal for int() with base 10: '0.45'

Dos problemas distintos en la misma línea:
  1. `int()` reventaba con cualquier decimal del .env -> config.py ni siquiera
     se importaba y `python main.py` moría antes de abrir la ventana.
  2. La MISMA variable la leen dos sistemas con escalas distintas
     (`core/vision` en 0..100 y el paquete `vision/` en 0..1). Aunque no
     reventara, `int(0.45)` = 0 dejaba el umbral en cero y YUE reaccionaba a
     lecturas de emoción dudosas.

Se ejecuta con:  python -m unittest tests.test_config_env -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import config  # noqa: E402
from core.vision.emotion_manager import EmotionManager, _umbral_pct  # noqa: E402


class TestLecturaNumerica(unittest.TestCase):
    """Los ayudantes de config nunca deben lanzar excepción."""

    def _con_env(self, valor):
        os.environ["__PRUEBA_NUM__"] = valor
        self.addCleanup(os.environ.pop, "__PRUEBA_NUM__", None)

    def test_entero_acepta_decimales(self):
        """El fallo exacto del arranque: int('0.45') reventaba."""
        self._con_env("0.45")
        self.assertEqual(config._env_int("__PRUEBA_NUM__", 60), 0)
        os.environ["__PRUEBA_NUM__"] = "5.9"
        self.assertEqual(config._env_int("__PRUEBA_NUM__", 60), 5)

    def test_entero_con_basura_usa_el_defecto(self):
        self._con_env("no-soy-un-numero")
        self.assertEqual(config._env_int("__PRUEBA_NUM__", 7), 7)

    def test_vacio_y_espacios_caen_al_defecto(self):
        self._con_env("")
        self.assertEqual(config._env_int("__PRUEBA_NUM__", 3), 3)
        self.assertAlmostEqual(config._env_float("__PRUEBA_NUM__", 1.5), 1.5)
        os.environ["__PRUEBA_NUM__"] = "    "
        self.assertEqual(config._env_int("__PRUEBA_NUM__", 3), 3)

    def test_tolera_comentario_y_comillas_pegados(self):
        self._con_env("4  # cada cuántos segundos")
        self.assertEqual(config._env_int("__PRUEBA_NUM__", 1), 4)
        os.environ["__PRUEBA_NUM__"] = '"2.5"'
        self.assertAlmostEqual(config._env_float("__PRUEBA_NUM__", 1.0), 2.5)

    def test_coma_decimal(self):
        self._con_env("0,7")
        self.assertAlmostEqual(config._env_float("__PRUEBA_NUM__", 0.1), 0.7)


class TestUmbralDeEmocion(unittest.TestCase):
    """0.45 y 45 tienen que significar lo mismo en los dos sistemas."""

    def test_config_publica_las_dos_escalas_y_concuerdan(self):
        self.assertLessEqual(config.VISION_EMOTION_MIN_CONFIDENCE, 1.0)
        self.assertGreaterEqual(config.VISION_EMOTION_MIN_CONFIDENCE, 0.0)
        self.assertAlmostEqual(
            config.VISION_EMOTION_MIN_CONFIDENCE_PCT,
            config.VISION_EMOTION_MIN_CONFIDENCE * 100.0,
            places=1,
        )

    def test_fraccion_y_porcentaje_son_equivalentes(self):
        self.assertAlmostEqual(_umbral_pct(0.45), 45.0)
        self.assertAlmostEqual(_umbral_pct(45), 45.0)
        self.assertAlmostEqual(_umbral_pct("0.6"), 60.0)
        self.assertAlmostEqual(_umbral_pct("basura", default=60), 60.0)
        self.assertLessEqual(_umbral_pct(150), 100.0)

    def test_el_umbral_vuelve_a_filtrar(self):
        """Con int(0.45)=0 el umbral desaparecía y todo pasaba el filtro."""
        m = EmotionManager()
        self.assertGreater(m.min_confidence, 1.0, "el umbral quedó en escala 0..1")
        m.min_confidence = 45.0
        self.assertIsNotNone(m.maybe_react("happy", 90, now=0.0))
        self.assertIsNone(m.maybe_react("happy", 20, now=500.0))


class TestConfigImporta(unittest.TestCase):
    def test_no_quedan_lecturas_fragiles(self):
        """Ningún int(os.getenv(...)) suelto puede volver a tumbar el arranque."""
        import re

        texto = (RAIZ / "config.py").read_text(encoding="utf-8")
        codigo = "\n".join(
            l for l in texto.splitlines() if not l.lstrip().startswith("#")
        )
        sueltos = re.findall(r"(?<![_\w])(?:int|float)\(os\.getenv", codigo)
        self.assertEqual(sueltos, [], "quedan parseos sin red de seguridad")


if __name__ == "__main__":
    unittest.main(verbosity=2)

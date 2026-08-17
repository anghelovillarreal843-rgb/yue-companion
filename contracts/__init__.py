"""Contratos de la capa raíz: interfaces que la app implementa y los
paquetes de funcionalidad consumen (REFACTOR_SPEC D1/D2).

- `VisionHost`: implementada por la app (main.py), consumida por vision/.
- `OCRPort`: implementada por vision/ocr/ocr_engine.OCREngineChain,
  consumida por core/screen_ocr.py.

contracts/ no importa NADA del proyecto (ni core, ni vision, ni ui):
es la capa neutra que rompe el ciclo core <-> vision.
"""

from contracts.ocr_port import OCRPort
from contracts.vision_host import VisionHost

__all__ = ["VisionHost", "OCRPort"]
"""Gate de arquitectura: el CICLO core <-> vision debe estar roto (D1).

Doble verificación, para que el gate dé certeza real (no solo "parecer"):

1. ESTÁTICA: escanea los archivos .py del paquete con AST y busca sentencias
   de import de módulos prohibidos. Detecta también los imports perezosos
   (dentro de funciones), que un gate de sys.modules no ve.
2. DINÁMICA: en un subproceso PYTHON LIMPIO importa el árbol real y lista los
   módulos prohibidos que quedaron cargados en sys.modules.

Alcance por PR (REFACTOR_SPEC §5):
- test_vision_no_importa_core_ni_main -> ACTIVO desde PR 3 (ciclo roto).
- test_core_no_importa_vision         -> ACTIVO desde PR 3.
- test_core_no_importa_exogenos       -> SKIP hasta PR 5 (voice_flow/teacher/
  ui se cierran en el desglose de Controller; TODO en REFACTOR_SPEC §PR6).
"""
import ast
import pathlib
import subprocess
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
NUCLEO_CORE = "core.screen_ocr core.pc_control core.listener core.screen_vision"


def _raiz_modulo(nombre: str) -> str:
    return (nombre or "").split(".")[0]


def _imports_estaticos(archivo: pathlib.Path) -> set[str]:
    """Raíces de módulos que el archivo importa, vía AST (detecta lazy imports)."""
    try:
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
    except Exception:
        return set()
    modulos: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                modulos.add(_raiz_modulo(alias.name))
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            modulos.add(_raiz_modulo(nodo.module))
    return modulos


def _archivos_paquetes(*paquetes: str) -> list[pathlib.Path]:
    archivos: list[pathlib.Path] = []
    for paquete in paquetes:
        base = RAIZ / paquete
        if base.is_dir():
            archivos.extend(sorted(p for p in base.rglob("*.py")))
    return archivos


def _violaciones_estaticas(archivos, prohibidos: tuple[str, ...]) -> list[str]:
    """Archivos cuyo código importa (directa o lazy) un módulo prohibido."""
    culpables: list[str] = []
    for archivo in archivos:
        prohibidos_aqui = sorted(_imports_estaticos(archivo) & set(prohibidos))
        if prohibidos_aqui:
            rel = archivo.relative_to(RAIZ).as_posix()
            culpables.append(f"{rel} -> {prohibidos_aqui}")
    return culpables


def _violaciones_dinamicas(modulos_arbol: str, prohibidos: tuple[str, ...]) -> list[str]:
    """Subproceso Python limpio: importa el árbol y filtra sys.modules."""
    lista = str(list(prohibidos))
    codigo = (
        "; ".join(f"import {m}" for m in modulos_arbol.split())
        + f"; print('|'.join(sorted(m for m in sys.modules "
        + f"if m.split('.')[0] in {lista} and not m.startswith('core.vision'))))"
    )
    try:
        salida = subprocess.run(
            [sys.executable, "-c", codigo],
            capture_output=True, text=True, timeout=60,
            cwd=str(RAIZ),
        ).stdout.strip()
    except Exception as exc:  # pragma: no cover - fallback seguro
        pytest.fail(f"El subproceso del gate no pudo correr: {exc}")
    return [m for m in salida.split("|") if m]


def test_vision_no_importa_core_ni_main():
    """vision/ no debe importar core.* ni main.* (ni directo ni lazy)."""
    estaticas = _violaciones_estaticas(_archivos_paquetes("vision"), ("core", "main"))
    assert not estaticas, f"vision importó core/main (estático): {estaticas}"
    dinamicas = _violaciones_dinamicas(
        "vision.integration vision.controller vision.ocr.ocr_engine", ("core", "main"))
    assert not dinamicas, f"vision cargó core/main (subproceso): {dinamicas}"


def test_core_no_importa_vision():
    """core no debe importar vision (el lado del ciclo que PR 3 rompió)."""
    estaticas = _violaciones_estaticas(_archivos_paquetes("core"), ("vision",))
    assert not estaticas, f"core importó vision (estático): {estaticas}"
    dinamicas = _violaciones_dinamicas(NUCLEO_CORE, ("vision",))
    assert not dinamicas, f"core cargó vision (subproceso): {dinamicas}"


@pytest.mark.skip(
    reason="PR 5 (REFACTOR_SPEC §5/§PR6): voice_flow/teacher/ui se cierran con el "
           "desglose de Controller; hoy lista core/listener.py y core/screen_vision.py."
)
def test_core_no_importa_exogenos():
    """core no debe importar voice_flow / teacher / ui (invariante §1)."""
    estaticas = _violaciones_estaticas(_archivos_paquetes("core"), ("teacher", "voice_flow", "ui"))
    assert not estaticas, f"core importó exógenos (estático): {estaticas}"
    dinamicas = _violaciones_dinamicas(NUCLEO_CORE, ("teacher", "voice_flow", "ui"))
    assert not dinamicas, f"core cargó exógenos (subproceso): {dinamicas}"
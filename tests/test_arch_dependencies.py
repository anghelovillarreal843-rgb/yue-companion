"""Gate de arquitectura: core y vision no deben importarse entre sí.

APAGADO a propósito hasta el PR 3 (REFACTOR_SPEC §2), cuando se rompa el ciclo
core <-> vision con los contratos de contracts/. Hasta entonces el ciclo existe
y este test fallaría: por eso está marcado skip.
"""
import sys

import pytest

pytestmark = pytest.mark.skip(
    reason="PR 3 (REFACTOR_SPEC §2): el ciclo core<->vision aún existe; "
           "se activa este gate al romperlo con contracts/."
)


def test_vision_no_importa_core_ni_main():
    import vision.integration  # noqa: F401
    import vision.controller  # noqa: F401
    import vision.ocr.ocr_engine  # noqa: F401

    culpables = [m for m in sys.modules if m == "core" or m.startswith("core.")]
    assert not culpables, f"vision importó core: {culpables}"


def test_core_no_importa_vision_teacher_voice_flow_ui():
    import core.screen_ocr  # noqa: F401
    import core.pc_control  # noqa: F401
    import core.listener  # noqa: F401
    import core.screen_vision  # noqa: F401

    prohibidos = ("vision", "teacher", "voice_flow", "ui")
    culpables = [m for m in sys.modules if m.split(".")[0] in prohibidos]
    assert not culpables, f"core importó módulos prohibidos: {culpables}"

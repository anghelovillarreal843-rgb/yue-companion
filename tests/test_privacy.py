"""Pruebas del panel de privacidad (FASE 4).

    python -m pytest tests/test_privacy.py
    python tests/test_privacy.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.privacy import PrivacyManager, Sensor


def test_por_defecto_todo_bloqueado():
    pm = PrivacyManager()
    for s in Sensor:
        assert pm.is_allowed(s) is False
        assert pm.can_use(s) is False


def test_permiso_habilita_uso():
    pm = PrivacyManager()
    pm.set_allowed(Sensor.CAMERA, True)
    assert pm.can_use(Sensor.CAMERA) is True
    assert pm.can_use(Sensor.MICROPHONE) is False  # los demás siguen bloqueados


def test_mark_active_requiere_permiso():
    pm = PrivacyManager()
    # Sin permiso no puede activarse.
    assert pm.mark_active(Sensor.CAMERA) is False
    pm.set_allowed(Sensor.CAMERA, True)
    assert pm.mark_active(Sensor.CAMERA) is True
    assert pm.is_active(Sensor.CAMERA) is True
    pm.mark_inactive(Sensor.CAMERA)
    assert pm.is_active(Sensor.CAMERA) is False


def test_modo_privacidad_apaga_y_bloquea_todo():
    pm = PrivacyManager()
    pm.set_allowed(Sensor.CAMERA, True)
    pm.mark_active(Sensor.CAMERA)
    assert pm.is_active(Sensor.CAMERA) is True

    pm.set_privacy_mode(True)
    # Se apagó el sensor activo y ya no puede volver a encenderse.
    assert pm.is_active(Sensor.CAMERA) is False
    assert pm.can_use(Sensor.CAMERA) is False
    assert pm.mark_active(Sensor.CAMERA) is False

    pm.set_privacy_mode(False)
    assert pm.can_use(Sensor.CAMERA) is True  # el permiso seguía puesto


def test_retirar_permiso_cierra_sesion_activa():
    pm = PrivacyManager()
    pm.set_allowed(Sensor.MICROPHONE, True)
    pm.mark_active(Sensor.MICROPHONE)
    pm.set_allowed(Sensor.MICROPHONE, False)
    assert pm.is_active(Sensor.MICROPHONE) is False


def test_auditoria_registra_uso():
    pm = PrivacyManager()
    pm.set_allowed(Sensor.CAMERA, True)
    pm.mark_active(Sensor.CAMERA, reason="orden /mira")
    pm.mark_inactive(Sensor.CAMERA)
    log = pm.audit_log(Sensor.CAMERA)
    assert len(log) == 1
    assert log[0]["inicio"] is not None
    assert log[0]["fin"] is not None


def test_panel_state_estructura():
    pm = PrivacyManager()
    pm.set_allowed(Sensor.SCREEN, True)
    pm.mark_active(Sensor.SCREEN)
    st = pm.panel_state()
    assert st["modo_privacidad"] is False
    assert st["sensores"]["screen"]["permitido"] is True
    assert st["sensores"]["screen"]["activo"] is True
    assert st["sensores"]["screen"]["veces_usado"] == 1
    assert "guardan" in st["garantia"]  # la garantía de no almacenar está presente


def test_persistencia_en_disco():
    d = tempfile.mkdtemp(prefix="yue_priv_")
    db = str(Path(d) / "privacy.db")
    pm = PrivacyManager(db)
    pm.set_allowed(Sensor.CAMERA, True)
    pm.mark_active(Sensor.CAMERA)
    pm.mark_inactive(Sensor.CAMERA)
    # Nueva instancia sobre el mismo archivo: recuerda permiso y auditoría.
    pm2 = PrivacyManager(db)
    assert pm2.is_allowed(Sensor.CAMERA) is True
    assert len(pm2.audit_log(Sensor.CAMERA)) == 1


def test_summary_text_cambia_con_estado():
    pm = PrivacyManager()
    assert "no tengo permiso" in pm.summary_text().lower()
    pm.set_allowed(Sensor.CAMERA, True)
    assert "camera" in pm.summary_text().lower()
    pm.set_privacy_mode(True)
    assert "privacidad" in pm.summary_text().lower()


if __name__ == "__main__":
    fallos = []
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            try:
                _f()
                print("  OK  " + _n)
            except Exception as _e:
                print("  FALLO  " + _n + f"  ·  {_e}")
                fallos.append(_n)
    print("\n" + ("TODO OK" if not fallos else f"FALLOS: {fallos}"))
    sys.exit(1 if fallos else 0)

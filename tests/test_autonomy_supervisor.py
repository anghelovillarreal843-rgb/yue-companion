"""Pruebas del supervisor de autonomía (FASE 8).

    python -m pytest tests/test_autonomy_supervisor.py
    python tests/test_autonomy_supervisor.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.autonomy_ext import AutonomySupervisor, Action, RiskLevel


def test_accion_inocua_es_segura():
    sup = AutonomySupervisor()
    d = sup.classify(Action(kind="abrir", target="Bloc de notas"))
    assert d["level"] == RiskLevel.SEGURO
    assert d["requires_confirmation"] is False


def test_borrar_pide_confirmacion():
    sup = AutonomySupervisor()
    d = sup.classify(Action(kind="borrar", target="informe.docx"))
    assert d["level"] == RiskLevel.CONFIRMAR
    assert d["requires_confirmation"] is True
    assert d["undo_hint"]  # trae pista de cómo deshacer


def test_enviar_correo_pide_confirmacion():
    sup = AutonomySupervisor()
    d = sup.classify(Action(kind="enviar", target="correo a mi jefe"))
    assert d["requires_confirmation"] is True


def test_pagar_pide_confirmacion():
    sup = AutonomySupervisor()
    d = sup.classify(Action(kind="pagar", target="suscripción"))
    assert d["level"] == RiskLevel.CONFIRMAR


def test_formatear_se_bloquea():
    sup = AutonomySupervisor()
    d = sup.classify(Action(kind="ejecutar_programa", description="formatear el disco C"))
    assert d["level"] == RiskLevel.BLOQUEADO
    assert d["blocked"] is True


def test_borrar_sistema_se_bloquea():
    sup = AutonomySupervisor()
    d = sup.classify(Action(kind="borrar", target="el sistema", description="rm -rf /"))
    assert d["level"] == RiskLevel.BLOQUEADO


def test_objetivo_sensible_no_es_seguro():
    sup = AutonomySupervisor()
    # "leer" normalmente es SEGURO, pero leer el .env con claves no lo es.
    d = sup.classify(Action(kind="leer", target="archivo .env con password"))
    assert d["level"] == RiskLevel.CONFIRMAR


def test_allowlist_baja_a_seguro():
    sup = AutonomySupervisor(allowlist=["descargar"])
    d = sup.classify(Action(kind="descargar", target="una imagen"))
    assert d["level"] == RiskLevel.SEGURO


def test_protected_sube_a_bloqueado():
    sup = AutonomySupervisor(protected=["fotos_familia"])
    d = sup.classify(Action(kind="mover", target="carpeta fotos_familia"))
    assert d["level"] == RiskLevel.BLOQUEADO


def test_desconocido_pide_confirmacion():
    sup = AutonomySupervisor()
    d = sup.classify(Action(kind="teletransportar", target="algo raro"))
    assert d["level"] == RiskLevel.CONFIRMAR


def test_dry_run_todo_seguro_procede():
    sup = AutonomySupervisor()
    plan = [Action("abrir", "navegador"), Action("buscar", "recetas"),
            Action("resumir", "el resultado")]
    r = sup.dry_run(plan)
    assert r["veredicto"] == "proceder"
    assert r["puede_automatico"] is True


def test_dry_run_con_confirmar_no_es_automatico():
    sup = AutonomySupervisor()
    plan = [Action("abrir", "correo"), Action("enviar", "mensaje")]
    r = sup.dry_run(plan)
    assert r["veredicto"] == "confirmar"
    assert r["puede_automatico"] is False


def test_dry_run_con_bloqueado_se_detiene():
    sup = AutonomySupervisor()
    plan = [Action("abrir", "cmd"), Action("borrar", "todo", description="formatear disco")]
    r = sup.dry_run(plan)
    assert r["veredicto"] == "detener"


def test_cola_aprobar_devuelve_accion():
    sup = AutonomySupervisor()
    tok = sup.queue_for_confirmation(Action("enviar", "correo importante"))
    assert tok in sup.pending()
    act = sup.approve(tok)
    assert act is not None and act.kind == "enviar"
    assert tok not in sup.pending()


def test_cola_rechazar_descarta():
    sup = AutonomySupervisor()
    tok = sup.queue_for_confirmation(Action("comprar", "algo caro"))
    assert sup.reject(tok) is True
    assert tok not in sup.pending()


def test_journal_registra_decisiones():
    sup = AutonomySupervisor()
    sup.classify(Action("abrir", "app"))
    sup.classify(Action("borrar", "archivo"))
    j = sup.journal()
    assert len(j) >= 2


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

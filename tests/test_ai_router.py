"""Pruebas del router multi-IA con relevo automático.

    python -m pytest tests/test_ai_router.py
    python tests/test_ai_router.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.ai_router import (
    AIRouter, Provider, ProviderError, AllProvidersFailed,
    default_providers,
)

MSGS = [{"role": "user", "content": "hola"}]


def _prov(name, key="k"):
    return Provider(name=name, base_url="http://x", api_key=key, model="m")


def test_primer_proveedor_responde():
    def send(p, m, t, temp):
        return f"respuesta de {p.name}"
    r = AIRouter([_prov("groq"), _prov("gemini")], send_fn=send)
    out = r.chat(MSGS)
    assert out["provider"] == "groq"
    assert out["text"] == "respuesta de groq"
    assert out["attempts"] == 1


def test_relevo_cuando_el_primero_no_tiene_cupo():
    def send(p, m, t, temp):
        if p.name == "groq":
            raise ProviderError("sin cupo", kind="quota")
        return f"ok {p.name}"
    r = AIRouter([_prov("groq"), _prov("gemini")], send_fn=send)
    out = r.chat(MSGS)
    assert out["provider"] == "gemini"   # saltó al segundo
    assert out["attempts"] == 2


def test_sin_pausa_entre_proveedores():
    import time
    def send(p, m, t, temp):
        if p.name != "together":
            raise ProviderError("falla", kind="error")
        return "ok"
    r = AIRouter([_prov("groq"), _prov("gemini"), _prov("together")], send_fn=send)
    t0 = time.time()
    r.chat(MSGS)
    # El relevo es inmediato: probar tres proveedores no debe tardar ~nada.
    assert time.time() - t0 < 0.5


def test_todos_fallan_lanza_error():
    def send(p, m, t, temp):
        raise ProviderError("caído", kind="network")
    r = AIRouter([_prov("groq"), _prov("gemini")], send_fn=send)
    try:
        r.chat(MSGS)
        assert False, "debió lanzar AllProvidersFailed"
    except AllProvidersFailed as e:
        assert "groq" in e.errores and "gemini" in e.errores


def test_proveedor_sin_clave_no_entra():
    def send(p, m, t, temp):
        return f"ok {p.name}"
    r = AIRouter([_prov("groq", key=""), _prov("gemini")], send_fn=send)
    out = r.chat(MSGS)
    assert out["provider"] == "gemini"  # groq sin clave se omite


def test_enfriamiento_tras_cupo():
    def send(p, m, t, temp):
        raise ProviderError("sin cupo", kind="quota")
    r = AIRouter([_prov("groq")], send_fn=send)
    try:
        r.chat(MSGS)
    except AllProvidersFailed:
        pass
    # Tras el fallo por cupo, el proveedor queda enfriando (no disponible ya).
    assert r.available() == []
    rep = r.report()[0]
    assert rep["enfriando_seg"] > 0
    assert "quota" in rep["ultimo_error"]


def test_reset_cooldowns_reactiva():
    def send(p, m, t, temp):
        raise ProviderError("x", kind="quota")
    r = AIRouter([_prov("groq")], send_fn=send)
    try:
        r.chat(MSGS)
    except AllProvidersFailed:
        pass
    assert r.available() == []
    r.reset_cooldowns()
    assert len(r.available()) == 1  # vuelve a estar disponible


def test_respuesta_vacia_cuenta_como_fallo():
    def send(p, m, t, temp):
        return "" if p.name == "groq" else "ok"
    r = AIRouter([_prov("groq"), _prov("gemini")], send_fn=send)
    out = r.chat(MSGS)
    assert out["provider"] == "gemini"


def test_default_providers_solo_con_clave():
    entorno = {
        "GROQ_API_KEY": "abc",
        "GEMINI_API_KEY": "",          # sin clave
        "OPENROUTER_API_KEY": "xyz",
    }
    fila = default_providers(getenv=lambda k, d="": entorno.get(k, d))
    activos = [p.name for p in fila if p.enabled]
    assert "groq" in activos
    assert "openrouter" in activos
    assert "gemini" not in activos    # sin clave -> deshabilitado


def test_default_providers_modelo_personalizado():
    entorno = {"GROQ_API_KEY": "abc", "GROQ_MODEL": "mi-modelo-especial"}
    fila = default_providers(getenv=lambda k, d="": entorno.get(k, d))
    groq = next(p for p in fila if p.name == "groq")
    assert groq.model == "mi-modelo-especial"


def test_recuperacion_tras_enfriar():
    estado = {"falla": True}
    def send(p, m, t, temp):
        if estado["falla"]:
            raise ProviderError("cupo", kind="quota")
        return "ya funciona"
    # Enfriamiento cortito para la prueba.
    r = AIRouter([_prov("groq")], send_fn=send, cooldowns={"quota": 0.05})
    try:
        r.chat(MSGS)
    except AllProvidersFailed:
        pass
    import time
    time.sleep(0.08)
    estado["falla"] = False
    out = r.chat(MSGS)  # ya pasó el enfriamiento
    assert out["text"] == "ya funciona"


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

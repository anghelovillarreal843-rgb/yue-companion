import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.ai_router import AIRouter, Provider, ProviderError, AllProvidersFailed, default_providers


def _p(name="groq-1", key="k"):
    return Provider(name=name, base_url="https://api.groq.com/openai/v1", api_key=key,
                    model="qwen/qwen3.6-27b")


def test_primera_clave_responde():
    r = AIRouter([_p()], send_fn=lambda p,m,t,temp: "hola")
    out = r.chat([{"role":"user","content":"hola"}])
    assert out["text"] == "hola"
    assert out["provider"] == "groq"


def test_auth_pasa_a_siguiente_clave():
    def send(p, *args):
        if p.name == "groq-1":
            raise ProviderError("mala", kind="auth", status=401)
        return "ok"
    r = AIRouter([_p("groq-1","a"), _p("groq-2","b")], send_fn=send)
    out = r.chat([{"role":"user","content":"x"}])
    assert out["key_slot"] == "groq-2"


def test_429_no_rota():
    calls = []
    def send(p, *args):
        calls.append(p.name)
        raise ProviderError("rate limit", kind="quota", status=429)
    r = AIRouter([_p("groq-1","a"), _p("groq-2","b")], send_fn=send)
    try:
        r.chat([{"role":"user","content":"x"}])
        assert False, "debió fallar"
    except AllProvidersFailed:
        pass
    assert calls == ["groq-1"]


def test_default_providers_una_sola_cuenta():
    """CAMBIO: por defecto YUE usa SOLO la clave de GROQ_API_KEY."""
    env = {
        "GROQ_API_KEY": "a",
        "GROQ_API_KEY_2": "b",
        "GROQ_API_KEY_3": "a",
        "GROQ_API_KEYS": "c,b",
        "GROQ_MODEL": "qwen/qwen3.6-27b",
    }
    fila = default_providers(getenv=lambda k,d="": env.get(k,d))
    assert [p.api_key for p in fila] == ["a"]
    assert all(p.model == "qwen/qwen3.6-27b" for p in fila)


def test_default_providers_lee_varias_claves_y_deduplica():
    """El modo antiguo sigue existiendo con GROQ_UNA_SOLA_CUENTA=false."""
    env = {
        "GROQ_UNA_SOLA_CUENTA": "false",
        "GROQ_API_KEY": "a",
        "GROQ_API_KEY_2": "b",
        "GROQ_API_KEY_3": "a",
        "GROQ_API_KEYS": "c,b",
        "GROQ_MODEL": "qwen/qwen3.6-27b",
    }
    fila = default_providers(getenv=lambda k,d="": env.get(k,d))
    assert [p.api_key for p in fila] == ["a","c","b"]
    assert all(p.model == "qwen/qwen3.6-27b" for p in fila)

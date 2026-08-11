"""Router de conversación Groq con relevo entre credenciales autorizadas.

YUE usa UN SOLO proveedor: Groq. Se puede configurar una clave principal y
claves adicionales del mismo entorno autorizado para continuidad operativa.

Importante: un HTTP 429 (rate limit/cuota) NO provoca rotación a otra clave.
El router respeta el límite y devuelve el error. El relevo sí puede ocurrir ante
credenciales revocadas/sin permiso, problemas de red o errores 5xx.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


class ProviderError(RuntimeError):
    def __init__(self, message: str, kind: str = "error", status: int = 0):
        super().__init__(message)
        self.kind = kind
        self.status = status


class AllProvidersFailed(RuntimeError):
    def __init__(self, errores: dict):
        self.errores = errores
        detalle = "; ".join(f"{k}: {v}" for k, v in errores.items()) or "sin claves Groq"
        super().__init__(f"Groq no pudo responder ({detalle})")


_COOLDOWN = {
    "quota": 60.0,
    "auth": 1800.0,
    "model_gone": 300.0,
    "network": 20.0,
    "server": 20.0,
    "error": 30.0,
}


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    enabled: bool = True
    cooldown_until: float = 0.0
    last_error: str = ""
    fails: int = 0
    ok: int = 0

    def usable(self, now: float) -> bool:
        return self.enabled and bool(self.api_key) and now >= self.cooldown_until


class AIRouter:
    def __init__(self, providers: list[Provider], send_fn=None, cooldowns: dict | None = None):
        self.providers = list(providers)
        self._send = send_fn or _default_send
        self._cooldowns = dict(_COOLDOWN)
        if cooldowns:
            self._cooldowns.update(cooldowns)

    def available(self, now: float | None = None) -> list[Provider]:
        now = time.time() if now is None else now
        return [p for p in self.providers if p.usable(now)]

    def report(self) -> list[dict]:
        now = time.time()
        return [{
            "nombre": p.name,
            "modelo": p.model,
            "disponible": p.usable(now),
            "tiene_clave": bool(p.api_key),
            "enfriando_seg": max(0.0, round(p.cooldown_until - now, 1)),
            "exitos": p.ok,
            "fallos": p.fails,
            "ultimo_error": p.last_error,
        } for p in self.providers]

    def chat(self, messages: list[dict], timeout: int = 45, temperature: float = 0.85) -> dict:
        errores: dict[str, str] = {}
        intentos = 0
        now = time.time()

        for p in self.providers:
            if not p.usable(now):
                if not p.api_key:
                    errores[p.name] = "sin clave configurada"
                elif not p.enabled:
                    errores[p.name] = "desactivado"
                else:
                    errores[p.name] = f"enfriando {max(0, int(p.cooldown_until-now))}s"
                continue

            intentos += 1
            try:
                text = self._send(p, messages, timeout, temperature)
                if not text or not text.strip():
                    raise ProviderError("respuesta vacía", kind="error")
                p.ok += 1
                p.last_error = ""
                return {"text": text.strip(), "provider": "groq", "key_slot": p.name,
                        "model": p.model, "attempts": intentos}
            except ProviderError as e:
                p.fails += 1
                p.last_error = f"{e.kind}: {e}"
                p.cooldown_until = now + self._cooldowns.get(e.kind, 30.0)
                errores[p.name] = p.last_error
                # No encadenar otra credencial para superar límites de Groq.
                if e.kind == "quota":
                    raise AllProvidersFailed(errores) from e
                # Un modelo retirado es común a las claves; AIEngine resolverá
                # un modelo vigente y reintentará, en vez de quemar más claves.
                if e.kind == "model_gone":
                    raise AllProvidersFailed(errores) from e
            except Exception as e:
                p.fails += 1
                p.last_error = f"error: {e}"
                p.cooldown_until = now + self._cooldowns.get("error", 30.0)
                errores[p.name] = p.last_error

        raise AllProvidersFailed(errores)

    def set_model(self, model: str) -> None:
        for p in self.providers:
            p.model = model

    def reset_cooldowns(self):
        for p in self.providers:
            p.cooldown_until = 0.0


def _una_sola_cuenta(getenv) -> bool:
    """Por defecto YUE usa UNA SOLA cuenta de Groq (la de GROQ_API_KEY).

    Se puede reactivar el relevo entre varias credenciales poniendo
    GROQ_UNA_SOLA_CUENTA=false en el .env, pero no es lo normal.
    """
    valor = (getenv("GROQ_UNA_SOLA_CUENTA", "true") or "true").strip().lower()
    return valor not in ("0", "false", "no", "off")


def _split_keys(getenv) -> list[str]:
    principal = (getenv("GROQ_API_KEY", "") or "").strip()

    # --- MODO NORMAL: una sola cuenta -------------------------------------
    # Nada de GROQ_API_KEYS ni GROQ_API_KEY_2..9: si la clave principal falla,
    # YUE lo dice claramente en vez de saltar a otra cuenta por detrás.
    if _una_sola_cuenta(getenv):
        return [principal] if principal else []

    # --- MODO ANTIGUO (opcional): varias credenciales en fila -------------
    claves = []
    if principal:
        claves.append(principal)
    for x in (getenv("GROQ_API_KEYS", "") or "").split(","):
        x = x.strip()
        if x:
            claves.append(x)
    for i in range(2, 10):
        x = (getenv(f"GROQ_API_KEY_{i}", "") or "").strip()
        if x:
            claves.append(x)
    return list(dict.fromkeys(claves))


def default_providers(getenv=None) -> list[Provider]:
    if getenv is None:
        import os
        getenv = os.getenv
    base = (getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1") or "").strip().rstrip("/")
    model = (getenv("GROQ_MODEL", "qwen/qwen3.6-27b") or "").strip() or "qwen/qwen3.6-27b"
    keys = _split_keys(getenv)
    return [Provider(name=f"groq-{i}", base_url=base, api_key=k, model=model)
            for i, k in enumerate(keys, 1)]


def build_default_router(send_fn=None, getenv=None) -> AIRouter:
    return AIRouter(default_providers(getenv=getenv), send_fn=send_fn)


def _default_send(provider: Provider, messages: list[dict], timeout: int, temperature: float) -> str:
    try:
        import requests
    except Exception as e:  # pragma: no cover
        raise ProviderError(f"falta requests: {e}", kind="error")

    url = f"{provider.base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"}
    payload = {"model": provider.model, "messages": messages, "temperature": temperature}

    # NUEVO: sin razonamiento visible y con tope de tokens -> respuestas mucho
    # más rápidas y sin el monólogo en inglés. Ver core/groq_tuning.py.
    try:
        from core import groq_tuning
    except Exception:
        groq_tuning = None
    if groq_tuning is not None:
        groq_tuning.aplicar_ajustes(payload)

    # NUEVO: conexión reutilizada (keep-alive). Ahorra el saludo TLS en cada
    # mensaje. Si no hay sesión disponible, se usa `requests` como siempre.
    http = groq_tuning.sesion() if groq_tuning is not None else None
    cliente = http or requests

    for intento in range(3):
        try:
            resp = cliente.post(url, headers=headers, json=payload, timeout=timeout)
        except Exception as e:
            raise ProviderError(f"red: {e}", kind="network")

        if resp.status_code == 200:
            try:
                crudo = (resp.json()["choices"][0]["message"]["content"] or "")
            except Exception as e:
                raise ProviderError(f"respuesta ilegible: {e}", kind="error")
            # NUEVO: fuera el <think>...</think> antes de que YUE lo diga.
            try:
                from core import text_sanitizer
                limpio = text_sanitizer.limpiar(crudo)
            except Exception:
                limpio = crudo.strip()
            if not limpio and crudo.strip():
                # Todo era razonamiento (respuesta truncada): lo tratamos como
                # respuesta vacía para que el motor reintente, no lo leemos.
                raise ProviderError("el modelo solo devolvió razonamiento", kind="error")
            return limpio

        # NUEVO: si el modelo no acepta alguno de los parámetros nuevos, se
        # retira ESE parámetro y se reintenta. Nunca hay regresión.
        if resp.status_code == 400 and groq_tuning is not None and intento < 2:
            try:
                cuerpo400 = resp.text[:500]
            except Exception:
                cuerpo400 = ""
            retirado = groq_tuning.soltar_parametro_no_soportado(payload, cuerpo400)
            if retirado:
                print(f"[ia] «{provider.model}» no acepta {retirado}; reintento sin él.")
                continue
        break

    try:
        cuerpo = resp.text[:500]
    except Exception:
        cuerpo = ""
    status = resp.status_code
    low = cuerpo.lower()
    if status == 429 or "rate limit" in low or "quota" in low:
        raise ProviderError(f"límite/cuota de Groq: {cuerpo}", kind="quota", status=status)
    if status in (401, 403):
        raise ProviderError(f"clave inválida o sin permiso: {cuerpo}", kind="auth", status=status)
    if status == 404 or ("model" in low and ("not found" in low or "decommission" in low)):
        raise ProviderError(f"modelo no disponible: {cuerpo}", kind="model_gone", status=status)
    if 500 <= status <= 599:
        raise ProviderError(f"servidor Groq HTTP {status}: {cuerpo}", kind="server", status=status)
    raise ProviderError(f"HTTP {status}: {cuerpo}", kind="error", status=status)

"""Router de varias IAs con relevo automático (multi-proveedor).

Problema real (lo viste en tu pantalla): cuando el proveedor de turno se cae, se
queda sin cupo o retira el modelo, YUE se queda sin responder. Este router lo
resuelve: tiene VARIOS proveedores gratis en fila y, si uno falla, pasa al
siguiente AL INSTANTE (sin esperas), hasta que uno responda.

Cómo funciona:
  - Cada proveedor es una API compatible con OpenAI (Groq, Gemini, OpenRouter,
    Cerebras, Together…). Todos hablan el mismo formato `chat/completions`, así
    que se manejan igual con solo cambiar base + clave + modelo.
  - Si un proveedor da error, se CLASIFICA el fallo (sin cupo / clave mala /
    modelo retirado / red) y se le pone un enfriamiento para no reintentarlo en
    vano; se salta al siguiente SIN pausa.
  - Cuando el enfriamiento pasa, ese proveedor vuelve a estar disponible solo.

Es ADITIVO: no reemplaza `core/ai_engine.py`. Este router se puede enchufar para
que `chat()` pruebe la fila de proveedores. Los proveedores se configuran por
variables de entorno (solo pones las claves que tengas); los que no tengan clave
simplemente no entran a la fila.

La parte de red usa `requests` si está; para pruebas, la función de envío se puede
inyectar, así el router se prueba sin internet.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


# ---- Clasificación de fallos -------------------------------------------------
class ProviderError(RuntimeError):
    """Fallo de un proveedor, con su tipo para decidir el enfriamiento."""

    def __init__(self, message: str, kind: str = "error", status: int = 0):
        super().__init__(message)
        # kind: quota | auth | model_gone | network | error
        self.kind = kind
        self.status = status


class AllProvidersFailed(RuntimeError):
    """Ningún proveedor de la fila pudo responder."""

    def __init__(self, errores: dict):
        self.errores = errores
        detalle = "; ".join(f"{k}: {v}" for k, v in errores.items()) or "sin proveedores"
        super().__init__(f"Todos los proveedores fallaron ({detalle})")


# Enfriamientos por tipo de fallo, en segundos. Sin cupo dura más (para rotar
# lejos de ese proveedor); clave mala dura mucho (no sirve reintentar pronto).
_COOLDOWN = {
    "quota": 300.0,       # 5 min: se acabó el cupo/límite por minuto
    "auth": 1800.0,       # 30 min: la clave está mal o sin permiso
    "model_gone": 900.0,  # 15 min: el modelo fue retirado
    "network": 20.0,      # 20 s: caída pasajera de red
    "error": 30.0,        # 30 s: error genérico
}


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    enabled: bool = True
    # Estado interno de salud (lo maneja el router).
    cooldown_until: float = 0.0
    last_error: str = ""
    fails: int = 0
    ok: int = 0

    def usable(self, now: float) -> bool:
        return (self.enabled and bool(self.api_key)
                and now >= self.cooldown_until)


class AIRouter:
    def __init__(self, providers: list[Provider], send_fn=None,
                 cooldowns: dict | None = None):
        self.providers = list(providers)
        # Función que hace la llamada real. Inyectable para pruebas.
        self._send = send_fn or _default_send
        self._cooldowns = dict(_COOLDOWN)
        if cooldowns:
            self._cooldowns.update(cooldowns)

    # ---- Consulta de estado ----------------------------------------------
    def available(self, now: float | None = None) -> list[Provider]:
        now = time.time() if now is None else now
        return [p for p in self.providers if p.usable(now)]

    def report(self) -> list[dict]:
        now = time.time()
        return [
            {
                "nombre": p.name,
                "modelo": p.model,
                "disponible": p.usable(now),
                "tiene_clave": bool(p.api_key),
                "enfriando_seg": max(0.0, round(p.cooldown_until - now, 1)),
                "exitos": p.ok,
                "fallos": p.fails,
                "ultimo_error": p.last_error,
            }
            for p in self.providers
        ]

    # ---- Llamada con relevo ----------------------------------------------
    def chat(self, messages: list[dict], timeout: int = 45,
             temperature: float = 0.85) -> dict:
        """Pide una respuesta probando la fila de proveedores hasta que uno responda.

        Devuelve {'text', 'provider', 'model', 'attempts'}. Si todos fallan,
        lanza AllProvidersFailed con el detalle por proveedor.
        """
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
                    errores[p.name] = f"enfriando {int(p.cooldown_until - now)}s"
                continue

            intentos += 1
            try:
                text = self._send(p, messages, timeout, temperature)
                if not text or not text.strip():
                    raise ProviderError("respuesta vacía", kind="error")
                p.ok += 1
                p.last_error = ""
                return {"text": text.strip(), "provider": p.name,
                        "model": p.model, "attempts": intentos}
            except ProviderError as e:
                # Clasifica, enfría y SIGUE con el siguiente SIN pausa.
                p.fails += 1
                p.last_error = f"{e.kind}: {e}"
                p.cooldown_until = now + self._cooldowns.get(e.kind, 30.0)
                errores[p.name] = p.last_error
            except Exception as e:  # cualquier otro fallo inesperado
                p.fails += 1
                p.last_error = f"error: {e}"
                p.cooldown_until = now + self._cooldowns.get("error", 30.0)
                errores[p.name] = p.last_error

        raise AllProvidersFailed(errores)

    def reset_cooldowns(self):
        """Vuelve a poner disponibles a todos (p. ej. al recuperar la red)."""
        for p in self.providers:
            p.cooldown_until = 0.0


# ---- Catálogo de proveedores gratis (por variables de entorno) --------------
# Solo entran a la fila los que tengan clave. El orden es la prioridad: primero
# los más rápidos/generosos. Ajusta con las variables de entorno del .env.
_CATALOGO = [
    # (nombre, base, variable_de_clave, modelo por defecto)
    ("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
     "llama-3.3-70b-versatile"),
    ("cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY",
     "llama-3.3-70b"),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
     "GEMINI_API_KEY", "gemini-2.0-flash"),
    ("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
     "meta-llama/llama-3.3-70b-instruct:free"),
    ("together", "https://api.together.xyz/v1", "TOGETHER_API_KEY",
     "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"),
    # OpenAI/ChatGPT no es gratis, pero queda el hueco: si pones OPENAI_API_KEY
    # entra a la fila automáticamente.
    ("openai", "https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
]


def default_providers(getenv=None) -> list[Provider]:
    """Arma la fila leyendo claves del entorno. Los sin clave quedan fuera.

    `getenv` se puede inyectar en pruebas; por defecto usa os.getenv.
    """
    if getenv is None:
        import os
        getenv = os.getenv
    fila = []
    for nombre, base, var_clave, modelo_def in _CATALOGO:
        clave = (getenv(var_clave, "") or "").strip()
        # Permite forzar el modelo por proveedor con VAR + _MODEL.
        modelo = (getenv(f"{nombre.upper()}_MODEL", "") or "").strip() or modelo_def
        # Un proveedor sin clave se agrega deshabilitado (para verlo en el reporte).
        fila.append(Provider(name=nombre, base_url=base, api_key=clave,
                             model=modelo, enabled=bool(clave)))
    return fila


def build_default_router(send_fn=None, getenv=None) -> AIRouter:
    return AIRouter(default_providers(getenv=getenv), send_fn=send_fn)


# ---- Envío real (compatible OpenAI) -----------------------------------------
def _default_send(provider: Provider, messages: list[dict], timeout: int,
                  temperature: float) -> str:
    """Llama a `{base}/chat/completions` y devuelve el texto. Clasifica errores."""
    try:
        import requests
    except Exception as e:  # pragma: no cover
        raise ProviderError(f"falta 'requests': {e}", kind="error")

    url = f"{provider.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": provider.model, "messages": messages,
               "temperature": temperature}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except Exception as e:
        raise ProviderError(f"red: {e}", kind="network")

    if resp.status_code == 200:
        try:
            data = resp.json()
            return (data["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:
            raise ProviderError(f"respuesta ilegible: {e}", kind="error")

    # Clasificación de errores HTTP.
    cuerpo = ""
    try:
        cuerpo = resp.text[:300]
    except Exception:
        pass
    status = resp.status_code
    if status == 429 or "quota" in cuerpo.lower() or "rate limit" in cuerpo.lower():
        raise ProviderError(f"sin cupo/límite: {cuerpo}", kind="quota", status=status)
    if status in (401, 403):
        raise ProviderError(f"clave inválida o sin permiso: {cuerpo}",
                            kind="auth", status=status)
    if status == 404 or "model" in cuerpo.lower() and "not" in cuerpo.lower():
        raise ProviderError(f"modelo no disponible: {cuerpo}",
                            kind="model_gone", status=status)
    raise ProviderError(f"HTTP {status}: {cuerpo}", kind="error", status=status)

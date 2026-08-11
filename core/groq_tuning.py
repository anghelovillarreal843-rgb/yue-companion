"""Ajustes de VELOCIDAD y de "sin razonamiento" para las llamadas a Groq. [ADITIVO]

Tres cosas, todas pensadas para que YUE conteste rápido y en español:

1. `sesion()`  -> una única `requests.Session` compartida. Antes cada mensaje
   abría una conexión TCP+TLS nueva contra api.groq.com (unos 200-400 ms de
   puro saludo). Con keep-alive esa espera desaparece a partir del segundo
   mensaje.

2. `aplicar_ajustes()` -> añade al payload:
     - `reasoning_format: "hidden"` y `reasoning_effort: "none"`, para que los
       modelos de razonamiento (Qwen3, gpt-oss) NO escriban su monólogo interno
       en inglés ni gasten cientos de tokens antes de contestar.
     - `max_completion_tokens`, porque YUE responde de 1 a 4 frases: sin tope,
       el modelo puede generar (y hacer esperar) mucho más de lo necesario.

3. `soltar_parametro_no_soportado()` -> si un modelo concreto no acepta alguno
   de esos parámetros, Groq responde HTTP 400 nombrándolo. En vez de fallar, se
   quita ESE parámetro y se reintenta una sola vez. Así nunca hay regresión con
   modelos que no soporten razonamiento configurable.
"""
from __future__ import annotations

import re
import threading

_lock = threading.Lock()
_sesion = None

# Parámetros que añadimos y que un modelo podría rechazar.
_OPCIONALES = ("reasoning_effort", "reasoning_format", "max_completion_tokens")


def sesion():
    """Sesión HTTP compartida (keep-alive). Si algo falla, devuelve None."""
    global _sesion
    if _sesion is not None:
        return _sesion
    with _lock:
        if _sesion is not None:
            return _sesion
        try:
            import requests
            s = requests.Session()
            try:
                from requests.adapters import HTTPAdapter
                adaptador = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
                s.mount("https://", adaptador)
                s.mount("http://", adaptador)
            except Exception:
                pass
            _sesion = s
        except Exception:
            _sesion = None
    return _sesion


def _cfg(nombre, defecto):
    try:
        import config
        return getattr(config, nombre, defecto)
    except Exception:
        return defecto


def aplicar_ajustes(payload: dict, max_tokens=None, para_chat: bool = True) -> dict:
    """Devuelve el MISMO payload con los ajustes de velocidad puestos.

    No pisa nada que ya venga definido por quien llama.
    """
    if not isinstance(payload, dict):
        return payload
    if not bool(_cfg("GROQ_SIN_RAZONAMIENTO", True)):
        return payload

    payload.setdefault("reasoning_format", "hidden")
    payload.setdefault("reasoning_effort", "none")

    tope = max_tokens
    if tope is None and para_chat:
        tope = int(_cfg("GROQ_MAX_TOKENS_CHAT", 220) or 0)
    if tope and "max_completion_tokens" not in payload and "max_tokens" not in payload:
        payload["max_completion_tokens"] = int(tope)
    return payload


def soltar_parametro_no_soportado(payload: dict, mensaje: str) -> str:
    """Quita del payload el parámetro del que se queja Groq.

    Devuelve el nombre del parámetro retirado ("" si no aplica), para que quien
    llama sepa si merece la pena reintentar.
    """
    if not isinstance(payload, dict) or not mensaje:
        return ""
    texto = str(mensaje).lower()
    for nombre in _OPCIONALES:
        if nombre in texto and nombre in payload:
            payload.pop(nombre, None)
            return nombre
    # Mensajes genéricos del tipo "unsupported parameter" / "unrecognized".
    if re.search(r"(unsupported|unrecognized|unknown|not supported|invalid).{0,40}(parameter|field|argument)", texto):
        for nombre in _OPCIONALES:
            if nombre in payload:
                payload.pop(nombre, None)
                return nombre
    return ""

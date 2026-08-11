"""Resolución de modelos Groq, errores legibles y respaldo sin visión.

Módulo ADITIVO: no reemplaza nada de `core/ai_engine.py`, solo le da tres cosas
que hoy le faltan y que son la causa del error 404 en cadena:

  1. `post_chat()`  -> lanza un error que SÍ dice el motivo real. Hoy
     `resp.raise_for_status()` tira el cuerpo de la respuesta a la basura, así
     que Groq contesta «the model X has been decommissioned» y en la consola
     solo se ve «404 Client Error: Not Found». Sin ese texto es imposible
     diagnosticarlo.
  2. `resolve_model()` -> pregunta a /v1/models qué modelos existen DE VERDAD en
     la cuenta y elige el mejor disponible si el del .env ya no está. Groq retira
     modelos cada pocos meses; el .env se queda apuntando a un fantasma.
  3. `ModelGoneError` -> distingue «el modelo no existe» de «el plan vino mal
     escrito», para que el ciclo del control del PC no queme sus 3 intentos
     repitiendo una llamada que jamás va a funcionar.

Solo usa `requests`, que ya está en requirements.txt.
"""
from __future__ import annotations

import time

import requests

# Cuánto tiempo confiamos en la lista de modelos antes de volver a pedirla.
_TTL_SEGUNDOS = 600

# Caché de proceso: {clave_base: (timestamp, [ids])}
_cache_modelos: dict[str, tuple[float, list[str]]] = {}
# Modelo ya resuelto y confirmado por rol: {"vision": "...", "text": "..."}
_resueltos: dict[str, str] = {}
# Modelos DESCARTADOS por rol tras fallar en tiempo real (lista negra de sesión):
# {"vision": {"modelo-que-fallo", ...}}. Persiste aunque se llame a olvidar().
_descartados: dict[str, set] = {}


def descartar(rol: str, modelo: str) -> None:
    """Marca un modelo como no usable para ese rol (falló al llamarlo).

    Así, aunque Groq lo siga listando en /models, no lo volveremos a elegir en
    esta sesión y probaremos el siguiente candidato.
    """
    if not modelo:
        return
    _descartados.setdefault(rol, set()).add(modelo)
    # Si estaba fijado como resuelto, lo soltamos para forzar una nueva elección.
    if _resueltos.get(rol) == modelo:
        _resueltos.pop(rol, None)


class GroqError(RuntimeError):
    """Error de la API con el mensaje real de Groq dentro."""

    def __init__(self, mensaje: str, status: int = 0, code: str = ""):
        super().__init__(mensaje)
        self.status = status
        self.code = code


class ModelGoneError(GroqError):
    """El modelo pedido no existe, fue retirado o no está en esta cuenta."""


# Pistas de nombre para reconocer un modelo con visión sin depender de una lista
# fija: los nombres cambian, pero estas piezas se repiten generación tras
# generación.
_PISTAS_VISION = (
    "scout", "maverick", "vision", "llava", "vl", "omni",
    "llama-4", "llama4", "pixtral", "gemma-3", "gemma3",
)
# Piezas que descartan un modelo para chat/planificación de texto.
_NO_TEXTO = (
    "whisper", "tts", "guard", "embed", "moderation", "distil-whisper",
    "playai", "compound",
)


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _mensaje_de_respuesta(resp) -> tuple[str, str]:
    """Saca (mensaje, code) del cuerpo JSON de error de Groq."""
    try:
        data = resp.json()
    except Exception:
        return (resp.text or "")[:300].strip(), ""
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        return str(error.get("message", ""))[:400], str(error.get("code", ""))
    return str(data)[:300], ""


def post_chat(base: str, api_key: str, payload: dict, timeout: int = 45) -> dict:
    """POST a /chat/completions que convierte los errores en algo entendible.

    Lanza ModelGoneError si el problema es el modelo, GroqError si es otra cosa.
    """
    url = f"{base}/chat/completions"

    # NUEVO: misma sesión HTTP para todas las llamadas (keep-alive). Ahorra el
    # saludo TLS de cada mensaje, que era medio segundo de espera por respuesta.
    try:
        from core import groq_tuning
        cliente = groq_tuning.sesion() or requests
    except Exception:
        groq_tuning = None
        cliente = requests

    intentos_param = 0
    while True:
        try:
            resp = cliente.post(url, json=payload, headers=_headers(api_key), timeout=timeout)
        except requests.RequestException as exc:
            raise GroqError(f"No pude hablar con Groq: {exc}") from exc

        # NUEVO: si el modelo rechaza uno de los parámetros de velocidad
        # (reasoning_effort, reasoning_format, max_completion_tokens), lo
        # quitamos y reintentamos en vez de dar la conversación por perdida.
        if resp.status_code == 400 and groq_tuning is not None and intentos_param < 3:
            try:
                cuerpo400 = resp.text[:500]
            except Exception:
                cuerpo400 = ""
            retirado = groq_tuning.soltar_parametro_no_soportado(payload, cuerpo400)
            if retirado:
                intentos_param += 1
                print(f"[ia] «{payload.get('model', '?')}» no acepta {retirado}; reintento sin él.")
                continue
        break

    if resp.status_code >= 400:
        mensaje, code = _mensaje_de_respuesta(resp)
        modelo = str(payload.get("model", "?"))
        pistas_modelo = (
            "model_not_found", "model_decommissioned", "does not exist",
            "has been decommissioned", "decommissioned", "no longer supported",
        )
        texto = f"{mensaje} {code}".lower()
        es_modelo = (
            resp.status_code == 404
            or any(p in texto for p in pistas_modelo)
        )
        detalle = mensaje or f"HTTP {resp.status_code} sin mensaje"
        if es_modelo:
            raise ModelGoneError(
                f"El modelo «{modelo}» no está disponible en tu cuenta de Groq: {detalle}",
                resp.status_code, code,
            )
        raise GroqError(
            f"Groq respondió HTTP {resp.status_code} con «{modelo}»: {detalle}",
            resp.status_code, code,
        )

    try:
        return resp.json()
    except Exception as exc:
        raise GroqError(f"Groq devolvió una respuesta ilegible: {exc}") from exc


def listar_modelos(base: str, api_key: str, timeout: int = 12, forzar: bool = False) -> list[str]:
    """IDs de los modelos que la cuenta puede usar ahora mismo (con caché)."""
    if not api_key:
        return []
    ahora = time.time()
    guardado = _cache_modelos.get(base)
    if guardado and not forzar and (ahora - guardado[0]) < _TTL_SEGUNDOS:
        return guardado[1]
    try:
        resp = requests.get(f"{base}/models", headers=_headers(api_key), timeout=timeout)
        resp.raise_for_status()
        datos = resp.json().get("data", [])
        ids = sorted(str(m.get("id", "")) for m in datos if m.get("id"))
    except Exception as exc:
        print("[ia] no pude listar los modelos de Groq:", exc)
        ids = []
    if ids:
        _cache_modelos[base] = (ahora, ids)
    return ids


def preflight(base: str, api_key: str, timeout: int = 10) -> dict:
    """Comprueba las credenciales de un endpoint compatible-OpenAI SIN gastar
    tokens ni capturar nada: hace un GET a /models e interpreta el resultado.

    Devuelve un dict (nunca lanza):
      {"ok": True,  "status": 200}
      {"ok": False, "reason": "no_base"}      falta la URL base
      {"ok": False, "reason": "no_key"}       falta la clave (API key)
      {"ok": False, "reason": "invalid_key"}  401/403: clave inválida o sin acceso
      {"ok": False, "reason": "unreachable"}  red/timeout/host caído
      {"ok": False, "reason": "http"}         otro error HTTP
    """
    if not base:
        return {"ok": False, "reason": "no_base", "detail": "falta la URL base"}
    if not api_key:
        return {"ok": False, "reason": "no_key", "detail": "falta la clave (API key)"}
    try:
        resp = requests.get(f"{base}/models", headers=_headers(api_key), timeout=timeout)
    except requests.RequestException as exc:
        return {"ok": False, "reason": "unreachable", "detail": str(exc)[:200]}
    if resp.status_code in (401, 403):
        mensaje, _ = _mensaje_de_respuesta(resp)
        return {
            "ok": False, "reason": "invalid_key", "status": resp.status_code,
            "detail": (mensaje or f"HTTP {resp.status_code}")[:200],
        }
    if resp.status_code >= 400:
        mensaje, _ = _mensaje_de_respuesta(resp)
        return {
            "ok": False, "reason": "http", "status": resp.status_code,
            "detail": (mensaje or f"HTTP {resp.status_code}")[:200],
        }
    return {"ok": True, "status": resp.status_code}


def _candidatos_vision(disponibles: list[str]) -> list[str]:
    return [m for m in disponibles if any(p in m.lower() for p in _PISTAS_VISION)]


def _candidatos_texto(disponibles: list[str]) -> list[str]:
    utiles = [m for m in disponibles if not any(p in m.lower() for p in _NO_TEXTO)]
    # Los más grandes primero: suelen planificar mejor.
    def peso(nombre: str) -> tuple:
        n = nombre.lower()
        return (
            0 if "70b" in n or "120b" in n or "maverick" in n else 1,
            0 if "versatile" in n or "instruct" in n else 1,
            nombre,
        )
    return sorted(utiles, key=peso)


def resolve_model(base: str, api_key: str, preferido: str, rol: str = "text",
                  extras: tuple = ()) -> str:
    """Devuelve un modelo que exista de verdad para ese rol.

    rol: "vision" o "text". `extras` son alternativas que quieras probar antes
    de la heurística (por ejemplo las del .env).
    Si no hay forma de comprobarlo, devuelve `preferido` sin romper nada.
    """
    if rol in _resueltos:
        return _resueltos[rol]

    black = _descartados.get(rol, set())
    disponibles = listar_modelos(base, api_key)
    disponibles = [m for m in disponibles if m not in black]
    if not disponibles:
        # Sin lista fiable: usamos el preferido salvo que esté descartado.
        return "" if preferido in black else preferido

    if preferido and preferido in disponibles and preferido not in black:
        _resueltos[rol] = preferido
        return preferido

    orden = [m for m in extras if m in disponibles and m not in black]
    orden += (_candidatos_vision(disponibles) if rol == "vision"
              else _candidatos_texto(disponibles))
    orden = [m for m in dict.fromkeys(orden) if m and m not in black]  # sin duplicados ni vetados

    if not orden:
        return ""                              # nada usable para este rol

    elegido = orden[0]
    _resueltos[rol] = elegido
    print(f"[ia] «{preferido}» no sirve para {rol}; uso «{elegido}».")
    return elegido


def olvidar(rol: str = ""):
    """Borra lo resuelto (úsalo si un modelo deja de funcionar a mitad)."""
    if rol:
        _resueltos.pop(rol, None)
    else:
        _resueltos.clear()
    _cache_modelos.clear()


def hay_vision(base: str, api_key: str) -> bool:
    """¿Esta cuenta tiene algún modelo capaz de mirar imágenes?"""
    return bool(_candidatos_vision(listar_modelos(base, api_key)))

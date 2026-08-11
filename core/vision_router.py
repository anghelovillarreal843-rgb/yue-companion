"""Router de VISIÓN con relevo automático (solo modelos multimodales).

Hermano de `core/ai_router.py`, pero con una diferencia clave: aquí SOLO entran
proveedores/modelos que aceptan IMÁGENES de verdad. Un modelo que sirve para
texto NO se asume multimodal: cada entrada declara `supports_vision` y, si el
proveedor responde "este modelo no acepta imágenes", se marca y no se vuelve a
intentar en la sesión.

Flujo:

    captura -> VisionRouter -> proveedor visual 1
                            -> (si falla) proveedor visual 2
                            -> (si falla) proveedor visual 3 ...
                            -> AllVisionProvidersFailed  (el llamador cae a OCR)

Fallos que saltan al siguiente proveedor:
    cuota agotada, HTTP 429, rate limit, timeout, error de conexión,
    modelo inexistente, modelo retirado, proveedor caído, 5xx, API key
    inválida, modelo que no acepta imágenes, respuesta vacía, respuesta
    incompatible.

NO hay bucles infinitos: se recorre la fila UNA vez y hay un tope duro de
intentos (`max_attempts`).

Comparte CONCEPTO (no lista) con AIRouter: mismas clases de error, mismos
enfriamientos, mismos logs. Las listas de modelos son independientes a propósito.

ADITIVO: no reemplaza `core/ai_engine.look()`; se enchufa detrás.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


# ---------------------------------------------------------------- errores
class VisionProviderError(RuntimeError):
    """Fallo de un proveedor visual, con su tipo para decidir el enfriamiento."""

    def __init__(self, message: str, kind: str = "error", status: int = 0):
        super().__init__(message)
        # kind: quota | auth | model_gone | no_vision | network | timeout |
        #       server | empty | bad_response | error
        self.kind = kind
        self.status = status


class AllVisionProvidersFailed(RuntimeError):
    """Ningún proveedor visual pudo responder. El llamador debe caer a OCR."""

    def __init__(self, errores: dict):
        self.errores = dict(errores)
        detalle = "; ".join(f"{k}: {v}" for k, v in errores.items()) or "sin proveedores visuales"
        super().__init__(f"Todos los modelos visuales fallaron ({detalle})")


# Enfriamientos por tipo de fallo (segundos). `no_vision` dura toda la sesión a
# efectos prácticos: un modelo que no acepta imágenes no va a empezar a hacerlo.
_COOLDOWN = {
    "quota": 300.0,        # 5 min  - sin cupo / límite por minuto
    "auth": 1800.0,        # 30 min - clave inválida o sin permiso
    "model_gone": 900.0,   # 15 min - modelo retirado o inexistente
    "no_vision": 86400.0,  # 24 h   - el modelo NO acepta imágenes
    "network": 20.0,       # 20 s   - caída pasajera de red
    "timeout": 45.0,       # 45 s   - se pasó de tiempo
    "server": 120.0,       # 2 min  - 5xx del proveedor
    "empty": 60.0,         # 1 min  - respondió vacío
    "bad_response": 60.0,  # 1 min  - respuesta incompatible
    "error": 30.0,
}


@dataclass
class VisionProvider:
    """Un endpoint compatible-OpenAI que acepta `image_url` en el contenido."""

    name: str
    base_url: str
    api_key: str
    model: str
    # Marca EXPLÍCITA: no se asume visión por el hecho de servir texto.
    supports_vision: bool = True
    enabled: bool = True
    # Estado de salud (lo maneja el router).
    cooldown_until: float = 0.0
    last_error: str = ""
    last_kind: str = ""
    fails: int = 0
    ok: int = 0

    def usable(self, now: float) -> bool:
        return (
            self.enabled
            and self.supports_vision
            and bool(self.api_key)
            and bool(self.base_url)
            and bool(self.model)
            and now >= self.cooldown_until
        )

    def motivo_no_usable(self, now: float) -> str:
        if not self.supports_vision:
            return "no acepta imágenes"
        if not self.enabled:
            return "desactivado"
        if not self.api_key:
            return "sin clave configurada"
        if not self.base_url or not self.model:
            return "sin URL o modelo"
        if now < self.cooldown_until:
            return f"enfriando {int(self.cooldown_until - now)}s ({self.last_kind or 'fallo'})"
        return ""


class VisionRouter:
    """Prueba la fila de proveedores VISUALES hasta que uno describa la imagen."""

    def __init__(self, providers: list, send_fn=None, cooldowns: dict | None = None,
                 log=None, max_attempts: int = 6):
        self.providers = list(providers)
        self._send = send_fn or _default_send_vision
        self._cooldowns = dict(_COOLDOWN)
        if cooldowns:
            self._cooldowns.update(cooldowns)
        self._log = log or _log_consola
        # Tope duro: aunque la fila creciera, nunca más de N llamadas por petición.
        self.max_attempts = max(1, int(max_attempts))

    # ---- estado ----------------------------------------------------------
    def available(self, now: float | None = None) -> list:
        now = time.time() if now is None else now
        return [p for p in self.providers if p.usable(now)]

    def hay_vision(self) -> bool:
        return bool(self.available())

    def report(self) -> list[dict]:
        now = time.time()
        return [
            {
                "nombre": p.name,
                "modelo": p.model,
                "acepta_imagenes": bool(p.supports_vision),
                "disponible": p.usable(now),
                "tiene_clave": bool(p.api_key),
                "enfriando_seg": max(0.0, round(p.cooldown_until - now, 1)),
                "exitos": p.ok,
                "fallos": p.fails,
                "ultimo_error": p.last_error,
            }
            for p in self.providers
        ]

    def reset_cooldowns(self, incluir_no_vision: bool = False):
        """Vuelve a poner disponibles a todos (p. ej. al recuperar la red).

        Por defecto NO revive a los marcados `no_vision`: esos fallan por diseño.
        """
        for p in self.providers:
            if p.last_kind == "no_vision" and not incluir_no_vision:
                continue
            p.cooldown_until = 0.0

    # ---- llamada con relevo ---------------------------------------------
    def describe(self, image_b64: str, instruction: str, system: str = "",
                 timeout: int = 45, temperature: float = 0.2,
                 max_tokens: int = 420, extra_images: list | None = None) -> dict:
        """Manda la imagen a la fila visual hasta que una responda.

        Devuelve {'text','provider','model','attempts','elapsed'}.
        Si todos fallan, lanza AllVisionProvidersFailed (el llamador cae a OCR).
        """
        if not image_b64:
            raise VisionProviderError("no hay imagen que analizar", kind="bad_response")

        errores: dict[str, str] = {}
        intentos = 0
        inicio = time.time()
        now = inicio

        for p in self.providers:
            if intentos >= self.max_attempts:
                errores.setdefault("_tope", f"tope de {self.max_attempts} intentos alcanzado")
                break
            if not p.usable(now):
                errores[p.name] = p.motivo_no_usable(now)
                continue

            intentos += 1
            self._log(f"Intentando {p.name} / {p.model}")
            try:
                texto = self._send(p, image_b64, instruction, system, timeout,
                                   temperature, max_tokens, extra_images or [])
                if not texto or not str(texto).strip():
                    raise VisionProviderError("respuesta vacía", kind="empty")
                p.ok += 1
                p.last_error = ""
                p.last_kind = ""
                elapsed = round(time.time() - inicio, 2)
                self._log(f"Vision success · {p.name}/{p.model} · {elapsed}s")
                return {
                    "text": str(texto).strip(),
                    "provider": p.name,
                    "model": p.model,
                    "attempts": intentos,
                    "elapsed": elapsed,
                }
            except VisionProviderError as e:
                self._penalizar(p, e.kind, str(e), now)
                errores[p.name] = p.last_error
                self._log(f"Provider failed: {e.kind} ({p.name})")
            except Exception as e:  # nunca dejamos escapar un fallo inesperado
                self._penalizar(p, "error", str(e), now)
                errores[p.name] = p.last_error
                self._log(f"Provider failed: error ({p.name})")

        raise AllVisionProvidersFailed(errores)

    def _penalizar(self, p, kind: str, mensaje: str, now: float):
        p.fails += 1
        p.last_kind = kind
        p.last_error = f"{kind}: {mensaje[:200]}"
        p.cooldown_until = now + self._cooldowns.get(kind, 30.0)
        if kind == "no_vision":
            # Marca permanente: este modelo no sirve para imágenes en esta sesión.
            p.supports_vision = False


def _log_consola(msg: str):
    print(f"[VISION] {msg}")


# ------------------------------------------------- catálogo SOLO multimodal
# Cada entrada es (nombre, base_url, variable_de_clave, modelo por defecto).
# Todos los modelos listados aceptan `image_url`. Si alguno dejara de hacerlo,
# el router lo marca `no_vision` en el primer fallo y no vuelve a usarlo.
_CATALOGO_VISION = [
    # Gemini vía su capa compatible-OpenAI: cupo gratis generoso y multimodal.
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
     "GEMINI_API_KEY", "gemini-2.0-flash"),
    # OpenRouter: varias opciones gratuitas con visión.
    ("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
     "meta-llama/llama-3.2-11b-vision-instruct:free"),
    # Groq: Llama-4 Scout es multimodal (si la cuenta lo tiene).
    ("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
     "meta-llama/llama-4-scout-17b-16e-instruct"),
    # Together: mismo Scout, otra cuenta.
    ("together", "https://api.together.xyz/v1", "TOGETHER_API_KEY",
     "meta-llama/Llama-4-Scout-17B-16E-Instruct"),
    # OpenAI (de pago, pero si hay clave entra a la fila sola).
    ("openai", "https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
]

# Proveedores de TEXTO que NO deben entrar nunca a la fila visual aunque tengan
# clave: sus modelos por defecto no aceptan imágenes.
_SOLO_TEXTO = ("cerebras",)


def default_vision_providers(getenv=None) -> list:
    """Arma la fila visual leyendo claves del entorno.

    Reglas:
      * Solo entran los que tengan clave (los demás se listan deshabilitados
        para que salgan en el diagnóstico).
      * `{NOMBRE}_VISION_MODEL` en el .env fuerza el modelo de ese proveedor.
      * `VISION_PROVIDER` distinto de auto/none deja SOLO ese proveedor delante.
      * `VISION_BASE_URL` + `VISION_API_KEY` + `VISION_MODEL` añaden un
        proveedor "custom" en primer lugar (para endpoints propios).
    """
    if getenv is None:
        import os
        # Importar `config` garantiza que el .env ya esta cargado (load_dotenv).
        # Sin esto, construir el router antes que config dejaria la fila sin
        # claves y YUE parecia no tener ningun proveedor visual.
        try:
            import config  # noqa: F401
        except Exception:
            pass
        getenv = os.getenv

    def _val(nombre, defecto=""):
        return (getenv(nombre, defecto) or "").strip()

    fila: list = []

    # 1) Proveedor a medida (tiene prioridad: lo puso el usuario a mano).
    c_base, c_key, c_model = _val("VISION_BASE_URL"), _val("VISION_API_KEY"), _val("VISION_MODEL")
    if c_base and c_key and c_model:
        fila.append(VisionProvider(name="custom", base_url=c_base.rstrip("/"),
                                   api_key=c_key, model=c_model))

    # 2) Catálogo multimodal conocido.
    for nombre, base, var_clave, modelo_def in _CATALOGO_VISION:
        if nombre in _SOLO_TEXTO:
            continue
        clave = _val(var_clave)
        # La clave dedicada de visión sirve para el proveedor elegido a mano.
        modelo = _val(f"{nombre.upper()}_VISION_MODEL") or modelo_def
        fila.append(VisionProvider(
            name=nombre, base_url=base, api_key=clave, model=modelo,
            supports_vision=True, enabled=bool(clave),
        ))

    # 3) Preferencia explícita: VISION_PROVIDER=gemini/openrouter/groq/...
    preferido = _val("VISION_PROVIDER", "auto").lower()
    if preferido and preferido not in ("auto", "none", ""):
        delante = [p for p in fila if p.name == preferido]
        detras = [p for p in fila if p.name != preferido]
        if delante:
            # El elegido va primero, pero los demás SIGUEN de respaldo: es lo que
            # evita que YUE se quede ciega cuando ese proveedor se cae.
            fila = delante + detras
    return fila


def build_default_vision_router(send_fn=None, getenv=None, log=None) -> VisionRouter:
    return VisionRouter(default_vision_providers(getenv=getenv), send_fn=send_fn, log=log)


# ------------------------------------------------------------ envío real
def _clasificar_http(status: int, cuerpo: str) -> tuple[str, str]:
    """(kind, detalle) a partir del código HTTP y el cuerpo del error."""
    bajo = (cuerpo or "").lower()
    pistas_sin_vision = (
        "does not support image", "no support for image", "not support images",
        "image input", "multimodal", "invalid content type", "image_url",
        "unsupported content", "only supports text", "vision is not",
    )
    pistas_modelo = (
        "model_not_found", "does not exist", "decommissioned",
        "no longer supported", "unknown model", "invalid model",
    )
    if status == 429 or "rate limit" in bajo or "quota" in bajo or "too many requests" in bajo:
        return "quota", cuerpo
    if status in (401, 403):
        return "auth", cuerpo
    if status == 404 or any(p in bajo for p in pistas_modelo):
        return "model_gone", cuerpo
    if status == 400 and any(p in bajo for p in pistas_sin_vision):
        return "no_vision", cuerpo
    if any(p in bajo for p in pistas_sin_vision):
        return "no_vision", cuerpo
    if 500 <= status < 600:
        return "server", cuerpo
    return "error", cuerpo


def _default_send_vision(provider: VisionProvider, image_b64: str, instruction: str,
                         system: str, timeout: int, temperature: float,
                         max_tokens: int, extra_images: list) -> str:
    """POST a `{base}/chat/completions` con contenido texto+imagen."""
    try:
        import requests
    except Exception as e:  # pragma: no cover
        raise VisionProviderError(f"falta 'requests': {e}", kind="error")

    content = [{"type": "text", "text": instruction}]
    for extra in (extra_images or []):
        if extra:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{extra}"},
            })
    content.append({
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
    })

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})

    payload = {
        "model": provider.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": int(max_tokens),
    }
    url = f"{provider.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except Exception as e:
        nombre = type(e).__name__.lower()
        kind = "timeout" if "timeout" in nombre else "network"
        raise VisionProviderError(f"{kind}: {e}", kind=kind)

    if resp.status_code == 200:
        try:
            data = resp.json()
        except Exception as e:
            raise VisionProviderError(f"JSON ilegible: {e}", kind="bad_response")
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except Exception:
            raise VisionProviderError("respuesta incompatible (sin choices/message)",
                                      kind="bad_response")

    cuerpo = ""
    try:
        cuerpo = resp.text[:400]
    except Exception:
        pass
    kind, detalle = _clasificar_http(resp.status_code, cuerpo)
    raise VisionProviderError(f"HTTP {resp.status_code}: {detalle[:200]}",
                              kind=kind, status=resp.status_code)

"""Visión de PANTALLA con Google Gemini. Módulo ADITIVO y aislado.

Qué es
------
Los "ojos" de YUE sobre la PANTALLA del PC. Habla directamente con la API nativa
de Gemini (`generativelanguage.googleapis.com`) en vez del atajo compatible con
OpenAI, porque la API nativa es la única que acepta:

  * imágenes en línea (`inline_data`) — capturas de pantalla, fotos, gráficos;
  * PDFs COMPLETOS como documento (`application/pdf`), sin rasterizar ni OCR;
  * varias imágenes en un mismo turno — así se entiende un vídeo con 3-5
    fotogramas en una sola llamada en vez de una llamada por fotograma.

Regla de seguridad (no negociable)
----------------------------------
Este módulo JAMÁS envía imágenes de la cámara web. El único origen permitido es
la pantalla o un archivo local que el usuario pidió analizar. La regla no es una
promesa en un comentario: cada envío pasa por `_verificar_fuente()`, que lanza
excepción si el origen no está en la lista blanca. La cámara (`core/camera_*`,
`vision/`) sigue viviendo aparte y no tiene ninguna vía hacia aquí.

    PANTALLA -> CAPTURA -> GEMINI -> YUE          (permitido)
    CÁMARA   -> GEMINI                            (imposible por diseño)

Nada de esto se activa solo: todas las funciones son bajo demanda. Si no hay
clave, red o modelo, se lanza `GeminiVisionError` con un mensaje en español que
YUE puede decir en voz alta, y el resto de YUE sigue funcionando igual.
"""
from __future__ import annotations

import base64
import io
import os
import time

import requests


# ---------------------------------------------------------------------------
# Lista blanca de orígenes. La cámara NO está aquí y nunca debe estarlo.
# ---------------------------------------------------------------------------
FUENTES_PERMITIDAS = frozenset({
    "pantalla",          # captura de pantalla completa
    "pantalla_video",    # varios fotogramas de la pantalla (vídeo reproduciéndose)
    "archivo_imagen",    # imagen local que el usuario pidió analizar
    "archivo_pdf",       # documento local (PDF u otro) que el usuario pidió analizar
})

# Palabras que delatan un origen de cámara. Si aparecen, se corta en seco.
_FUENTES_VETADAS = (
    "camara", "cámara", "camera", "webcam", "frame_hub", "framehub",
    "camera_service", "camera_observer", "perception", "rostro", "face",
)


class GeminiVisionError(RuntimeError):
    """Fallo de la visión Gemini con motivo clasificado y frase para el usuario.

    `kind` es uno de: clave, cuota, red, modelo, tamano, formato, captura,
    respuesta, config, error.
    `hablado` es lo que YUE dice en voz alta (corto y sin tecnicismos).
    """

    def __init__(self, mensaje: str, kind: str = "error", hablado: str = "",
                 status: int = 0):
        super().__init__(mensaje)
        self.kind = kind
        self.status = status
        self.hablado = hablado or _HABLADO.get(kind, _HABLADO["error"])


_HABLADO = {
    "clave": "Mi vista no arranca: la clave de Gemini no es válida. Revísala en el .env.",
    "cuota": "Gemini me cortó por límite de solicitudes. Dame un momento y lo intento otra vez.",
    "red": "No tengo conexión para mirar tu pantalla ahora mismo.",
    "modelo": "El modelo de visión que tengo configurado no está disponible.",
    "tamano": "Lo que quieres que mire es demasiado grande para enviarlo entero.",
    "formato": "Ese formato de archivo no lo puedo leer con mis ojos.",
    "captura": "No pude capturar tu pantalla. Puede faltar un permiso o una librería.",
    "respuesta": "Gemini me contestó algo que no entendí. ¿Lo intento de nuevo?",
    "config": "No tengo configurada la visión de pantalla con Gemini.",
    "error": "Ahora mismo no pude mirar tu pantalla.",
}


# ---------------------------------------------------------------------------
# Configuración (SIEMPRE desde el entorno / .env — nunca escrita en el código)
# ---------------------------------------------------------------------------
_BASE_POR_DEFECTO = "https://generativelanguage.googleapis.com/v1beta"
_MODELO_POR_DEFECTO = "gemini-2.0-flash"


def _cfg(nombre: str, defecto=None):
    """Lee del config.py de YUE y, si no está, del entorno. Nunca revienta."""
    try:
        import config as _config
        valor = getattr(_config, nombre, None)
        if valor not in (None, ""):
            return valor
    except Exception:
        pass
    valor = os.getenv(nombre, "")
    valor = valor.strip() if isinstance(valor, str) else valor
    return valor if valor not in (None, "") else defecto


def api_key() -> str:
    """Clave de Gemini. Orden: VISION_API_KEY -> GEMINI_API_KEY -> GOOGLE_API_KEY.

    NUNCA hay una clave escrita en el código: si las tres están vacías, la visión
    simplemente no está disponible y se avisa.
    """
    for nombre in ("VISION_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        clave = _cfg(nombre, "")
        if clave:
            return str(clave).strip()
    return ""


def base_url() -> str:
    base = str(_cfg("VISION_BASE_URL", "") or "").strip().rstrip("/")
    if not base:
        return _BASE_POR_DEFECTO
    # Si el usuario pegó la URL del atajo compatible con OpenAI, la corregimos:
    # ese endpoint no acepta PDFs y aquí necesitamos la API nativa.
    if base.endswith("/openai"):
        base = base[: -len("/openai")]
    return base


def modelo() -> str:
    m = str(_cfg("VISION_MODEL", "") or "").strip()
    if not m:
        m = str(_cfg("GEMINI_MODEL", "") or "").strip()
    if not m:
        m = _MODELO_POR_DEFECTO
    # Gemini acepta "models/gemini-..." y "gemini-..."; normalizamos al corto.
    return m[len("models/"):] if m.startswith("models/") else m


def activo() -> bool:
    """¿El .env pide que la visión de pantalla sea de Google?"""
    prov = str(_cfg("VISION_PROVIDER", "none") or "none").strip().lower()
    return prov in ("google", "gemini", "google-gemini")


def disponible() -> bool:
    """¿Está listo para usarse ahora mismo? (proveedor + clave)."""
    return bool(activo() and api_key())


def politica_camara() -> str:
    """Garantía legible del flujo. Se usa en el diagnóstico y en /vision_pantalla."""
    return ("Flujo permitido: PANTALLA -> CAPTURA -> GEMINI -> YUE. "
            "La cámara web NO tiene ninguna vía hacia Gemini: todo envío pasa "
            "por una lista blanca de orígenes que no la incluye.")


# ---------------------------------------------------------------------------
# Verificación de origen: el candado real contra "cámara -> Gemini"
# ---------------------------------------------------------------------------
def _verificar_fuente(origen: str) -> str:
    origen = (origen or "").strip().lower()
    if not origen:
        raise GeminiVisionError(
            "Envío a Gemini sin declarar el origen de la imagen (bloqueado).",
            kind="config",
            hablado="No envié nada: no estaba claro de dónde salía esa imagen.",
        )
    for veto in _FUENTES_VETADAS:
        if veto in origen:
            raise GeminiVisionError(
                f"BLOQUEADO: se intentó enviar a Gemini una imagen de origen «{origen}». "
                "La cámara web nunca sale de este equipo.",
                kind="config",
                hablado="Eso viene de la cámara y la cámara no sale de aquí. No lo envié.",
            )
    if origen not in FUENTES_PERMITIDAS:
        raise GeminiVisionError(
            f"BLOQUEADO: origen «{origen}» no está en la lista blanca "
            f"({', '.join(sorted(FUENTES_PERMITIDAS))}).",
            kind="config",
            hablado="No envié esa imagen: su origen no está permitido.",
        )
    return origen


# ---------------------------------------------------------------------------
# Llamada HTTP a Gemini
# ---------------------------------------------------------------------------
def _headers() -> dict:
    return {"x-goog-api-key": api_key(), "Content-Type": "application/json"}


def _clasificar_http(status: int, cuerpo: str) -> GeminiVisionError:
    """Traduce un error HTTP de Gemini a un GeminiVisionError con motivo."""
    texto = (cuerpo or "").lower()
    if status in (401, 403) or "api key not valid" in texto or "api_key_invalid" in texto:
        return GeminiVisionError(
            f"Gemini rechazó la clave (HTTP {status}): {cuerpo[:250]}",
            kind="clave", status=status)
    if status == 429 or "quota" in texto or "rate limit" in texto or "resource_exhausted" in texto:
        return GeminiVisionError(
            f"Gemini sin cupo o con límite de solicitudes (HTTP {status}): {cuerpo[:250]}",
            kind="cuota", status=status)
    if status == 404 or "is not found" in texto or "not supported" in texto:
        return GeminiVisionError(
            f"El modelo «{modelo()}» no existe o no acepta este contenido "
            f"(HTTP {status}): {cuerpo[:250]}",
            kind="modelo", status=status)
    if status == 413 or "request payload size" in texto or "too large" in texto:
        return GeminiVisionError(
            f"El contenido enviado es demasiado grande (HTTP {status}): {cuerpo[:250]}",
            kind="tamano", status=status)
    if "unsupported mime" in texto or "mime type" in texto:
        return GeminiVisionError(
            f"Gemini no admite ese tipo de archivo (HTTP {status}): {cuerpo[:250]}",
            kind="formato", status=status)
    return GeminiVisionError(
        f"Gemini respondió HTTP {status}: {cuerpo[:250]}", kind="error", status=status)


def _post(partes: list, system: str = "", timeout: int = 90,
          temperatura: float = 0.4, max_tokens: int = 900) -> str:
    """Manda un turno a Gemini y devuelve el texto. Lanza GeminiVisionError."""
    clave = api_key()
    if not clave:
        raise GeminiVisionError(
            "Falta la clave de Gemini. Define VISION_API_KEY (o GEMINI_API_KEY) "
            "en el .env; nunca la escribas en el código.",
            kind="config",
            hablado="No tengo clave de Gemini configurada, así que no puedo mirar la pantalla.")

    url = f"{base_url()}/models/{modelo()}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": partes}],
        "generationConfig": {
            "temperature": float(temperatura),
            "maxOutputTokens": int(max_tokens),
        },
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=timeout)
    except requests.Timeout as exc:
        raise GeminiVisionError(f"Gemini tardó demasiado: {exc}", kind="red",
                                hablado="Gemini tardó demasiado en contestarme.") from exc
    except requests.RequestException as exc:
        raise GeminiVisionError(f"Sin conexión con Gemini: {exc}", kind="red") from exc

    if resp.status_code >= 400:
        cuerpo = ""
        try:
            cuerpo = resp.text
        except Exception:
            pass
        raise _clasificar_http(resp.status_code, cuerpo)

    try:
        data = resp.json()
    except Exception as exc:
        raise GeminiVisionError(f"Respuesta ilegible de Gemini: {exc}",
                                kind="respuesta") from exc
    return _texto_de_respuesta(data)


def _texto_de_respuesta(data: dict) -> str:
    """Junta el texto de `candidates[0].content.parts[*].text`.

    Contempla el caso real más molesto: que Gemini devuelva 200 pero sin texto
    porque cortó por filtros de seguridad o por límite de tokens.
    """
    try:
        candidatos = data.get("candidates") or []
        if not candidatos:
            motivo = ((data.get("promptFeedback") or {}).get("blockReason") or "")
            raise GeminiVisionError(
                f"Gemini no devolvió ningún resultado (motivo: {motivo or 'desconocido'}).",
                kind="respuesta",
                hablado="Gemini no quiso analizar esa imagen.")
        cand = candidatos[0]
        partes = ((cand.get("content") or {}).get("parts") or [])
        texto = "".join(str(p.get("text", "")) for p in partes).strip()
        if not texto:
            razon = str(cand.get("finishReason", "") or "")
            if razon.upper() == "MAX_TOKENS":
                raise GeminiVisionError(
                    "Gemini cortó la respuesta por longitud.", kind="respuesta",
                    hablado="Me quedé sin espacio para responder; pídeme algo más concreto.")
            raise GeminiVisionError(
                f"Gemini devolvió una respuesta vacía (finishReason={razon or '?'}).",
                kind="respuesta")
        return texto
    except GeminiVisionError:
        raise
    except Exception as exc:
        raise GeminiVisionError(f"No pude leer la respuesta de Gemini: {exc}",
                                kind="respuesta") from exc


# ---------------------------------------------------------------------------
# Construcción de partes
# ---------------------------------------------------------------------------
def _parte_imagen(b64: str, mime: str = "image/jpeg") -> dict:
    return {"inline_data": {"mime_type": mime, "data": b64}}


def _tamano_b64_mb(b64: str) -> float:
    return (len(b64) * 3 / 4) / (1024 * 1024)


# Gemini acepta ~20 MB por petición contando TODO. Dejamos margen.
_LIMITE_INLINE_MB = 15.0


# ---------------------------------------------------------------------------
# API pública: pantalla
# ---------------------------------------------------------------------------
def analizar_pantalla(instruccion: str, system: str = "", max_width: int = 1600,
                      quality: int = 78, timeout: int = 90) -> str:
    """Captura la pantalla AHORA y se la manda a Gemini con la pregunta.

    Este es el camino normal de "YUE, mira mi pantalla". Una sola captura, una
    sola llamada, solo cuando el usuario lo pide.
    """
    b64 = capturar_pantalla_b64(max_width=max_width, quality=quality)
    return analizar_imagen_b64(b64, instruccion, system=system,
                               origen="pantalla", timeout=timeout)


def capturar_pantalla_b64(max_width: int = 1600, quality: int = 78) -> str:
    """Captura de pantalla en JPEG base64, reutilizando el módulo de siempre."""
    try:
        from core import screen_capture
        return screen_capture.capture_b64(max_width=max_width, quality=quality)
    except GeminiVisionError:
        raise
    except Exception as exc:
        raise GeminiVisionError(f"No pude capturar la pantalla: {exc}",
                                kind="captura") from exc


def analizar_imagen_b64(b64: str, instruccion: str, system: str = "",
                        origen: str = "pantalla", mime: str = "image/jpeg",
                        timeout: int = 90) -> str:
    """Manda UNA imagen ya codificada. `origen` debe estar en la lista blanca."""
    _verificar_fuente(origen)
    if not b64:
        raise GeminiVisionError("Imagen vacía.", kind="captura")
    if _tamano_b64_mb(b64) > _LIMITE_INLINE_MB:
        raise GeminiVisionError(
            f"La imagen pesa {_tamano_b64_mb(b64):.1f} MB y supera el límite de "
            f"{_LIMITE_INLINE_MB:.0f} MB por petición. Baja VISION_SCREEN_MAX_WIDTH "
            "o VISION_SCREEN_QUALITY en el .env.",
            kind="tamano")
    partes = [{"text": instruccion}, _parte_imagen(b64, mime)]
    return _post(partes, system=system, timeout=timeout)


def analizar_imagen_archivo(ruta: str, instruccion: str, system: str = "",
                            timeout: int = 90) -> str:
    """Analiza una imagen de disco (la que el usuario pidió, no de cámara)."""
    _verificar_fuente("archivo_imagen")
    mime = _mime_de_ruta(ruta)
    if not mime.startswith("image/"):
        raise GeminiVisionError(f"«{ruta}» no parece una imagen ({mime}).",
                                kind="formato")
    try:
        with open(ruta, "rb") as f:
            crudo = f.read()
    except Exception as exc:
        raise GeminiVisionError(f"No pude abrir «{ruta}»: {exc}", kind="formato") from exc
    b64 = base64.b64encode(_reducir_si_hace_falta(crudo, mime)).decode("ascii")
    return analizar_imagen_b64(b64, instruccion, system=system,
                               origen="archivo_imagen", mime=mime, timeout=timeout)


def _reducir_si_hace_falta(crudo: bytes, mime: str) -> bytes:
    """Si la imagen pesa demasiado, la reescala a JPEG. Si no puede, la deja."""
    if len(crudo) <= _LIMITE_INLINE_MB * 1024 * 1024:
        return crudo
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(crudo)).convert("RGB")
        w, h = img.size
        if w > 1600:
            img = img.resize((1600, int(h * 1600 / w)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75)
        return buf.getvalue()
    except Exception:
        return crudo


def _mime_de_ruta(ruta: str) -> str:
    ext = os.path.splitext(str(ruta))[1].lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
        ".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/plain",
    }.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------
# API pública: vídeo en pantalla (muestreo de fotogramas, NO continuo)
# ---------------------------------------------------------------------------
def analizar_video_pantalla(instruccion: str, system: str = "", fotogramas: int = 4,
                            intervalo: float = 1.5, max_width: int = 1100,
                            quality: int = 70, timeout: int = 120) -> str:
    """Toma N capturas espaciadas y las manda JUNTAS en una sola llamada.

    Así YUE entiende movimiento sin analizar cada fotograma: 4 capturas cada
    1,5 s cubren 6 segundos de vídeo con UNA petición a la API, no con 150.
    """
    fotogramas = max(2, min(int(fotogramas), 8))
    intervalo = max(0.3, min(float(intervalo), 5.0))
    _verificar_fuente("pantalla_video")

    partes: list = [{"text": instruccion}]
    capturadas = 0
    for i in range(fotogramas):
        if i:
            time.sleep(intervalo)
        try:
            b64 = capturar_pantalla_b64(max_width=max_width, quality=quality)
        except GeminiVisionError:
            if capturadas == 0:
                raise
            break
        partes.append({"text": f"[fotograma {i + 1} · t={i * intervalo:.1f}s]"})
        partes.append(_parte_imagen(b64))
        capturadas += 1

    if capturadas < 2:
        raise GeminiVisionError(
            "Solo conseguí un fotograma; no puedo juzgar el movimiento.",
            kind="captura",
            hablado="Solo alcancé a ver un instante del vídeo. ¿Lo intento otra vez?")

    total_mb = sum(_tamano_b64_mb(p["inline_data"]["data"])
                   for p in partes if "inline_data" in p)
    if total_mb > _LIMITE_INLINE_MB:
        raise GeminiVisionError(
            f"Los {capturadas} fotogramas suman {total_mb:.1f} MB. Baja "
            "VISION_VIDEO_FRAMES o VISION_VIDEO_MAX_WIDTH en el .env.",
            kind="tamano")

    partes.insert(1, {"text": (
        f"Te doy {capturadas} fotogramas consecutivos de la pantalla, separados "
        f"{intervalo:.1f} s. Compáralos entre sí para deducir QUÉ OCURRE (qué se "
        "mueve, qué hace la persona, qué cambia). No los describas uno por uno.")})
    return _post(partes, system=system, timeout=timeout, max_tokens=900)


# ---------------------------------------------------------------------------
# API pública: PDFs y documentos
# ---------------------------------------------------------------------------
def analizar_pdf(ruta: str, instruccion: str, system: str = "",
                 timeout: int = 180) -> str:
    """Manda el PDF ENTERO a Gemini (camino más eficiente y fiel).

    Gemini lee el PDF nativo: texto, tablas, gráficos y páginas escaneadas, sin
    OCR previo ni rasterizado. Si el archivo pesa demasiado para ir en línea, se
    sube con la File API; si eso tampoco funciona, quien llama debe caer al
    camino de texto/OCR que YUE ya tiene (`teacher.documents`).
    """
    _verificar_fuente("archivo_pdf")
    if not os.path.isfile(ruta):
        raise GeminiVisionError(f"No encuentro el archivo «{ruta}».", kind="formato",
                                hablado="No encuentro ese documento en el disco.")
    mime = _mime_de_ruta(ruta)
    if mime not in ("application/pdf", "text/plain"):
        raise GeminiVisionError(
            f"«{os.path.basename(ruta)}» no es un PDF ({mime}). Para Word, Excel o "
            "PowerPoint, YUE los lee con su lector de documentos.",
            kind="formato")

    peso_mb = os.path.getsize(ruta) / (1024 * 1024)
    if peso_mb <= _LIMITE_INLINE_MB:
        with open(ruta, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        partes = [{"text": instruccion},
                  {"inline_data": {"mime_type": mime, "data": b64}}]
        return _post(partes, system=system, timeout=timeout, max_tokens=1200)

    # PDF grande: File API (subida reanudable) y referencia por URI.
    uri = subir_archivo(ruta, mime, timeout=timeout)
    partes = [{"text": instruccion},
              {"file_data": {"mime_type": mime, "file_uri": uri}}]
    return _post(partes, system=system, timeout=timeout, max_tokens=1200)


def subir_archivo(ruta: str, mime: str, timeout: int = 180) -> str:
    """Sube un archivo con la File API de Gemini y devuelve su `file_uri`.

    Protocolo reanudable en dos pasos (start + upload/finalize) y espera a que
    el archivo pase de PROCESSING a ACTIVE antes de poder usarse.
    """
    clave = api_key()
    if not clave:
        raise GeminiVisionError("Falta la clave de Gemini para subir el archivo.",
                                kind="config")
    tam = os.path.getsize(ruta)
    inicio_url = f"{base_url().replace('/v1beta', '')}/upload/v1beta/files"
    try:
        r1 = requests.post(
            inicio_url,
            headers={
                "x-goog-api-key": clave,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(tam),
                "X-Goog-Upload-Header-Content-Type": mime,
                "Content-Type": "application/json",
            },
            json={"file": {"display_name": os.path.basename(ruta)}},
            timeout=60,
        )
    except requests.RequestException as exc:
        raise GeminiVisionError(f"No pude iniciar la subida: {exc}", kind="red") from exc
    if r1.status_code >= 400:
        raise _clasificar_http(r1.status_code, r1.text)
    destino = r1.headers.get("X-Goog-Upload-URL") or r1.headers.get("x-goog-upload-url")
    if not destino:
        raise GeminiVisionError("Gemini no devolvió la URL de subida.", kind="respuesta")

    try:
        with open(ruta, "rb") as f:
            r2 = requests.post(
                destino,
                headers={
                    "Content-Length": str(tam),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                },
                data=f, timeout=timeout,
            )
    except requests.RequestException as exc:
        raise GeminiVisionError(f"Falló la subida del archivo: {exc}", kind="red") from exc
    if r2.status_code >= 400:
        raise _clasificar_http(r2.status_code, r2.text)

    try:
        info = (r2.json() or {}).get("file") or {}
    except Exception as exc:
        raise GeminiVisionError(f"Respuesta ilegible al subir: {exc}",
                                kind="respuesta") from exc
    uri, nombre, estado = info.get("uri", ""), info.get("name", ""), info.get("state", "")
    if not uri:
        raise GeminiVisionError("Gemini no devolvió el URI del archivo subido.",
                                kind="respuesta")

    # Esperamos a que termine de procesarse (los PDFs grandes tardan unos segundos).
    limite = time.time() + 90
    while estado.upper() == "PROCESSING" and time.time() < limite:
        time.sleep(2)
        try:
            r3 = requests.get(f"{base_url()}/{nombre}", headers=_headers(), timeout=30)
            estado = str((r3.json() or {}).get("state", ""))
        except Exception:
            break
    if estado.upper() == "FAILED":
        raise GeminiVisionError("Gemini no pudo procesar ese documento.", kind="formato")
    return uri


# ---------------------------------------------------------------------------
# Diagnóstico (no gasta tokens)
# ---------------------------------------------------------------------------
def preflight(timeout: int = 15) -> dict:
    """Valida proveedor, clave y modelo con un GET /models. Nunca lanza."""
    if not activo():
        return {"ok": False, "reason": "disabled", "provider": str(_cfg("VISION_PROVIDER", "none")),
                "detail": "VISION_PROVIDER no es 'google'"}
    if not api_key():
        return {"ok": False, "reason": "no_key", "provider": "google",
                "detail": "falta VISION_API_KEY / GEMINI_API_KEY en el .env"}
    try:
        resp = requests.get(f"{base_url()}/models", headers=_headers(), timeout=timeout)
    except requests.RequestException as exc:
        return {"ok": False, "reason": "unreachable", "provider": "google",
                "detail": str(exc)[:200]}
    if resp.status_code in (400, 401, 403):
        return {"ok": False, "reason": "invalid_key", "provider": "google",
                "status": resp.status_code, "detail": resp.text[:200]}
    if resp.status_code >= 400:
        return {"ok": False, "reason": "http", "provider": "google",
                "status": resp.status_code, "detail": resp.text[:200]}
    ids = []
    try:
        ids = [str(m.get("name", "")).replace("models/", "")
               for m in (resp.json() or {}).get("models", [])]
    except Exception:
        pass
    quiero = modelo()
    if ids and quiero not in ids:
        return {"ok": False, "reason": "model_missing", "provider": "google",
                "model": quiero,
                "detail": f"«{quiero}» no está en tu cuenta. Disponibles: "
                          + ", ".join(ids[:8])}
    return {"ok": True, "provider": "google", "model": quiero,
            "host": "generativelanguage.googleapis.com", "modelos": len(ids)}

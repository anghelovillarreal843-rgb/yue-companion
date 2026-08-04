"""Generacion de imagenes con FLUX.

Yue convierte un texto creativo en una imagen. Soporta varios
proveedores (se elige con FLUX_PROVIDER en el .env):

  together  -> Together AI. Tiene un FLUX.1-schnell GRATIS. (recomendado)
  bfl       -> API oficial de Black Forest Labs (de pago, patron asincrono).
  openai    -> Cualquier endpoint compatible con OpenAI Images.
  local     -> diffusers + GPU en tu PC (pesado; requiere torch/diffusers).

Funcion principal:  generate(prompt, out_path) -> ruta de la imagen PNG.
"""
import base64
import time

import requests

import config
from core import activity


def generate(prompt, out_path, width=None, height=None):
    """Genera una imagen a partir de un prompt y la guarda en out_path (PNG).

    Devuelve la ruta (str). Lanza excepcion si falla (el llamador la maneja)."""
    width = width or config.IMAGE_WIDTH
    height = height or config.IMAGE_HEIGHT
    # FLUX exige multiplos de 16
    width = max(256, (width // 16) * 16)
    height = max(256, (height // 16) * 16)

    provider = config.FLUX_PROVIDER
    if provider == "together":
        data = _together(prompt, width, height)
    elif provider == "bfl":
        data = _bfl(prompt, width, height)
    elif provider == "openai":
        data = _openai(prompt, width, height)
    elif provider == "local":
        data = _local(prompt, width, height)
    else:
        raise RuntimeError(f"FLUX_PROVIDER desconocido: {provider}")

    with open(out_path, "wb") as f:
        f.write(data)
    # NUEVO (bitácora): registramos la creación con una descripción corta (el
    # propio prompt recortado), nunca el archivo.
    activity.log("imagen", f"generó una imagen de: {str(prompt or '').strip()[:80]}", origen="image_gen")
    return str(out_path)


# ---------------- Together AI (FLUX.1-schnell gratis) ----------------
def _together(prompt, width, height):
    if not config.TOGETHER_API_KEY:
        raise RuntimeError(
            "Falta TOGETHER_API_KEY en el .env. Consíguela gratis en "
            "https://api.together.ai/settings/api-keys"
        )
    url = f"{config.TOGETHER_BASE_URL}/images/generations"
    payload = {
        "model": config.TOGETHER_MODEL,
        "prompt": prompt,
        "width": width,
        "height": height,
        "steps": config.FLUX_STEPS,
        "n": 1,
        "response_format": "b64_json",
    }
    r = requests.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {config.TOGETHER_API_KEY}",
                 "Content-Type": "application/json"},
        timeout=120,
    )
    r.raise_for_status()
    item = r.json()["data"][0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    # algunos modelos devuelven url
    return requests.get(item["url"], timeout=120).content


# ---------------- Black Forest Labs (oficial, asincrono) ----------------
def _bfl(prompt, width, height):
    if not config.BFL_API_KEY:
        raise RuntimeError("Falta BFL_API_KEY en el .env (https://dashboard.bfl.ai)")
    headers = {"x-key": config.BFL_API_KEY, "Content-Type": "application/json"}
    submit = requests.post(
        f"{config.BFL_BASE_URL}/v1/{config.BFL_MODEL}",
        json={"prompt": prompt, "width": width, "height": height},
        headers=headers, timeout=60,
    )
    submit.raise_for_status()
    info = submit.json()
    polling_url = info.get("polling_url")
    if not polling_url:
        raise RuntimeError(f"Respuesta inesperada de BFL: {info}")

    deadline = time.time() + 180
    while time.time() < deadline:
        res = requests.get(polling_url, headers=headers, timeout=60).json()
        status = res.get("status")
        if status == "Ready":
            sample = res["result"]["sample"]  # url temporal (expira ~10 min)
            return requests.get(sample, timeout=120).content
        if status in ("Error", "Failed", "Content Moderated", "Request Moderated"):
            raise RuntimeError(f"BFL no pudo generar la imagen: {status}")
        time.sleep(2)
    raise RuntimeError("BFL tardó demasiado en responder.")


# ---------------- Compatible con OpenAI Images ----------------
def _openai(prompt, width, height):
    if not config.OPENAI_IMAGE_KEY:
        raise RuntimeError("Falta OPENAI_IMAGE_KEY en el .env")
    # OpenAI usa tamaños 'WxH' como cadena; aproximamos al cuadrado mas comun
    size = f"{width}x{height}"
    r = requests.post(
        f"{config.OPENAI_IMAGE_BASE_URL}/images/generations",
        json={"model": config.OPENAI_IMAGE_MODEL, "prompt": prompt,
              "size": size, "n": 1},
        headers={"Authorization": f"Bearer {config.OPENAI_IMAGE_KEY}",
                 "Content-Type": "application/json"},
        timeout=120,
    )
    r.raise_for_status()
    item = r.json()["data"][0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    return requests.get(item["url"], timeout=120).content


# ---------------- Local (diffusers, opcional) ----------------
_PIPE = None


def _local(prompt, width, height):
    global _PIPE
    try:
        import torch
        from diffusers import FluxPipeline
    except Exception as e:
        raise RuntimeError(
            "Modo local de FLUX requiere 'torch' y 'diffusers' (y una GPU potente). "
            f"Instálalos o usa FLUX_PROVIDER=together. Detalle: {e}"
        )
    if _PIPE is None:
        dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        _PIPE = FluxPipeline.from_pretrained(config.LOCAL_FLUX_MODEL, torch_dtype=dtype)
        if torch.cuda.is_available():
            _PIPE = _PIPE.to("cuda")
        else:
            _PIPE.enable_model_cpu_offload()
    img = _PIPE(prompt, width=width, height=height,
                num_inference_steps=config.FLUX_STEPS,
                guidance_scale=0.0).images[0]
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

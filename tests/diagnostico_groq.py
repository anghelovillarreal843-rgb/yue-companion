"""Diagnóstico de Groq: ¿qué modelos existen HOY en tu cuenta y cuáles sirven?

Uso:
    python tests/diagnostico_groq.py

Responde a la pregunta exacta que causó el «404 Client Error: Not Found»:
qué modelo del .env ya no existe y cuál poner en su lugar.
"""
import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core import ai_fallback


def _imagen_prueba_b64() -> str:
    """Una imagen mínima con un cuadro rojo, para ver si el modelo mira."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return ""
    img = Image.new("RGB", (200, 120), "white")
    ImageDraw.Draw(img).rectangle([40, 30, 160, 90], fill="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def main():
    base, key = config.GROQ_BASE_URL, config.GROQ_API_KEY
    print(f"base      : {base}")
    print(f"api_key   : {'sí (' + str(len(key)) + ' chars)' if key else 'NO HAY -> revisa el .env'}")
    print(f".env texto : {config.GROQ_MODEL}")
    print(f".env visión: {config.GROQ_VISION_MODEL}")
    if not key:
        return 1

    print("\n── Modelos disponibles en tu cuenta ──")
    modelos = ai_fallback.listar_modelos(base, key, forzar=True)
    if not modelos:
        print("  No pude listar los modelos. ¿La API key es válida? ¿Hay internet?")
        return 1
    for m in modelos:
        print("  ·", m)

    print("\n── Veredicto ──")
    for etiqueta, pedido, rol in (
        ("texto ", config.GROQ_MODEL, "text"),
        ("visión", config.GROQ_VISION_MODEL, "vision"),
    ):
        existe = pedido in modelos
        marca = "OK" if existe else "NO EXISTE  <-- esto causa el 404"
        print(f"  {etiqueta}: {pedido}  ->  {marca}")
        if not existe:
            ai_fallback.olvidar(rol)
            sugerido = ai_fallback.resolve_model(base, key, "", rol)
            print(f"           sugerencia para el .env: {sugerido or '(ninguna: no hay modelo de ese tipo)'}")

    print("\n── Prueba real de chat ──")
    modelo_texto = ai_fallback.resolve_model(
        base, key, config.GROQ_MODEL, "text", tuple(config.GROQ_MODEL_FALLBACKS))
    try:
        data = ai_fallback.post_chat(base, key, {
            "model": modelo_texto,
            "messages": [{"role": "user", "content": "Responde solo: ok"}],
            "max_completion_tokens": 10,
        }, timeout=30)
        print(f"  {modelo_texto} -> {data['choices'][0]['message']['content'].strip()!r}")
    except Exception as exc:
        print(f"  {modelo_texto} -> FALLA: {exc}")

    print("\n── Prueba real de visión ──")
    img = _imagen_prueba_b64()
    if not img:
        print("  (sin Pillow, me la salto)")
        return 0

    v_base, v_key, v_model = config.vision_endpoint()
    print(f"  proveedor : {config.VISION_PROVIDER}")
    if not v_base or not v_key:
        print("  No hay proveedor de visión configurado (VISION_PROVIDER=none).")
        return 0
    print(f"  endpoint  : {v_base}")
    print(f"  modelo    : {v_model}")
    if _probar_vision(v_base, v_key, v_model, img):
        print("\n  La visión funciona. No hay nada más que hacer.")
        return 0

    # Si el proveedor de visión es Groq y falló, buscamos empíricamente: en vez
    # de adivinar por el nombre, le mandamos la imagen a cada modelo de texto y
    # vemos cuál la acepta. Es la única forma fiable de saberlo.
    if v_base == config.GROQ_BASE_URL:
        print("\n── ¿Algún modelo de tu cuenta acepta imágenes? ──")
        descartar = ("whisper", "tts", "guard", "orpheus", "embed", "safeguard")
        candidatos = [m for m in modelos if not any(d in m.lower() for d in descartar)]
        aceptan = [m for m in candidatos if _probar_vision(v_base, v_key, m, img)]
        if aceptan:
            print("\n  Pon esto en tu .env:")
            print(f"     GROQ_VISION_MODEL={aceptan[0]}")
        else:
            print("\n  Ninguno acepta imágenes: tu cuenta de Groq NO tiene visión.")
            _sugerir_otro_proveedor()
    return 0


def _probar_vision(base, key, modelo, img) -> bool:
    """Le manda el rectángulo rojo a un modelo y ve si de verdad lo mira."""
    try:
        data = ai_fallback.post_chat(base, key, {
            "model": modelo,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "¿De qué color es el rectángulo? Una palabra."},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{img}"}},
            ]}],
            "max_completion_tokens": 10,
        }, timeout=40)
        texto = (data["choices"][0]["message"]["content"] or "").strip()
        ve = "rojo" in texto.lower() or "red" in texto.lower()
        print(f"  {'VE' if ve else '??'}  {modelo} -> {texto!r}")
        return ve
    except Exception as exc:
        print(f"  no  {modelo} -> {str(exc)[:110]}")
        return False


def _sugerir_otro_proveedor():
    """Con qué claves que YA tienes se puede recuperar la visión."""
    print("\n  Opciones, de menos a más trabajo:")
    print("\n  A) Quedarte sin visión (ya funciona así). Yue planifica con")
    print("     ui_elements de pywinauto + OCR. Para calculadora, Bloc de notas")
    print("     y Paint es suficiente; se nota en webs y ventanas raras.")
    print("        VISION_PROVIDER=none")
    if config.TOGETHER_API_KEY:
        print("\n  B) Together (ya tienes TOGETHER_API_KEY en el .env):")
        print("        VISION_PROVIDER=together")
        print("        VISION_MODEL=meta-llama/Llama-4-Scout-17B-16E-Instruct")
    if config.OPENAI_IMAGE_KEY:
        print("\n  C) OpenAI (ya tienes OPENAI_IMAGE_KEY en el .env):")
        print("        VISION_PROVIDER=openai")
        print("        VISION_MODEL=gpt-4o-mini")
    print("\n  Comprueba el modelo exacto con:  python tests/diagnostico_groq.py")


    return 0


if __name__ == "__main__":
    raise SystemExit(main())

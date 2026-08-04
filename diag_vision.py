"""Diagnóstico de la VISIÓN de YUE (modo OCR y/o multimodal).

Ejecuta:   python diag_vision.py

Comprueba, en orden y sin gastar tokens:
  1) Config de visión leída del .env.
  2) CAPTURA de pantalla (mss / Pillow).
  3) OCR: ¿está Tesseract listo? ¿lee texto de la pantalla ahora mismo?
  4) (Solo si NO usas OCR) validez de la clave del proveedor multimodal.
  5) Veredicto con el siguiente paso.

Es de solo lectura: no modifica ningún archivo.
"""
from __future__ import annotations

import sys

try:
    import config
except Exception as exc:
    print("No pude importar config.py (¿ejecutas esto desde la carpeta yue_companion?):", exc)
    sys.exit(1)


def main() -> None:
    print("=" * 62)
    print("  DIAGNÓSTICO DE LA VISIÓN DE YUE")
    print("=" * 62)

    ocr_only = bool(getattr(config, "VISION_OCR_ONLY", False))
    prov = getattr(config, "VISION_PROVIDER", "groq")
    print("\n1) Config")
    print(f"   VISION_OCR_ONLY = {ocr_only}")
    print(f"   VISION_PROVIDER = {prov}")
    print(f"   OCR_LANG        = {getattr(config, 'OCR_LANG', '(por defecto)')}")
    print(f"   OCR_TESSERACT_CMD = {getattr(config, 'OCR_TESSERACT_CMD', '') or '(auto)'}")

    # 2) Captura de pantalla
    print("\n2) Captura de pantalla (mss / Pillow)")
    try:
        from core import screen_capture as vision
        info = vision.capture_info()
        if info.get("ok"):
            print(f"   ✅ Captura OK: {info['width']}x{info['height']}px, {info['bytes']} bytes")
        else:
            print(f"   ❌ No pude capturar: {info.get('note')}")
            print("      -> Instala las librerías:  pip install mss pillow")
    except Exception as exc:
        print(f"   ❌ Error en la captura: {exc}")

    # 3) OCR
    print("\n3) OCR (lectura de texto de la pantalla)")
    try:
        from core import screen_text
        motor = screen_text.get_engine()
        if not motor.available():
            print(f"   ❌ El motor OCR «{motor.name}» NO está listo (falta Tesseract).")
            print("      Instálalo:  winget install UB-Mannheim.TesseractOCR")
            print("      Si ya está instalado pero no lo encuentra, pon la ruta en el .env:")
            print(r"        OCR_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe")
        else:
            print(f"   ✅ Motor OCR «{motor.name}» listo.")
            # Captura robusta y OCR real
            imagen = None
            try:
                from core import screen_capture as vision
                imagen = vision._grab()
            except Exception:
                imagen = None
            texto = (screen_text.read_screen(image=imagen) or "").strip()
            if texto:
                muestra = texto[:200].replace("\n", " ")
                print(f"   ✅ Leí {len(texto)} caracteres. Muestra: «{muestra}…»")
            else:
                print("   ⚠️ El OCR funciona pero no encontró texto en la pantalla actual.")
                print("      Abre un PDF/documento/página con texto en primer plano y reintenta.")
    except Exception as exc:
        print(f"   ❌ Error en el OCR: {exc}")

    # 4) Clave multimodal (solo si no vas por OCR)
    if not ocr_only and prov != "none":
        print("\n4) Clave del proveedor multimodal")
        try:
            from core import ai_fallback
            base, key, model = config.vision_endpoint()
            if not base or not key:
                print("   ⚠️ Sin URL o clave de visión multimodal configurada.")
            else:
                res = ai_fallback.preflight(base, key)
                if res.get("ok"):
                    print(f"   ✅ La clave funciona en {base}.")
                elif res.get("reason") == "invalid_key":
                    print(f"   ❌ 401/403: el proveedor rechaza la clave. Detalle: {res.get('detail')}")
                    print("      Genera una nueva en https://api.together.ai/settings/api-keys")
                else:
                    print(f"   ⚠️ {res.get('reason')}: {res.get('detail')}")
        except Exception as exc:
            print(f"   ❌ Error validando la clave: {exc}")
    else:
        print("\n4) Clave multimodal: OMITIDA (estás en visión por OCR).")

    print("\n5) Veredicto")
    if ocr_only:
        print("   Estás en visión por OCR. Para que YUE 'mire', necesita: captura OK (paso 2)")
        print("   + Tesseract listo (paso 3). Si ambos están en verde, pídele «mira la")
        print("   pantalla» con un PDF o documento con texto abierto delante.")
    else:
        print("   Estás en visión multimodal. Necesita captura OK (paso 2) + clave válida (paso 4),")
        print("   o pon VISION_OCR_ONLY=true en el .env para usar solo OCR.")


if __name__ == "__main__":
    main()

"""Diagnóstico simple de las credenciales Groq configuradas, sin imprimirlas."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from core import ai_fallback

print("=== YUE / GROQ ===")
print("Modelo:", config.GROQ_MODEL)
print("Claves configuradas:", len(config.GROQ_API_KEYS))
if not config.GROQ_API_KEYS:
    print("ERROR: configura GROQ_API_KEY en .env")
else:
    for i, key in enumerate(config.GROQ_API_KEYS, 1):
        r = ai_fallback.preflight(config.GROQ_BASE_URL, key)
        print(f"groq-{i}:", "OK" if r.get("ok") else r.get("reason", "error"))

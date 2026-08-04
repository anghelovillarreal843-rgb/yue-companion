"""Detector automático de secretos (FASE 1 · Seguridad).

Recorre el proyecto buscando claves API, tokens y contraseñas reales metidas
por error en el código, en .env o hasta en .env.example. NUNCA imprime el valor
completo del secreto: lo enmascara. Devuelve código de salida distinto de cero
si encuentra algo, para poder usarlo como "puerta" antes de crear el instalador.

Uso:
    python -m tools.secret_scanner                # analiza la carpeta del proyecto
    python -m tools.secret_scanner --path ruta    # analiza otra carpeta
    python -m tools.secret_scanner --strict       # también avisa de sospechas leves

ADITIVO: no toca ningún archivo, solo lee y reporta.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

# Carpeta raíz del proyecto (este archivo vive en tools/).
ROOT = Path(__file__).resolve().parent.parent

# Carpetas y archivos que no tiene sentido escanear (binarios, cachés, modelos).
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build",
    "assets", ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
}
_SKIP_SUFFIXES = {
    ".pyc", ".pyo", ".vrm", ".glb", ".fbx", ".onnx", ".bin", ".png", ".jpg",
    ".jpeg", ".gif", ".webp", ".mp3", ".wav", ".ogg", ".mp4", ".zip", ".db",
    ".sqlite", ".sqlite3", ".xlsx", ".pptx", ".docx", ".pdf", ".task",
    ".xml",  # los haarcascades son enormes y no contienen secretos
}
_MAX_BYTES = 2_000_000  # no leer archivos gigantes

# --- Patrones de secretos concretos (alta confianza) -----------------------
# Cada entrada: (nombre legible, regex). El grupo 0 es lo que se enmascara.
_HIGH_CONFIDENCE = [
    ("Groq API key", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    ("OpenAI API key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("Clave privada (PEM)", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
]

# --- Heurística genérica: VAR_SECRETA = valor_largo_y_aleatorio -------------
_ASSIGN = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|BEARER|ACCESS[_-]?KEY)[A-Z0-9_]*)"
    r"\s*[:=]\s*['\"]?([^\s'\"#]{16,})"
)

# Valores que son claramente marcadores de posición (no secretos reales).
_PLACEHOLDER = re.compile(
    r"(?i)^(?:tu[_-]?|your[_-]?|pon[_-]?|poner|change[_-]?me|placeholder|example|"
    r"xxx+|<.*>|\.\.\.|none|null|todo|aqui|here|dummy|test|fake|sample)"
)


def _entropy(s: str) -> float:
    """Entropía de Shannon (bits por carácter). Los secretos reales rondan >3.5."""
    if not s:
        return 0.0
    freq = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def _is_placeholder(value: str) -> bool:
    v = value.strip().strip("'\"")
    if not v:
        return True
    if _PLACEHOLDER.match(v):
        return True
    # Todo el mismo carácter (aaaa, 0000) o muy repetitivo.
    if len(set(v)) <= 2:
        return True
    return False


def mask(secret: str) -> str:
    """Enmascara un secreto dejando pistas mínimas: 4 primeros + 2 últimos."""
    s = secret.strip().strip("'\"")
    if len(s) <= 8:
        return s[0] + "*" * (len(s) - 1) if s else ""
    return f"{s[:4]}{'*' * 8}{s[-2:]}"


def _iter_text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        try:
            if path.stat().st_size > _MAX_BYTES:
                continue
        except OSError:
            continue
        yield path


def scan_text(text: str, strict: bool = False):
    """Devuelve lista de hallazgos: (linea, tipo, valor_enmascarado, confianza)."""
    hits = []
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        # 1) Patrones concretos (alta confianza).
        for name, rx in _HIGH_CONFIDENCE:
            for m in rx.finditer(line):
                hits.append((i, name, mask(m.group(0)), "ALTA"))
        # 2) Heurística VAR=valor.
        for m in _ASSIGN.finditer(line):
            var, value = m.group(1), m.group(2)
            if _is_placeholder(value):
                continue
            # Descartar valores que son claramente código, no secretos:
            # p. ej.  tokens = {tok for ...}  ó  token = set(...)
            if any(ch in value for ch in "{}()[]<>$"):
                continue
            ent = _entropy(value)
            if ent >= 3.5 and len(value) >= 20:
                hits.append((i, f"{var} (asignación sospechosa)", mask(value), "MEDIA"))
            elif strict and ent >= 2.8 and len(value) >= 16:
                hits.append((i, f"{var} (posible)", mask(value), "BAJA"))
    return hits


def scan_project(root: Path, strict: bool = False):
    """Escanea todo el árbol. Devuelve dict {ruta_relativa: [hallazgos]}."""
    report = {}
    for path in _iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hits = scan_text(text, strict=strict)
        if hits:
            report[str(path.relative_to(root))] = hits
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Detector de secretos de YUE")
    ap.add_argument("--path", default=str(ROOT), help="Carpeta a analizar")
    ap.add_argument("--strict", action="store_true", help="Incluir sospechas de baja confianza")
    args = ap.parse_args(argv)

    root = Path(args.path).resolve()
    report = scan_project(root, strict=args.strict)

    print(f"[secret-scanner] Analizando: {root}")
    if not report:
        print("[secret-scanner] OK · no se encontraron secretos reales.")
        return 0

    total = sum(len(v) for v in report.values())
    print(f"[secret-scanner] ⚠  {total} posible(s) secreto(s) en {len(report)} archivo(s):\n")
    high = 0
    for rel, hits in report.items():
        print(f"  {rel}")
        for line, name, masked, conf in hits:
            flag = "‼" if conf == "ALTA" else ("•" if conf == "MEDIA" else "·")
            if conf == "ALTA":
                high += 1
            print(f"    {flag} L{line:<5} {conf:<5} {name}: {masked}")
        print()

    print("Qué hacer:")
    print("  1) REVOCA y regenera cualquier clave con confianza ALTA (ya está expuesta).")
    print("  2) Sácala del código y ponla solo en .env (que NO se distribuye).")
    print("  3) En .env.example deja un marcador, nunca una clave real.")
    # Solo bloquea el build si hay confianza ALTA (evita falsos positivos en CI).
    return 1 if high else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Constructor de paquete limpio para distribución (FASE 1 · Producción).

Hace lo que un ZIP hecho a mano NO hace: garantiza que el paquete que compartes
no lleva secretos ni datos personales. El proceso:

  1) Copia el proyecto a una carpeta temporal de "staging" (tu carpeta real
     NUNCA se toca; tus recuerdos y tu .env quedan como están).
  2) Limpia el staging (cachés, datos personales, .env, documentos generados).
  3) Verifica que exista .env.example y que NO exista .env.
  4) Pasa el detector de secretos por el staging. Si hay claves reales -> ABORTA.
  5) Crea dist/yue_companion_<version>_clean.zip.

Uso:
    python -m tools.build_release
    python -m tools.build_release --version 2.0.0

Requiere: tools/clean_project.py y tools/secret_scanner.py (ya incluidos).
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path

from tools import clean_project, secret_scanner

ROOT = Path(__file__).resolve().parent.parent

# Lo que NUNCA se copia al staging (además de lo que borra clean_project).
_EXCLUDE_TOP = {".git", ".venv", "venv", "dist", "build", "__pycache__",
                ".pytest_cache", ".mypy_cache", ".idea", ".vscode"}


def _copytree_filtered(src: Path, dst: Path):
    def ignore(dirpath, names):
        skip = set()
        for n in names:
            if n in _EXCLUDE_TOP or n == ".env":
                skip.add(n)
            if n.endswith((".pyc", ".pyo")):
                skip.add(n)
        return skip
    shutil.copytree(src, dst, ignore=ignore)


def _detect_version(root: Path) -> str:
    """Intenta leer una versión del CHANGELOG; si no, usa 'dev'."""
    changelog = root / "CHANGELOG.md"
    if changelog.exists():
        import re
        for line in changelog.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.search(r"(\d+\.\d+\.\d+)", line)
            if m:
                return m.group(1)
    return "dev"


def build(root: Path, version: str | None = None) -> int:
    version = version or _detect_version(root)
    dist = root / "dist"
    dist.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="yue_stage_") as tmp:
        staging = Path(tmp) / "yue_companion"
        print(f"[build] 1/5 Copiando a staging temporal…")
        _copytree_filtered(root, staging)

        print(f"[build] 2/5 Limpiando staging (datos personales, cachés)…")
        clean_project.clean(staging, apply=True)

        print(f"[build] 3/5 Verificando plantilla de configuración…")
        if (staging / ".env").exists():
            print("[build] ABORTADO: .env sigue presente en el staging.")
            return 2
        if not (staging / ".env.example").exists():
            print("[build] ABORTADO: falta .env.example (plantilla obligatoria).")
            return 2

        print(f"[build] 4/5 Buscando secretos en el staging…")
        report = secret_scanner.scan_project(staging)
        high = sum(1 for hits in report.values() for _, _, _, conf in hits if conf == "ALTA")
        if high:
            print(f"[build] ABORTADO: {high} secreto(s) de confianza ALTA en el paquete.")
            print("        Ejecuta:  python -m tools.secret_scanner")
            print("        Revoca las claves y usa marcadores en .env.example.")
            return 3
        if report:
            n = sum(len(v) for v in report.values())
            print(f"[build] Aviso: {n} coincidencia(s) de confianza media/baja (no bloquean).")

        print(f"[build] 5/5 Empaquetando ZIP…")
        out = dist / f"yue_companion_{version}_clean.zip"
        if out.exists():
            out.unlink()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(staging.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(staging.parent))

        size_mb = out.stat().st_size / 1_048_576
        print(f"\n[build] OK · {out}  ({size_mb:.1f} MB)")
        print("[build] Paquete verificado: sin .env, sin datos personales, sin claves ALTA.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Empaquetado limpio de YUE")
    ap.add_argument("--version", default=None)
    args = ap.parse_args(argv)
    return build(ROOT, version=args.version)


if __name__ == "__main__":
    raise SystemExit(main())

"""Catálogo dinámico y cacheado de aplicaciones instaladas en Windows."""
from __future__ import annotations

import json
import os
import platform
import re
import threading
import time
import unicodedata
from pathlib import Path

import config


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFD", (value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_windows_target(value: str) -> str:
    """Limpia DisplayIcon/App Paths: comillas, índice de icono y variables."""
    raw = os.path.expandvars(str(value or "").strip())
    # Formas habituales: "C:\Ruta\app.exe",0  o  C:\Ruta\app.exe,-12
    match = re.match(r'^"([^"]+)"(?:,\s*-?\d+)?$', raw)
    if match:
        return match.group(1).strip()
    raw = re.sub(r',\s*-?\d+\s*$', '', raw).strip()
    return raw.strip('"').strip()


class AppCatalog:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or getattr(config, "PC_APP_CATALOG_PATH", config.DATA_DIR / "apps_catalogo.json"))
        self._lock = threading.RLock()
        self._scan_lock = threading.Lock()
        self._data = {"version": 1, "updated_at": 0, "apps": []}
        self._index: dict[str, dict] = {}
        self.load()

    def load(self) -> dict:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("apps"), list):
                    with self._lock:
                        self._data = data
                        self._index = {str(a.get("key", "")): a for a in data.get("apps", []) if a.get("key")}
        except Exception as exc:
            print("[apps] no pude leer el catálogo:", exc)
        return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data, ensure_ascii=False))

    def scan_async(self, force: bool = False):
        if platform.system().lower() != "windows":
            return None
        max_age = float(getattr(config, "PC_APP_CATALOG_MAX_AGE_HOURS", 72)) * 3600
        if not force and self._data.get("apps") and time.time() - float(self._data.get("updated_at", 0)) < max_age:
            return None
        worker = threading.Thread(target=self.scan, kwargs={"force": force}, daemon=True, name="YueAppScanner")
        worker.start()
        return worker

    def scan(self, force: bool = False) -> dict:
        # El escaneo inicial y /reescanear_apps pueden coincidir. Solo uno toca
        # el Registro/menú Inicio y el archivo temporal cada vez.
        acquired = self._scan_lock.acquire(blocking=bool(force))
        if not acquired:
            return self.snapshot()
        try:
            return self._scan_impl()
        finally:
            self._scan_lock.release()

    def _scan_impl(self) -> dict:
        if platform.system().lower() != "windows":
            return self.snapshot()
        entries = []
        entries.extend(self._scan_app_paths())
        entries.extend(self._scan_uninstall_registry())
        entries.extend(self._scan_start_menu())
        dedup = {}
        for entry in entries:
            name = str(entry.get("name", "")).strip()
            target = str(entry.get("target", "")).strip()
            if not name or not target:
                continue
            key = normalize_name(name)
            if not key:
                continue
            # Priorizamos ejecutables/rutas directas frente a accesos .lnk.
            current = dedup.get(key)
            if current and current.get("target", "").lower().endswith(".exe"):
                continue
            entry["key"] = key
            dedup[key] = entry
        data = {
            "version": 1,
            "updated_at": time.time(),
            "apps": sorted(dedup.values(), key=lambda item: item["key"]),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as exc:
            print("[apps] no pude guardar el catálogo:", exc)
        with self._lock:
            self._data = data
            self._index = {str(a.get("key", "")): a for a in data.get("apps", []) if a.get("key")}
        print(f"[apps] catálogo actualizado: {len(data['apps'])} aplicaciones")
        return self.snapshot()

    def resolve(self, requested: str) -> dict | None:
        wanted = normalize_name(requested)
        if not wanted:
            return None
        with self._lock:
            exact = self._index.get(wanted)
            apps = list(self._data.get("apps", []))
        if exact:
            return dict(exact)
        # Coincidencia conservadora: todas las palabras solicitadas deben aparecer.
        words = set(wanted.split())
        candidates = []
        for app in apps:
            key = str(app.get("key", ""))
            app_words = set(key.split())
            if words <= app_words or app_words <= words:
                score = len(words & app_words) / max(1, len(words | app_words))
                candidates.append((score, -abs(len(key) - len(wanted)), app))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return dict(candidates[0][2]) if candidates[0][0] >= 0.5 else None

    @staticmethod
    def launch(entry: dict):
        target = str(entry.get("target", "")).strip().strip('"')
        args = str(entry.get("args", "")).strip()
        if not target:
            raise RuntimeError("La aplicación del catálogo no tiene ruta de inicio.")
        if platform.system().lower() == "windows" and hasattr(os, "startfile"):
            # os.startfile abre .lnk, .url, AppsFolder y ejecutables respetando Windows.
            try:
                os.startfile(target, arguments=args or None)
                return
            except TypeError:  # Python/Windows antiguos no aceptan arguments=
                os.startfile(target)
                return
        import subprocess
        command = [target] + ([args] if args else [])
        subprocess.Popen(command, shell=False)

    @staticmethod
    def _scan_app_paths() -> list[dict]:
        out = []
        try:
            import winreg
        except Exception:
            return out
        roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
        bases = (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
        )
        for root in roots:
            for base in bases:
                try:
                    key = winreg.OpenKey(root, base)
                except OSError:
                    continue
                try:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        subname = winreg.EnumKey(key, i)
                        try:
                            with winreg.OpenKey(key, subname) as sub:
                                target = clean_windows_target(winreg.QueryValue(sub, None))
                        except OSError:
                            continue
                        name = Path(subname).stem
                        if target:
                            out.append({"name": name, "target": target, "source": "app_paths"})
                finally:
                    winreg.CloseKey(key)
        return out

    @staticmethod
    def _scan_uninstall_registry() -> list[dict]:
        out = []
        try:
            import winreg
        except Exception:
            return out
        roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
        bases = (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        )
        for root in roots:
            for base in bases:
                try:
                    key = winreg.OpenKey(root, base)
                except OSError:
                    continue
                try:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                                name = str(winreg.QueryValueEx(sub, "DisplayName")[0] or "").strip()
                                target = ""
                                for field in ("DisplayIcon", "InstallLocation"):
                                    try:
                                        value = clean_windows_target(winreg.QueryValueEx(sub, field)[0])
                                    except OSError:
                                        continue
                                    if field == "InstallLocation" and value:
                                        # No inventamos exe dentro de la carpeta; solo DisplayIcon es lanzable.
                                        continue
                                    if value and Path(value).exists():
                                        target = value
                                        break
                        except OSError:
                            continue
                        if name and target:
                            out.append({"name": name, "target": target, "source": "uninstall"})
                finally:
                    winreg.CloseKey(key)
        return out

    @staticmethod
    def _scan_start_menu() -> list[dict]:
        roots = []
        for env in ("APPDATA", "PROGRAMDATA"):
            value = os.getenv(env)
            if value:
                roots.append(Path(value) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
        out = []
        shell = None
        try:
            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
        except Exception:
            pass
        for root in roots:
            if not root.exists():
                continue
            for shortcut in root.rglob("*.lnk"):
                target, args = str(shortcut), ""
                if shell is not None:
                    try:
                        link = shell.CreateShortcut(str(shortcut))
                        if link.TargetPath:
                            target = str(link.TargetPath)
                            args = str(link.Arguments or "")
                    except Exception:
                        pass
                out.append({
                    "name": shortcut.stem,
                    "target": target,
                    "args": args,
                    "source": "start_menu",
                })
        return out

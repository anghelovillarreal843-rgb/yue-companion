"""Biblioteca de habilidades: recetas de órdenes de PC que ya funcionaron.

Archivo: data/learning/skills.json

Idea: si Yue ya resolvió "abre el bloc de notas y escribe la lista", la próxima
vez no hace falta gastar 3 ciclos de visión — se repite el plan guardado. Si el
plan repetido falla dos veces seguidas, la receta se degrada y volvemos al
planificador visual.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import config

# Campos de un paso cuyo contenido literal DEBE aparecer en la orden nueva.
# Evita que "escribe hola mundo" reutilice la receta de "escribe adiós mundo".
_CAMPOS_LITERALES = {
    "type_text": "text",
    "search_web": "query",
    "open_url": "url",
    "open_app": "name",
    "click_text": "text",
    "click_element": "name",
    "focus_window": "title",
    "close_window": "title",
}


def normalize(text: str) -> str:
    """Minúsculas, sin tildes, sin puntuación y sin espacios repetidos.

    También quita el vocativo y las muletillas de cortesía: en órdenes por voz,
    "abre el bloc de notas" y "yue, abre el bloc de notas por favor" son la misma
    orden y deben compartir receta.
    """
    text = (text or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\b(yue|oye yue)\b", " ", text)
    text = re.sub(r"\b(por favor|porfa|porfis|plis|please|gracias)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> list[str]:
    return normalize(text).split()


def similarity(a: str, b: str) -> float:
    """Similitud por tokens normalizados (0.0 a 1.0).

    Mezcla el orden real de las palabras con el conjunto ordenado, para que
    "abre notepad y escribe hola" y "escribe hola y abre notepad" se parezcan,
    pero "escribe hola" y "escribe adios" NO.
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    en_orden = SequenceMatcher(None, ta, tb).ratio()
    ordenados = SequenceMatcher(None, sorted(ta), sorted(tb)).ratio()
    return max(en_orden, ordenados)


class SkillLibrary:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else Path(config.LEARNING_DIR) / "skills.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()

    # ------------------------------------------------------------ disco
    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("skills"), list):
                return data
        except FileNotFoundError:
            pass
        except Exception as exc:
            print("[habilidades] archivo ilegible, empiezo de cero:", exc)
        return {"version": 1, "skills": [], "updated": 0}

    def _save(self) -> None:
        """Escritura atómica: nunca dejamos el JSON a medias."""
        with self._lock:
            self._data["updated"] = time.time()
            del self._data["skills"][int(getattr(config, "LEARNING_MAX_SKILLS", 200)):]
            tmp = self.path.with_suffix(".tmp")
            try:
                tmp.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(self.path)
            except Exception as exc:
                print("[habilidades] no pude guardar:", exc)

    # ------------------------------------------------------------ consulta
    def _best_match(self, instruction: str, threshold: float) -> tuple[dict | None, float]:
        objetivo = normalize(instruction)
        mejor, puntaje = None, 0.0
        for receta in self._data.get("skills", []):
            valor = similarity(objetivo, receta.get("instruction_norm", ""))
            if valor > puntaje:
                mejor, puntaje = receta, valor
        if mejor is not None and puntaje >= threshold:
            return mejor, puntaje
        return None, puntaje

    def find(self, instruction: str) -> dict | None:
        """Receta fiable para esta orden, o None para usar el planificador visual."""
        if not getattr(config, "LEARNING_ENABLED", True):
            return None
        umbral = float(getattr(config, "LEARNING_SIM_THRESHOLD", 0.85))
        with self._lock:
            receta, puntaje = self._best_match(instruction, umbral)
            if not receta:
                return None
            if receta.get("degraded"):
                return None
            exitos = int(receta.get("successes", 0))
            fallos = int(receta.get("failures", 0))
            if exitos < int(getattr(config, "LEARNING_MIN_SUCCESSES", 2)):
                return None
            ratio = exitos / max(1, exitos + fallos)
            if ratio < float(getattr(config, "LEARNING_MIN_RATIO", 0.7)):
                return None
            if not self._literales_compatibles(receta, instruction):
                # Misma forma, distinto contenido: mejor replanificar.
                return None
            copia = json.loads(json.dumps(receta))
            copia["_similarity"] = round(puntaje, 3)
            return copia

    @staticmethod
    def _literales_compatibles(receta: dict, instruction: str) -> bool:
        """El texto literal del plan debe seguir presente en la orden nueva."""
        orden = normalize(instruction)
        for paso in receta.get("plan", []):
            campo = _CAMPOS_LITERALES.get(str(paso.get("action", "")))
            if not campo:
                continue
            valor = normalize(str(paso.get(campo, "")))
            if not valor or len(valor) < 3:
                continue
            if valor not in orden:
                return False
        return True

    # ------------------------------------------------------------ escritura
    def record_success(self, instruction: str, actions: list[dict], cycles: int = 1) -> dict | None:
        """Guarda una receta nueva o refuerza una existente."""
        if not getattr(config, "LEARNING_ENABLED", True):
            return None
        plan = self._limpiar_plan(actions)
        if not plan:
            return None
        ciclos = max(1, int(cycles or 1))
        ahora = time.time()
        with self._lock:
            receta, _ = self._best_match(
                instruction, float(getattr(config, "LEARNING_SIM_THRESHOLD", 0.85))
            )
            if receta is None:
                receta = {
                    "instruction": instruction.strip()[:300],
                    "instruction_norm": normalize(instruction),
                    "plan": plan,
                    "successes": 1,
                    "failures": 0,
                    "fail_streak": 0,
                    "degraded": False,
                    "avg_cycles": float(ciclos),
                    "last_used": ahora,
                    "created": ahora,
                }
                self._data.setdefault("skills", []).insert(0, receta)
            else:
                exitos = int(receta.get("successes", 0)) + 1
                previo = float(receta.get("avg_cycles", ciclos))
                receta["successes"] = exitos
                receta["avg_cycles"] = round(
                    (previo * (exitos - 1) + ciclos) / exitos, 2
                )
                receta["last_used"] = ahora
                receta["fail_streak"] = 0
                # Un éxito fresco rehabilita una receta degradada, con el plan nuevo.
                receta["degraded"] = False
                receta["plan"] = plan
                receta["instruction"] = instruction.strip()[:300]
                receta["instruction_norm"] = normalize(instruction)
            self._data["skills"].sort(key=lambda r: r.get("last_used", 0), reverse=True)
            self._save()
            return json.loads(json.dumps(receta))

    def record_failure(self, instruction: str) -> dict | None:
        """Suma un fallo. A los 2 fallos seguidos, la receta se degrada."""
        with self._lock:
            receta, _ = self._best_match(
                instruction, float(getattr(config, "LEARNING_SIM_THRESHOLD", 0.85))
            )
            if receta is None:
                return None
            receta["failures"] = int(receta.get("failures", 0)) + 1
            receta["fail_streak"] = int(receta.get("fail_streak", 0)) + 1
            if receta["fail_streak"] >= int(getattr(config, "LEARNING_FAIL_STREAK", 2)):
                receta["degraded"] = True
                print(f"[habilidades] receta degradada: «{receta.get('instruction', '')}»")
            self._save()
            return json.loads(json.dumps(receta))

    @staticmethod
    def _limpiar_plan(actions: list[dict]) -> list[dict]:
        """Solo guardamos pasos serializables y con acción declarada."""
        plan: list[dict] = []
        for paso in actions or []:
            if not isinstance(paso, dict):
                continue
            accion = str(paso.get("action", "")).strip().lower()
            if not accion:
                continue
            try:
                limpio = json.loads(json.dumps(paso))
            except Exception:
                continue
            limpio["action"] = accion
            plan.append(limpio)
        limite = int(getattr(config, "LEARNING_MAX_PLAN_STEPS", 24))
        return plan[:limite]

    # ------------------------------------------------------------ utilidades
    def all(self) -> list[dict]:
        with self._lock:
            return json.loads(json.dumps(self._data.get("skills", [])))

    def forget(self, instruction: str) -> bool:
        with self._lock:
            receta, _ = self._best_match(instruction, 0.85)
            if receta is None:
                return False
            self._data["skills"].remove(receta)
            self._save()
            return True

    def stats(self) -> dict:
        with self._lock:
            recetas = self._data.get("skills", [])
            return {
                "total": len(recetas),
                "activas": sum(1 for r in recetas if not r.get("degraded")),
                "degradadas": sum(1 for r in recetas if r.get("degraded")),
            }

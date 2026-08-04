"""Memoria de errores: qué falló, por qué y qué hacer distinto la próxima vez.

Archivo: data/learning/lessons.json

La reflexión se le pide al LLM (AIEngine.chat) en español. Como record() puede
llamarse desde el hilo de UI (_on_pc_failed), la llamada al LLM se hace en un
hilo aparte: el fallo se guarda al instante y la lección se rellena después.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import config
from core.learning.skills import normalize, similarity


class LessonBook:
    def __init__(self, path: Path | None = None, engine=None):
        self.path = Path(path) if path else Path(config.LEARNING_DIR) / "lessons.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = engine
        self._lock = threading.RLock()
        self._data = self._load()

    # ------------------------------------------------------------ disco
    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("lessons"), list):
                return data
        except FileNotFoundError:
            pass
        except Exception as exc:
            print("[lecciones] archivo ilegible, empiezo de cero:", exc)
        return {"version": 1, "lessons": [], "updated": 0}

    def _save(self) -> None:
        with self._lock:
            self._data["updated"] = time.time()
            del self._data["lessons"][int(getattr(config, "LEARNING_MAX_LESSONS", 200)):]
            tmp = self.path.with_suffix(".tmp")
            try:
                tmp.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(self.path)
            except Exception as exc:
                print("[lecciones] no pude guardar:", exc)

    # ------------------------------------------------------------ registro
    def record(
        self,
        instruction: str,
        history: list[str] | None = None,
        error: str = "",
        engine=None,
        blocking: bool = False,
    ) -> dict:
        """Guarda el fallo ya mismo y pide la reflexión al LLM en segundo plano."""
        entrada = {
            "id": f"{int(time.time() * 1000)}",
            "instruction": (instruction or "").strip()[:300],
            "instruction_norm": normalize(instruction),
            "error": str(error or "")[:400],
            "history": [str(h)[:200] for h in (history or [])][-12:],
            "lesson": "",
            "ts": time.time(),
        }
        with self._lock:
            self._data.setdefault("lessons", []).insert(0, entrada)
            self._save()

        if not getattr(config, "LEARNING_REFLECT", True):
            return entrada
        motor = engine or self.engine
        if motor is None:
            return entrada
        if blocking:
            self._reflect(entrada["id"], motor)
        else:
            hilo = threading.Thread(
                target=self._reflect,
                args=(entrada["id"], motor),
                name="yue-leccion",
                daemon=True,
            )
            hilo.start()
        return entrada

    def _reflect(self, lesson_id: str, engine) -> None:
        """Pide al LLM 2-3 frases sobre por qué falló y qué hacer distinto."""
        with self._lock:
            entrada = next(
                (l for l in self._data.get("lessons", []) if l.get("id") == lesson_id), None
            )
            if entrada is None:
                return
            datos = json.loads(json.dumps(entrada))

        sistema = (
            "Eres el módulo de aprendizaje de YUE, un asistente que controla un PC "
            "con Windows. Te doy una orden que FALLÓ, los pasos que se ejecutaron y "
            "el error. Responde en español con 2 o 3 frases, sin listas, sin preámbulo "
            "y sin disculpas: primero por qué falló, luego qué habría que hacer distinto "
            "la próxima vez con las acciones disponibles (open_app, click_element, "
            "click_text, focus_window, hotkey, type_text, press, wait). Sé concreto y "
            "accionable; si no hay información suficiente, dilo en una frase."
        )
        usuario = (
            f"Orden: {datos.get('instruction', '')}\n"
            f"Error: {datos.get('error', '')}\n"
            "Pasos ejecutados:\n" + ("\n".join(datos.get("history", [])) or "(ninguno)")
        )
        try:
            texto = engine.chat(
                [{"role": "system", "content": sistema},
                 {"role": "user", "content": usuario}],
                timeout=40,
            )
            leccion = " ".join(str(texto or "").split())[:400]
        except Exception as exc:
            print("[lecciones] no pude pedir la reflexión al LLM:", exc)
            return

        if not leccion:
            return
        with self._lock:
            for item in self._data.get("lessons", []):
                if item.get("id") == lesson_id:
                    item["lesson"] = leccion
                    break
            self._save()

    # ------------------------------------------------------------ consulta
    def relevant(self, instruction: str, k: int = 3) -> list[str]:
        """Lecciones de órdenes parecidas, de la más parecida a la menos."""
        if not getattr(config, "LEARNING_ENABLED", True):
            return []
        umbral = float(getattr(config, "LEARNING_LESSON_THRESHOLD", 0.55))
        objetivo = normalize(instruction)
        candidatos: list[tuple[float, str]] = []
        with self._lock:
            for item in self._data.get("lessons", []):
                leccion = str(item.get("lesson", "")).strip()
                if not leccion:
                    continue
                valor = similarity(objetivo, item.get("instruction_norm", ""))
                if valor >= umbral:
                    candidatos.append((valor, leccion))
        candidatos.sort(key=lambda x: x[0], reverse=True)
        vistas: list[str] = []
        for _, leccion in candidatos:
            if leccion not in vistas:
                vistas.append(leccion)
            if len(vistas) >= max(1, k):
                break
        return vistas

    def all(self) -> list[dict]:
        with self._lock:
            return json.loads(json.dumps(self._data.get("lessons", [])))

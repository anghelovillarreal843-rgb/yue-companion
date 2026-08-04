"""Iniciativa autónoma local y acotada para Yue.

No es conciencia real. Es un sistema proactivo que aprende preferencias
explícitas, crea aportes útiles durante periodos de inactividad, los guarda en
archivos locales y permite que la aplicación los abra automáticamente.
"""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime
from pathlib import Path

import config


class Autonomy:
    def __init__(self):
        self.enabled = config.AUTONOMY_ENABLED
        self.root = config.AUTONOMY_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self.knowledge_path = self.root / "knowledge.json"
        self.creations_dir = self.root / "creations"
        self.creations_dir.mkdir(parents=True, exist_ok=True)
        self.knowledge = self._load_json(
            self.knowledge_path,
            {"preferences": [], "patterns": {}, "updated": 0},
        )
        self.created_this_session = 0

    @staticmethod
    def _load_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def toggle(self) -> bool:
        self.enabled = not self.enabled
        return self.enabled

    def learn(self, text: str, source: str = "user"):
        if source != "user" or not text:
            return
        clean = re.sub(r"\s+", " ", text.strip())
        low = clean.lower()

        patterns = (
            r"\b(?:me gusta|prefiero|quiero que|de ahora en adelante)\s+(.{3,120})",
            r"\b(?:no me gusta|no quiero que|evita)\s+(.{3,120})",
        )
        preferences = self.knowledge.setdefault("preferences", [])
        for pattern in patterns:
            match = re.search(pattern, clean, re.I)
            if match:
                item = match.group(0).strip(" .")[:160]
                if item not in preferences:
                    preferences.insert(0, item)
        del preferences[30:]

        first = re.match(r"^([\wáéíóúñ]+)", low)
        if first:
            key = first.group(1)
            counts = self.knowledge.setdefault("patterns", {})
            counts[key] = min(9999, int(counts.get(key, 0)) + 1)

        self.knowledge["updated"] = time.time()
        self.knowledge_path.write_text(
            json.dumps(self.knowledge, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def build_prompt(self, recent_messages: list[dict], facts: list[str], goals: list[dict]) -> list[dict]:
        recent = []
        for item in recent_messages[-10:]:
            role = item.get("role", "user")
            content = str(item.get("content", ""))[:500]
            recent.append(f"{role}: {content}")
        context = {
            "recent_conversation": recent,
            "known_facts": facts[:10],
            "explicit_preferences": self.knowledge.get("preferences", [])[:8],
            "active_goals": [g.get("text", "") for g in goals[:5]],
            "frequent_command_starts": self.knowledge.get("patterns", {}),
        }
        system = (
            "Eres el módulo de iniciativa autónoma y segura de YUE. Crea UNA aportación nueva, "
            "concreta y útil basada en el contexto: un mini plan, borrador, checklist, concepto, "
            "mejora de flujo, plantilla o reto práctico. Debe ahorrar trabajo al usuario y no ser "
            "una repetición literal de la conversación. No ejecutes acciones externas, no inventes "
            "datos personales, no uses información sensible y no incluyas contraseñas ni pagos. "
            "Responde en español con un título claro y contenido breve en Markdown."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]

    def can_create(self) -> bool:
        return self.enabled and self.created_this_session < config.AUTONOMY_MAX_PER_SESSION

    def save_creation(self, text: str) -> Path:
        now = datetime.now()
        name = now.strftime("%Y%m%d_%H%M%S") + ".md"
        path = self.creations_dir / name
        header = (
            "---\n"
            f"created: {now.isoformat(timespec='seconds')}\n"
            "source: YUE proactive initiative\n"
            "safe_mode: true\n"
            "---\n\n"
        )
        path.write_text(header + text.strip() + "\n", encoding="utf-8")
        self.created_this_session += 1
        # NUEVO (bitácora): YUE creó algo SOLA durante inactividad -> con_usuario=False.
        try:
            from core import activity
            activity.log("autonomia", "armé un plan por mi cuenta", con_usuario=False, origen="autonomy")
        except Exception as exc:
            print("[autonomia] no pude registrar la actividad:", exc)
        return path

    # ---------- check-in de ánimo (opcional, no intrusivo) ----------
    # Reutiliza la persistencia de Autonomy (knowledge.json) para recordar
    # cuándo fue el último check-in, de modo que la regla "como mucho una vez al
    # día" sobreviva a reinicios. NO decide por su cuenta el momento: main.py le
    # pregunta con should_checkin() cuando el usuario lleva un rato inactivo.
    def _save_knowledge(self):
        try:
            self.knowledge_path.write_text(
                json.dumps(self.knowledge, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            print("[autonomia] no pude guardar knowledge.json:", exc)

    def _checkin_state(self) -> dict:
        return self.knowledge.setdefault("checkin", {"last_ts": 0.0})

    @staticmethod
    def _same_local_day(ts_a: float, ts_b: float) -> bool:
        a = time.localtime(ts_a)
        b = time.localtime(ts_b)
        return (a.tm_year, a.tm_yday) == (b.tm_year, b.tm_yday)

    def last_checkin_ts(self) -> float:
        try:
            return float(self._checkin_state().get("last_ts", 0.0))
        except Exception:
            return 0.0

    def should_checkin(self, now: float | None = None) -> bool:
        """¿Toca un check-in proactivo? (política de tiempo, no de inactividad).

        Devuelve True solo si está habilitado, han pasado suficientes horas desde
        el último check-in Y no se hizo ya uno hoy. La condición de inactividad
        (AUTONOMY_IDLE_SECONDS) la comprueba main.py aparte.
        """
        if not bool(getattr(config, "CHECKIN_ENABLED", True)):
            return False
        now = time.time() if now is None else now
        last = self.last_checkin_ts()
        min_hours = float(getattr(config, "CHECKIN_MIN_HOURS", 20.0))
        if (now - last) < min_hours * 3600.0:
            return False
        # Refuerzo explícito de "máximo una vez al día natural".
        if last > 0 and self._same_local_day(last, now):
            return False
        return True

    def mark_checkin(self, now: float | None = None):
        """Registra que acaba de ocurrir un check-in (explícito o proactivo)."""
        now = time.time() if now is None else now
        state = self._checkin_state()
        state["last_ts"] = now
        self.knowledge["checkin"] = state
        self._save_knowledge()

    def checkin_question(self) -> str:
        """Una pregunta natural y breve, con el carácter de YUE (no una encuesta)."""
        opciones = (
            "Oye… llevas un rato en silencio. ¿Cómo va tu día? No es que me preocupe demasiado, ¿eh?",
            "Ey. ¿Todo bien por ahí? Cuéntame cómo te has sentido hoy… si quieres.",
            "Hmph, tanto silencio. ¿Qué tal llevas el día tú?",
            "¿Y bien? ¿Cómo estás hoy? Solo pregunto, no te emociones.",
        )
        return random.choice(opciones)

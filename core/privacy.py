"""Panel de privacidad central de YUE (FASE 4).

Hoy cada sensor (cámara, micrófono, pantalla, audio del sistema) se enciende por
su cuenta y no hay un sitio único que diga QUÉ está capturando YUE ni cuándo lo
hizo. Este módulo centraliza eso, con tres objetivos claros:

  1) CONSENTIMIENTO explícito por sensor: nada se activa si el usuario no lo
     permitió. Un interruptor por cámara, micro, pantalla y audio del sistema.
  2) CONSTANCIA de uso: un registro (auditoría) de cuándo se activó y se apagó
     cada sensor, para que el usuario pueda revisarlo. Ayuda a la confianza.
  3) MODO PRIVACIDAD maestro (tipo "cortina"): un solo interruptor que apaga y
     bloquea TODOS los sensores al instante.

Regla explícita del proyecto: las imágenes de cámara NO se guardan ni se envían.
Este módulo solo registra METADATOS (qué sensor, cuándo, cuánto), nunca el
contenido (ni fotos, ni audio, ni texto de pantalla).

Es ADITIVO: los módulos de cámara/voz/pantalla pueden consultar `can_use()` antes
de abrir el hardware y llamar a `mark_active/mark_inactive`, sin cambiar su lógica
interna. Solo usa la librería estándar (sqlite3); funciona offline.
"""
from __future__ import annotations

import enum
import sqlite3
import threading
from datetime import datetime
from pathlib import Path


class Sensor(str, enum.Enum):
    CAMERA = "camera"
    MICROPHONE = "microphone"
    SCREEN = "screen"
    SYSTEM_AUDIO = "system_audio"


# Texto de garantía que YUE puede mostrar/decir. No promete nada que no cumpla.
GARANTIA_NO_ALMACENA = (
    "Las imágenes de la cámara y el audio se procesan en el momento y NO se "
    "guardan ni se envían a ningún servidor. Solo se registra cuándo se usó cada "
    "sensor, nunca su contenido."
)

# Por defecto, qué sensores vienen permitidos. Conservador: cámara y micro piden
# permiso explícito; pantalla y audio del sistema también. Todo empieza en False
# salvo que el integrador decida otra cosa vía set_allowed().
_DEFAULT_ALLOWED = {
    Sensor.CAMERA: False,
    Sensor.MICROPHONE: False,
    Sensor.SCREEN: False,
    Sensor.SYSTEM_AUDIO: False,
}


class PrivacyManager:
    def __init__(self, db_path: str | Path | None = None):
        # Si no se da ruta, funciona en memoria (útil para pruebas).
        self.db_path = ":memory:" if db_path is None else str(db_path)
        self._lock = threading.RLock()
        self._privacy_mode = False  # "cortina" maestra
        self._allowed = dict(_DEFAULT_ALLOWED)
        self._active: dict[Sensor, int | None] = {s: None for s in Sensor}
        # Conexión persistente si es en memoria (para no perder el registro).
        self._mem_conn = sqlite3.connect(self.db_path) if self.db_path == ":memory:" else None
        self._ensure_schema()
        self._load_prefs()

    # ---- Infraestructura --------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _ensure_schema(self):
        c = self._conn()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS privacy_prefs (
                sensor TEXT PRIMARY KEY,
                allowed INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS privacy_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor TEXT,
                started_at TEXT,
                ended_at TEXT,
                reason TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS privacy_flags (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        c.commit()
        if self._mem_conn is None:
            c.close()

    def _load_prefs(self):
        c = self._conn()
        for r in c.execute("SELECT sensor, allowed FROM privacy_prefs"):
            try:
                self._allowed[Sensor(r[0])] = bool(r[1])
            except ValueError:
                pass
        row = c.execute("SELECT value FROM privacy_flags WHERE key='privacy_mode'").fetchone()
        if row is not None:
            self._privacy_mode = row[0] == "1"
        if self._mem_conn is None:
            c.close()

    def _save_pref(self, sensor: Sensor, allowed: bool):
        c = self._conn()
        c.execute(
            "INSERT INTO privacy_prefs (sensor, allowed) VALUES (?,?) "
            "ON CONFLICT(sensor) DO UPDATE SET allowed=excluded.allowed",
            (sensor.value, 1 if allowed else 0),
        )
        c.commit()
        if self._mem_conn is None:
            c.close()

    def _save_flag(self, key: str, value: str):
        c = self._conn()
        c.execute(
            "INSERT INTO privacy_flags (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        c.commit()
        if self._mem_conn is None:
            c.close()

    # ---- Consentimiento por sensor ---------------------------------------
    def set_allowed(self, sensor: Sensor, allowed: bool):
        """El usuario permite (o retira permiso a) un sensor."""
        sensor = Sensor(sensor)
        with self._lock:
            self._allowed[sensor] = bool(allowed)
            self._save_pref(sensor, bool(allowed))
            # Si se retira el permiso y estaba activo, se cierra su sesión.
            if not allowed and self._active[sensor] is not None:
                self._close_session_locked(sensor, reason="permiso retirado")

    def is_allowed(self, sensor: Sensor) -> bool:
        with self._lock:
            return bool(self._allowed[Sensor(sensor)])

    # ---- Modo privacidad maestro -----------------------------------------
    def set_privacy_mode(self, on: bool):
        """Cortina maestra: al activarla, apaga y bloquea todos los sensores."""
        with self._lock:
            self._privacy_mode = bool(on)
            self._save_flag("privacy_mode", "1" if on else "0")
            if on:
                for s in Sensor:
                    if self._active[s] is not None:
                        self._close_session_locked(s, reason="modo privacidad")

    @property
    def privacy_mode(self) -> bool:
        with self._lock:
            return self._privacy_mode

    # ---- Puerta de uso: ¿puede este sensor encenderse ahora? --------------
    def can_use(self, sensor: Sensor) -> bool:
        """True solo si NO está el modo privacidad y el sensor tiene permiso."""
        sensor = Sensor(sensor)
        with self._lock:
            return (not self._privacy_mode) and bool(self._allowed[sensor])

    # ---- Registro de actividad (metadatos, nunca contenido) --------------
    def mark_active(self, sensor: Sensor, reason: str = "") -> bool:
        """Marca que un sensor empezó a usarse. Devuelve False si no está permitido.

        Nunca guarda lo que capta el sensor: solo la marca de tiempo de inicio.
        """
        sensor = Sensor(sensor)
        with self._lock:
            if not self.can_use(sensor):
                return False
            if self._active[sensor] is not None:
                return True  # ya estaba activo; no duplica sesión
            now = datetime.now().isoformat(timespec="seconds")
            c = self._conn()
            cur = c.execute(
                "INSERT INTO privacy_audit (sensor, started_at, reason) VALUES (?,?,?)",
                (sensor.value, now, reason),
            )
            c.commit()
            self._active[sensor] = cur.lastrowid
            if self._mem_conn is None:
                c.close()
            return True

    def mark_inactive(self, sensor: Sensor):
        """Marca que un sensor dejó de usarse (cierra su sesión de auditoría)."""
        sensor = Sensor(sensor)
        with self._lock:
            self._close_session_locked(sensor, reason="")

    def _close_session_locked(self, sensor: Sensor, reason: str):
        sid = self._active.get(sensor)
        if sid is None:
            return
        now = datetime.now().isoformat(timespec="seconds")
        c = self._conn()
        # Si hay razón de cierre (p. ej. modo privacidad), la anota al final.
        if reason:
            c.execute(
                "UPDATE privacy_audit SET ended_at=?, reason=COALESCE(NULLIF(reason,''),?) "
                "WHERE id=?",
                (now, reason, sid),
            )
        else:
            c.execute("UPDATE privacy_audit SET ended_at=? WHERE id=?", (now, sid))
        c.commit()
        if self._mem_conn is None:
            c.close()
        self._active[sensor] = None

    def is_active(self, sensor: Sensor) -> bool:
        with self._lock:
            return self._active[Sensor(sensor)] is not None

    # ---- Vistas para la interfaz -----------------------------------------
    def panel_state(self) -> dict:
        """Estado completo para un panel: permiso, actividad y último uso."""
        with self._lock:
            c = self._conn()
            estado = {}
            for s in Sensor:
                row = c.execute(
                    "SELECT started_at, COUNT(*) AS n FROM privacy_audit "
                    "WHERE sensor=? ORDER BY id DESC LIMIT 1",
                    (s.value,),
                ).fetchone()
                # Recalcular total (la fila anterior trae solo la última).
                total = c.execute(
                    "SELECT COUNT(*) FROM privacy_audit WHERE sensor=?", (s.value,)
                ).fetchone()[0]
                estado[s.value] = {
                    "permitido": bool(self._allowed[s]),
                    "activo": self._active[s] is not None,
                    "ultimo_uso": (row[0] if row else None),
                    "veces_usado": total,
                }
            if self._mem_conn is None:
                c.close()
            return {
                "modo_privacidad": self._privacy_mode,
                "sensores": estado,
                "garantia": GARANTIA_NO_ALMACENA,
            }

    def audit_log(self, sensor: Sensor | None = None, limit: int = 50):
        """Historial de uso (metadatos). Útil para 'muéstrame cuándo usaste la cámara'."""
        with self._lock:
            c = self._conn()
            if sensor is not None:
                q = ("SELECT id, sensor, started_at, ended_at, reason FROM privacy_audit "
                     "WHERE sensor=? ORDER BY id DESC LIMIT ?")
                rows = c.execute(q, (Sensor(sensor).value, limit)).fetchall()
            else:
                q = ("SELECT id, sensor, started_at, ended_at, reason FROM privacy_audit "
                     "ORDER BY id DESC LIMIT ?")
                rows = c.execute(q, (limit,)).fetchall()
            out = [
                {"id": r[0], "sensor": r[1], "inicio": r[2], "fin": r[3], "motivo": r[4]}
                for r in rows
            ]
            if self._mem_conn is None:
                c.close()
            return out

    def summary_text(self) -> str:
        """Frase corta y clara del estado, lista para que YUE la diga."""
        st = self.panel_state()
        if st["modo_privacidad"]:
            return "Modo privacidad activo: cámara, micrófono, pantalla y audio están apagados."
        permitidos = [s for s, d in st["sensores"].items() if d["permitido"]]
        if not permitidos:
            return "Ahora mismo no tengo permiso para usar ningún sensor."
        activos = [s for s, d in st["sensores"].items() if d["activo"]]
        base = "Sensores permitidos: " + ", ".join(permitidos) + "."
        if activos:
            base += " Activos en este momento: " + ", ".join(activos) + "."
        else:
            base += " Ninguno está activo ahora mismo."
        return base

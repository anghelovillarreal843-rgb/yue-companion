"""AutonomyDirector (PR 5, paso 8 — §4.1 fila 8).

Extrae de Controller la iniciativa automática y las acciones internas:
_toggle_autonomy, _autonomous_create, _on_autonomy_done/_failed,
_run_internal_action y _set_head_control.

Patrón de traspaso de dueño (mismo que ctx.camera en el paso 7 y que
ctx.pc_busy/ctx.vision_busy): `ctx.autonomy_busy` pasaba a ser escrito por
Controller desde _autonomous_create/_on_autonomy_done/_on_autonomy_failed
todavía no le pertenecían; a partir de este paso el ESCRIBENTE es
AutonomyDirector (la inicialización a False sigue en el cableado de
main.py, igual que pc_busy y vision_busy). Los lectores existentes
(VisionDirector:505, MemoryProactive:390) siguen leyendo ctx.autonomy_busy,
el mismo estado, sin cambio de lectura.

Comunicación con PCDirector SIN acoplamiento director->director: por canales
del controller_ctx (patrón WorkerRegistry ya establecido en pasos previos):
`ctx.pc` (core, ya publicado), y los callbacks/canales que el maestro publica
todavía (ctx.pc_director, ctx.save_routine, ctx.list_routines (ya existía),
ctx.show_activity, ctx.describe_audio, ctx.handle_command, ctx.autonomy_timer
y ctx.head_control). AutonomyDirector nunca conoce al Controller ni a los
otros directores; solo al ctx.

Dependencias del director (todas por controller_ctx):
- estado get/set: autonomy_busy, pc_busy, vision_busy, vision_on,
  chat_request_id, last_user_activity.
- componentes/canales: autonomy, chat, speaker, listener, memory, engine,
  memory_proactive, pc, pc_director, save_routine, list_routines,
  show_activity, describe_audio, handle_command, glance, camera, head_control,
  autonomy_timer, say, workers.
- config directo (AUTONOMY_*, HEAD_CONTROL_ENABLED, CRISIS_* no aplica aquí).
"""
from __future__ import annotations

import time

from core import commands
from engine.autonomy_worker import AutonomyWorker


def _cfg(name: str, default):
    try:
        import config  # toplevel del proyecto
        return getattr(config, name, default)
    except Exception:
        return default


class AutonomyDirector:
    """Estado interno: _autonomy_started_at (marca de inicio del aporte)."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._autonomy_started_at = 0.0

    # ---------- iniciativa automática ----------
    def toggle_autonomy(self):
        enabled = self.ctx.autonomy.toggle()
        if enabled:
            self.ctx.autonomy_timer.start()
            self.ctx.say(
                "Iniciativa automática activada. Prepararé aportes útiles cuando estés inactivo."
            )
        else:
            self.ctx.autonomy_timer.stop()
            self.ctx.say("Iniciativa automática pausada.")

    def autonomous_create(self):
        if (self.ctx.autonomy_busy or self.ctx.pc_busy or self.ctx.vision_busy):
            return
        if not self.ctx.autonomy.can_create() or self.ctx.chat.is_user_composing():
            return
        # NUEVO: la iniciativa autónoma también respeta el estado de YUE. Si el
        # cerebro central decidió que toca acompañar en silencio, no se generan
        # aportes «útiles» por encima de eso.
        if not self.ctx.memory_proactive.yue_may_take_initiative("medium"):
            return
        idle = time.time() - self.ctx.last_user_activity
        if idle < _cfg("AUTONOMY_IDLE_SECONDS", 90):
            return

        self.ctx.autonomy_busy = True
        self._autonomy_started_at = self.ctx.last_user_activity
        self.ctx.chat.set_status("Yue está preparando algo útil por iniciativa propia…")
        messages = self.ctx.autonomy.build_prompt(
            self.ctx.memory.recent_messages(10),
            self.ctx.memory.get_facts(),
            self.ctx.memory.list_goals(),
        )
        worker = AutonomyWorker(self.ctx.engine, messages)
        worker.done.connect(self._on_autonomy_done)
        worker.failed.connect(self._on_autonomy_failed)
        self.ctx.workers.track(worker)

    def _on_autonomy_done(self, text):
        self.ctx.autonomy_busy = False
        self.ctx.chat.set_status("")
        path = self.ctx.autonomy.save_creation(text)
        still_idle = (
            self.ctx.last_user_activity == self._autonomy_started_at
            and time.time() - self.ctx.last_user_activity
            >= _cfg("AUTONOMY_IDLE_SECONDS", 90)
            and not self.ctx.chat.is_user_composing()
        )
        opened = False
        if (still_idle and _cfg("AUTONOMY_PC_ENABLED", False)
                and _cfg("AUTONOMY_OPEN_CREATIONS", False)):
            try:
                self.ctx.pc.open_path(path)
                opened = True
            except Exception as exc:
                print("[autonomia] no pude abrir la creación:", exc)
        if still_idle and _cfg("AUTONOMY_NOTIFY", True):
            suffix = " y la abrí" if opened else ""
            self.ctx.say(
                f"Preparé algo nuevo, lo guardé como {path.name}{suffix}."
            )
        else:
            print("[autonomia] creación guardada:", path)

    def _on_autonomy_failed(self, error):
        self.ctx.autonomy_busy = False
        self.ctx.chat.set_status("")
        print("[autonomia] error:", error)

    # ---------- acciones internas y comandos ----------
    def run_internal_action(self, action, arg):
        if action == "stop_current":
            self.ctx.pc.cancel()
            self.ctx.speaker.stop()
            self.ctx.chat_request_id += 1
            self.ctx.chat.set_status("")
            self.ctx.say("Me detuve. Te escucho.")
        elif action == "pc_undo":
            self.ctx.pc_director.undo_last_pc()
        elif action == "pc_repeat":
            self.ctx.pc_director.repeat_last_pc()
        elif action == "routine_save":
            self.ctx.save_routine(commands.routine_name(arg))
        elif action == "routine_run":
            self.ctx.pc_director.run_routine(commands.routine_name(arg))
        elif action == "routine_list":
            self.ctx.list_routines()
        elif action == "activity_summary":
            self.ctx.show_activity()
        elif action == "voice_on":
            if not self.ctx.speaker.enabled:
                self.ctx.speaker.toggle()
            self.ctx.say("Bien, volveré a hablar en voz alta.")
        elif action == "voice_off":
            if self.ctx.speaker.enabled:
                self.ctx.speaker.toggle()
            self.ctx.chat.show_reply("Voz desactivada.")
        elif action == "mic_off":
            if getattr(self.ctx.listener, "_wanted_enabled", False):
                self.ctx.listener.toggle()
            self.ctx.say("Dejé de escucharte por el micrófono.")
        elif action == "mic_on":
            if not getattr(self.ctx.listener, "_wanted_enabled", False):
                self.ctx.listener.toggle()
            self.ctx.say("Micrófono activado. Puedes interrumpirme mientras hablo.")
        elif action == "vision_look":
            # Pasamos la frase EXACTA del usuario ("¿qué error aparece?",
            # "explícame este gráfico") para que el analizador priorice eso y no
            # dé una descripción genérica de toda la pantalla.
            self.ctx.glance(pregunta=str(arg or ""))
        elif action == "vision_off":
            self.ctx.vision_on = False
            self.ctx.say("De acuerdo, dejo de mirar la pantalla.")
        elif action == "audio_describe":
            # YUE dice qué está sonando y, si hay contenido, da su impresión.
            self.ctx.describe_audio()
        elif action == "camera_status":
            self.ctx.say(self.ctx.camera.describe())
        elif action == "show_goals":
            self.ctx.handle_command("/metas")
        elif action == "autonomy_toggle":
            self.toggle_autonomy()
        elif action == "head_control_on":
            self.set_head_control(True)
        elif action == "head_control_off":
            self.set_head_control(False)
        elif action == "help":
            self.ctx.handle_command("/help")

    def set_head_control(self, on: bool):
        """Activa/pausa el control del cursor con la cabeza (voz o comando).

        Enciende la cadencia rápida de la cámara solo mientras está activo, y
        habla desde el carácter de YUE. Respeta el interruptor maestro de config.
        """
        if not bool(_cfg("HEAD_CONTROL_ENABLED", True)):
            self.ctx.say("El control por cabeza está desactivado en la configuración.")
            return
        if getattr(self.ctx, "head_control", None) is None:
            self.ctx.say("El control por cabeza no está disponible ahora mismo.")
            return
        if on:
            if not self.ctx.camera.active:
                self.ctx.say("Necesito la cámara encendida para moverte el cursor con la cabeza.")
                return
            self.ctx.head_control.enable()
            self.ctx.camera.set_fast_mode(True)
            self.ctx.say(
                "Bien… control por cabeza activado. Mira de frente un segundo mientras "
                "te calibro. Para hacer clic, deja el cursor quietecito un momento."
            )
        else:
            self.ctx.head_control.disable()
            self.ctx.camera.set_fast_mode(False)
            self.ctx.say("Listo, solté el cursor. Control por cabeza desactivado.")
"""DialogueDirector (PR 5, paso 10.1 — ROUTING).

Extrae de Controller el despacho de comandos (`_handle_command`) y los
«one-liners huérfanos». Cada comando quedó como DELEGACIÓN a un canal de
`ctx` (regla «≤5 líneas = delegación», confirmada en §4.1):

- Comandos con director dueño -> su canal/director por ctx.
- Comandos sin director propio (/actividad, /recuerda, /metas, /vinculo,
  /animo_historial, /reescanear_apps, /camara|/vision) -> delegación directa
  a servicios de ctx (ctx.memory, ctx.memory_proactive, ctx.system_context,
  ctx.list_routines, ctx.show_activity, ctx.pc, vision.commands).

Direcciones del paquete:
- main -> director (público): handle_command (publicado como ctx.handle_command).
- director -> ctx (dependencias): say, chat, memory, memory_proactive,
  pc, pc_director, list_routines, show_activity, glance, vision_director,
  vision_mp, voice, autonomy, autonomy_director, head_control, modes,
  apply_mode_switch, teacher, workers, system_context, camera.
- No lee NI conoce al Controller (mismo patrón que los demás directores).
"""
from __future__ import annotations


class DialogueDirector:
    def __init__(self, ctx) -> None:
        self.ctx = ctx

    # ---------- despacho de comandos ----------
    def handle_command(self, text):
        parts = text.split(" ", 1)
        command = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if command == "/help":
            self.ctx.say("Usa /pc seguido de una orden, /mira, /camara, /recuerda, /recuerdos, /meta, /metas, /vinculo, /autonomia, /animo, /animo_historial, /cabeza, /rutinas, /actividad, /reescanear_apps, /modo, /reporte, /diagvision o /diagvoz.")
        elif command in {"/actividad", "/bitacora"}:
            self.ctx.show_activity()
        elif command in {"/reescanear_apps", "/reescanear-apps", "/apps"}:
            self.ctx.chat.set_status("Reescaneando aplicaciones instaladas…")
            from engine.pc_worker import AppScanWorker
            worker = AppScanWorker(self.ctx.pc)
            worker.done.connect(lambda result: (
                self.ctx.chat.set_status(""),
                self.ctx.say(f"Catálogo actualizado: encontré {result.get('count', 0)} aplicaciones.")
            ))
            worker.failed.connect(lambda error: (
                self.ctx.chat.set_status(""),
                self.ctx.say(f"No pude reescanear las aplicaciones: {error}")
            ))
            self.ctx.workers.track(worker)
        elif command in {"/rutinas", "/rutina"}:
            arg_l = (arg or "").strip()
            if not arg_l:
                self.ctx.list_routines()
            else:
                # "/rutinas <nombre>" ejecuta esa rutina directamente.
                self.ctx.pc_director.run_routine(arg_l)
        elif command in {"/animo", "/ánimo"}:
            self.ctx.memory_proactive.start_mood_checkin()
        elif command in {"/animo_historial", "/ánimo_historial", "/animohistorial", "/animo-historial"}:
            resumen = ""
            try:
                resumen = self.ctx.memory.get_mood_summary(7)
            except Exception as exc:
                print("[checkin] no pude leer el resumen de ánimo:", exc)
            if resumen:
                # get_mood_summary empieza en minúscula ("esta semana…"); la
                # ponemos con mayúscula inicial para leerla sola.
                self.ctx.say(resumen[0].upper() + resumen[1:])
            else:
                self.ctx.say(
                    "Todavía no tengo suficientes señales de tu ánimo estos días. "
                    "Cuéntame cómo estás, o usa /animo para dejarme una nota del 1 al 5."
                )
        elif command == "/pc" and arg:
            self.ctx.pc_director.run_pc_order(arg)
        elif command == "/mira":
            self.ctx.glance()
        elif command in {"/diagvision", "/diagnosticovision", "/diagvisión"}:
            self.ctx.vision_director.diagnose_vision()
        elif command in {"/diagvoz", "/diagmicro", "/diagmic", "/diagoido", "/diagoído"}:
            self.ctx.voice.diagnose_voice()
        elif command in {"/camara", "/cámara"}:
            # Aditivo: si el sistema de visión MediaPipe está enganchado, deja que
            # maneje on/off; si no reconoce el argumento, cae al comportamiento
            # clásico de siempre (ctx.camera.describe()).
            _resp = None
            try:
                from vision import commands as _vision_cmds
                _resp = _vision_cmds.handle(getattr(self.ctx, "vision_mp", None), command, arg)
            except Exception:
                _resp = None
            self.ctx.say(_resp if _resp else self.ctx.camera.describe())
        elif command in {"/vision", "/visión"}:
            # Comandos del sistema de visión por cámara (MediaPipe Tasks).
            _resp = None
            try:
                from vision import commands as _vision_cmds
                _resp = _vision_cmds.handle(getattr(self.ctx, "vision_mp", None), command, arg)
            except Exception as _exc:
                _resp = f"No pude consultar la visión: {_exc}"
            self.ctx.say(_resp or "El sistema de visión no está disponible.")
        elif command == "/recuerda" and arg:
            self.ctx.memory.add_fact(arg)
            self.ctx.say("Lo guardé en mi memoria.")
        elif command in {"/recuerdos", "/diagmemoria", "/diagrecuerdos"}:
            self.ctx.memory_proactive.diagnose_memory(arg)
        elif command == "/meta" and arg:
            self.ctx.memory.add_goal(arg)
            self.ctx.memory.add_bond_points(2)
            self.ctx.system_context()
            self.ctx.say("Meta registrada. Te ayudaré a mantenerla presente.")
        elif command == "/metas":
            goals = self.ctx.memory.list_goals()
            self.ctx.say(
                "Tus metas: " + " · ".join(goal["text"] for goal in goals)
                if goals else "No tienes metas activas."
            )
        elif command == "/vinculo":
            from core import bonding
            points = self.ctx.memory.get_bond_points()
            current, next_level, _ = bonding.progress(points)
            if next_level:
                self.ctx.say(
                    f"Nivel {current.index}/10. Faltan {next_level.threshold - points} puntos para avanzar."
                )
            else:
                self.ctx.say("El vínculo está en el nivel máximo.")
        elif command == "/autonomia":
            if arg.lower() in {"on", "activar", "activa"} and not self.ctx.autonomy.enabled:
                self.ctx.autonomy_director.toggle_autonomy()
            elif arg.lower() in {"off", "pausar", "desactivar"} and self.ctx.autonomy.enabled:
                self.ctx.autonomy_director.toggle_autonomy()
            else:
                self.ctx.say("La iniciativa automática está " + ("activa." if self.ctx.autonomy.enabled else "pausada."))
        elif command in {"/cabeza", "/head"}:
            arg_l = arg.lower().strip()
            if arg_l in {"off", "no", "pausa", "pausar", "desactiva", "desactivar"}:
                self.ctx.autonomy_director.set_head_control(False)
            elif arg_l in {"on", "si", "sí", "activa", "activar", ""}:
                # Sin argumento: alterna según el estado actual.
                if not arg_l and getattr(self.ctx, "head_control", None) and self.ctx.head_control.is_active:
                    self.ctx.autonomy_director.set_head_control(False)
                else:
                    self.ctx.autonomy_director.set_head_control(True)
            else:
                self.ctx.autonomy_director.set_head_control(True)
        elif command == "/modo":
            # NUEVO: consulta o cambia de modo por comando.
            if not getattr(self.ctx, "modes", None):
                self.ctx.say("El sistema de modos no está disponible.")
            elif arg:
                route = self.ctx.modes.handle(arg)
                if route.switched:
                    self.ctx.apply_mode_switch(route)
                else:
                    self.ctx.say(f"No reconocí ese modo. Ahora estoy en modo {self.ctx.modes.current_meta().get('label','')}.")
            else:
                meta = self.ctx.modes.current_meta()
                self.ctx.say(f"Estoy en modo {meta.get('emoji','')} {meta.get('label','')}.")
        elif command == "/reporte":
            # NUEVO: exporta el progreso de la clase (md por defecto; csv/xlsx opcional).
            if not getattr(self.ctx, "teacher", None):
                self.ctx.say("El modo profesora no está disponible.")
            else:
                fmt = (arg or "md").lower().strip()
                try:
                    import os
                    import config
                    ruta = os.path.join(str(config.DATA_DIR), "teacher", f"reporte.{ 'xlsx' if fmt in ('xlsx','excel') else 'csv' if fmt=='csv' else 'md'}")
                    salida = self.ctx.teacher.export_report(ruta, fmt)
                    self.ctx.say(f"Guardé el reporte del progreso en: {salida}")
                except Exception as exc:
                    self.ctx.say("No pude generar el reporte.")
                    print("[profesora] error de reporte:", exc)
        else:
            self.ctx.say("No conozco ese comando. Usa /help.")
"""PCDirector (PR 5, paso 4).

Extrae de Controller el paquete de control del PC: órdenes, recuperación
(deshacer/repetir/rutinas), bitácora y confirmación verbal de acciones
irreversibles. Vive con ctx.workers: los QThread (PCWorker/PCRecoveryWorker)
están en engine/pc_worker.py.
"""
from __future__ import annotations

import re
import threading
import unicodedata

from engine.pc_worker import PCRecoveryWorker, PCWorker


def _cfg(name: str, default):
    """Lee config sin conocer a Controller (mismo patrón que VoiceDirector)."""
    try:
        import config  # toplevel del proyecto
        return getattr(config, name, default)
    except Exception:
        return default


class PCDirector:
    """Estado interno: `_instruction` (última orden, para el aprendizaje).

    Estado compartido (en ctx, get/set): pc_busy, pc_confirm.
    Componentes del host: ctx.pc, ctx.engine, ctx.memory, ctx.chat.
    Callbacks del host: ctx.say, ctx.avatar_emotion, ctx.confirm_speak,
    ctx.confirm_notify, ctx.user_float, ctx.list_routines.
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._instruction = ""

    def on_pc_action_log(self, actions):
        """Recibe la bitácora desde PCWorker y la pinta en el hilo de Qt."""
        try:
            from core import ui_bridge
            ui_bridge.post_to_ui_thread(lambda: self.ctx.chat.set_pc_actions(actions))
        except Exception as exc:
            print("[control-pc] no pude mostrar la bitácora:", exc)

    def run_pc_order(self, instruction):
        if self.ctx.pc_busy:
            self.ctx.say("Ya hay una orden en curso. Di «Yue, detente» para cancelarla.")
            return
        self.ctx.pc_busy = True
        self._instruction = instruction      # la necesitan los hooks de aprendizaje
        self.ctx.chat_set_status("Yue está comprendiendo y planificando la orden…")
        self.ctx.avatar_emotion("focused", 0.95, 9000,
                                priority=self.ctx.avatar_conversation_priority,
                                source="control_pc")
        worker = PCWorker(self.ctx.pc, self.ctx.engine, instruction)
        worker.progress.connect(self.ctx.chat_set_status)
        worker.done.connect(self.on_pc_done)
        worker.failed.connect(self.on_pc_failed)
        self.ctx.workers.track(worker)

    def on_pc_done(self, result):
        self.ctx.pc_busy = False
        self.ctx.chat_set_status("")
        # NUEVO (recuperación): recuerda este bloque para "deshacer"/"repetir",
        # también si vino de una receta aprendida (que no pasa por execute()).
        try:
            self.ctx.pc.remember_result(self._instruction, result)
        except Exception as exc:
            print("[control-pc] no pude recordar el bloque:", exc)
        # --- aprendizaje: refuerza o crea la receta si todo salió bien ---
        try:
            from core.learning import integration as learning
            learning.after_result(self._instruction, result)
        except Exception as exc:
            print("[aprendizaje] no pude registrar el resultado:", exc)
        count = len(result.get("actions", []))
        cycles = result.get("cycles", 1)
        if result.get("from_skill"):
            self.ctx.say(f"Listo. Esto ya lo sabía hacer: {count} paso{'s' if count != 1 else ''} de memoria.")
            return
        mode = (
            f"con el agente autónomo en {cycles} fase{'s' if cycles != 1 else ''}"
            if result.get("used_ai") else "con un plan determinista verificado"
        )
        if result.get("used_ai") and not result.get("completed", False):
            self.ctx.say(
                f"Ejecuté {count} paso{'s' if count != 1 else ''} {mode}, pero alcancé el límite sin verificar el final. Revisa el resultado."
            )
        else:
            self.ctx.say(f"Ya terminé. Ejecuté {count} paso{'s' if count != 1 else ''} {mode}.")

    def on_pc_failed(self, error):
        self.ctx.pc_busy = False
        self.ctx.chat_set_status("")
        # --- aprendizaje: guarda el fallo y pide la reflexión en segundo plano ---
        try:
            from core.learning import integration as learning
            learning.after_error(self._instruction, error, self.ctx.pc.last_history)
        except Exception as exc:
            print("[aprendizaje] no pude registrar el error:", exc)
        if "cancelada" not in str(error).lower():
            self.ctx.say(f"No completé esa orden: {error}")
        print("[control-pc] error:", error)

    def undo_last_pc(self):
        """«Deshaz eso»: revierte la última acción si es reversible con seguridad."""
        if self.ctx.pc_busy:
            self.ctx.say("Espera a que termine lo de ahora y enseguida te lo deshago.")
            return
        self.ctx.pc_busy = True
        self.ctx.chat_set_status("Deshaciendo lo último…")
        worker = PCRecoveryWorker(self.ctx.pc, "undo")
        worker.done.connect(self.on_undo_done)
        worker.failed.connect(self.on_recovery_failed)
        self.ctx.workers.track(worker)

    def on_undo_done(self, res):
        self.ctx.pc_busy = False
        self.ctx.chat_set_status("")
        if res.get("undone"):
            accion = res.get("action", "lo último")
            self.ctx.say(f"Hecho, deshice {accion}.")
            return
        motivo = str(res.get("reason", ""))
        if "no tengo" in motivo or "no hay" in motivo:
            self.ctx.say("No tengo ninguna acción reciente que deshacer.")
        else:
            # Sin adivinar: le digo con claridad por qué no me arriesgo.
            self.ctx.say(
                f"No puedo deshacer eso: {motivo}. Prefiero no adivinar y liarla más. "
                "Dime qué quieres corregir y lo hago."
            )

    def repeat_last_pc(self):
        """«Repite eso» / «hazlo de nuevo»: reejecuta el último bloque."""
        if self.ctx.pc_busy:
            self.ctx.say("Ya hay algo en curso; cuando termine te lo repito.")
            return
        self.ctx.pc_busy = True
        self.ctx.chat_set_status("Repitiendo lo último…")
        self.ctx.avatar_emotion("focused", 0.9, 6000,
                                priority=self.ctx.avatar_conversation_priority,
                                source="control_pc")
        worker = PCRecoveryWorker(self.ctx.pc, "repeat")
        worker.progress.connect(self.ctx.chat_set_status)
        worker.done.connect(self.on_repeat_done)
        worker.failed.connect(self.on_recovery_failed)
        self.ctx.workers.track(worker)

    def on_repeat_done(self, res):
        self.ctx.pc_busy = False
        self.ctx.chat_set_status("")
        if not res.get("repeated"):
            motivo = str(res.get("reason", ""))
            if "no tengo" in motivo:
                self.ctx.say("No tengo ninguna orden reciente que repetir. Pídemela una vez y luego ya puedo repetirla.")
            else:
                self.ctx.say(f"No pude repetir lo último: {motivo}.")
            return
        n = len(res.get("actions", []))
        pasos = f"{n} paso{'s' if n != 1 else ''}"
        if res.get("ok"):
            self.ctx.say(f"Listo, repetí lo último ({pasos}).")
        else:
            self.ctx.say(f"Repetí lo último ({pasos}), pero algo no salió igual que antes. Échale un vistazo.")

    def on_recovery_failed(self, error):
        self.ctx.pc_busy = False
        self.ctx.chat_set_status("")
        self.ctx.say(f"No pude completar eso: {error}")

    def run_routine(self, nombre):
        """«Ejecuta mi rutina X»: reejecuta el plan por el pipeline seguro."""
        nombre = (nombre or "").strip()
        if not nombre:
            self.ctx.list_routines(prefijo="¿Cuál de estas quieres que ejecute? ")
            return
        rutina = self.ctx.memory.get_routine(nombre)
        if not rutina or not rutina.get("pasos"):
            self.ctx.say(
                f"No encuentro una rutina llamada «{nombre}». Di «mis rutinas» para ver las que tienes."
            )
            return
        if self.ctx.pc_busy:
            self.ctx.say("Ya hay algo en curso; cuando termine lanzo tu rutina.")
            return
        self.ctx.pc_busy = True
        self._instruction = f"rutina: {rutina['nombre']}"
        self.ctx.chat_set_status(f"Ejecutando la rutina «{rutina['nombre']}»…")
        self.ctx.avatar_emotion("focused", 0.9, 8000,
                                priority=self.ctx.avatar_conversation_priority,
                                source="control_pc")
        worker = PCRecoveryWorker(self.ctx.pc, "routine", actions=rutina["pasos"], label=rutina["nombre"])
        worker.progress.connect(self.ctx.chat_set_status)
        worker.done.connect(self.on_routine_done)
        worker.failed.connect(self.on_recovery_failed)
        self.ctx.workers.track(worker)

    def on_routine_done(self, res):
        self.ctx.pc_busy = False
        self.ctx.chat_set_status("")
        # Aprendizaje: refuerza/crea receta si todo salió bien (igual que una orden).
        try:
            from core.learning import integration as learning
            learning.after_result(self._instruction, res)
        except Exception as exc:
            print("[aprendizaje] no pude registrar la rutina:", exc)
        if not res.get("ran"):
            self.ctx.say(f"No pude ejecutar la rutina: {res.get('reason', 'algo salió mal')}.")
            return

    def pc_confirm_by_voice(self, question: str) -> bool:
        """Pide confirmación por voz y ESPERA la respuesta (sí/no).

        Se llama desde el hilo del PCWorker, así que aquí SOLO se marshaliza la
        pregunta al hilo principal (voz + UI) y se bloquea en un Event hasta que
        el listener/chat entregue la respuesta o venza el tiempo. Si no hay
        respuesta clara a tiempo, devuelve False (no ejecutar) por seguridad.
        """
        event = threading.Event()
        holder = {"result": False}
        self.ctx.pc_confirm = {"event": event, "holder": holder}
        # Que YUE lo pregunte por voz en el hilo principal (usa core/voice.py).
        self.ctx.confirm_speak(question)
        timeout = float(_cfg("ACCESSIBILITY_CONFIRM_TIMEOUT", 25.0))
        answered = event.wait(max(3.0, timeout))
        self.ctx.pc_confirm = None
        if not answered:
            self.ctx.confirm_notify(
                "No te escuché, así que mejor no lo hago. Si lo quieres, dímelo otra vez."
            )
            return False
        return bool(holder["result"])

    def do_confirm_ask(self, question: str):
        """Habla la pregunta de confirmación (siempre en el hilo principal)."""
        self.ctx.chat_set_status("Esperando tu «sí» o «no»…")
        self.ctx.say(question)

    def resolve_pc_confirmation(self, text: str):
        """Interpreta la respuesta del usuario a una confirmación pendiente."""
        pending = self.ctx.pc_confirm
        if pending is None:
            return
        self.ctx.user_float(text)
        verdict = self.parse_yes_no(text)
        if verdict is None:
            # No fue un sí/no claro: re-preguntamos y seguimos esperando.
            self.ctx.say("Perdona, solo dime «sí» o «no».")
            return
        if pending["event"].is_set():
            return  # ya resuelto (p. ej. dos respuestas seguidas)
        pending["holder"]["result"] = verdict
        pending["event"].set()
        self.ctx.chat_set_status("")
        # Un reconocimiento breve; el resultado final de la orden lo confirmará.
        self.ctx.say("Vale, lo hago." if verdict else "Vale, lo dejo así.")

    @staticmethod
    def parse_yes_no(text: str):
        """Devuelve True (sí), False (no) o None (no está claro)."""
        base = unicodedata.normalize("NFD", (text or "").lower())
        n = "".join(c for c in base if unicodedata.category(c) != "Mn")
        n = re.sub(r"[^a-z0-9 ]", " ", n)
        n = re.sub(r"\s+", " ", n).strip()
        if not n:
            return None
        tokens = n.split()
        # Incertidumbre explícita: no la tomamos como "no", volvemos a preguntar.
        if n in ("no se", "no lo se", "ni idea", "quiza", "quizas", "tal vez") \
                or n.startswith("no estoy segur"):
            return None
        no_frases = ("mejor no", "no lo hagas", "no quiero", "para nada", "ni se te ocurra")
        si_frases = ("de acuerdo", "por supuesto", "esta bien", "hazlo ya", "adelante con eso")
        no_palabras = {"no", "nel", "negativo", "cancela", "cancelar", "detente",
                       "dejalo", "olvidalo", "nop", "nope"}
        si_palabras = {"si", "sii", "sisi", "claro", "dale", "hazlo", "adelante",
                       "confirmo", "confirmado", "correcto", "vale", "ok", "okay",
                       "afirmativo", "sip", "hagalo", "procede", "eso"}
        # La negación tiene prioridad (más seguro ante ambigüedad).
        if any(f in n for f in no_frases) or (no_palabras & set(tokens)):
            return False
        if any(f in n for f in si_frases) or (si_palabras & set(tokens)):
            return True
        return None
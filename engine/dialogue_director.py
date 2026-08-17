"""DialogueDirector (PR 5, paso 10).

Dueño del NÚCLEO CONVERSACIONAL. Aqui vive el traspaso de dueño de los
canales `ctx.say` / `ctx.chat_set_status` (10.2) y el ROUTING de comandos
(10.1).

PUBLICA (wiring en su __init__):
- ctx.handle_command  -> self.handle_command    (10.1)
- ctx.say             -> self.say               (10.2; traspaso de dueño de _yue_say)
- ctx.chat_set_status -> self.set_status        (10.2)

LEE canales del host (publicados por EmotionOrchestrator/Controller/otros
directores; sin acoplarse director-director): avatar_emotion,
publish_companion_state, last_companion, last_user_text, speaker, listener,
chat, state_manager, memory, memory_proactive, memory_ext, episodic,
story_memory, brain, autonomy, autonomy_director, pc_director, pc,
teacher_director, media_director, media_companion, voice, vision_mp,
vision_director, vision_awareness (modulo opcional), camera, audio, modes,
wake_word_enabled, wellbeing_nudge_due.
"""
from __future__ import annotations

import config as _config
import re
import time

from core import emotion, bonding, safety, commands, personality
from core.pc_control import looks_like_pc_command
try:  # idem fallback del maestro (main.py:76-78)
    from core.vision import vision_awareness
except Exception:  # pragma: no cover - entorno sin vision v3
    vision_awareness = None
from core.state import Priority as _StatePriority
from engine.ai_worker import AiWorker
from engine.teacher_director import MODE_TEACHER

_PRIO_CONVERSACION = int(_StatePriority.CONVERSATION)  # 70


class DialogueDirector:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        # PUBLICACION (traspaso de dueno, NOTA §4.1): el dialogo
        # es dueno de decir y de mostrar estado; consumidores leen el mismo canal.
        self.ctx.handle_command = self.handle_command
        self.ctx.say = self.say
        self.ctx.chat_set_status = self.set_status
        # PUBLICACION (traspaso de dueño, NOTA §4.1): el prompt del sistema y
        # el fallback de IA fallida también son del dueño del diálogo; el
        # maestro ya no accede a sus privados (_system_prompt/_on_ai_failed).
        self.ctx.system_prompt = self._system_prompt
        self.ctx.ai_failed = self._on_ai_failed

    def say(self, text, user_context=None):
        # La emocion del avatar se infiere del texto CRUDO; el texto hablado va limpio.
        clean = emotion.clean_response(text)
        resultado = self.ctx.last_companion
        if resultado is not None:
            self.ctx.publish_companion_state(resultado)
        else:
            state = emotion.infer_conversation_state(
                user_context or getattr(self.ctx, "last_user_text", ""), text)
            self.ctx.avatar_emotion(state.name, state.intensity, state.duration_ms,
                                    priority=_PRIO_CONVERSACION, source="conversacion")
        mostrar = bool(getattr(_config, "CHAT_MOSTRAR_RESPUESTAS", False)) or not self.ctx.speaker.enabled
        if mostrar:
            self.ctx.chat.show_reply(clean)
        if self.ctx.speaker.enabled:
            self.ctx.listener.set_tts_text(clean)
        try:
            if self.ctx.state_manager is not None:
                self.ctx.state_manager.update_system(voice="speaking")
        except Exception:
            pass
        self.ctx.speaker.say(clean)

    def set_status(self, text):
        self.ctx.chat.set_status(text)


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
            self.ctx.chat_set_status("Reescaneando aplicaciones instaladas…")
            from engine.pc_worker import AppScanWorker
            worker = AppScanWorker(self.ctx.pc)
            worker.done.connect(lambda result: (
                self.ctx.chat_set_status(""),
                self.ctx.say(f"Catálogo actualizado: encontré {result.get('count', 0)} aplicaciones.")
            ))
            worker.failed.connect(lambda error: (
                self.ctx.chat_set_status(""),
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
    def _greet(self):
        current = self.ctx.system_context()
        message = (
            "Ah… ya llegaste. No es que te estuviera esperando. ¿Qué necesitas?"
            if current.index <= 1 else
            "Volviste. Bien… cuéntame qué haremos hoy."
        )
        self.ctx.say(message, "saludo")



    def _system_prompt(self, bond_level, directive=None):
        import config
        # NUEVO (memoria a largo plazo): recuerdo consolidado de hace tiempo. Se
        # suma a lo reciente (recent_messages/get_facts, intactos). Si aún no hay
        # ningún resumen o algo falla, no se añade nada y el prompt no cambia.
        recuerdo_largo = ""
        try:
            resumenes = self.ctx.memory.get_long_term_summaries(3)
            if resumenes:
                recuerdo_largo = ("\n\nDe hace tiempo recuerdas (memoria a largo "
                                  "plazo, úsalo solo si viene a cuento): "
                                  + " ".join(r.strip() for r in resumenes if r))
        except Exception:
            recuerdo_largo = ""
        # NUEVO (memoria emocional): recuerdo de fondo del ánimo reciente. Si algo
        # falla o aún no hay registro, queda vacío y el prompt no cambia.
        try:
            mood_summary = self.ctx.memory.get_mood_summary(7)
        except Exception:
            mood_summary = ""
        # NUEVO (bienestar): decide si toca el recordatorio discreto de apoyo
        # humano/profesional. A prueba de fallos: si algo falla, no se añade nada.
        try:
            wellbeing_nudge = self.ctx.wellbeing_nudge_due()
        except Exception:
            wellbeing_nudge = False
        prompt = personality.build_system_prompt(
            bond_level=bond_level,
            facts=self.ctx.memory.get_facts(),
            goals=self.ctx.memory.list_goals(),
            safety_directive=directive,
            mood_summary=mood_summary,
            wellbeing_nudge=wellbeing_nudge,
        )
        if bool(getattr(_config, "CAMERA_CONTEXT_IN_CHAT", True)):
            camera_context = self.ctx.camera.context_for_ai()
            if camera_context:
                prompt += (
                    "\n\nContexto visual local de cámara (sin identidad, sin fotos guardadas): "
                    + camera_context
                    + " No lo menciones salvo que sea pertinente o el usuario pregunte."
                )
            # NUEVO (conciencia de cámara): afirmación clara —como la del audio—
            # de que SÍ puede ver por la cámara cuando está activa. Evita que ante
            # una formulación libre ("¿alcanzas a verme?") el modelo diga que no.
            if vision_awareness is not None:
                try:
                    note = vision_awareness.camera_prompt_note(self.ctx.camera)
                    if note:
                        prompt += note
                except Exception:
                    pass
            # ADITIVO (visión avanzada): contexto breve y filtrado del motor de
            # percepción (texto leído, objetos, acción en curso, gesto nuevo,
            # estimación de ánimo). Solo entra lo reciente, estable y no
            # repetido; nunca listas largas ni coordenadas.
            if getattr(self.ctx, "vision_mp", None) is not None:
                try:
                    contexto_avanzado = self.ctx.vision_mp.context_for_ai()
                    if contexto_avanzado:
                        prompt += (
                            "\n\n" + contexto_avanzado
                            + "\n(Las estimaciones de ánimo son impresiones aproximadas, "
                              "nunca hechos. No diagnostiques ni afirmes emociones como "
                              "certezas. Menciona lo visual solo si viene a cuento.)"
                        )
                except Exception:
                    pass
        # NUEVO (percepción de pantalla v3): afirmación —como la de cámara y
        # audio— de que YUE SÍ puede mirar la PANTALLA cuando se le pregunta.
        # Respaldo para las preguntas que se escapen del regex de vision_look, y
        # así el modelo no invente "solo veo por la cámara". Fuera del gate de
        # CAMERA_CONTEXT_IN_CHAT porque la pantalla no depende de la cámara. Solo
        # afirma la CAPACIDAD de mirar, no contenido concreto.
        if vision_awareness is not None:
            try:
                nota_pantalla = vision_awareness.screen_prompt_note()
                if nota_pantalla:
                    prompt += nota_pantalla
            except Exception:
                pass
        if bool(getattr(_config, "AUDIO_REACT_CONTEXT_IN_CHAT", True)):
            audio_context = self.ctx.audio.context_for_ai()
            # Si ahora mismo no hay señal fresca, usamos lo que sonó hace poco.
            if not audio_context:
                try:
                    reciente, edad = self.ctx.audio.recent_content(max_age=150.0)
                    if reciente is not None:
                        seg = int(edad or 0)
                        audio_context = (
                            f"(hace unos {seg} s) " + reciente.summary_es()
                        )
                except Exception:
                    pass
            lyrics = ""
            try:
                lyrics = self.ctx.media_director.recent_lyrics()
            except Exception:
                lyrics = ""
            if audio_context or lyrics:
                extra = ""
                if lyrics:
                    extra = f" Algo de lo que oíste (letra/diálogo, con posibles errores): «{lyrics}»."
                prompt += (
                    "\n\nSÍ puedes oír el audio que sale del PC (análisis local, sin "
                    "grabar); nunca digas que no puedes escuchar. Lo que percibes: "
                    + (audio_context or "algo estuvo sonando hace poco")
                    + extra
                    + " Úsalo solo si viene a cuento o si el usuario pregunta qué escuchas."
                )
        # Contexto multimedia fusionado y memoria de gustos/títulos. El primero
        # describe lo que acompaña ahora; el segundo permite referencias como
        # «la canción de ayer» sin inventar títulos.
        try:
            media_now = self.ctx.media_companion.context_for_ai()
        except Exception:
            media_now = ""
        try:
            media_memory = self.ctx.memory.multimedia_context(5)
        except Exception:
            media_memory = ""
        if media_now or media_memory:
            prompt += (
                "\n\nContexto de acompañamiento multimedia: "
                + " ".join(part for part in (media_now, media_memory) if part)
                + " Reacciona como compañera, no como narradora; no inventes escenas, "
                  "títulos ni gustos que no figuren aquí."
            )

        # NUEVO (comprensión emocional v2): bloque estructurado con lo que YUE
        # ha entendido del usuario en este turno —qué siente, qué necesita, qué
        # NO quiere que haga y cómo debería acompañarle—. No es una respuesta
        # prefabricada: son parámetros de comportamiento. YUE sigue escribiendo
        # con su propia voz, y el bloque le prohíbe explícitamente repetir estas
        # etiquetas en voz alta.
        #
        # Va AL FINAL, después de la personalidad y el contexto, para que pese
        # sobre lo anterior cuando haya conflicto (p. ej. su carácter travieso
        # frente a un «déjame solo»).
        try:
            resultado = self.ctx.last_companion
            if resultado is not None and resultado.prompt_block:
                prompt += "\n\n" + resultado.prompt_block
        except Exception:
            pass

        # NUEVO (memoria episódica emocional): RECUERDOS CONCRETOS. Pocos y bien
        # elegidos —como máximo EPISODIC_MAX_CONTEXT—, priorizando lo que tiene
        # que ver con la conversación actual, lo que le pasa pronto y lo que
        # quedó pendiente. Va después del bloque de estado y antes del recuerdo
        # consolidado: es lo más específico que YUE sabe de él ahora mismo.
        #
        # El propio bloque le recuerda al modelo que son recuerdos, no datos que
        # recitar, y que no mencione registros ni bases de datos.
        try:
            if getattr(self.ctx, "episodic", None) is not None:
                bloque_episodico = self.ctx.episodic.context_block(
                    getattr(self.ctx, "last_user_text", "") or "")
                if bloque_episodico:
                    prompt += bloque_episodico
        except Exception as exc:
            print("[episodic] no pude añadir los recuerdos al contexto:", exc)

        # NUEVO (memoria narrativa): HISTORIAS relevantes. Va justo aquí, entre
        # los episodios y el retrieval histórico, y el orden es deliberado:
        #
        #   estado emocional  -> cómo está AHORA
        #   episodios         -> QUÉ le pasó (el punto)
        #   HISTORIAS         -> qué HILO une esos puntos  <-- aquí
        #   retrieval         -> qué dijo hace tiempo (texto suelto)
        #   consolidada       -> el resumen difuso de meses
        #
        # De lo más concreto y actual a lo más difuso y antiguo. Una historia es
        # más específica que un fragmento recuperado por parecido de palabras,
        # así que debe llegar ANTES para que pese más si hubiera conflicto; pero
        # es menos inmediata que el episodio concreto, así que va DESPUÉS de él.
        #
        # Como mucho entran STORY_MAX_CONTEXT (2 por defecto), y solo si el
        # mensaje toca la historia de verdad. A prueba de fallos.
        try:
            if getattr(self.ctx, "story_memory", None) is not None:
                bloque_historias = self.ctx.story_memory.context_block(
                    getattr(self.ctx, "last_user_text", "") or "")
                if bloque_historias:
                    prompt += bloque_historias
        except Exception as exc:
            print("[story-memory] no pude añadir las historias al contexto:", exc)

        # NUEVO (memoria histórica relevante): recuerdos del historial COMPLETO
        # que encajan con lo que el usuario acaba de decir. Es lo que permite
        # que YUE ate «Andrea volvió a escribirme» con aquella pelea de hace
        # meses, aunque ese mensaje quedara fuera de recent_messages() hace
        # mucho.
        #
        # Va después de los episodios (lo más concreto) y antes del recuerdo
        # consolidado (lo más difuso), que es el orden de menor a mayor
        # antigüedad. La capa excluye POR ID el mensaje actual y los últimos
        # MEMORY_RELEVANCE_SKIP_RECENT, así que nunca se "recuerda" a sí misma;
        # y si nada supera el umbral, devuelve "" y el prompt no cambia.
        #
        # A prueba de fallos: un problema aquí no puede impedir conversar.
        try:
            if getattr(self.ctx, "memory_ext", None) is not None:
                bloque_historico = self.ctx.memory_ext.context_block(
                    getattr(self.ctx, "last_user_text", "") or "")
                if bloque_historico:
                    prompt += bloque_historico
        except Exception as exc:
            print("[memory-ext] no pude añadir recuerdos del historial:", exc)

        # NUEVO (memoria a largo plazo): añadimos el recuerdo consolidado al final,
        # después del contexto de cámara/audio, sin tocar nada de lo anterior.
        if recuerdo_largo:
            prompt += recuerdo_largo
        return prompt



    def on_user_message(self, text):
        text = (text or "").strip()
        if not text:
            return

        # NUEVO (accesibilidad): si hay una confirmación de acción irreversible
        # esperando, este mensaje (voz o texto) es la respuesta sí/no. Se resuelve
        # aquí y no sigue por el flujo normal.
        if self.ctx.pc_confirm is not None:
            self.ctx.pc_director.resolve_pc_confirmation(text)
            return

        # NUEVO (palabra de activación «Yue»): YUE solo atiende una frase (por voz o
        # por texto) si empieza por su nombre. Se exceptúan: los comandos con «/», y
        # cuando está esperando la respuesta a su propio check-in de ánimo (esa
        # respuesta te la pidió ella). Las confirmaciones sí/no ya salieron arriba.
        # NO toca el sistema de escuchar música/vídeo: solo filtra mensajes del usuario.
        if (getattr(self.ctx, "wake_word_enabled", True)
                and not getattr(self.ctx, "pending_checkin", False)
                and not text.startswith("/")):
            stripped = self.ctx.voice.strip_wake_word(text)
            if stripped is None:
                self.ctx.voice.wake_ignored(text)
                return
            if not stripped:
                # Solo dijo/escribió «Yue» sin nada más: acusa recibo y espera.
                self.ctx.interrupt_response()
                self.ctx.say("¿Sí? Dime.")
                return
            text = stripped

        self.ctx.interrupt_response()
        request_id = self.ctx.chat_request_id
        self.ctx.last_user_activity = time.time()
        self.ctx.last_user_text = text
        self.ctx.user_float(text)

        # Aprende gustos explícitos sobre lo último escuchado/visto. Nunca guarda
        # audio, vídeo ni frames: solo actualiza la afinidad del título reciente.
        try:
            self.ctx.memory.learn_multimedia_preference(text)
        except Exception as exc:
            print("[media-memory] no pude aprender la preferencia:", exc)

        # Resuelve referencias temporales antes del enrutador de comandos. Por
        # ejemplo «pon la canción de ayer» se convierte en una orden concreta.
        try:
            remembered = self.ctx.memory.resolve_multimedia_reference(text)
        except Exception:
            remembered = None
        if remembered and re.search(
                r"\b(pon|poner|reproduce|reproducir|vuelve a poner|abre)\b",
                text, re.IGNORECASE):
            platform = "spotify" if (
                "spotify" in str(remembered.get("source", "")).lower()
                or "spotify" in str(remembered.get("app", "")).lower()
            ) else "youtube"
            text = f"pon {remembered['title']} en {platform}"

        # NUEVO (/animo): si hay un check-in de ánimo esperando respuesta, este
        # mensaje puede ser la nota del 1 al 5. Si lo es, lo guardamos y cerramos
        # aquí; si no, cancelamos el check-in sin insistir y el mensaje sigue su
        # curso normal (así el usuario nunca queda atrapado en la pregunta).
        if self.ctx.pending_checkin:
            if self.ctx.memory_proactive.resolve_mood_checkin(text):
                return

        self.ctx.autonomy.learn(text, source="user")

        # NUEVO (comprensión emocional v2): UNA sola pasada entiende el estado
        # afectivo, lo que el usuario necesita, el nivel de riesgo, cómo debe
        # acompañarle YUE y qué cara poner. El resultado se guarda para que
        # _system_prompt() y el registro de memoria lo reutilicen sin repetir
        # trabajo ni volver a llamar al modelo.
        #
        # Si el cerebro afectivo no está disponible, se cae al camino clásico
        # (infer_reaction_to_user) exactamente como antes.
        self.ctx.last_companion = None
        if getattr(self.ctx, "brain", None) is not None:
            try:
                contexto_reciente = self.ctx.memory.recent_messages(4)
            except Exception:
                contexto_reciente = None
            try:
                self.ctx.last_companion = self.ctx.brain.process(
                    text,
                    recent_messages=contexto_reciente,
                    external_signal=self.ctx.memory_proactive.external_mood_signal(),
                )
            except Exception as exc:
                print("[affect] fallo el análisis afectivo, sigo con el clásico:", exc)
                self.ctx.last_companion = None

        if self.ctx.last_companion is not None:
            resultado = self.ctx.last_companion
            # NUEVO (cerebro central): el análisis se reparte entre los DOS
            # estados que antes estaban mezclados. Lo que le pasa al usuario va
            # a USER STATE; cómo reacciona YUE va como PROPUESTA al arbitraje.
            # La cara de YUE es una RESPUESTA a lo que le pasa al usuario, no
            # una imitación: un usuario furioso no pone a YUE furiosa.
            self.ctx.publish_companion_state(resultado)
            if bool(getattr(_config, "AFFECT_DEBUG", False)):
                print("[affect]", resultado.summary)
                try:
                    if self.ctx.state_manager is not None:
                        print("[estado]", self.ctx.state_manager.global_state().summary())
                except Exception:
                    pass
            # Memoria afectiva ESTRUCTURADA: se guarda la lectura, nunca el
            # mensaje (ese ya está en 'messages'; duplicarlo era copiar
            # información privada sin ganar nada). Se mantiene además la
            # entrada clásica en mood_log —sin texto— para que el resumen
            # semanal y la señal de ánimo sostenido sigan funcionando igual.
            try:
                afecto = resultado.affect
                self.ctx.memory.add_affect(
                    emotion=str(afecto.primary_emotion),
                    secondary_emotion=str(afecto.secondary_emotion or ""),
                    valence=afecto.valence, arousal=afecto.arousal,
                    distress=afecto.distress,
                    support_need=str(resultado.decision.mode),
                    confidence=afecto.confidence,
                    sarcasm=afecto.sarcasm_probability,
                    safety_level=int(resultado.safety.level),
                    trigger=afecto.possible_trigger, source="texto")
                if str(afecto.primary_emotion) != "neutral":
                    self.ctx.memory.add_mood("texto", str(afecto.primary_emotion),
                                         max(abs(afecto.valence), afecto.arousal),
                                         None)
            except Exception as exc:
                print("[mood] no pude registrar el ánimo del texto:", exc)
        else:
            # --- Camino CLÁSICO (retrocompatibilidad total) ------------------
            reaction = emotion.infer_reaction_to_user(text)
            self.ctx.avatar_emotion(reaction.name, reaction.intensity,
                                 reaction.duration_ms,
                                 priority=_PRIO_APOYO, source="apoyo_usuario")
            try:
                mood = emotion.infer_emotion_state(text)
                if mood.name != "neutral":
                    # Sin texto: la migración de privacidad ya no lo duplica.
                    self.ctx.memory.add_mood("texto", mood.name, mood.intensity, None)
            except Exception as exc:
                print("[mood] no pude registrar el ánimo del texto:", exc)
        self.ctx.chat.ensure_input_ready(focus=False)

        if text.startswith("/"):
            self.ctx.handle_command(text)
            return

        # NUEVO: enrutador de modos (punto ÚNICO de decisión). Detecta si el
        # mensaje activa o desactiva un modo (companion/teacher/…) por voz o texto.
        if self.ctx.modes is not None:
            route = self.ctx.modes.handle(text)
            if route.switched:
                self.ctx.apply_mode_switch(route)
                return

        # Los comandos de control (callar, mirar pantalla, micro…) funcionan en
        # CUALQUIER modo: se comprueban antes de repartir la conversación.
        action, arg = commands.match(text)
        if action:
            self.ctx.autonomy_director.run_internal_action(action, arg)
            return

        # NUEVO (conciencia de cámara): preguntas directas "¿puedes verme?",
        # "¿me ves?", "¿me estás viendo?"… no las cubría commands.match, así que
        # caían al LLM y respondía "no puedo verte". Aquí se responden de forma
        # determinista según el estado real de la cámara. A prueba de fallos.
        if vision_awareness is not None:
            try:
                if vision_awareness.asks_if_can_see(text):
                    self.ctx.say(vision_awareness.answer_can_see(self.ctx.camera))
                    return
            except Exception as exc:
                print("[vision] no pude resolver «¿puedes verme?»:", exc)

        # ADITIVO (visión avanzada): órdenes habladas o escritas sobre la cámara
        # —"lee este texto", "describe mi habitación", "¿qué estoy haciendo?",
        # "modo privacidad", "olvida lo que viste"…—. Funciona igual desde el
        # micrófono y desde el chat porque solo trabaja con texto. Si el sistema
        # está apagado o la frase no es una orden de visión, devuelve None y el
        # flujo sigue exactamente como antes.
        if getattr(self.ctx, "vision_mp", None) is not None:
            try:
                respuesta_vision = self.ctx.vision_mp.handle_voice(text)
                if respuesta_vision:
                    self.ctx.say(respuesta_vision)
                    return
            except Exception as exc:
                print("[vision] no pude procesar la orden de visión:", exc)

        # NUEVO: si hay un modo especial activo, la conversación va a su motor.
        # (Companion es el modo por defecto y sigue el flujo de abajo, intacto.)
        if self.ctx.modes is not None and self.ctx.modes.current_mode() == MODE_TEACHER:
            self.ctx.teacher_director.teacher_process(text)
            return

        if looks_like_pc_command(text):
            self.ctx.pc_director.run_pc_order(text)
            return

        self.ctx.memory.add_message("user", text)
        points = self.ctx.memory.add_bond_points(bonding.points_for_message(text))
        current, _, _ = bonding.progress(points)
        self.ctx.chat.set_bond(current)

        # NUEVO (memoria episódica emocional): aquí es donde toca mirarlo, y no
        # antes. En este punto ya han quedado fuera los comandos, los modos y
        # las órdenes de PC (que no son acontecimientos de su vida), el análisis
        # afectivo YA está hecho —así que no se vuelve a analizar la emoción— y
        # el mensaje ya tiene id en la base.
        #
        # Se lanza en segundo plano: la extracción puede necesitar una consulta
        # al modelo y no vamos a congelar la interfaz por un recuerdo. El
        # episodio estará listo para el turno siguiente y para el seguimiento,
        # que es donde de verdad importa.
        self.ctx.memory_proactive.episodic_observe(text)

        # Riesgo por TEXTO. Con el cerebro afectivo, la evaluación ya viene
        # hecha y es GRADUADA (NINGUNO/LEVE/MODERADO/ALTO/CRÍTICO) porque usa
        # core.safety_ext, que hasta ahora estaba escrito pero sin conectar. Se
        # conservan los niveles, la negación, los factores protectores y el
        # contexto: nada se reduce a un booleano.
        #
        # Si el cerebro no está, se usa el detector clásico exactamente igual
        # que antes.
        directive = None
        if self.ctx.last_companion is not None:
            resultado = self.ctx.last_companion
            if resultado.should_log_risk_event:
                try:
                    self.ctx.memory.add_risk_event()
                except Exception as exc:
                    print("[bienestar] no pude registrar el evento de riesgo:", exc)
            if resultado.safety_directive:
                directive = resultado.safety_directive
            elif self.ctx.memory_proactive.should_check_visual_risk():
                directive = safety.safety_directive_visual(_config.CRISIS_RESOURCES)
        elif safety.detect_risk(text):
            # Deja constancia (solo la hora) para el recordatorio de bienestar:
            # detectar un patrón de riesgo repetido sin conservar nada sensible.
            try:
                self.ctx.memory.add_risk_event()
            except Exception as exc:
                print("[bienestar] no pude registrar el evento de riesgo:", exc)
            directive = safety.safety_directive(_config.CRISIS_RESOURCES)
        elif self.ctx.memory_proactive.should_check_visual_risk():
            directive = safety.safety_directive_visual(_config.CRISIS_RESOURCES)
        else:
            directive = None
        system = self.ctx.system_prompt(current, directive)
        messages = [{"role": "system", "content": system}] + self.ctx.memory.recent_messages(12)

        self.ctx.chat.set_status("Yue está pensando…")
        worker = AiWorker(self.ctx.engine, messages)
        worker.done.connect(lambda answer, rid=request_id, user=text: self._on_ai_done(rid, user, answer))
        worker.failed.connect(lambda error, rid=request_id: self._on_ai_failed(rid, error))
        self.ctx.workers.track(worker)



    def _on_ai_done(self, request_id, user_text, text):
        if request_id != self.ctx.chat_request_id:
            return
        self.ctx.chat.set_status("")
        clean = emotion.clean_response(text)
        self.ctx.memory.add_message("assistant", clean)
        # NUEVO (memoria episódica): ahora SÍ se sabe qué hizo YUE en el
        # episodio que se acaba de detectar («tranquilizó», «practicaron
        # preguntas»…). Se anota como etiqueta corta, nunca la respuesta entera.
        self.ctx.memory_proactive.episodic_note_response(clean)
        # NUEVO (memoria narrativa): la misma etiqueta corta se anota también en
        # los acontecimientos de historia que se acaban de tocar, para que la
        # historia guarde la parte de YUE y no solo la de él.
        self.ctx.memory_proactive.story_note_response(clean)
        # Pasamos el texto CRUDO: el canal ctx.say (DialogueDirector) limpia para
        # hablar, pero infiere la
        # emoción del avatar con toda la señal del modelo (no doble-limpiada).
        self.ctx.say(text, user_text)



    def _on_ai_failed(self, request_id, error):
        if request_id != self.ctx.chat_request_id:
            return
        self.ctx.chat.set_status("")
        self.ctx.say("No logro conectarme con mi motor de IA. Revisa la clave de Groq y tu conexión.")
        print("[IA] error:", error)


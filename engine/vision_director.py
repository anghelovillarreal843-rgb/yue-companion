"""VisionDirector (PR 5, paso 7).

Extrae de Controller el paquete de visión: la mirada explícita de pantalla
(_glance), sus workers y respuestas, el diagnóstico y el preflight, el estado
del sistema de visión por cámara, el pipeline de cámara local (observación,
ánimo, espejo empático y el comentario emocional racionado) y los resúmenes
vivos de la visión (vision_state/vision_resumen).

Cierra la deuda de PR 4 y del paso 3 (§4.1 fila 5): VisionHostAdapter ya NO
envuelve a Controller con un __getattr__; envuelve a VisionDirector, y cada
dependencia llega por controller_ctx (patrón WorkerRegistry):
chat, teacher, pet, say, _on_vision_status, observe_emotion, flags _*_busy y
_yue_may_take_initiative.

También toma la posesión del registro de cámara mutable (paso 3): crea el
CameraObserver y publica ctx.camera en setup_camera, y vuelve a publicarlo
tras la migración a la percepción nueva (migrate_camera). MemoryProactive y la
seguridad siguen leyendo siempre ctx.camera.

Direcciones del paquete:
- main -> director (público): setup_camera(callbacks), migrate_camera(...),
  glance(pregunta), diagnose_vision(), vision_preflight(), on_camera_status,
  on_camera_observation. Los bridges del host conectan sus señales a los
  métodos on_camera_* .
- director -> ctx (dependencias): chat, pet, engine, speaker, audio, memory,
  modes, state_manager, memory_proactive, workers, camera (propio), say,
  system_prompt, system_context, ai_failed.
- ctx -> director (estado compartido get/set): vision_on, vision_busy, pc_busy,
  autonomy_busy, chat_request_id. Publica en ctx: camera, vision_mp, glance.
"""
from __future__ import annotations

import time

from contracts.vision_host import VisionHost
from PyQt5.QtCore import QTimer

from core import emotion, safety
from core.camera_observer import CameraObserver, CameraObservation
from engine.ai_worker import AiWorker
from engine.teacher_director import MODE_TEACHER
from engine.vision_worker import (OcrVisionWorker, VisionDiagWorker,
                                  VisionPreflightWorker, VisionWorker)


def _cfg(name: str, default):
    """Lee config sin conocer a Controller (mismo patrón que PCDirector)."""
    try:
        import config  # toplevel del proyecto
        return getattr(config, name, default)
    except Exception:
        return default


# Prioridad de la propuesta del espejo empático (equivale a Priority.MEDIA=40).
_PRIO_MEDIA = 40

_CAMARA_VA = {
    "happy": (0.7, 0.6), "sad": (-0.7, 0.25), "angry": (-0.6, 0.85),
    "fear": (-0.6, 0.8), "surprise": (0.15, 0.8), "disgust": (-0.5, 0.5),
    "tired": (-0.35, 0.15), "neutral": (0.0, 0.3),
}


def _valencia_activacion_camara(clave):
    """(valencia, activación) para una etiqueta facial. Neutro si no se conoce."""
    return _CAMARA_VA.get(str(clave or "").strip().lower(), (0.0, 0.3))


class VisionDirector:
    """Estado interno: _last_screen_observation, llaves/tiempos del espejo
    empático y del comentario emocional (todo aditivo de cámara).

    Estado compartido (en ctx, get/set): vision_on, vision_busy, pc_busy,
    autonomy_busy. Componente propio publicado en ctx: camera (registro
    mutable). Callbacks del host: ctx.say, ctx.system_prompt,
    ctx.system_context (_refresh_bond).
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._camera = None
        self._status_callback = None
        self._last_screen_observation = None
        self._last_empathy_key = ""
        self._last_empathy_at = 0.0
        self._last_mood_cam_key = ""
        self._last_mood_cam_at = 0.0
        self._emotion_talk_pending = False
        self._emotion_talk_streak = 0
        self._emotion_talk_streak_key = ""
        self._last_emotion_talk_at = 0.0
        self._last_emotion_talk_key = ""
        self._last_emotion_talk_same_at = 0.0

    # ---------- cámara local (registro mutable, paso 3) ----------
    def setup_camera(self, observation_callback, status_callback):
        """Crea el CameraObserver y publica el registro en ctx.camera.

        Los callbacks son del host (los emite su CameraBridge); el arranque
        físico lo decide el host llamando ctx.camera.start() en su init.
        """
        self._status_callback = status_callback
        self._camera = CameraObserver(
            observation_callback=observation_callback,
            status_callback=status_callback,
        )
        # PR 5.3: el registro de cámara vive en el ctx; MemoryProactive y la
        # seguridad lo leen SIEMPRE por ctx, nunca por referencia guardada.
        self.ctx.camera = self._camera
        # El director publica también el estado "visión en pausa" (get/set).
        self.ctx.vision_on = bool(_cfg("VISION_ENABLED", True))
        # Recableo PR 5.7: el viejo puente del maestro (ctx.glance) apuntaba a
        # _glance; ahora lo publica el director para que cualquier consumidor
        # (incluido el puente sin rótulo) termine aquí.
        self.ctx.glance = self.glance

    def migrate_camera(self, perception, context_max_age=8.0):
        """Sustituye el registro mutable por el adaptador de la percepción
        nueva (VISION_REPLACE_LEGACY) y vuelve a publicarlo en ctx.camera."""
        from vision.legacy_adapter import LegacyCameraObserverAdapter
        try:
            self._camera.stop()          # libera la webcam del observador clásico
        except Exception:
            pass
        self._camera = LegacyCameraObserverAdapter(
            perception,
            status_callback=self._status_callback,
            context_max_age=float(context_max_age),
        )
        self.ctx.camera = self._camera
        self._camera.start()

    def check_legacy_migration(self):
        """Config-guard de VISION_REPLACE_LEGACY (punto 16 del pedido): si el
        observador clásico sigue activo y la percepción nueva está enganchada,
        sustituye el registro por el adaptador. El host lo llama cuando el
        sistema de percepción ya está publicado en ctx.vision_mp."""
        if self._camera is None:
            return
        if not _cfg("VISION_REPLACE_LEGACY", False):
            return
        perception = getattr(self.ctx.vision_mp, "perception", None)
        if perception is None:
            return
        try:
            self.migrate_camera(perception)
            print("[vision-mp] migración activa: CameraObserver -> adaptador de percepción.")
        except Exception as exc:
            print("[vision-mp] no pude activar el adaptador de migración:", exc)

    # ---------- mirar la pantalla ----------
    def glance(self, pregunta: str = ""):
        """Observación explícita; la visión automática permanece silenciosa.

        `pregunta` es la frase original del usuario ("¿qué error aparece?",
        "explícame este gráfico"…). Se pasa entera al analizador para que el
        modelo priorice lo que de verdad se preguntó.
        """
        # Es una petición explícita del usuario: si la visión estaba en pausa, la
        # reactivamos para poder mirar ahora mismo.
        if not self.ctx.vision_on:
            self.ctx.vision_on = True
        if self.ctx.vision_busy or self.ctx.pc_busy or self.ctx.chat.is_user_composing():
            return
        current = self.ctx.system_context()
        instruction = (
            "Mira la pantalla actual y responde en español con una observación útil y breve. "
            "Ignora las ventanas del propio avatar de Yue. No enumeres todo lo visible; "
            "menciona solo lo relevante para la petición explícita del usuario."
        )
        self.ctx.vision_busy = True
        self.ctx.chat_set_status("Yue está mirando tu pantalla…")
        # ADITIVO: si se pidió visión-solo-OCR de forma EXPLÍCITA, seguimos con el
        # camino OCR de siempre. En cualquier otro caso usamos el flujo completo
        # (captura -> clasificación -> VisionRouter -> fallback OCR), que YA
        # incluye el OCR: si toda la visión multimodal se cae, YUE igual cuenta
        # qué texto hay en pantalla en vez de decir "no puedo ver".
        if bool(_cfg("VISION_OCR_ONLY", False)):
            instruccion_ocr = (
                "Vas a mirar la pantalla del usuario a través del texto que hay en "
                "ella (leído por OCR). Responde en español, breve y útil, a su "
                "petición. Ignora menús o barras del sistema si no vienen a cuento. "
                "Si el texto no basta para responder, dilo con naturalidad."
            )
            worker = OcrVisionWorker(self.ctx.engine, self.ctx.system_prompt(current), instruccion_ocr)
        else:
            worker = VisionWorker(
                self.ctx.engine, self.ctx.system_prompt(current), instruction,
                question=(pregunta or instruction),
                # Una petición explícita SIEMPRE captura de nuevo: nunca se
                # responde con una observación vieja cuando el usuario dice
                # "mira mi pantalla" o "qué ves ahora".
                force_fresh=True,
            )
            worker.observed.connect(self.on_screen_observed)
        worker.done.connect(self.on_vision_done)
        worker.failed.connect(self.on_vision_failed)
        self.ctx.workers.track(worker)

    def on_screen_observed(self, obs):
        """Guarda la última observación estructurada de pantalla.

        La capa conversacional puede consultarla (por ejemplo /diagvision, o para
        dar contexto al chat) sin volver a capturar.
        """
        self._last_screen_observation = obs
        try:
            print("[VISION] " + obs.resumen_log())
        except Exception:
            pass

    def on_vision_done(self, text):
        self.ctx.vision_busy = False
        self.ctx.chat_set_status("")
        if text:
            self.ctx.say(text)
        else:
            self.ctx.say("Miré la pantalla pero no obtuve una descripción. ¿Lo intento de nuevo?")

    def on_vision_failed(self, error):
        self.ctx.vision_busy = False
        self.ctx.chat_set_status("")
        # Antes esto solo se imprimía y parecía que "la visión no funciona".
        print("[vision] error:", error)
        msg = str(error or "")
        low = msg.lower()
        if any(k in low for k in ("ocr", "tesseract")):
            hablado = ("No encontré texto legible en la pantalla. Si querías que "
                       "leyera algo con texto, ábrelo en primer plano; y si no tengo "
                       "OCR instalado, hará falta Tesseract.")
        elif any(k in low for k in ("api", "key", "clave", "auth", "401", "403")):
            hablado = "No pude usar mi visión: revisa la clave del modelo de visión en la configuración."
        elif any(k in low for k in ("mss", "imagegrab", "pillow", "capturar", "captur", "screenshot", "display", "grab", "no module")):
            hablado = "No pude capturar la pantalla; puede faltar una librería o un permiso de captura."
        elif any(k in low for k in ("negra", "black")):
            hablado = ("La captura salió completamente negra. Suele pasar con vídeo "
                       "protegido o con la aceleración por hardware; prueba a "
                       "desactivarla en esa aplicación.")
        elif any(k in low for k in ("antigua", "frame_age")):
            hablado = "La captura llegó demasiado tarde; déjame intentarlo otra vez."
        elif any(k in low for k in ("todos los modelos visuales", "cuota", "quota", "rate limit", "429")):
            hablado = ("Mis modelos visuales están sin cupo ahora mismo y tampoco "
                       "encontré texto legible en la pantalla.")
        elif any(k in low for k in ("model", "modelo", "decommission", "not found", "404", "400", "unsupported", "image")):
            hablado = "El modelo de visión rechazó la imagen. Puede que el modelo ya no exista o no acepte imágenes."
        else:
            hablado = "Ahora mismo no pude ver la pantalla."
        self.ctx.say(hablado)
        # Mostramos SIEMPRE el detalle técnico en el chat (aunque YUE esté en modo
        # solo-voz), para poder diagnosticarlo. Es un mensaje de sistema.
        try:
            self.ctx.chat.show_reply("⚠️ Detalle de visión: " + msg[:400])
        except Exception:
            pass

    # ---------- diagnóstico y preflight ----------
    def diagnose_vision(self):
        """Prueba captura y modelo por separado y muestra el resultado en el chat."""
        try:
            self.ctx.chat.show_reply("🔎 Diagnóstico de visión en curso… (captura y modelo)")
        except Exception:
            pass
        self.ctx.chat_set_status("Diagnosticando la visión…")
        worker = VisionDiagWorker(self.ctx.engine)
        worker.done.connect(self.on_vision_diag)
        self.ctx.workers.track(worker)

    def on_vision_diag(self, report):
        self.ctx.chat_set_status("")
        lineas = ["🔎 Diagnóstico de visión:"]
        todo_ok = True
        for nombre, ok, detalle in report:
            lineas.append(f"{'✅' if ok else '❌'} {nombre}: {detalle}")
            todo_ok = todo_ok and ok
        try:
            self.ctx.chat.show_reply("\n".join(lineas))
        except Exception:
            pass
        if todo_ok:
            self.ctx.say("Mi visión funciona bien. Ya puedo mirar tu pantalla.")
        else:
            self.ctx.say("Encontré el problema de mi visión; te dejé el detalle en el chat.")

    def vision_preflight(self):
        """Al arrancar, valida en segundo plano la clave de visión y avisa claro
        si el proveedor la rechaza. No bloquea la interfaz ni gasta tokens."""
        # Si la visión es solo-OCR, no usamos modelo multimodal: la clave da igual.
        if _cfg("VISION_OCR_ONLY", False):
            return
        if not _cfg("VISION_PREFLIGHT", True):
            return
        if not _cfg("VISION_ENABLED", True):
            return
        worker = VisionPreflightWorker(self.ctx.engine)
        worker.done.connect(self.on_vision_preflight)
        self.ctx.workers.track(worker)

    def on_vision_preflight(self, res):
        res = res or {}
        host = res.get("host", "?")
        model = res.get("model", "?")
        detail = res.get("detail", "")
        if res.get("ok"):
            print(f"[vision] preflight OK · host={host} · modelo={model}")
            return
        reason = res.get("reason", "")
        if reason == "disabled":
            return  # el usuario apagó la visión a propósito (VISION_PROVIDER=none)
        print(f"[vision] preflight FALLÓ · motivo={reason} · host={host} · {detail}")
        if reason == "invalid_key":
            aviso = (
                f"⚠️ Mi visión está apagada: el proveedor «{host}» rechazó la clave "
                "(401/403 – clave inválida o sin acceso al modelo). Pon una clave "
                f"VÁLIDA en VISION_API_KEY dentro del .env y reiníciame. "
                f"Detalle del proveedor: {detail}"
            )
        elif reason in ("no_key", "no_base"):
            aviso = (
                "⚠️ Mi visión no tiene credenciales. En el .env define VISION_PROVIDER "
                "(together/openai/groq), VISION_API_KEY y VISION_BASE_URL de tu "
                "proveedor de visión, y reiníciame."
            )
        elif reason == "unreachable":
            aviso = (
                f"⚠️ No pude contactar al proveedor de visión «{host}» (red o host "
                f"caído). Revisa tu conexión. Detalle: {detail}"
            )
        else:
            aviso = f"⚠️ Mi visión no está lista ({reason or 'desconocido'}). Detalle: {detail}"
        try:
            self.ctx.chat.show_reply(aviso)
        except Exception:
            pass

    # ---------- estado del sistema de visión por cámara ----------
    def on_vision_status(self, text, active):
        """Estado del sistema de visión por cámara (llega en el hilo de la UI).

        Aditivo y silencioso: solo registra en consola. Si quieres verlo en el
        chat, cambia el print por self.ctx.say(text).
        """
        try:
            print(f"[vision-mp] {'ON' if active else 'off'}: {text}")
        except Exception:
            pass
        # ADITIVO: el indicador de cámara del avatar sigue el estado real de la
        # visión nueva, no solo el del observador clásico.
        try:
            if self.ctx.pet is not None:
                self.ctx.pet.set_camera_active(bool(active))
        except Exception:
            pass

    def on_camera_status(self, message, active):
        self.ctx.pet.set_camera_active(bool(active))
        print(f"[camara] estado: {message}")

    def on_camera_observation(self, observation):
        # El dato se conserva dentro de CameraObserver. No se narra en el chat.
        if not isinstance(observation, CameraObservation):
            return
        # NUEVO (memoria emocional): guardamos en segundo plano como se ve a la
        # persona. SOLO la etiqueta y la hora: nunca fotos ni datos que la
        # identifiquen. Es independiente del espejo empatico de abajo, para que
        # el recuerdo se forme aunque YUE este hablando u ocupada.
        self._log_camera_mood(observation)
        # NUEVO (YUE habla al ver tu emoción): si detecta una emoción MUY fuerte y
        # sostenida, o distrés sostenido (peligro), te lo comenta en voz y te
        # pregunta por qué estás así. Muy racionado (no habla a cada gesto). Va
        # ANTES del espejo empático (que solo pone cara) y es independiente de él.
        try:
            self._maybe_camera_emotion_checkin(observation)
        except Exception as exc:
            print("[camara] fallo evaluando el comentario emocional:", exc)
        # ESPEJO EMPÁTICO (reescrito): la cámara pasa de DECIDIR a REPORTAR.
        #
        # Antes esto llamaba a `_set_avatar_emotion(...)` directamente, así que
        # una cara mal leída cambiaba el estado de YUE sin que nada pudiera
        # contradecirla. Ahora la cámara solo dice:
        #
        #     "creo que el usuario parece triste, con confianza 0.63"
        #
        # y el gestor decide. Si el usuario acaba de ESCRIBIR que está triste,
        # el texto (peso 1.00) manda sobre la cámara (peso 0.55) y no hay
        # conflicto. Y si el usuario dice que está bien mientras la cámara ve
        # agotamiento, eso queda como emoción SECUNDARIA: no se tira el dato,
        # pero tampoco se le lleva la contraria a la persona.
        if not bool(_cfg("CAMERA_EMPATHY_ENABLED", True)):
            return
        emociones = getattr(observation, "person_emotions", ()) or ()
        if not emociones:
            return
        # Nos quedamos con la emoción no neutra de mayor confianza.
        fuertes = [e for e in emociones if getattr(e, "key", "neutral") != "neutral"]
        if not fuertes:
            return
        mejor = max(fuertes, key=lambda e: getattr(e, "confidence", 0.0))
        confianza = float(getattr(mejor, "confidence", 0.0))
        if confianza < float(_cfg("CAMERA_EMPATHY_MIN_CONFIDENCE", 0.55)):
            return

        # Cuentagotas: solo al cambiar de ánimo y no más de una vez cada X seg.
        # La cámara analiza ~una vez por segundo; sin esto inundaría la fusión.
        ahora = time.time()
        ultima_key = self._last_empathy_key
        ultima_at = self._last_empathy_at
        gap = float(_cfg("CAMERA_EMPATHY_MIN_GAP", 8.0))
        if mejor.key == ultima_key and (ahora - ultima_at) < gap:
            return
        self._last_empathy_key = mejor.key
        self._last_empathy_at = ahora

        # 1) OBSERVACIÓN: siempre se reporta, gobierne quien gobierne la cara.
        #    Que la música esté sonando no significa que YUE deba dejar de
        #    ENTERARSE de cómo está la persona; solo que no debe cambiar de cara.
        gestor = self.ctx.state_manager
        if gestor is not None:
            try:
                valencia, activacion = _valencia_activacion_camara(mejor.key)
                gestor.observe_emotion(
                    "camera", str(mejor.key), confianza,
                    valence=valencia, arousal=activacion,
                    explicit=False, detail="lectura facial")
            except Exception as exc:
                print("[camara] no pude registrar la observación:", exc)

        # 2) PROPUESTA: solo si la cara está libre. Se mantienen las mismas
        #    guardas de antes (multimedia, órdenes en curso, YUE hablando).
        try:
            if (getattr(self.ctx.audio, "media_playing", False) or self.ctx.pc_busy
                    or self.ctx.vision_busy or self.ctx.speaker.is_speaking):
                return
        except Exception:
            pass
        try:
            from core import face_emotion
            espejo = face_emotion.empathic_avatar_emotion(mejor.key)
        except Exception:
            espejo = None
        if not espejo:
            return
        nombre, inten, dur = espejo
        try:
            self.ctx.avatar_emotion(nombre, inten, dur,
                                    priority=_PRIO_MEDIA, source="camara")
        except Exception as exc:
            print("[camara] no pude proponer el espejo empático:", exc)

    def _log_camera_mood(self, observation):
        """Registra en mood_log la emoción NO neutra leída por la cámara.

        Privacidad por diseño: NO se guarda ninguna imagen ni dato identificable,
        solo la etiqueta emocional, su confianza y la hora. Lleva su propio
        cuentagotas para que el historial tenga sentido sin inflarse fotograma a
        fotograma (la cámara analiza ~una vez por segundo).
        """
        try:
            emociones = getattr(observation, "person_emotions", ()) or ()
            fuertes = [e for e in emociones if getattr(e, "key", "neutral") != "neutral"]
            if not fuertes:
                return
            mejor = max(fuertes, key=lambda e: getattr(e, "confidence", 0.0))
            conf = float(getattr(mejor, "confidence", 0.0))
            if conf < float(_cfg("CAMERA_MOOD_MIN_CONFIDENCE", 0.5)):
                return
            # Cuentagotas propio (independiente del espejo empático): registra al
            # cambiar de emoción o cuando pasó suficiente tiempo del mismo ánimo.
            ahora = time.time()
            ultima_key = self._last_mood_cam_key
            ultima_at = self._last_mood_cam_at
            gap = float(_cfg("CAMERA_MOOD_MIN_GAP", 45.0))
            if mejor.key == ultima_key and (ahora - ultima_at) < gap:
                return
            self._last_mood_cam_key = mejor.key
            self._last_mood_cam_at = ahora
            self.ctx.memory.add_mood("camara", mejor.key, conf, None)
        except Exception as exc:
            print("[mood] no pude registrar el ánimo de la cámara:", exc)

    # ---------- YUE HABLA al ver tu emoción por la cámara ----------
    def _maybe_camera_emotion_checkin(self, observation):
        """Si YUE ve una emoción MUY fuerte y sostenida —o distrés sostenido, que
        tratamos como "peligro"—, te lo comenta EN VOZ y te pregunta por qué estás
        así, con su propio tono.

        Muy racionado a propósito (la petición era: que NO hable a cada gesto):
          · La emoción tiene que ser fuerte (confianza alta) Y mantenerse varias
            lecturas seguidas (racha), no un gesto de un segundo.
          · Cuentagotas global (no habla más de una vez cada pocos minutos) y por
            misma emoción (no repite el mismo ánimo hasta bastante después).
          · El "peligro" (distrés sostenido de varios minutos) tiene prioridad: usa
            un tono más cuidadoso y salta aunque no se cumpla la racha corta.
          · Respeta todos los cortes de cortesía existentes: no pisa su propia voz,
            ni interrumpe si escribes, ni durante media/orden de PC/visión o clase.
        A prueba de fallos: ante cualquier error no dice nada.
        """
        if not bool(_cfg("CAMERA_EMOTION_TALK_ENABLED", True)):
            return
        # Cortes de cortesía (mismos que usa el resto de iniciativas de YUE).
        try:
            if (self.ctx.speaker.is_speaking or self.ctx.chat.is_user_composing()
                    or self.ctx.pc_busy or self.ctx.vision_busy
                    or getattr(self.ctx.audio, "media_playing", False)
                    or self.ctx.autonomy_busy
                    or self._emotion_talk_pending):
                return
            if (getattr(self.ctx, "modes", None) is not None
                    and self.ctx.modes.current_mode() == MODE_TEACHER):
                return
        except Exception:
            return

        ahora = time.time()

        # ¿Peligro? = distrés (tristeza/tensión) SOSTENIDO durante varios minutos.
        peligro = False
        try:
            peligro = bool(self.ctx.camera.risk_signal())
        except Exception:
            peligro = False

        # Mejor emoción NO neutra de este fotograma (para el caso "muy fuerte").
        emociones = getattr(observation, "person_emotions", ()) or ()
        fuertes = [e for e in emociones if getattr(e, "key", "neutral") != "neutral"]
        mejor = max(fuertes, key=lambda e: getattr(e, "confidence", 0.0)) if fuertes else None
        conf = float(getattr(mejor, "confidence", 0.0)) if mejor else 0.0
        min_conf = float(_cfg("CAMERA_EMOTION_TALK_MIN_CONFIDENCE", 0.72))

        # Racha: contamos lecturas seguidas de la MISMA emoción fuerte para no
        # reaccionar a un gesto puntual. Se reinicia si cambia o baja la confianza.
        min_streak = max(1, int(_cfg("CAMERA_EMOTION_TALK_MIN_STREAK", 3)))
        prev_key = self._emotion_talk_streak_key
        if mejor is not None and conf >= min_conf:
            if mejor.key == prev_key:
                self._emotion_talk_streak = int(self._emotion_talk_streak) + 1
            else:
                self._emotion_talk_streak_key = mejor.key
                self._emotion_talk_streak = 1
        else:
            self._emotion_talk_streak_key = ""
            self._emotion_talk_streak = 0
        racha = int(self._emotion_talk_streak)

        # ¿Toca hablar? Peligro, o emoción fuerte con racha suficiente.
        emocion_fuerte = mejor is not None and conf >= min_conf and racha >= min_streak
        if not (peligro or emocion_fuerte):
            return

        # Elegimos la clave a comentar: si hay peligro, priorizamos la emoción
        # negativa que lo provoca; si no, la emoción fuerte del momento.
        key = (getattr(mejor, "key", "") if mejor is not None else "") or "neutral"
        if peligro and (mejor is None or mejor.key not in ("triste", "molesta")):
            key = "triste"  # el distrés sostenido se lee como tristeza/tensión

        # Cuentagotas GLOBAL (no habla seguido) y por MISMA emoción (no repite el
        # mismo ánimo pronto). El peligro relaja el corte por misma emoción, pero
        # sigue respetando el global para no volverse una alarma.
        min_gap = float(_cfg("CAMERA_EMOTION_TALK_MIN_GAP", 240.0))
        same_gap = float(_cfg("CAMERA_EMOTION_TALK_SAME_GAP", 900.0))
        ultima_at = float(self._last_emotion_talk_at)
        if (ahora - ultima_at) < min_gap:
            return
        if not peligro:
            ultima_key = self._last_emotion_talk_key
            ultima_same = float(self._last_emotion_talk_same_at)
            if key == ultima_key and (ahora - ultima_same) < same_gap:
                return

        # A partir de aquí SÍ hablamos: fijamos cuentagotas y reiniciamos la racha
        # para no encadenar comentarios.
        self._last_emotion_talk_at = ahora
        self._last_emotion_talk_key = key
        self._last_emotion_talk_same_at = ahora
        self._emotion_talk_streak = 0
        self._emotion_talk_streak_key = ""

        self._deliver_emotion_checkin(key, peligro)

    def _deliver_emotion_checkin(self, key, peligro):
        """Genera con el LLM (en el tono de YUE) el comentario/pregunta sobre la
        emoción vista y lo dice en voz. Si el LLM no está, usa una frase de
        respaldo para no quedarse muda. Aditivo y a prueba de fallos."""
        from core import face_emotion
        # Marcamos pendiente para no lanzar dos a la vez (se limpia al terminar).
        self._emotion_talk_pending = True
        try:
            hint = face_emotion.talk_prompt_hint(key)
            current = self.ctx.system_context()
            # Si hay peligro, inyectamos la directiva suave de cuidado (la misma que
            # usa el flujo por texto), para que acompañe con tacto.
            directive = None
            if peligro:
                try:
                    directive = safety.safety_directive_visual(_cfg("CRISIS_RESOURCES", ""))
                except Exception:
                    directive = None
            system = self.ctx.system_prompt(current, directive)
            if peligro:
                encargo = (
                    "IMPORTANTE: por la cámara notas que la persona lleva un buen rato "
                    f"con una expresión de que algo va mal ({hint}). No es un gesto "
                    "puntual, se ha mantenido. Con cariño y tu estilo (baja un poco el "
                    "tono tsundere aquí, sin dramatizar), dile que la ves así y "
                    "pregúntale qué le pasa o si está bien. UNA o dos frases, natural, "
                    "en español mexicano. No inventes qué le pasó; solo pregunta."
                )
            else:
                encargo = (
                    f"Por la cámara acabas de notar que a la persona {hint}. "
                    "Coméntaselo y pregúntale por qué está así, con tu estilo tsundere "
                    "juguetón (te importa aunque lo disimules). UNA o dos frases como "
                    "mucho, natural y en español mexicano. No inventes el motivo; solo "
                    "pregúntale."
                )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": encargo},
            ]
            respaldo = face_emotion.talk_fallback_line(key, riesgo=peligro)
            worker = AiWorker(self.ctx.engine, messages)
            worker.done.connect(lambda answer, k=key: self.on_emotion_talk_done(k, answer))
            worker.failed.connect(
                lambda _error, d=respaldo: self.on_emotion_talk_done(key, d)
            )
            self.ctx.workers.track(worker)
        except Exception as exc:
            print("[camara] no pude preparar el comentario emocional:", exc)
            self._emotion_talk_pending = False
            try:
                self.on_emotion_talk_done(key, face_emotion.talk_fallback_line(key, riesgo=peligro))
            except Exception:
                pass

    def on_emotion_talk_done(self, key, text):
        """Dice en voz el comentario emocional y lo deja en memoria como turno de
        YUE, para que tu respuesta ('estoy triste porque…') fluya con contexto."""
        self._emotion_talk_pending = False
        try:
            clean = emotion.clean_response(text or "")
            if not clean.strip():
                clean = None
            # Vuelve a comprobar cortesía: si mientras pensaba empezaste a hablar o
            # a escribir, no te pisamos; el momento ya pasó.
            if (self.ctx.speaker.is_speaking or self.ctx.chat.is_user_composing()
                    or self.ctx.pc_busy or self.ctx.vision_busy):
                return
            if not clean:
                return
            try:
                self.ctx.memory.add_message("assistant", clean)
            except Exception:
                pass
            self.ctx.say(clean, "camara-emocion")
        except Exception as exc:
            print("[camara] no pude decir el comentario emocional:", exc)

    # ---------- iniciativa y observación (deuda de PR 4, fila 5) ----------
    def _yue_may_take_initiative(self, minimo="medium"):
        """¿Puede YUE hablar primero? Delega en MemoryProactive, que consulta la
        iniciativa real del cerebro central (y degrada a True sin gestor)."""
        try:
            return bool(self.ctx.memory_proactive.yue_may_take_initiative(minimo))
        except Exception:
            return False

    def observe_emotion(self, name, confidence, valence=0.0, arousal=0.3,
                        explicit=False, detail="visión"):
        """La visión REPORTA lo que cree ver en el usuario (evidencia, no verdad).

        Alimenta el USER STATE del gestor; no cambia nada de YUE.
        """
        gestor = self.ctx.state_manager
        if gestor is None:
            return
        try:
            gestor.observe_emotion("camera", str(name), float(confidence),
                                   valence=float(valence), arousal=float(arousal),
                                   explicit=bool(explicit), detail=str(detail or "visión"))
        except Exception:
            pass

    # ---------- estado vivo de percepción visual (punto 10) ----------
    def vision_state(self):
        """El `vision_state` que puede consultar CUALQUIER parte de YUE.

        Devuelve SIEMPRE un diccionario con la forma completa (personas,
        emociones, objetos, gestos, texto, postura, mirada, escena, cámara y
        marca de actualización), incluso con la visión apagada. Así quien lo
        use no necesita comprobar None ni claves ausentes:

            estado = self.vision_state()
            if estado["personas"]["hay_persona"] and estado["mirada"]["mira_a_yue"]:
                ...
        """
        sistema = getattr(self.ctx, "vision_mp", None)
        if sistema is not None:
            try:
                return sistema.vision_state()
            except Exception as exc:
                print("[vision] no pude leer el estado visual:", exc)
        from vision.live_state import _estado_vacio
        return _estado_vacio()

    def vision_resumen(self):
        """Una línea en español con lo que YUE ve ahora mismo."""
        sistema = getattr(self.ctx, "vision_mp", None)
        if sistema is None:
            return "El sistema de visión por cámara está apagado."
        try:
            return sistema.resumen_visual()
        except Exception:
            return "No consigo leer el estado de la visión."


class VisionHostAdapter(VisionHost):
    """Adapta el VisionDirector al contrato VisionHost para vision/integration
    (PR 4, cerrado en PR 5.7: §4.1 fila 5).

    Implementa los 4 miembros del contrato y la superficie duck-typed que
    vision/integration.py sigue usando. CADA dependencia llega por
    controller_ctx (patrón WorkerRegistry): chat, teacher, pet, say,
    _on_vision_status, observe_emotion, flags _*_busy y
    _yue_may_take_initiative. NO hay __getattr__ hacia Controller.
    """

    def __init__(self, director):
        self._director = director

    @property
    def _ctx(self):
        return self._director.ctx

    def request_emotion(self, name: str, intensity: float, duration_ms: int,
                        priority: int, source: str) -> None:
        gestor = getattr(self._ctx, "state_manager", None)
        if gestor is not None:
            gestor.request_emotion(str(name), float(intensity), int(duration_ms),
                                   priority=int(priority), source=str(source or "vision"))

    def emotion_would_win(self, priority: int) -> bool:
        gestor = getattr(self._ctx, "state_manager", None)
        if gestor is None:
            return False  # prudente: sin gestor la visión no propone
        try:
            return bool(gestor.would_win(int(priority), "vision"))
        except Exception:
            return False

    @property
    def media_playing(self) -> bool:
        audio = getattr(self._ctx, "audio", None)
        try:
            return bool(getattr(audio, "media_playing", False))
        except Exception:
            return False

    @property
    def is_speaking(self) -> bool:
        speaker = getattr(self._ctx, "speaker", None)
        try:
            return bool(getattr(speaker, "is_speaking", False))
        except Exception:
            return False

    # ---- superficie duck-typed de vision/integration.py (explícita, por ctx)
    @property
    def chat(self):
        return getattr(self._ctx, "chat", None)

    @property
    def teacher(self):
        return getattr(self._ctx, "teacher", None)

    @property
    def pet(self):
        return getattr(self._ctx, "pet", None)

    @property
    def state_manager(self):
        return getattr(self._ctx, "state_manager", None)

    @property
    def _pc_busy(self) -> bool:
        return bool(getattr(self._ctx, "pc_busy", False))

    @property
    def _vision_busy(self) -> bool:
        return bool(getattr(self._ctx, "vision_busy", False))

    @property
    def _autonomy_busy(self) -> bool:
        return bool(getattr(self._ctx, "autonomy_busy", False))

    def _yue_may_take_initiative(self, minimo="medium"):
        return self._director._yue_may_take_initiative(minimo)

    def _yue_say(self, text, user_context=None):
        self._director.ctx.say(text, user_context)

    def _on_vision_status(self, text, active):
        self._director.on_vision_status(text, active)

    def observe_emotion(self, name, confidence, valence=0.0, arousal=0.3,
                        explicit=False, detail="visión MP"):
        self._director.observe_emotion(name, confidence, valence=valence,
                                       arousal=arousal, explicit=explicit,
                                       detail=detail)
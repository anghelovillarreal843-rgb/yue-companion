"""VoiceDirector: voz, palabra de activación y micrófono (PR 5, paso 2).

Extraído de Controller (REFACTOR_SPEC §4.1 fila 2): _toggle_voice,
_toggle_mic, _diagnose_voice, _build_wake_re, _strip_wake_word,
_wake_ignored, _on_heard, _on_text_message, _on_barge_in, _on_mic_status.

Regla del refactor: el director NO conoce al Controller.  Sus dependencias
llegan vía ControllerContext (service-locator, patrón WorkerRegistry):
- componentes del host: ctx.speaker, ctx.listener, ctx.chat, ctx.pet,
  ctx.state_manager
- callbacks del host: ctx.say (hoy Controller._yue_say), ctx.user_message
  (hoy Controller.on_user_message, futuro DiálogoDirector), ctx.avatar_emotion
  (hoy Controller._set_avatar_emotion, futuro EmotionOrchestrator)
- estado mutable: ctx.input_source (patrón last_visual_risk_ask del spec)
- constantes: ctx.avatar_conversation_priority (equivale a
  core.state.Priority.CONVERSATION = 70, congelada como EMOTION_PRIORITY)
"""

from __future__ import annotations

import config


class VoiceDirector:
    """Voz (TTS), micrófono y palabra de activación. Sin conocer al host."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._wake_re = None  # compilado perezoso desde config

    # ---------------------------------------------------------------- voz
    def toggle_voice(self):
        enabled = self.ctx.speaker.toggle()
        try:
            if self.ctx.state_manager is not None:
                self.ctx.state_manager.update_system(
                    voice="silent" if not enabled else "silent")
        except Exception:
            pass
        if enabled:
            self.ctx.say("Voz activada.")
        else:
            self.ctx.chat.show_reply("Voz desactivada.")

    def toggle_mic(self):
        enabled = self.ctx.listener.toggle()
        try:
            if self.ctx.state_manager is not None:
                self.ctx.state_manager.update_system(
                    mic="listening" if enabled else "off")
        except Exception:
            pass
        self.ctx.say("Micrófono activado." if enabled else "Micrófono desactivado.")

    def diagnose_voice(self):
        """Informe del micrófono al chat y consola (arreglo "no me escucha")."""
        try:
            informe = self.ctx.listener.diagnose()
        except Exception as exc:
            informe = f"No pude diagnosticar el micrófono: {exc}"
        print("[oido] diagnóstico:\n" + informe)
        # Al chat lo mandamos como texto plano (sin TTS) para que se lea completo.
        self.ctx.chat.show_reply("Diagnóstico del micrófono:\n" + informe)

    # ----------------------------------------------------- palabra de activación
    def build_wake_re(self):
        """Compila el patrón de «Yue» desde config (perezoso)."""
        import re
        palabras = getattr(config, "WAKE_WORDS", "yue,yué,llue,jue,hue")
        if isinstance(palabras, str):
            palabras = [p.strip() for p in palabras.split(",") if p.strip()]
        if not palabras:
            palabras = ["yue"]
        alternativas = "|".join(re.escape(p) for p in palabras)
        # (saludo opcional) + palabra de activación + separadores (coma, dos puntos…)
        patron = (r"^\s*(?:(?:oye|oiga|hey|ey|ok|okay|escucha|disculpa)[\s,]+)?"
                  r"(?:" + alternativas + r")\b[\s,:.\-–—!¡¿?]*")
        self._wake_re = re.compile(patron, re.IGNORECASE)
        return self._wake_re

    def strip_wake_word(self, text):
        """Si empieza por «Yue» devuelve el resto; si no, None (se ignora)."""
        rex = self._wake_re or self.build_wake_re()
        m = rex.match(text or "")
        if not m:
            return None
        return (text[m.end():]).strip()

    def wake_ignored(self, text):
        """Mensaje ignorado por no empezar con «Yue». Aviso discreto (no hablado)."""
        if self.ctx.input_source == "texto":
            try:
                self.ctx.chat.set_status("Empieza con «Yue…» para que te responda.")
            except Exception:
                pass
        # Por voz no decimos nada: así no reacciona a conversaciones ajenas.

    # -------------------------------------------------------------- entradas
    def on_heard(self, text):
        """Llegó por VOZ: lo marca y lo envía al diálogo (word activación)."""
        self.ctx.input_source = "voz"
        self.ctx.user_message(text)

    def on_text_message(self, text):
        """Mensaje escrito en el chat: lo marca como TEXTO y lo envía."""
        self.ctx.input_source = "texto"
        self.ctx.user_message(text)

    def on_barge_in(self, text):
        """Corta la historia/voz al instante (se ejecuta ANTES de heard)."""
        self.ctx.speaker.stop()
        self.ctx.pet.set_talking(False)
        self.ctx.avatar_emotion(
            "focused", 0.78, 2600,
            priority=self.ctx.avatar_conversation_priority, source="vision")
        self.ctx.chat.set_status("Te escucho…")

    def on_mic_status(self, message):
        if getattr(config, "DEBUG_STATUS", False):
            print(f"[oido] estado: {message}")
        if message.startswith(("no llego", "no pude", "sin ")):
            self.ctx.chat.show_reply("No te estoy oyendo bien: " + message)
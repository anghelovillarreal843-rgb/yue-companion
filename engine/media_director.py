"""MediaCompanionDirector (PR 5, pasos 4 y 11.2).

Paso 4: extrae de Controller _on_media_heard y _recent_lyrics (fila 4 de
§4.1). El buffer rolling (timestamp, texto) vive aquí; Controller solo
conecta la señal listener.media_heard.

Paso 11.2: extrae de Controller `_describe_audio` (el flujo «¿qué tal la
música / qué escuchas?») y publica el canal `ctx.describe_audio` en su
__init__, con el patrón de dueño del PR 5. El consumidor
(AutonomyDirector, `audio_describe`) lee el MISMO canal: el traspaso es
invisible para él. Dependencias del host por `ctx` (audio, say,
chat_set_status, system_context, system_prompt, chat_request_id, memory,
workers) — nunca por referencia al Controller.
"""
from __future__ import annotations

import time

_CORTE_S = 180.0      # ventana máxima del rolling
_MAX_FRAGMENTOS = 25


class MediaCompanionDirector:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._buffer: list[tuple[float, str]] = []
        # PR 5 paso 11.2: dueño del canal describe_audio (antes lo publicaba
        # Controller con `_describe_audio`).
        self.ctx.describe_audio = self.describe_audio

    def remember_heard(self, text: str) -> None:
        """Guarda la letra/diálogo que YUE oye mientras suena media (rolling)."""
        text = (text or "").strip()
        if not text:
            return
        ahora = time.time()
        self._buffer.append((ahora, text))
        corte = ahora - _CORTE_S
        self._buffer = [(t, s) for (t, s) in self._buffer if t >= corte][-_MAX_FRAGMENTOS:]

    def recent_lyrics(self, max_age: float = 150.0, max_chars: int = 600) -> str:
        """Texto reciente oído del audio (letra/diálogo), para dar contexto a YUE."""
        ahora = time.time()
        trozos = [s for (t, s) in self._buffer if ahora - t <= max_age]
        if not trozos:
            return ""
        salida = []
        total = 0
        for s in reversed(trozos):
            if total + len(s) > max_chars:
                break
            salida.append(s)
            total += len(s)
        return " … ".join(reversed(salida)).strip()

    def describe_audio(self):
        """Responde a «¿qué tal la música / qué escuchas / qué te pareció?» diciendo
        qué suena (o sonaba hace un momento) y dando una impresión con la voz de YUE,
        usando la letra/diálogo que captó. Nunca dice «no escucho nada» si de hecho
        acaba de sonar algo."""
        desc = ""
        try:
            desc = self.ctx.audio.describe() or ""
        except Exception as exc:
            print("[audio] describe() falló:", exc)
        # 1) ¿Hay contenido AHORA? 2) Si no, ¿sonó algo hace poco?
        prof = None
        try:
            prof = self.ctx.audio.media_profile()
        except Exception:
            prof = None
        activo = bool(getattr(self.ctx.audio, "active", False))
        ahora_suena = activo and prof is not None and getattr(prof, "media_type", "") in (
            "video_musical", "video_normal",
        )
        reciente = None
        edad = None
        if not ahora_suena:
            try:
                reciente, edad = self.ctx.audio.recent_content(max_age=150.0)
            except Exception:
                reciente, edad = (None, None)

        lyrics = self.recent_lyrics()

        # Si no hay nada actual ni reciente NI letra captada, respondemos directo.
        if not ahora_suena and reciente is None and not lyrics:
            self.ctx.say(desc or "Ahora mismo no distingo nada sonando en tu PC. "
                          "Si quieres que te diga qué tal, ponlo a sonar un momento.")
            return

        # Construimos el contexto para que YUE dé una impresión natural.
        if ahora_suena:
            percibo = desc or (prof.summary_es() if prof else "algo está sonando")
            cuando = "Ahora mismo"
        elif reciente is not None:
            percibo = reciente.summary_es()
            seg = int(edad or 0)
            cuando = f"Hace unos {seg} segundos" if seg >= 3 else "Hace un momento"
        else:
            percibo = "algo estuvo sonando hace poco"
            cuando = "Hace un momento"

        contexto_letra = ""
        if lyrics:
            contexto_letra = (
                "\nAlgo de lo que alcanzaste a oír (letra o diálogo, puede venir con "
                f"errores de transcripción): «{lyrics}»."
            )

        current = self.ctx.system_context()
        system = self.ctx.system_prompt(current)
        user = (
            "El usuario te pregunta qué te pareció lo que suena (o acaba de sonar) en "
            f"su computadora. {cuando} percibes esto de tu escucha del audio del "
            f"sistema: «{percibo}».{contexto_letra}\n"
            "Responde en español, en 1-3 frases, con tu personalidad. Da una impresión "
            "natural: si es música, comenta el ambiente, la energía o de qué parece ir "
            "por lo que oíste; si es un vídeo o peli, coméntalo. Puedes referirte a lo "
            "que oíste, pero NO inventes título ni artista si no te consta. Nunca digas "
            "que no puedes escuchar: sí puedes oír el audio del PC."
        )
        request_id = self.ctx.chat_request_id
        self.ctx.chat_set_status("Yue está recordando lo que sonó…")
        from engine.ai_worker import AiWorker
        worker = AiWorker(self.ctx.engine, [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])
        # Si el modelo falla, al menos decimos la clasificación (no «no escucho nada»).
        respaldo = desc or (f"{cuando} sonaba {percibo}." if percibo else "")
        worker.done.connect(
            lambda answer, rid=request_id: self._on_describe_done(rid, answer))
        worker.failed.connect(
            lambda _error, d=respaldo: (self.ctx.chat_set_status(""), self.ctx.say(d)))
        self.ctx.workers.track(worker)

    def _on_describe_done(self, request_id, text):
        """Cierre del worker de describe (patrón de _say_followup de memoria):
        descarta respuestas de turnos interrumpidos, deja el turno en el
        historial como assistant y dice por el canal del diálogo. Ciclo de
        vida único: el worker solo vive en ctx.workers."""
        if request_id != self.ctx.chat_request_id:
            return
        self.ctx.chat_set_status("")
        try:
            from core import emotion
            clean = emotion.clean_response(text)
        except Exception:
            clean = text
        try:
            self.ctx.memory.add_message("assistant", clean)
        except Exception:
            pass
        self.ctx.say(clean, "¿qué tal la música/lo que sonaba?")
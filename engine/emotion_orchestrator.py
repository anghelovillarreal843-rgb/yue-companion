"""EmotionOrchestrator (PR 5, paso 9 — §4.1 fila 9).

Extrae de Controller el paquete de emoción/avatar/estado: _set_avatar_emotion,
_publish_companion_state, _state_tick, _sync_system_state, _refresh_bond y
debug_state. (`_update_mode_indicator` ya vive en TeacherDirector desde el
paso 6; no se duplica aquí.)

Traspaso de dueño en el MISMO paso (§4.1 NOTA tras el paso 7): `ctx.avatar_emotion`
pasaba a ser publicado por Controller (main.py:399 -> self._set_avatar_emotion);
desde este paso lo publica EmotionOrchestrator en su __init__
(`ctx.avatar_emotion = self.set_avatar_emotion`). Los consumidores
(VisionDirector: espejo empático; PCDirector: cara de "enfocado" en órdenes;
TeacherDirector: gesto de modo; y los futuros directores) siguen leyendo
ctx.avatar_emotion: el MISMO estado, sin cambio de lectura.

`core.state.Priority` (rutas EMERGENCY/USER/TEACHER/CONVERSATION/EMOTION/MEDIA/
AMBIENT/IDLE): territorio NO-visión que PR 3/PR 4 dejaron explícitamente sin
tocar; vive aquí, dentro de _set_avatar_emotion, con import LOCAL de core.state
(no añade imports de vision -> sin ciclo; test_arch_dependencies en verde).

Dependencias del director (todas por controller_ctx):
- componentes/canales: state_manager, chat, pet, memory, speaker, listener,
  camera, vision_mp, vision_v3, teacher, audio, say.
- estado get/set: pc_busy, vision_busy, autonomy_busy.
El director no conoce a Controller ni a otros directores.
"""
from __future__ import annotations

from core import bonding


class EmotionOrchestrator:
    """Estado interno: ninguno persistente (todo vive en core/state)."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        # Traspaso de dueño (PR 5 paso 9): el canal del avatar lo publica el
        # orquestador desde YA, no Controller. Consumidores (Vision, PC,
        # Teacher) leían ctx.avatar_emotion y siguen leyendo el mismo canal.
        self.ctx.avatar_emotion = self.set_avatar_emotion

    # ==================================================================
    # CEREBRO CENTRAL: latido, estado del sistema y propuestas
    # ==================================================================
    def state_tick(self):
        """Latido del gestor de estado (cada 250 ms, en el hilo de la interfaz).

        Hace dos cosas:

        1. `state_manager.tick()` caduca las propuestas vencidas y RECALCULA el
           ganador. Antes este método existía pero no lo llamaba nadie, así que
           el modo profesora podía seguir gobernando la cara de YUE mucho
           después de haber terminado la explicación.
        2. Refresca el SYSTEM STATE con los interruptores de verdad. Los campos
           `mic`, `camera`, `voice`, etc. estaban definidos pero casi nadie los
           escribía: eran decorativos y mentían. Ahora se leen del sitio real.

        Todo va dentro de try: un fallo aquí no puede tumbar la aplicación, y
        se ejecuta muy a menudo.
        """
        gestor = getattr(self.ctx, "state_manager", None)
        if gestor is None:
            return
        try:
            gestor.tick()
        except Exception as exc:
            print("[estado] fallo en el latido:", exc)
        try:
            self.sync_system_state()
        except Exception as exc:
            print("[estado] no pude sincronizar el estado del sistema:", exc)

    def sync_system_state(self):
        """Vuelca el estado REAL de la aplicación en el SYSTEM STATE.

        Se lee todo con `getattr` y valores por defecto: si algún subsistema no
        está disponible (o aún no se creó), el campo se queda como estaba en vez
        de reventar.
        """
        gestor = getattr(self.ctx, "state_manager", None)
        if gestor is None:
            return

        # --- micrófono ---
        escuchando = bool(getattr(getattr(self.ctx, "listener", None), "enabled", False))
        mic = "listening" if escuchando else "off"

        # --- cámara: cuenta cualquiera de los tres sistemas de visión ---
        camara = False
        for atributo in ("camera", "vision_mp", "vision_v3"):
            objeto = getattr(self.ctx, atributo, None)
            if objeto is not None and bool(getattr(objeto, "active", False)):
                camara = True
                break

        # --- voz: `is_speaking` es una PROPIEDAD, no un método (ya nos mordió) ---
        hablando = bool(getattr(getattr(self.ctx, "speaker", None), "is_speaking", False))

        # --- multimedia y modo profesora ---
        sonando = bool(getattr(getattr(self.ctx, "audio", None), "media_playing", False))
        profesora = bool(getattr(getattr(self.ctx, "teacher", None), "is_active", False))

        pc_ocupado = bool(getattr(self.ctx, "pc_busy", False))
        vision_ocupada = bool(getattr(self.ctx, "vision_busy", False))
        autonomia = bool(getattr(self.ctx, "autonomy_busy", False))

        # --- actividad: lo que YUE está haciendo AHORA, de más a menos urgente ---
        if pc_ocupado:
            actividad, modo = "controlling", "control"
        elif profesora:
            actividad, modo = "teaching", "teacher"
        elif vision_ocupada:
            actividad, modo = "watching", "companion"
        elif autonomia:
            actividad, modo = "conversing", "autonomy"
        elif hablando:
            actividad, modo = "conversing", "companion"
        else:
            actividad, modo = "idle", "companion"

        gestor.update_system(
            mic=mic,
            camera="active" if camara else "off",
            voice="speaking" if hablando else "silent",
            mode=modo,
            media_playing=sonando,
            pc_busy=pc_ocupado,
            vision_busy=vision_ocupada,
            autonomy_busy=autonomia,
            teacher_active=profesora,
            activity=actividad,
        )

    def publish_companion_state(self, resultado):
        """Vuelca un `CompanionResult` en el cerebro central.

        Aquí se hace efectiva la separación que da nombre a todo esto:

            result.affect + result.context + result.intent  →  USER STATE
            result.expression + result.decision             →  YUE PROPOSAL

        Se reutilizan las piezas que ya existían tal cual. `CompanionExpression
        Policy` ya decidía bien la cara de YUE (responde al usuario en vez de
        imitarlo); lo único que se añade es el resto del comportamiento
        (qué hace, cómo suena, cuánta iniciativa se permite) para que el estado
        final sea coherente y no una cara suelta.
        """
        gestor = getattr(self.ctx, "state_manager", None)
        if gestor is None or resultado is None:
            return
        try:
            from core.state import (
                observation_from_companion, proposal_from_companion,
                user_state_from_companion,
            )
        except Exception:
            return

        # 1) El texto entra como OBSERVACIÓN, con su peso (1.00) y su marca de
        #    explícito. Es lo que impide que una cara neutra en cámara tumbe un
        #    "estoy muy triste" escrito con todas las letras.
        try:
            observacion = observation_from_companion(resultado)
            if observacion is not None:
                gestor.observe(observacion)
        except Exception as exc:
            print("[estado] no pude registrar la observación de texto:", exc)

        # 2) Lo que ningún sensor sabe (necesidad, tendencia, riesgo) lo aporta
        #    el cerebro afectivo. NO se recalcula: se copia de AffectiveContext.
        try:
            base = gestor.user_state()
            usuario = user_state_from_companion(resultado, base=base)
            gestor.update_user(
                need=usuario.need, secondary_need=usuario.secondary_need,
                trend=usuario.trend, sustained=usuario.sustained,
                duration_s=usuario.duration_s, stability=usuario.stability,
                distress=usuario.distress, trigger=usuario.trigger,
                safety_level=usuario.safety_level,
            )
        except Exception as exc:
            print("[estado] no pude actualizar el estado del usuario:", exc)

        # 3) La reacción de YUE va como PROPUESTA. Puede perder (si la profesora
        #    o una emergencia mandan) y no pasa nada: seguirá viva y tomará el
        #    mando en cuanto la otra caduque.
        try:
            propuesta = proposal_from_companion(resultado)
            if propuesta is not None:
                gestor.propose(propuesta)
        except Exception as exc:
            print("[estado] no pude enviar la propuesta de comportamiento:", exc)

    def set_avatar_emotion(self, name, intensity=0.6, duration_ms=4500,
                           *, priority=None, source="sistema"):
        """Propone una cara para YUE. Ya NO la aplica: eso es del renderer.

        Antes había llamadas sueltas a `pet.set_emotion(...)` desde la
        conversación, la música, la cámara y el control del PC, y ganaba siempre
        la última en llegar. Por eso una canción alegre podía poner al avatar
        eufórico justo mientras el usuario contaba algo doloroso.

        Ahora esto es solo una PROPUESTA. `YueStateManager` arbitra por
        PRIORIDAD y `AvatarRenderer` pinta al ganador (y es el único sitio del
        proyecto que llama a `pet.set_emotion`):

            EMERGENCY (seguridad) > USER (apoyo) > TEACHER (profesora) >
            CONVERSATION > EMOTION > MEDIA (música) > AMBIENT > IDLE

        Devuelve si esta propuesta gobierna AHORA. Devolver False no significa
        que se haya perdido: sigue viva y ganará cuando caduque la de arriba.

        Si el gestor no estuviera disponible (arranque degradado), se cae al
        modo directo de siempre para no dejar el avatar congelado.
        """
        try:
            from core.state import Priority
            prioridad = Priority.EMOTION if priority is None else priority
        except Exception:
            prioridad = 60

        gestor = getattr(self.ctx, "state_manager", None)
        if gestor is not None:
            try:
                # El renderer, suscrito al gestor, pintará al ganador. Aquí NO
                # se toca el avatar: esa es justamente la regla que se quería
                # imponer, y el único punto que la cumple es core/state/renderer.
                return bool(gestor.request_emotion(
                    name, intensity, duration_ms,
                    priority=int(prioridad), source=source))
            except Exception as exc:
                print("[estado] fallo al proponer la emoción:", exc)

        # EXCEPCIÓN CONSERVADA A PROPÓSITO: sin gestor de estado no hay
        # renderer, y sin renderer nadie pintaría nunca al avatar. Antes que
        # dejar a YUE con la cara congelada, se aplica directo. Solo ocurre si
        # `core.state` no llegó a importarse en el arranque.
        try:
            self.ctx.pet.set_emotion(name, intensity, duration_ms)
        except Exception as exc:
            print("[avatar] no pude aplicar la emoción:", exc)
            return False
        return True

    # ---------- vínculo y prompt ----------
    def refresh_bond(self):
        points = self.ctx.memory.get_bond_points()
        current, _, _ = bonding.progress(points)
        self.ctx.chat.set_bond(current)
        return current

    # ==================================================================
    # DEPURACIÓN: inspeccionar por qué YUE hizo lo que hizo
    # ==================================================================
    def debug_state(self, imprimir=True):
        """Foto completa del cerebro central: los tres estados y el arbitraje.

        Muestra USER STATE, YUE STATE, SYSTEM STATE, la propuesta ganadora, las
        propuestas activas con su TTL restante y las observaciones vivas de cada
        sensor con su confianza ponderada.

        Es la herramienta para cuando YUE ponga una cara rara y no se sepa por
        qué: aquí se ve quién la pidió, con qué prioridad y cuánto le queda.

            >>> app.debug_state()
        """
        gestor = getattr(self.ctx, "state_manager", None)
        if gestor is None:
            if imprimir:
                print("[estado] el gestor central no está disponible.")
            return {}
        try:
            foto = gestor.debug_snapshot()
        except Exception as exc:
            print("[estado] no pude tomar la foto:", exc)
            return {}
        if not imprimir:
            return foto

        u, y, s = foto["user"], foto["yue"], foto["system"]
        print("\n" + "=" * 62)
        print("  ESTADO DEL USUARIO  (qué le pasa)")
        print(f"  necesidad={u['need'] or '-'}  "
              f"tendencia={u['trend'] or '-'}  sostenida={u['sustained']}")
        print(f"  distress={round(u['distress'], 2)}  "
              f"duracion={round(u['duration_s'], 1)}s  "
              f"estabilidad={round(u['stability'], 2)}")
        print(f"  detonante={u['trigger'] or '-'}  "
              f"seguridad={u['safety_level'] or '-'}  "
              f"politica={u['safety_note'] or '-'}")
        print("  ── OBSERVACIONES VIVAS " + "─" * 38)
        if not u["observations"]:
            print("     (ninguna)")
        for o in u["observations"]:
            print(f"     {o['source']:<8} {o['emotion']:<12}"
                  f" bruta={o['confidence']:<6} ponderada={o['effective']:<6}"
                  f" explícito={o['explicit']} hace {o['age_s']}s")
        print("  ── YUE STATE  (cómo está YUE)")
        print(f"  emoción={y.get('emotion', '-') or '-'}  "
              f"foco={y.get('focus', '-') or '-'}")
        print("  ── PROPUESTAS ACTIVAS " + "─" * 39)
        if not foto["proposals"]:
            print("     (ninguna: YUE en reposo)")
        for p in foto["proposals"]:
            marca = "►" if p["source"] == foto["winner"] else " "
            print(f"   {marca} p{p['priority']:<4} {p['source']:<14}"
                  f" {p['emotion']:<10} {p['behavior']:<14}"
                  f" ttl={p['ttl_remaining']}")
        print("  ── SYSTEM STATE  (interruptores reales)")
        for clave, valor in s.items():
            print(f"     {clave:<18} {valor}")
        print("=" * 62 + "\n")
        return foto
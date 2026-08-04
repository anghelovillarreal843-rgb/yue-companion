"""Pruebas del comentario CON CONTEXTO: cuando YUE sabe qué suena, comenta con el
LLM en vez del banco fijo; y cuando no lo sabe (o el LLM falla/tarda), cae
EXACTAMENTE al comportamiento de siempre. Sin red, sin audio, sin GUI.

Cubre:
  - core/now_playing.py          -> buzón "qué suena" (set/get/clear + caducidad).
  - core/music_emotion.comment_with_context -> frase vía LLM mockeado / fallos.
  - decide_reaction (system_audio) -> preferencia LLM>banco y degradación intacta.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fallos = []
def check(nombre, cond):
    print(("  OK  " if cond else "  FALLO  ") + nombre)
    if not cond:
        fallos.append(nombre)


# --- dobles de prueba para el motor de IA (nunca tocan la red) ---
class FakeEngine:
    """Motor mínimo: responde lo que le digamos. api_key no vacía = 'disponible'."""
    def __init__(self, reply="Me encanta esta parte, la tenía olvidada", api_key="k"):
        self.api_key = api_key
        self._reply = reply
        self.llamadas = 0
    def chat(self, messages, timeout=60, model=None):
        self.llamadas += 1
        return self._reply

class BoomEngine:
    def __init__(self, api_key="k"):
        self.api_key = api_key
    def chat(self, messages, timeout=60, model=None):
        raise RuntimeError("se cayó / tardó demasiado")

class SlowNoApiEngine:
    """Sin api_key: debe tratarse como NO disponible, sin llamar a chat."""
    api_key = ""
    def chat(self, *a, **k):
        raise AssertionError("no debería llamarse sin api_key")


print("\n[1] now_playing: buzón de 'qué suena'")
from core import now_playing as npmod

npmod.clear()
check("vacío -> ('','',0)", npmod.get_now_playing() == ("", "", 0.0))
npmod.set_now_playing("bad bunny titi me pregunto")
t, a, ts = npmod.get_now_playing()
check("set/get título", t == "bad bunny titi me pregunto" and ts > 0)
npmod.set_now_playing("   ")  # título vacío no pisa el anterior
check("título vacío no pisa", npmod.get_now_playing()[0] == "bad bunny titi me pregunto")
npmod.clear()
check("clear -> vacío", npmod.get_now_playing() == ("", "", 0.0))
# Caducidad: forzamos un timestamp viejo.
npmod.set_now_playing("cancion vieja")
npmod._ts = time.time() - (npmod._TTL + 5)  # noqa: SLF001 (test)
check("caducado -> vacío", npmod.get_now_playing() == ("", "", 0.0))
npmod.clear()


print("\n[2] music_emotion.comment_with_context")
from core import music_emotion as me

frase = me.comment_with_context("happy", "titi me pregunto", "video_musical",
                                ai_engine=FakeEngine("Ay, esta me sube el ánimo"))
check("LLM ok -> frase limpia", frase == "Ay, esta me sube el ánimo")
# Sin título -> None (ni intenta el LLM).
check("sin título -> None",
      me.comment_with_context("happy", "", ai_engine=FakeEngine()) is None)
# Sin api_key -> None (no llama a chat).
check("sin api_key -> None",
      me.comment_with_context("happy", "algo", ai_engine=SlowNoApiEngine()) is None)
# El LLM revienta -> None (degradación).
check("LLM excepción -> None",
      me.comment_with_context("sad", "algo", ai_engine=BoomEngine()) is None)
# Respuesta con comillas y salto de línea -> se limpia a una frase.
sucio = me.comment_with_context("love", "algo",
                                ai_engine=FakeEngine('  "Qué preciosa es esto"\notra línea '))
check("limpia comillas/multilínea", sucio == "Qué preciosa es esto")
# Respuesta kilométrica -> None (mejor banco que párrafo).
largo = me.comment_with_context("happy", "algo", ai_engine=FakeEngine("palabra " * 40))
check("respuesta larga -> None", largo is None)


print("\n[3] decide_reaction: preferencia LLM > banco, y degradación")
from core.system_audio import decide_reaction, ReactorState, AudioObservation

def obs_musica():
    # Música (cambio desde silencio) para que dispare comentario.
    return AudioObservation(timestamp=time.time(), rms=0.09, kind="musica",
                            energetic=False, tempo_bpm=100, brightness=0.12,
                            bass=0.3, onset=False)

def estado_listo():
    # last_kind='silencio' -> cambio de categoría; sin comentario reciente.
    return ReactorState(last_kind="silencio", last_comment_at=0.0)

FRASE_LLM = "Uy, justo esta canción la tenía en la cabeza"

# (a) Título presente + LLM en caché -> usa la frase con contexto.
r = decide_reaction(obs_musica(), estado_listo(), allow_comments=True,
                    media_type="video_musical", now_playing="titi me pregunto",
                    llm_lookup=lambda _t: FRASE_LLM)
check("título+LLM -> comentario con contexto", r is not None and r.comment == FRASE_LLM)

# (b) Sin título -> banco de siempre (no llama a llm_lookup).
def _no_debe(_t):
    raise AssertionError("no debería consultarse sin título")
r = decide_reaction(obs_musica(), estado_listo(), allow_comments=True,
                    media_type="video_musical", now_playing="",
                    llm_lookup=_no_debe)
check("sin título -> banco (comenta algo)", r is not None and isinstance(r.comment, str)
      and r.comment != FRASE_LLM)

# (c) LLM aún sin frase (caché fría, devuelve None) -> banco de siempre.
r = decide_reaction(obs_musica(), estado_listo(), allow_comments=True,
                    media_type="video_musical", now_playing="titi me pregunto",
                    llm_lookup=lambda _t: None)
check("caché fría -> banco", r is not None and isinstance(r.comment, str)
      and r.comment != FRASE_LLM)

# (d) llm_lookup revienta -> NO rompe decide_reaction, cae al banco.
def _boom(_t):
    raise RuntimeError("lookup roto")
r = decide_reaction(obs_musica(), estado_listo(), allow_comments=True,
                    media_type="video_musical", now_playing="titi me pregunto",
                    llm_lookup=_boom)
check("lookup roto -> banco sin romper", r is not None and isinstance(r.comment, str)
      and r.comment != FRASE_LLM)

# (e) Compatibilidad: sin los parámetros nuevos, se comporta como siempre.
r = decide_reaction(obs_musica(), estado_listo(), allow_comments=True,
                    media_type="video_musical")
check("firma vieja sigue funcionando", r is not None and isinstance(r.comment, str))


print("\n" + ("TODO OK" if not fallos else f"FALLOS: {fallos}"))
sys.exit(1 if fallos else 0)

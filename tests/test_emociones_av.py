"""Pruebas de la lógica emocional nueva (sin cámara, sin audio, sin GUI).

Cubre:
  - core/music_emotion.py  -> lo que YUE siente al escuchar.
  - core/face_emotion.py    -> cómo lee la emoción de la persona.
  - integración en decide_reaction (system_audio) y en CameraObservation.
"""
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fallos = []
def check(nombre, cond):
    print(("  OK  " if cond else "  FALLO  ") + nombre)
    if not cond:
        fallos.append(nombre)


# --- objeto mínimo que imita AudioObservation para las pruebas ---
@dataclass
class Obs:
    kind: str = "musica"
    energetic: bool = False
    rms: float = 0.08
    tempo_bpm: float = 100.0
    brightness: float = 0.12
    bass: float = 0.3
    onset: bool = False


print("\n[1] music_emotion: lo que YUE siente")
from core import music_emotion as me

# Música lenta y oscura -> melancólica (sad).
f = me.feel_from_audio(Obs(kind="musica", tempo_bpm=70, brightness=0.05, bass=0.3, rms=0.06))
check("lento+oscuro -> sad", f is not None and f.emotion == "sad")

# Música movida y brillante -> con chispa (excited/playful).
f = me.feel_from_audio(Obs(kind="musica", energetic=True, tempo_bpm=140, brightness=0.2, rms=0.16))
check("movido+brillante -> excited/playful", f is not None and f.emotion in ("excited", "playful"))

# Música muy suave y cálida -> ternura (love).
f = me.feel_from_audio(Obs(kind="musica", energetic=False, tempo_bpm=95, brightness=0.16, rms=0.03))
check("muy suave y brillante -> love", f is not None and f.emotion == "love")

# Diálogo con golpe fuerte -> sorprendida.
f = me.feel_from_audio(Obs(kind="voz", onset=True, rms=0.14))
check("voz + golpe -> surprised", f is not None and f.emotion == "surprised")

# Silencio -> None (deja la base).
check("silencio -> None", me.feel_from_audio(Obs(kind="silencio")) is None)

# Determinismo: misma entrada, misma cara.
a = me.feel_from_audio(Obs(kind="musica", tempo_bpm=70, brightness=0.05, rms=0.06))
b = me.feel_from_audio(Obs(kind="musica", tempo_bpm=70, brightness=0.05, rms=0.06))
check("determinista", a == b)

# Comentario acorde a la emoción sentida.
c = me.comment_for_feeling("sad")
check("hay frase para 'sad'", isinstance(c, str) and len(c) > 0)


print("\n[2] face_emotion: cómo se siente la persona")
from core import face_emotion as fe

# Sonrisa marcada -> contenta.
e = fe.infer_person_emotion({"mouthSmileLeft": 0.8, "mouthSmileRight": 0.75, "cheekSquintLeft": 0.4})
check("sonrisa -> feliz", e is not None and e.key == "feliz")

# Comisuras abajo + cejas internas arriba -> triste.
e = fe.infer_person_emotion({"mouthFrownLeft": 0.7, "mouthFrownRight": 0.65, "browInnerUp": 0.5})
check("ceño caído -> triste", e is not None and e.key == "triste")

# Boca abierta + ojos muy abiertos -> sorprendida.
e = fe.infer_person_emotion({"jawOpen": 0.7, "eyeWideLeft": 0.7, "eyeWideRight": 0.7, "browOuterUpLeft": 0.5})
check("mandíbula+ojos -> sorprendida", e is not None and e.key == "sorprendida")

# Cejas abajo + entrecerrar + nariz -> molesta.
e = fe.infer_person_emotion({"browDownLeft": 0.7, "browDownRight": 0.7, "noseSneerLeft": 0.5, "eyeSquintLeft": 0.4})
check("ceño fruncido -> molesta", e is not None and e.key == "molesta")

# Cara sin señales -> neutral.
e = fe.infer_person_emotion({"jawOpen": 0.02})
check("sin señales -> neutral", e is not None and e.key == "neutral")

# Lectura en español y espejo empático.
reading = fe.reading_es([fe.PersonEmotion("feliz", 0.8, "contenta")])
check("lectura menciona 'contenta'", "contenta" in reading)
esp = fe.empathic_avatar_emotion("triste")
check("espejo de 'triste' -> worried", esp is not None and esp[0] == "worried")
check("espejo de 'neutral' -> None", fe.empathic_avatar_emotion("neutral") is None)


print("\n[3] Integración: decide_reaction usa la emoción sentida")
from core.system_audio import decide_reaction, ReactorState, AudioObservation
import time as _t
obs = AudioObservation(timestamp=_t.time(), rms=0.06, kind="musica", energetic=False,
                       tempo_bpm=70, brightness=0.05, bass=0.3, onset=False)
st = ReactorState(last_kind="silencio")
r = decide_reaction(obs, st, allow_comments=False)
check("música lenta/oscura -> cara 'sad'", r is not None and r.emotion == "sad")


print("\n[4] Integración: CameraObservation da lectura emocional")
from core.camera_observer import CameraObservation
o = CameraObservation(_t.time(), 0, faces=1, face_cues=("sonrisa visible",),
                      person_emotions=(fe.PersonEmotion("feliz", 0.8, "contenta"),))
rich = o.rich_summary_es()
check("rich_summary incluye impresión", "contenta" in rich)
# El summary_es clásico NO cambia (compatibilidad con el test existente).
check("summary_es clásico intacto", "no una lectura segura" in o.summary_es())
# Sin emociones, rich == summary base (no inventa nada).
o2 = CameraObservation(_t.time(), 0, faces=1, face_cues=("sonrisa visible",))
check("sin emociones no añade impresión", o2.rich_summary_es() == o2.summary_es())


print("\n" + ("TODO OK" if not fallos else f"FALLOS: {fallos}"))
sys.exit(1 if fallos else 0)

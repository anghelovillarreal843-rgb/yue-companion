import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.media_companion import (
    ContinuousEmotionState,
    MusicAnalysis,
    analyze_music,
    fuse_emotions,
    parse_visual_response,
)
from core.memory import Memory


def observation(**values):
    defaults = dict(
        timestamp=1000.0,
        kind="musica",
        rms=0.08,
        energetic=False,
        tempo_bpm=80.0,
        brightness=0.08,
        bass=0.15,
        onset=False,
    )
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_music_analyzer_covers_sad_and_terror_categories():
    sad = analyze_music(observation(rms=0.07, tempo_bpm=65, brightness=0.02, bass=0.20))
    terror = analyze_music(observation(
        rms=0.14, tempo_bpm=70, brightness=0.0, bass=0.42, energetic=True
    ))
    assert sad.category == "triste"
    assert sad.dominant_emotion == "sad"
    assert terror.category == "terror"
    assert terror.dominant_emotion == "worried"


def test_visual_json_and_audio_video_fusion_reinforce_same_emotion():
    visual = parse_visual_response(
        '{"resumen":"Una chica está llorando", "personas":["una chica"], '
        '"expresiones":["llorando"], "emociones":["triste"], '
        '"colores":["azul"], "acciones":["llora"], "objetos":[], '
        '"texto_visible":"", "ambiente":"melancólico", '
        '"tipo_contenido":"película", "cambio_escena":0.4, "importancia":0.8}',
        0.4,
    )
    audio = MusicAnalysis(
        timestamp=1000.0,
        energy=0.3,
        rhythm=0.2,
        tempo_bpm=62,
        speed=0.1,
        intensity=0.45,
        category="triste",
        atmosphere="melancólica",
        dominant_emotion="sad",
        emotion_weights={"sad": 0.82},
        media_type="video_normal",
    )
    fused = fuse_emotions(audio, visual)
    assert visual.summary == "Una chica está llorando"
    assert visual.colors == ("azul",)
    assert fused["sad"] > 0.9
    assert fused["sad"] > fused["neutral"]


def test_continuous_emotion_never_jumps_and_decays_gradually():
    state = ContinuousEmotionState(rise_seconds=3.0, fall_seconds=6.0)
    state.set_target({"happy": 1.0, "neutral": 0.0})
    _, _, first = state.step(0.2)
    assert 0.0 < first["happy"] < 0.2
    for _ in range(20):
        state.step(0.2)
    before_decay = state.snapshot()["happy"]
    state.neutralize()
    state.step(0.2)
    after_decay = state.snapshot()["happy"]
    assert 0.0 < after_decay < before_decay


def test_multimedia_memory_resolves_reference_and_preference(tmp_path: Path):
    memory = Memory(tmp_path / "memory.db")
    session = memory.start_multimedia_session(
        source="youtube", app="YouTube", title="Opening de Frieren",
        media_type="video_musical",
    )
    memory.remember_multimedia_item(
        "musica", "Opening de Frieren", source="youtube", app="YouTube",
        emotion="happy", intensity=0.85, session_id=session,
    )
    found = memory.resolve_multimedia_reference("pon la canción anterior")
    assert found and found["title"] == "Opening de Frieren"

    positive = memory.learn_multimedia_preference("me encantó esta canción")
    assert positive["preference_score"] == 1.0
    negative = memory.learn_multimedia_preference("no me gusta esta canción")
    assert negative["preference_score"] == -1.0

    memory.add_multimedia_event(
        session, "escena", "final", "happy", 0.9,
        detail="Final feliz", favorite=True, comment="Me gustó esa parte.",
    )
    memory.end_multimedia_session(session, "happy", 0.9, "Sesión alegre")
    context = memory.multimedia_context()
    assert "Opening de Frieren" in context
    assert "no le gustó" in context

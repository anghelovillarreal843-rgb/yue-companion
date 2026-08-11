"""Pruebas de la MEMORIA NARRATIVA (Story Memory). Sin red, sin GUI, sin LLM.

    python tests/test_story_memory.py

Todo corre OFFLINE y con el extractor determinista a propósito: si las
historias solo funcionaran con modelo, YUE se quedaría sin memoria narrativa
justo en el modo sin conexión que tanto costó construir.

Cubre los quince casos del encargo:
  1. crear la historia de una persona
  2. volver a mencionarla hace MERGE, no duplica
  3. varios acontecimientos en la misma historia
  4. unresolved -> resolved
  5. los acontecimientos viejos SOBREVIVEN a la resolución
  6. confianza de lo explícito frente a lo inferido
  7. recuperar una historia por nombre
  8. NO recuperar historias que no vienen a cuento
  9. NO crear historia para un detalle trivial
 10. una historia de meta conserva su motivación
 11. `progress` sigue en NULL sin evidencia
 12. persistencia tras reiniciar el objeto de memoria
 13. migración segura de una base creada ANTES de Story Memory
 14. tope de historias que entran al prompt
 15. mayúsculas y tildes no duplican a la misma persona
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fallos = []


def check(nombre, cond):
    print(("  OK  " if cond else "  FALLO  ") + nombre)
    if not cond:
        fallos.append(nombre)


from core.memory import Memory  # noqa: E402
from core import memory_consolidation as mc  # noqa: E402
from core.story_memory import (  # noqa: E402
    StoryMemory, compute_significance, detect_closure, detect_people,
    parse_stories, reinforce_confidence, significance_label, slugify,
    story_key_for,
)

DIA = 86400.0


def nueva(ruta=None):
    """Memoria + capa narrativa sobre una base temporal."""
    ruta = ruta or (Path(tempfile.mkdtemp()) / "yue_test.db")
    memoria = Memory(str(ruta))
    return memoria, StoryMemory(memory=memoria), ruta


class Afecto:
    """Doble mínimo de `core.affect.AffectiveState` (solo lo que se lee aquí)."""

    def __init__(self, emocion="sadness", arousal=0.8, valence=-0.7):
        self.primary_emotion = emocion
        self.arousal = arousal
        self.valence = valence


# ===========================================================================
print("\n[0] normalización de claves")
# ===========================================================================
check("slugify quita tildes y mayúsculas", slugify("Andréa") == "andrea")
check("slugify une palabras", slugify("María José") == "maria_jose")
check("clave de persona", story_key_for("person", "Andrea") == "person:andrea")
check("relationship comparte espacio de claves con person",
      story_key_for("relationship", "Andrea") == "person:andrea")
check("clave de meta",
      story_key_for("goal", "aprender programación") == "goal:aprender_programacion")


# ===========================================================================
print("\n[1] crear la historia de una persona")
# ===========================================================================
m1, s1, _ = nueva()
t0 = time.time() - 30 * DIA
r = s1.observe("Andrea es una amiga muy cercana.", now=t0)
check("se crea la historia", r["action"] == "created" and len(r["stories"]) == 1)
h = m1.find_story_by_key("person:andrea")
check("existe con la clave normalizada", h is not None)
check("asciende a 'relationship' al decir la relación",
      h and h["story_type"] == "relationship")
ents = m1.get_story_entities(h["id"])
check("guarda la entidad con su relación",
      len(ents) == 1 and ents[0]["relation"] == "amiga")
check("lo dicho tiene confianza alta", ents[0]["relation_confidence"] >= 0.85)


# ===========================================================================
print("\n[2] volver a mencionarla hace MERGE, no duplica")
# ===========================================================================
s1.observe("Hoy discutí horrible con Andrea, me dolió lo que dijo.",
           affect=Afecto(), now=t0 + 10 * DIA)
check("sigue habiendo UNA sola historia", m1.count_stories() == 1)
h = m1.find_story_by_key("person:andrea")
check("cuenta las menciones", int(h["mention_count"]) >= 2)
check("no se crearon dos entidades para Andrea",
      len(m1.get_story_entities(h["id"])) == 1)


# ===========================================================================
print("\n[3] varios acontecimientos en la misma historia")
# ===========================================================================
s1.observe("Andrea me escribió otra vez y me sentí raro.",
           affect=Afecto("anxiety", 0.75), now=t0 + 15 * DIA)
eventos = m1.get_story_events(h["id"], limit=20)
check("hay más de un acontecimiento", len(eventos) >= 2)
check("están en orden cronológico",
      all(eventos[i]["happened_at"] <= eventos[i + 1]["happened_at"]
          for i in range(len(eventos) - 1)))
check("cada uno apunta a su mensaje/origen",
      all(e["source_type"] for e in eventos))


# ===========================================================================
print("\n[4] unresolved -> resolved")
# ===========================================================================
abiertos_antes = m1.get_story_events(h["id"], limit=20, only_unresolved=True)
check("la discusión constaba SIN resolver", len(abiertos_antes) >= 1)
r = s1.observe("Ya hablamos y nos reconciliamos.", now=t0 + 20 * DIA)
check("se reconoce el cierre", r["action"] == "resolved")
check("ya no queda nada pendiente",
      m1.get_story_events(h["id"], limit=20, only_unresolved=True) == [])
h = m1.find_story_by_key("person:andrea")
check("el estado presente se actualiza", bool(h["current_state"]))


# ===========================================================================
print("\n[5] los acontecimientos viejos SOBREVIVEN a la resolución")
# ===========================================================================
eventos = m1.get_story_events(h["id"], limit=20)
resumenes = " | ".join(e["summary"].lower() for e in eventos)
check("la discusión sigue en la historia", "discut" in resumenes)
check("y además está la reconciliación",
      any(e["event_type"] == "resolution" for e in eventos))
check("la discusión quedó marcada como resuelta, no borrada",
      any("discut" in e["summary"].lower() and e["resolved_at"] for e in eventos))
check("el pasado no se reescribió", len(eventos) >= 3)


# ===========================================================================
print("\n[6] confianza: lo explícito frente a lo inferido")
# ===========================================================================
m6, s6, _ = nueva()
s6.observe("Creo que Andrea estudia conmigo.", now=t0)
h6 = m6.find_story_by_key("person:andrea")
inferida = m6.get_story_entities(h6["id"])[0]["confidence"] if h6 else 1.0
m6b, s6b, _ = nueva()
s6b.observe("Andrea es mi mejor amiga.", now=t0)
h6b = m6b.find_story_by_key("person:andrea")
explicita = m6b.get_story_entities(h6b["id"])[0]["confidence"]
check("lo dicho vale más que lo supuesto", explicita > inferida)
check("una suposición no se guarda como certeza", inferida <= 0.60)
check("la corroboración sube la confianza",
      reinforce_confidence(0.55, explicit=True) > 0.55)
check("pero una inferencia repetida NO asciende a hecho",
      reinforce_confidence(reinforce_confidence(
          reinforce_confidence(0.55), ), ) <= 0.76)
check("y lo explícito puede llegar más alto",
      reinforce_confidence(0.90, explicit=True) > 0.90)


# ===========================================================================
print("\n[7] recuperar una historia por nombre")
# ===========================================================================
relevantes = s1.get_relevant_stories("Andrea volvió a escribirme",
                                     limit=2, now=t0 + 21 * DIA)
check("encuentra la historia de Andrea",
      len(relevantes) == 1 and relevantes[0]["story_key"] == "person:andrea")
bloque = s1.context_block("Andrea volvió a escribirme", now=t0 + 21 * DIA)
check("el bloque menciona a Andrea", "Andrea" in bloque)
check("el bloque prohíbe hablar de bases de datos",
      "base de datos" in bloque.lower())
check("el bloque prohíbe inventar", "inventes" in bloque.lower())


# ===========================================================================
print("\n[8] NO recuperar historias que no vienen a cuento")
# ===========================================================================
check("una consulta ajena no arrastra la historia",
      s1.get_relevant_stories("cómo configuro un servidor de Python",
                              limit=2, now=t0 + 21 * DIA) == [])
check("y el bloque queda vacío",
      s1.context_block("cómo configuro un servidor de Python",
                       now=t0 + 21 * DIA) == "")
check("sin consulta tampoco se recupera nada",
      s1.get_relevant_stories("", limit=2) == [])


# ===========================================================================
print("\n[9] NO crear historia para un detalle trivial")
# ===========================================================================
m9, s9, _ = nueva()
for trivial in ("Hoy comí pizza.", "Hace mucho calor.",
                "Vi un video gracioso en YouTube.", "Buenos días."):
    s9.observe(trivial, now=t0)
check("ninguna trivialidad crea historia", m9.count_stories() == 0)
# Un nombre suelto SIN señal tampoco basta.
s9.observe("Hoy fui a Trujillo.", now=t0)
check("un topónimo no se vuelve una persona", m9.count_stories() == 0)


# ===========================================================================
print("\n[10] una historia de meta conserva su motivación")
# ===========================================================================
m10, s10, _ = nueva()
m10.add_goal("aprender programación")
creadas = s10.sync_goals(now=t0)
check("la meta estrena su historia", creadas == 1)
hm = m10.find_story_by_key("goal:aprender_programacion")
check("con la clave normalizada de meta", hm is not None)
m10.update_story(hm["id"], motivation="crear YUE")
hm = m10.find_story_by_key("goal:aprender_programacion")
check("conserva la motivación", hm["motivation"] == "crear YUE")
check("la tabla goals sigue intacta",
      len(m10.list_goals(only_active=True)) == 1)


# ===========================================================================
print("\n[11] `progress` sigue en NULL sin evidencia")
# ===========================================================================
check("recién creada, el progreso es NULL", hm["progress"] is None)
propuesta = parse_stories(
    '{"stories":[{"story_type":"goal","subject":"aprender programación",'
    '"progress":0.72,"progress_evidence":null,"emotional_significance":0.9,'
    '"confidence":0.9}]}')
informe = {"created": 0, "updated": 0, "events": 0, "resolved": 0, "skipped": 0}
s10._aplicar_propuesta(propuesta[0], informe, now=t0)
hm = m10.find_story_by_key("goal:aprender_programacion")
check("un progreso SIN evidencia no se guarda", hm["progress"] is None)
propuesta[0]["progress_evidence"] = "terminó el módulo de memoria"
s10._aplicar_propuesta(propuesta[0], informe, now=t0)
hm = m10.find_story_by_key("goal:aprender_programacion")
check("con evidencia SÍ se guarda", hm["progress"] is not None)


# ===========================================================================
print("\n[12] persistencia tras reiniciar el objeto de memoria")
# ===========================================================================
m12, s12, ruta12 = nueva()
s12.observe("Andrea es mi amiga.", now=t0)
s12.observe("Discutí con Andrea y me dolió.", affect=Afecto(), now=t0 + 2 * DIA)
del m12, s12
m12b = Memory(str(ruta12))
s12b = StoryMemory(memory=m12b)
h12 = m12b.find_story_by_key("person:andrea")
check("la historia sobrevive al reinicio", h12 is not None)
check("y sus acontecimientos también",
      len(m12b.get_story_events(h12["id"], limit=10)) >= 1)
check("y sus entidades también", len(m12b.get_story_entities(h12["id"])) == 1)
check("se recupera igual tras reiniciar",
      len(s12b.get_relevant_stories("Andrea", limit=2, now=t0 + 3 * DIA)) == 1)


# ===========================================================================
print("\n[13] migración segura de una base creada ANTES de Story Memory")
# ===========================================================================
ruta13 = Path(tempfile.mkdtemp()) / "vieja.db"
con = sqlite3.connect(str(ruta13))
con.executescript(
    """
    CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT, content TEXT, ts REAL);
    CREATE TABLE facts(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, ts REAL);
    CREATE TABLE goals(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT,
        status TEXT DEFAULT 'activa', created REAL, done REAL);
    CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE memoria_larga(id INTEGER PRIMARY KEY AUTOINCREMENT,
        periodo_inicio REAL, periodo_fin REAL, resumen TEXT, ts REAL);
    -- Una tabla de historias de una versión ANTERIOR, sin varias columnas.
    CREATE TABLE memory_stories(id INTEGER PRIMARY KEY AUTOINCREMENT,
        story_type TEXT NOT NULL, story_key TEXT NOT NULL UNIQUE, title TEXT,
        status TEXT DEFAULT 'active', summary TEXT, created_at REAL,
        updated_at REAL);
    """)
con.execute("INSERT INTO messages(role,content,ts) VALUES('user','hola viejo',1.0)")
con.execute("INSERT INTO facts(text,ts) VALUES('se llama Jamir',1.0)")
con.execute("INSERT INTO memoria_larga(periodo_inicio,periodo_fin,resumen,ts) "
            "VALUES(0,1,'resumen antiguo',1.0)")
con.execute("INSERT INTO memory_stories(story_type,story_key,title,created_at,"
            "updated_at) VALUES('person','person:lucia','Lucía',1.0,1.0)")
con.commit()
con.close()

m13 = Memory(str(ruta13))  # no debe reventar ni borrar nada
check("los mensajes antiguos siguen ahí",
      len(m13.messages_between(0.0, time.time() + 1)) == 1)
check("los hechos antiguos siguen ahí", m13.get_facts() == ["se llama Jamir"])
check("memoria_larga sigue intacta",
      m13.get_long_term_summaries() == ["resumen antiguo"])
check("la historia antigua sobrevive",
      m13.find_story_by_key("person:lucia") is not None)
columnas = {f[1] for f in sqlite3.connect(str(ruta13))
            .execute("PRAGMA table_info(memory_stories)").fetchall()}
check("se añadieron las columnas que faltaban",
      {"emotional_significance", "confidence", "motivation", "progress",
       "current_state", "mention_count", "last_evidence_at"} <= columnas)
s13 = StoryMemory(memory=m13)
s13.observe("Lucía es mi hermana.", now=t0)
check("la historia antigua se REUTILIZA, no se duplica",
      m13.count_stories() == 1)
check("y ahora tiene su entidad",
      len(m13.get_story_entities(m13.find_story_by_key("person:lucia")["id"])) == 1)
# La migración es idempotente: abrir otra vez no rompe nada.
Memory(str(ruta13))
check("reabrir la base es idempotente", m13.count_stories() == 1)


# ===========================================================================
print("\n[14] tope de historias que entran al prompt")
# ===========================================================================
m14, s14, _ = nueva()
for nombre in ("Andrea", "Lucia", "Carla", "Marta"):
    s14.observe(f"{nombre} es mi amiga.", now=t0)
    s14.observe(f"Discutí con {nombre} y me dolió mucho.",
                affect=Afecto(), now=t0 + DIA)
check("hay cuatro historias", m14.count_stories() == 4)
consulta = "Hoy hablé con Andrea, Lucia, Carla y Marta"
check("se respeta el tope configurable",
      len(s14.get_relevant_stories(consulta, limit=2, now=t0 + 2 * DIA)) == 2)
check("el bloque no las mete todas",
      s14.context_block(consulta, now=t0 + 2 * DIA).count("\n- ") <= 2)


# ===========================================================================
print("\n[15] mayúsculas y tildes no duplican a la misma persona")
# ===========================================================================
m15, s15, _ = nueva()
s15.observe("Andrés es mi amigo.", now=t0)
s15.observe("Discutí con ANDRES ayer.", affect=Afecto(), now=t0 + DIA)
s15.observe("andres me escribió hoy.", affect=Afecto(), now=t0 + 2 * DIA)
check("una sola historia pese a las variantes", m15.count_stories() == 1)
h15 = m15.find_story_by_key("person:andres")
check("una sola entidad", len(m15.get_story_entities(h15["id"])) == 1)
check("todas las variantes suman menciones",
      int(h15["mention_count"]) >= 3)


# ===========================================================================
print("\n[16] significancia y detección (unidades sueltas)")
# ===========================================================================
alta = compute_significance(importance=0.9, intensity=0.9, mention_count=5,
                            span_days=40, unresolved=True, goal_related=True)
baja = compute_significance(importance=0.1, intensity=0.1, mention_count=1)
check("la significancia es un número en 0..1", 0.0 <= alta <= 1.0)
check("lo grave pesa más que lo leve", alta > baja)
check("lo pendiente pesa más que lo cerrado",
      compute_significance(importance=0.5, unresolved=True)
      > compute_significance(importance=0.5, unresolved=False))
check("la recurrencia suma",
      compute_significance(importance=0.5, mention_count=6)
      > compute_significance(importance=0.5, mention_count=1))
check("la etiqueta se deriva del número",
      significance_label(0.85) == "alta" and significance_label(0.1) == "baja")
check("detecta el cierre", detect_closure("ya hablamos y nos reconciliamos"))
check("no confunde un conflicto con un cierre",
      not detect_closure("discutí horrible con ella"))
personas = detect_people("Discutí con Andrea y con Lucía.")
check("detecta varias personas de un mensaje", len(personas) == 2)
check("no infiere una relación que él no dijo",
      all(p.relation is None for p in personas))
explicita = detect_people("Andrea es mi hermana.")
check("y sí recoge la que dijo",
      len(explicita) == 1 and explicita[0].relation == "hermana"
      and explicita[0].explicit)


# ===========================================================================
print("\n[17] el extractor con LLM no puede inventar recuerdos")
# ===========================================================================
check("respuesta vacía -> nada", parse_stories("") == [])
check("respuesta sin JSON -> nada", parse_stories("lo siento, no puedo") == [])
check("JSON roto -> nada", parse_stories("{stories: [") == [])
check("JSON sin historias -> nada", parse_stories('{"stories": []}') == [])
sucio = parse_stories(
    '```json\n{"stories":[{"story_type":"inventado","subject":"Andrea",'
    '"confidence":9.5,"events":[{"summary":"algo"}]}]}\n```')
check("tipo desconocido cae a 'other'",
      len(sucio) == 1 and sucio[0]["story_type"] == "other")
check("la confianza fuera de rango se recorta", sucio[0]["confidence"] <= 1.0)

# Una propuesta NO puede cerrar algo que nunca constó abierto.
m17, s17, _ = nueva()
s17.observe("Marta es mi amiga.", now=t0)
h17 = m17.find_story_by_key("person:marta")
informe = {"created": 0, "updated": 0, "events": 0, "resolved": 0, "skipped": 0}
s17._aplicar_propuesta(
    {"story_type": "person", "subject": "Marta", "story_key": "person:marta",
     "title": "Marta", "relation": None, "summary": "", "current_state": None,
     "motivation": None, "progress": None, "progress_evidence": None,
     "emotional_significance": 0.9, "confidence": 0.9,
     "resolves_previous": True, "events": []},
    informe, now=t0 + DIA)
check("no resuelve nada sin constancia de que estuviera abierto",
      informe["resolved"] == 0)
check("y no duplicó la historia", m17.count_stories() == 1)
# La confianza de lo que propone el modelo tiene techo.
h17 = m17.get_story(h17["id"])
check("la confianza del modelo no llega a certeza", h17["confidence"] <= 0.98)


# ===========================================================================
print("\n[18] convivencia con lo que ya existía")
# ===========================================================================
m18, s18, _ = nueva()
m18.add_message("user", "hola")
m18.add_long_term_summary(0, 1, "resumen consolidado de siempre")
m18.add_goal("terminar YUE")
m18.add_emotional_episode(event_type="exam", event_label="examen de mate",
                          emotion="anxiety", intensity=0.8, importance=0.8)
s18.observe("Andrea es mi amiga.", now=t0)
check("memoria_larga intacta",
      m18.get_long_term_summaries() == ["resumen consolidado de siempre"])
check("goals intacta", len(m18.list_goals()) == 1)
check("episodios intactos", m18.count_episodes() == 1)
check("messages_between conserva role y content",
      set(m18.messages_between(0, time.time() + 1)[0]) >= {"role", "content"})
check("y ahora además trae id y ts para trazar el origen",
      set(m18.messages_between(0, time.time() + 1)[0]) >= {"id", "ts"})
# El gancho de consolidación no rompe nada aunque no haya motor.
check("consolidate_stories sin motor no revienta",
      mc.consolidate_stories(m18, None) is not None)


print("\n" + ("TODO OK" if not fallos else f"FALLOS ({len(fallos)}): {fallos}"))
sys.exit(1 if fallos else 0)

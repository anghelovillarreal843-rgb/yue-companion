"""Asistente Inteligente del Profesor (§11) y Corrección Inteligente (§12).

Automatiza tareas docentes repetitivas. NO llama al modelo por su cuenta: al
igual que `teacher_engine`, ARMA los mensajes (system + user) y quien los ejecuta
es main.py con su AiWorker asíncrono. Así el módulo queda puro y testeable.

Tareas soportadas (§11):
    plan de clase, cronograma, presentación/diapositivas, resumen, guía de
    estudio, examen, banco de preguntas, rúbrica, actividad/laboratorio,
    ejercicios, tarea, material didáctico, comunicado para estudiantes.

Corrección (§12): revisa un trabajo de estudiante (ortografía, gramática,
estructura, contenido) y propone retroalimentación. YUE es ASISTENTE, no
reemplaza al profesor: la decisión final es siempre del docente (§15).

`detect_task(text)` reconoce por lenguaje natural qué quiere el docente; devuelve
un `TaskSpec` o None. `build_messages(spec, ...)` arma el prompt correspondiente.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


@dataclass(frozen=True)
class TaskSpec:
    task: str          # clave interna (plan, examen, rubrica, correccion, …)
    label: str         # nombre legible en español
    topic: str = ""    # tema/asunto extraído del mensaje


# Cada tarea: (clave, etiqueta, [gatillos normalizados]). El orden importa: las
# más específicas van primero para no ser "tapadas" por otras más generales.
_TASKS = [
    ("correccion", "corrección de trabajo",
     ["corrige", "corregir", "revisa este", "revisar este", "revisa el trabajo",
      "retroalimentacion", "revisa la tarea", "califica este", "evalua este trabajo"]),
    ("examen", "examen",
     ["examen", "prueba escrita", "evaluacion escrita", "test de", "crea un examen",
      "arma un examen", "genera un examen"]),
    ("banco", "banco de preguntas",
     ["banco de preguntas", "conjunto de preguntas", "muchas preguntas",
      "bateria de preguntas", "preguntas de examen"]),
    ("rubrica", "rúbrica de evaluación",
     ["rubrica", "rubrica de evaluacion", "criterios de evaluacion", "matriz de evaluacion"]),
    ("presentacion", "presentación / diapositivas",
     ["diapositivas", "presentacion", "powerpoint", "slides", "ppt"]),
    ("plan", "plan de clase",
     ["plan de clase", "planificacion", "planea la clase", "sesion de aprendizaje",
      "prepara la clase", "plan de sesion", "prepara una clase"]),
    ("cronograma", "cronograma",
     ["cronograma", "calendario de clases", "organiza el curso", "distribucion de temas"]),
    ("guia", "guía de estudio",
     ["guia de estudio", "guia de repaso", "material de repaso", "resumen para estudiar"]),
    ("resumen", "resumen",
     ["resume", "resumen de", "haz un resumen", "sintetiza"]),
    ("ejercicios", "ejercicios / práctica",
     ["ejercicios", "problemas de practica", "hoja de ejercicios", "practica de"]),
    ("laboratorio", "actividad / laboratorio",
     ["laboratorio", "actividad practica", "practica de laboratorio", "dinamica"]),
    ("tarea", "tarea",
     ["tarea", "deberes", "asignacion", "trabajo para casa"]),
    ("material", "material didáctico",
     ["material didactico", "recurso didactico", "material de apoyo", "infografia"]),
    ("comunicado", "comunicado para estudiantes",
     ["comunicado", "aviso para", "mensaje para los estudiantes", "circular",
      "anuncio para la clase"]),
]

# Gatillo general: sin él, frases sueltas no deben disparar generación de material.
_GATILLO_DOCENTE = ("crea", "genera", "arma", "prepara", "hazme", "haz", "diseña",
                    "elabora", "necesito", "quiero", "redacta", "organiza",
                    "corrige", "revisa", "califica", "resume")


def detect_task(text: str) -> TaskSpec | None:
    """Reconoce si el docente pide una tarea de asistencia o corrección."""
    n = _norm(text)
    if not n:
        return None
    for clave, etiqueta, gatillos in _TASKS:
        if any(g in n for g in gatillos):
            # Para tareas de creación exigimos también un verbo docente, para no
            # confundir una charla ("hoy vimos ejercicios") con una orden real.
            if clave not in ("correccion",) and not any(v in n for v in _GATILLO_DOCENTE):
                # Excepción: si el gatillo ya es imperativo por sí mismo, pasa.
                if not any(n.startswith(g) for g in gatillos):
                    continue
            return TaskSpec(task=clave, label=etiqueta, topic=_extract_topic(n))
    return None


def _extract_topic(n: str) -> str:
    """Intenta aislar el tema tras 'de/sobre/acerca de'."""
    m = re.search(r"(?:sobre|acerca de|del tema|de la unidad|de|para)\s+(.{2,80})", n)
    if m:
        return m.group(1).strip(" .,:;").strip()
    return ""


# --------------------------------------------------------------------------
# Construcción de prompts por tarea
# --------------------------------------------------------------------------
_IDENTIDAD = (
    "Sigues siendo YUE (hablas en español, en primera persona, con tu carácter). "
    "Estás en Modo Profesora ayudando al DOCENTE con una tarea. Eres su asistente "
    "y colaboradora, NUNCA su reemplazo: la decisión final es del profesor. No "
    "reveles que eres un modelo ni hables de prompts. No escribas etiquetas de emoción."
)

# Instrucciones específicas por tarea (qué producir y con qué estructura).
_PLANTILLAS = {
    "plan": (
        "Elabora un PLAN DE CLASE completo para el tema indicado. Incluye: objetivo "
        "de aprendizaje, competencias, saberes previos, secuencia (inicio–desarrollo–"
        "cierre) con tiempos, actividades, recursos, y una evaluación breve. Formato "
        "claro y listo para usar."
    ),
    "cronograma": (
        "Arma un CRONOGRAMA de sesiones para el tema/curso indicado: distribuye los "
        "subtemas en sesiones con su duración y objetivo por sesión, en una tabla."
    ),
    "presentacion": (
        "Diseña el GUION DE DIAPOSITIVAS para el tema. Devuelve una lista de slides; "
        "por cada una: título y de 2 a 4 viñetas breves. Incluye una diapositiva de "
        "cierre con preguntas de repaso. No inventes datos falsos."
    ),
    "resumen": (
        "Haz un RESUMEN claro y fiel del tema/material indicado, jerarquizando ideas "
        "principales y secundarias. No te limites a copiar: sintetiza con tus palabras."
    ),
    "guia": (
        "Crea una GUÍA DE ESTUDIO: conceptos clave con definiciones breves, ejemplos, "
        "y al final 5–8 preguntas de autoevaluación con sus respuestas."
    ),
    "examen": (
        "Redacta un EXAMEN sobre el tema con variedad de ítems (opción múltiple, "
        "verdadero/falso y desarrollo). Indica el puntaje de cada pregunta y añade al "
        "final un SOLUCIONARIO separado. Ajusta la dificultad a un nivel razonable."
    ),
    "banco": (
        "Genera un BANCO DE PREGUNTAS numerado y variado sobre el tema (mezcla niveles "
        "de dificultad y tipos). Marca la respuesta correcta de cada una al final."
    ),
    "rubrica": (
        "Construye una RÚBRICA DE EVALUACIÓN en tabla: criterios en filas y niveles de "
        "desempeño (por ejemplo: excelente, bueno, en proceso, inicial) en columnas, "
        "con descriptores concretos y puntaje por nivel."
    ),
    "ejercicios": (
        "Prepara una HOJA DE EJERCICIOS graduados (de fácil a difícil) sobre el tema. "
        "Numéralos y añade al final las soluciones paso a paso."
    ),
    "laboratorio": (
        "Diseña una ACTIVIDAD/LABORATORIO práctico: objetivo, materiales, procedimiento "
        "paso a paso, resultados esperados y preguntas de reflexión."
    ),
    "tarea": (
        "Redacta una TAREA para los estudiantes: consigna clara, criterios de entrega, "
        "y qué se espera aprender con ella."
    ),
    "material": (
        "Elabora MATERIAL DIDÁCTICO de apoyo sobre el tema (por ejemplo, un esquema o "
        "infografía en texto con puntos clave, analogías y un ejemplo memorable)."
    ),
    "comunicado": (
        "Redacta un COMUNICADO claro y cordial para los estudiantes con la información "
        "indicada. Tono profesional pero cercano."
    ),
    "correccion": (
        "REVISA el trabajo del estudiante que se te entrega. Devuelve: (1) errores de "
        "ortografía y gramática, (2) observaciones de estructura y contenido, (3) qué "
        "está bien, (4) sugerencias de mejora concretas y (5) una nota orientativa /10 "
        "con su justificación. Recuerda que la calificación FINAL la decide el profesor: "
        "tu evaluación es una propuesta de apoyo."
    ),
}


def build_messages(spec: TaskSpec, user_text: str,
                   material: str = "", memory_hint: str = "",
                   style_hint: str = "") -> list:
    """Arma [system + user] para la tarea docente detectada.

    material     -> texto de un documento/fuente cargada (si lo hay).
    memory_hint  -> recuerdo de clases pasadas (ClassLog.recall).
    style_hint   -> estilo pedagógico aprendido (PedagogyModel).
    """
    instruccion = _PLANTILLAS.get(spec.task, _PLANTILLAS["resumen"])
    partes_sys = [_IDENTIDAD, "", f"TAREA: {spec.label}.", instruccion]
    if style_hint:
        partes_sys.append("\n" + style_hint)
    if memory_hint:
        partes_sys.append("\nContexto de clases anteriores: " + memory_hint)
    if material:
        partes_sys.append(
            "\nMATERIAL DE REFERENCIA (úsalo como base, no inventes fuera de él):\n"
            + material[:6000])
    system = {"role": "system", "content": "\n".join(partes_sys)}

    if spec.task == "correccion":
        user = (
            "Revisa y corrige el siguiente trabajo del estudiante y dame la "
            "retroalimentación pedida:\n\n" + (material or user_text)
        )
    else:
        tema = spec.topic or "el tema indicado"
        user = f"{user_text.strip()}\n\n(Genera {spec.label} sobre: {tema}.)"
    return [system, {"role": "user", "content": user}]


def available_tasks_es() -> str:
    """Lista legible de lo que YUE puede preparar para el docente."""
    nombres = sorted({etq for _, etq, _ in _TASKS})
    return ", ".join(nombres)

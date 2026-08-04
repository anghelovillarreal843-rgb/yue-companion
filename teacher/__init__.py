"""Modo Profesora de YUE: motor, progreso, lectura y comprensión de material,
memoria de clases, aprendizaje del profesor, asistencia docente y participación.

Público:
    TeacherEngine    -> motor de la clase (prompt, evaluación, progreso, reportes)
    ProgressStore    -> almacén de progreso de alumnos (aciertos/notas)
    ClassLog         -> memoria de clases y estadísticas educativas (§5/§6/§13/§14)
    PedagogyModel    -> modelo pedagógico aprendido del profesor (§4)
    structure        -> comprensión estructurada de documentos (§2/§3)
    assistant        -> asistente docente y corrección de trabajos (§11/§12)
    participation    -> participación activa durante la clase (§8)
    documents        -> lectores de documentos/web/OCR (degradables §2)
"""
from teacher.teacher_engine import TeacherEngine
from teacher.progress import ProgressStore, EvalItem
from teacher.classlog import ClassLog
from teacher.pedagogy import PedagogyModel
from teacher import documents
from teacher import structure
from teacher import assistant
from teacher import participation

__all__ = [
    "TeacherEngine", "ProgressStore", "EvalItem", "ClassLog", "PedagogyModel",
    "documents", "structure", "assistant", "participation",
]

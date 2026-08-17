"""Directores del motor (PR 5, REFACTOR_SPEC §4).

Cada director es una clase autocontenida que NUNCA conoce al Controller
(main.py): recibe sus dependencias por inyección o vía ControllerContext,
el service-locator compartido (patrón WorkerRegistry).
"""
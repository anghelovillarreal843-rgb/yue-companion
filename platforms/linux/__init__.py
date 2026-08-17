"""Stub Linux de PlatformController (punto de extension futuro).

PR 2: NO implementa control real.  Hereda de DegradedController, así
main.py arranca sin ventanas/UI nativas y toda operación devuelve el
valor degradado seguro ([] / "" / None / False), nunca lanza.

Extensión futura: implementar aquí (p. ej. vía AT-SPI/GTK o i3ipc) y
sobrescribir ``is_available`` -> True cuando exista control real.

La firma ``raise_if_production`` existe para que el código nuevo que
requiera control real (y no pueda degradar) falle con un mensaje claro
en lugar de fingir éxito; NO se usa en el flujo normal.
"""

from __future__ import annotations

from platforms.contracts import DegradedController


class LinuxController(DegradedController):
    """Control de plataforma Linux: degradado por ahora, extension futura."""

    def raise_if_production(self) -> None:
        raise NotImplementedError(
            "Controle de plataforma Linux aún no implementado: "
            "las operaciones de ventana/UI/office requieren la extensión futura."
        )
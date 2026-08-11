"""ProposalArbiter — quién manda sobre el comportamiento de YUE, y hasta cuándo.

Todos los módulos (conversación, profesora, multimedia, ambiental, seguridad)
PROPONEN. Ninguno decide. Aquí se decide.

La regla es la prioridad, no la confianza ni el orden de llegada:

    EMERGENCY    100   seguridad: manda sobre todo
    USER          90   apoyo directo al usuario
    TEACHER       80   modo profesora
    CONVERSATION  70   conversación activa
    EMOTION       60   reacción emocional
    MEDIA         40   música y vídeo
    AMBIENT       20   ambiente, ruido de fondo
    IDLE          10   reposo

Lo importante —y lo que faltaba antes— es qué pasa cuando una propuesta CADUCA.
No se vuelve a neutral sin pensar: se recalcula. Las propuestas de menor
prioridad siguen vivas ahí abajo, esperando su turno:

    la profesora termina de explicar   (TEACHER caduca)
        ↓
    se recalcula
        ↓
    la conversación sigue activa       (CONVERSATION gana)

Es la diferencia entre un avatar que se apaga cada pocos segundos y uno que se
comporta con continuidad.

Cada fuente tiene UNA propuesta viva: la nueva sustituye a la anterior. Así una
fuente ruidosa no puede inundar el árbitro.
"""
from __future__ import annotations

import threading
import time

from .models import IDLE_PROPOSAL, YueProposal


class ProposalArbiter:
    """Mantiene las propuestas activas y decide cuál gobierna a YUE."""

    def __init__(self):
        self._lock = threading.RLock()
        self._proposals: dict[str, YueProposal] = {}
        #: Contador de llegada: desempata entre propuestas de igual prioridad.
        #: Gana la más reciente, que es la que refleja la situación actual.
        self._seq = 0
        self._order: dict[str, int] = {}

    # ------------------------------------------------------------------ API
    def submit(self, proposal: YueProposal) -> bool:
        """Registra una propuesta. Devuelve si es la que gobierna ahora.

        Importante: devolver False NO significa que se haya perdido. La
        propuesta queda viva y puede ganar en cuanto caduque la que manda.
        """
        if proposal is None:
            return False
        with self._lock:
            self._seq += 1
            self._proposals[proposal.source] = proposal
            self._order[proposal.source] = self._seq
            ganadora = self._winner_locked()
            return ganadora.source == proposal.source

    def withdraw(self, source: str) -> bool:
        """Una fuente retira su propuesta (terminó de hacer lo suyo)."""
        clave = (source or "").strip().lower()
        with self._lock:
            self._order.pop(clave, None)
            return self._proposals.pop(clave, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._proposals.clear()
            self._order.clear()

    def winner(self, now: float | None = None) -> YueProposal:
        """La propuesta que gobierna a YUE ahora mismo."""
        with self._lock:
            return self._winner_locked(now)

    def active(self, now: float | None = None) -> list[YueProposal]:
        """Propuestas vivas, de más a menos autoridad."""
        now = time.time() if now is None else now
        with self._lock:
            vivas = [p for p in self._proposals.values() if not p.is_expired(now)]
            vivas.sort(key=lambda p: (p.priority, self._order.get(p.source, 0)),
                       reverse=True)
            return vivas

    def prune(self, now: float | None = None) -> list[str]:
        """Elimina las propuestas caducadas. Devuelve qué fuentes cayeron.

        Lo llama `tick()`. Es lo que dispara el recálculo del punto 23: cuando
        aquí cae la propuesta de la profesora, arriba se vuelve a elegir ganador
        y la conversación recupera el mando sola.
        """
        now = time.time() if now is None else now
        with self._lock:
            caducadas = [s for s, p in self._proposals.items() if p.is_expired(now)]
            for s in caducadas:
                self._proposals.pop(s, None)
                self._order.pop(s, None)
            return caducadas

    def would_win(self, priority: int, source: str = "",
                  now: float | None = None) -> bool:
        """¿Ganaría una propuesta con esta prioridad, sin llegar a enviarla?

        Útil para que un módulo caro (visión, multimedia) no se moleste en
        calcular una reacción que iba a descartarse igualmente.
        """
        actual = self.winner(now)
        if actual.source == (source or "").strip().lower():
            return True
        return int(priority) >= int(actual.priority)

    def snapshot(self) -> list[dict]:
        """Para el panel de depuración: quién propone qué y cuánto le queda."""
        return [p.to_dict() for p in self.active()]

    # -------------------------------------------------------------- interno
    def _winner_locked(self, now: float | None = None) -> YueProposal:
        now = time.time() if now is None else now
        vivas = [p for p in self._proposals.values() if not p.is_expired(now)]
        if not vivas:
            return IDLE_PROPOSAL
        # Mayor prioridad; a igualdad, la que llegó más tarde.
        return max(vivas, key=lambda p: (p.priority, self._order.get(p.source, 0)))

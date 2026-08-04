"""Gestor central de recursos físicos (FASE 2 · Arquitectura).

Resuelve un problema real de YUE: que dos módulos abran la MISMA cámara a la vez
(cámara clásica vs Vision V3), o que voz y música peleen por el altavoz. Aquí un
recurso físico tiene UN solo dueño a la vez. Si otro con MAYOR prioridad lo
necesita, se avisa al dueño actual (evento) y se le cede; si tiene prioridad
igual o menor, la petición se rechaza limpiamente.

Uso típico:
    rm = ResourceManager()
    tok = rm.acquire(Resource.CAMERA, "vision_v3", priority=60)
    if tok:
        ...usar la cámara...
        rm.release(tok)

    # o con context manager:
    with rm.acquire_ctx(Resource.CAMERA, "clasico", priority=40) as tok:
        if tok: ...

ADITIVO: los módulos existentes siguen funcionando; pueden adoptar esto poco a
poco para dejar de chocar por el hardware.
"""
from __future__ import annotations

import enum
import itertools
import threading
import time
from dataclasses import dataclass, field

from .events import EventBus


class Resource(str, enum.Enum):
    CAMERA = "camera"
    MICROPHONE = "microphone"
    SPEAKERS = "speakers"
    SCREEN = "screen"
    AVATAR = "avatar"
    DATABASE = "database"
    AI_MODEL = "ai_model"
    PC_AUTOMATION = "pc_automation"


@dataclass
class Lease:
    """Comprobante de que un dueño tiene un recurso reservado."""
    id: int
    resource: Resource
    owner: str
    priority: int
    acquired_at: float = field(default_factory=time.time)
    active: bool = True


class ResourceBusy(Exception):
    """Se pidió un recurso ocupado por alguien de prioridad >= a la tuya."""

    def __init__(self, resource: Resource, holder: str, holder_priority: int):
        super().__init__(
            f"'{resource.value}' está en uso por '{holder}' (prioridad {holder_priority})"
        )
        self.resource = resource
        self.holder = holder
        self.holder_priority = holder_priority


class ResourceManager:
    def __init__(self, bus: EventBus | None = None):
        self._lock = threading.RLock()
        self._holders: dict[Resource, Lease] = {}
        self._ids = itertools.count(1)
        self.bus = bus or EventBus()

    def acquire(self, resource: Resource, owner: str, priority: int = 0,
                raise_on_busy: bool = False) -> Lease | None:
        """Reserva un recurso. Devuelve un Lease o None si está ocupado.

        Si el recurso lo tiene alguien con prioridad ESTRICTAMENTE menor, se le
        expropia: se marca su lease como inactivo y se emite el evento
        'recurso_expropiado' para que ese dueño suelte el hardware.
        """
        resource = Resource(resource)
        with self._lock:
            current = self._holders.get(resource)
            if current and current.active:
                if priority > current.priority:
                    # Expropiación: avisar al dueño anterior.
                    current.active = False
                    self.bus.publish(
                        "recurso_expropiado",
                        resource=resource, previous_owner=current.owner,
                        new_owner=owner, lease_id=current.id,
                    )
                else:
                    if raise_on_busy:
                        raise ResourceBusy(resource, current.owner, current.priority)
                    return None
            lease = Lease(id=next(self._ids), resource=resource,
                          owner=owner, priority=priority)
            self._holders[resource] = lease
        self.bus.publish("recurso_adquirido", resource=resource,
                         owner=owner, priority=priority, lease_id=lease.id)
        return lease

    def release(self, lease: Lease | None) -> bool:
        """Libera el recurso si el lease sigue siendo el dueño activo."""
        if lease is None:
            return False
        with self._lock:
            current = self._holders.get(lease.resource)
            if current is not None and current.id == lease.id and current.active:
                current.active = False
                del self._holders[lease.resource]
                released = True
            else:
                released = False
        if released:
            self.bus.publish("recurso_liberado", resource=lease.resource,
                             owner=lease.owner, lease_id=lease.id)
        return released

    def owner_of(self, resource: Resource) -> str | None:
        with self._lock:
            cur = self._holders.get(Resource(resource))
            return cur.owner if cur and cur.active else None

    def is_free(self, resource: Resource) -> bool:
        return self.owner_of(resource) is None

    def snapshot(self) -> dict[str, str]:
        """Estado legible: qué módulo tiene cada recurso ahora mismo."""
        with self._lock:
            return {
                r.value: (l.owner if l.active else "—")
                for r, l in self._holders.items()
            }

    class _Ctx:
        def __init__(self, rm, resource, owner, priority):
            self._rm, self._r, self._o, self._p = rm, resource, owner, priority
            self.lease = None

        def __enter__(self):
            self.lease = self._rm.acquire(self._r, self._o, self._p)
            return self.lease

        def __exit__(self, *exc):
            self._rm.release(self.lease)
            return False

    def acquire_ctx(self, resource: Resource, owner: str, priority: int = 0):
        """Versión con 'with': libera el recurso automáticamente al salir."""
        return ResourceManager._Ctx(self, resource, owner, priority)

"""Bloqueos de recursos compartidos del escritorio."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager


class LockTimeout(RuntimeError):
    pass


class LocksManager:
    """Gestiona locks reentrantes y los adquiere siempre en orden estable."""

    CORE_RESOURCES = ("keyboard", "mouse", "clipboard", "active_window")

    def __init__(self):
        self._guard = threading.RLock()
        self._locks: dict[str, threading.RLock] = {
            name: threading.RLock() for name in self.CORE_RESOURCES
        }

    def _get(self, name: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(name, threading.RLock())

    @contextmanager
    def acquire(self, resources, timeout: float = 15.0):
        names = sorted(set(str(r) for r in resources if r))
        acquired: list[threading.RLock] = []
        deadline = time.monotonic() + max(0.05, timeout)
        try:
            for name in names:
                lock = self._get(name)
                remaining = max(0.0, deadline - time.monotonic())
                if not lock.acquire(timeout=remaining):
                    raise LockTimeout(f"Timeout esperando el lock {name!r}")
                acquired.append(lock)
            yield
        finally:
            for lock in reversed(acquired):
                lock.release()

    @staticmethod
    def conflict(resources_a, resources_b) -> bool:
        return bool(set(resources_a) & set(resources_b))

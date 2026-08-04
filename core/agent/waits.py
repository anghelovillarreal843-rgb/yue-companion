"""Esperas dinámicas basadas en condiciones observables."""
from __future__ import annotations

import time
from typing import Callable


class WaitTimeout(RuntimeError):
    pass


class SmartWaiter:
    def __init__(self, cancel_check: Callable[[], None] | None = None):
        self.cancel_check = cancel_check or (lambda: None)

    def until(
        self,
        predicate: Callable[[], object],
        *,
        timeout: float = 10.0,
        interval: float = 0.12,
        description: str = "condición",
    ):
        deadline = time.monotonic() + max(0.05, timeout)
        delay = max(0.02, interval)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            self.cancel_check()
            try:
                value = predicate()
                if value:
                    return value
            except Exception as exc:  # la condición puede no estar lista todavía
                last_error = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(delay, remaining))
            delay = min(0.6, delay * 1.25)
        suffix = f" ({last_error})" if last_error else ""
        raise WaitTimeout(f"Timeout esperando {description}{suffix}")

    def until_stable(
        self,
        probe: Callable[[], object],
        *,
        timeout: float = 10.0,
        interval: float = 0.12,
        stable_samples: int = 3,
        description: str = "un estado estable",
    ):
        """Espera hasta observar el mismo estado varias veces consecutivas.

        No supone cuánto tarda una interfaz: mide estabilidad y respeta timeout
        y cancelación. Es útil cuando no existe un evento nativo de «carga lista».
        """
        needed = max(2, int(stable_samples))
        state = {"seen": False, "value": None, "count": 0}

        def stable():
            value = probe()
            if state["seen"] and value == state["value"]:
                state["count"] += 1
            else:
                state["seen"] = True
                state["value"] = value
                state["count"] = 1
            return value if state["count"] >= needed else None

        return self.until(
            stable, timeout=timeout, interval=interval, description=description,
        )

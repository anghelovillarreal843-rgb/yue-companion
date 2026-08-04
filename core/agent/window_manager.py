"""Gestión de ventanas desacoplada del planificador."""
from __future__ import annotations

from typing import Callable

from .waits import SmartWaiter


class WindowManager:
    def __init__(self, desktop_module, waiter: SmartWaiter):
        self.desktop = desktop_module
        self.waiter = waiter

    def list(self, limit: int = 40) -> list[dict]:
        return list(self.desktop.list_windows(limit=limit) or [])

    def active_title(self) -> str:
        try:
            return str(self.desktop.active_window_title() or "")
        except Exception:
            return ""

    def find(self, hints, threshold: float = 0.72) -> dict | None:
        best = None
        best_score = 0.0
        hints = [str(h) for h in hints if str(h).strip()]
        for window in self.list():
            title = str(window.get("title", ""))
            if not title:
                continue
            score = max((self.desktop.similarity(h, title) for h in hints), default=0.0)
            if score > best_score:
                best, best_score = window, score
        return best if best and best_score >= threshold else None

    def focus(self, title: str) -> str:
        return self.desktop.focus_window(title)

    def wait_present(self, hints, timeout: float = 10.0) -> dict:
        return self.waiter.until(
            lambda: self.find(hints), timeout=timeout,
            description=f"la ventana {' / '.join(str(x) for x in hints)}",
        )

    def wait_absent(self, title: str, timeout: float = 8.0) -> bool:
        return bool(self.waiter.until(
            lambda: not self.find([title], threshold=0.82),
            timeout=timeout,
            description=f"el cierre de {title}",
        ))

    def wait_focused(self, title: str, timeout: float = 5.0) -> str:
        return self.waiter.until(
            lambda: self.active_title() if self.desktop.similarity(title, self.active_title()) >= 0.72 else "",
            timeout=timeout,
            description=f"el foco en {title}",
        )

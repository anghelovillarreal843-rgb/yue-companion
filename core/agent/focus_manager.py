"""Garantías de foco antes de acciones interactivas."""
from __future__ import annotations


class FocusManager:
    def __init__(self, windows):
        self.windows = windows

    def ensure_window(self, title: str, timeout: float = 5.0) -> str:
        current = self.windows.active_title()
        if current and self.windows.desktop.similarity(title, current) >= 0.72:
            return current
        focused = self.windows.focus(title)
        return self.windows.wait_focused(focused or title, timeout=timeout)

    def is_focused(self, title: str) -> bool:
        current = self.windows.active_title()
        return bool(current and self.windows.desktop.similarity(title, current) >= 0.72)

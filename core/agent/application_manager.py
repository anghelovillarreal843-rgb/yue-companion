"""Detección, reutilización y apertura idempotente de aplicaciones."""
from __future__ import annotations

from typing import Callable


class ApplicationManager:
    def __init__(
        self, windows, aliases: dict[str, str],
        window_hints: dict[str, tuple[str, ...]], focus=None,
    ):
        self.windows = windows
        self.aliases = aliases
        self.window_hints = window_hints
        self.focus = focus

    def canonical(self, requested: str) -> str:
        key = str(requested or "").strip().lower()
        return self.aliases.get(key, key)

    def hints_for(self, requested: str) -> tuple[str, ...]:
        alias = self.canonical(requested)
        return self.window_hints.get(alias, (requested, alias))

    def find_open(self, requested: str) -> dict | None:
        return self.windows.find(self.hints_for(requested))

    def ensure_open(
        self,
        requested: str,
        launcher: Callable[[str], str | None],
        *,
        timeout: float = 12.0,
    ) -> tuple[str, bool, str]:
        existing = self.find_open(requested)
        if existing:
            title = str(existing.get("title", ""))
            if self.focus is not None:
                self.focus.ensure_window(title, timeout=min(timeout, 5.0))
            else:
                focused = self.windows.focus(title)
                self.windows.wait_focused(focused or title, timeout=min(timeout, 5.0))
            return title, True, "instancia existente enfocada"
        detail = launcher(requested) or "inicio solicitado"
        found = self.windows.wait_present(self.hints_for(requested), timeout=timeout)
        title = str(found.get("title", ""))
        if title:
            try:
                if self.focus is not None:
                    self.focus.ensure_window(title, timeout=min(timeout, 5.0))
                else:
                    self.windows.focus(title)
            except Exception:
                pass
        return title, False, detail

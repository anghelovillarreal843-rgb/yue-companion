"""Verificación posterior a cada acción mediante estado observable."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .models import AgentTask, VerificationOutcome
from .waits import SmartWaiter, WaitTimeout


@dataclass(slots=True)
class Observation:
    active_window: str = ""
    windows: tuple[str, ...] = ()
    window_rects: dict[str, tuple[int, int, int, int]] = None
    screen_signature: object = None


class VerificationEngine:
    def __init__(
        self,
        *,
        windows,
        applications,
        waiter: SmartWaiter,
        screen_signature=None,
        screen_changed=None,
        focused_element=None,
        element_present=None,
    ):
        self.windows = windows
        self.applications = applications
        self.waiter = waiter
        self.screen_signature = screen_signature
        self.screen_changed = screen_changed
        self.focused_element = focused_element
        self.element_present = element_present

    def capture(self, task: AgentTask) -> Observation:
        listed = list(self.windows.list() or [])
        titles = tuple(str(w.get("title", "")) for w in listed if w.get("title"))
        rects = {
            str(w.get("title", "")): tuple(int(value) for value in w.get("rect", [])[:4])
            for w in listed if w.get("title") and len(w.get("rect", []) or []) >= 4
        }
        signature = None
        if task.verification.kind in {
            "ui_effect", "browser_ready", "path_open", "file_saved", "window_moved"
        } and self.screen_signature:
            try:
                signature = self.screen_signature()
            except Exception:
                signature = None
        return Observation(self.windows.active_title(), titles, rects, signature)

    def verify(self, task: AgentTask, raw_ok: bool, raw_detail: str, before: Observation) -> VerificationOutcome:
        if not raw_ok:
            return VerificationOutcome(False, raw_detail)
        kind = task.verification.kind
        timeout = task.verification.timeout
        try:
            if kind in {
                "app_open", "browser_ready", "office_ready", "window_focused",
                "window_closed", "window_moved", "ui_effect", "office_content",
            } and not self._desktop_available():
                return VerificationOutcome(
                    True, raw_detail + " · verificación UI no disponible en este sistema"
                )
            if kind in {"none", "cursor"}:
                return VerificationOutcome(True, raw_detail)
            if kind == "condition":
                return self._verify_condition(task, before)
            if kind == "app_open":
                found = self.applications.find_open(str(task.params.get("name", "")))
                if not found:
                    found = self.windows.wait_present(
                        self.applications.hints_for(str(task.params.get("name", ""))),
                        timeout=timeout,
                    )
                return VerificationOutcome(True, raw_detail, {"window": found.get("title", "")})
            if kind == "browser_ready":
                hints = (
                    "Google Chrome", "Microsoft Edge", "Mozilla Firefox",
                    "Chrome", "Edge", "Firefox",
                )
                before_had_browser = any(
                    max((self.windows.desktop.similarity(hint, title) for hint in hints), default=0.0) >= 0.62
                    for title in before.windows
                )

                def browser_effect():
                    browser = self.windows.find(hints, threshold=0.62)
                    if not browser:
                        return None
                    if not before_had_browser or self._observable_state_changed(before):
                        return browser
                    return None

                browser = self.waiter.until(
                    browser_effect, timeout=timeout, description="la navegación solicitada",
                )
                return VerificationOutcome(True, raw_detail, {"window": browser.get("title", "")})
            if kind == "path_open":
                path = Path(str(task.params.get("path", ""))).expanduser()
                if not path.exists():
                    return VerificationOutcome(False, raw_detail, {"path": str(path), "exists": False})
                if not self._desktop_available():
                    return VerificationOutcome(True, raw_detail, {"path": str(path), "exists": True})
                target_name = path.stem or path.name
                active = self.windows.active_title()
                if target_name and active and self.windows.desktop.similarity(target_name, active) >= 0.68:
                    return VerificationOutcome(True, raw_detail, {"path": str(path), "active_window": active})
                self.waiter.until(
                    lambda: self._observable_state_changed(before), timeout=timeout,
                    description=f"la apertura de {path.name}",
                )
                return VerificationOutcome(True, raw_detail, {"path": str(path), "exists": True})
            if kind == "office_ready":
                app = str(task.params.get("app", ""))
                found = self.applications.find_open(app)
                if found or "método=COM" in raw_detail:
                    return VerificationOutcome(True, raw_detail, {"window": (found or {}).get("title", "")})
                found = self.windows.wait_present(self.applications.hints_for(app), timeout=timeout)
                return VerificationOutcome(True, raw_detail, {"window": found.get("title", "")})
            if kind == "window_focused":
                title = str(task.params.get("title", ""))
                active = self.windows.wait_focused(title, timeout=timeout)
                return VerificationOutcome(True, raw_detail, {"active_window": active})
            if kind == "window_closed":
                self.windows.wait_absent(str(task.params.get("title", "")), timeout=timeout)
                return VerificationOutcome(True, raw_detail)
            if kind in {"file_exists", "file_saved"}:
                path = self._expected_path(task, raw_detail)
                if path:
                    found = self.waiter.until(
                        lambda: Path(path).expanduser().exists(), timeout=timeout,
                        description=f"el archivo {path}",
                    )
                    return VerificationOutcome(True, raw_detail, {"path": str(path), "exists": bool(found)})
                if kind == "file_saved" and self._desktop_available():
                    self.waiter.until(
                        lambda: self._observable_state_changed(before), timeout=timeout,
                        description="la confirmación visual del guardado",
                    )
                    return VerificationOutcome(True, raw_detail, {"state_changed": True})
                return VerificationOutcome(True, raw_detail)
            if kind == "office_content":
                # COM ya confirma inserción; el fallback se valida por efecto visual.
                if "método=COM" in raw_detail:
                    return VerificationOutcome(True, raw_detail)
                return self._verify_ui_effect(task, raw_detail, before)
            if kind == "window_moved":
                title = str(task.params.get("title", ""))
                expected_x = int(task.params.get("x", 0))
                expected_y = int(task.params.get("y", 0))

                def moved():
                    found = self.windows.find([title], threshold=0.72)
                    rect = list((found or {}).get("rect", []) or [])
                    if len(rect) < 4:
                        return None
                    if abs(int(rect[0]) - expected_x) > 4 or abs(int(rect[1]) - expected_y) > 4:
                        return None
                    width = task.params.get("width")
                    height = task.params.get("height")
                    if width is not None and abs((int(rect[2]) - int(rect[0])) - int(width)) > 6:
                        return None
                    if height is not None and abs((int(rect[3]) - int(rect[1])) - int(height)) > 6:
                        return None
                    return rect

                rect = self.waiter.until(moved, timeout=timeout, description=f"el movimiento de {title}")
                return VerificationOutcome(True, raw_detail, {"rect": rect})
            if kind == "ui_effect":
                return self._verify_ui_effect(task, raw_detail, before)
            return VerificationOutcome(True, raw_detail)
        except WaitTimeout as exc:
            return VerificationOutcome(False, f"{raw_detail} · {exc}")
        except Exception as exc:
            return VerificationOutcome(False, f"{raw_detail} · verificación falló: {exc}")

    def _verify_ui_effect(self, task: AgentTask, detail: str, before: Observation) -> VerificationOutcome:
        if before.screen_signature is None or not self.screen_signature or not self.screen_changed:
            # Cuando UIA confirma foco o el handler nativo confirmó la operación,
            # no inventamos un fallo solo porque el diff no está disponible.
            return VerificationOutcome(True, detail)

        def changed():
            after = self.screen_signature()
            if after is not None and self.screen_changed(before.screen_signature, after):
                return True
            if task.action == "click_element" and self.focused_element:
                return bool(self.focused_element(str(task.params.get("name", ""))))
            return False

        try:
            self.waiter.until(
                changed,
                timeout=min(task.verification.timeout, 4.0),
                description="el efecto visible de la acción",
            )
            return VerificationOutcome(True, detail)
        except WaitTimeout:
            return VerificationOutcome(False, f"{detail} · sin efecto verificable")

    def _verify_condition(self, task: AgentTask, before: Observation) -> VerificationOutcome:
        condition = str(task.params.get("condition", "state_change")).strip().lower()
        timeout = float(task.params.get("timeout", task.verification.timeout))
        expected = task.params.get("expected") or task.verification.expected
        if condition in {"window", "app", "app_ready", "application_ready"}:
            target = str(expected or task.params.get("target") or task.params.get("app") or "")
            found = self.windows.wait_present(self.applications.hints_for(target), timeout=timeout)
            return VerificationOutcome(True, f"condición cumplida: {condition}", {"window": found.get("title", "")})
        if condition == "active_window":
            title = str(expected or task.params.get("target") or "")
            active = self.windows.wait_focused(title, timeout=timeout)
            return VerificationOutcome(True, "ventana enfocada", {"active_window": active})
        if condition == "file_exists":
            path = Path(str(expected or task.params.get("path") or "")).expanduser()
            self.waiter.until(path.exists, timeout=timeout, description=f"el archivo {path}")
            return VerificationOutcome(True, "archivo disponible", {"path": str(path)})
        if condition in {"element", "element_present", "control_present"}:
            target = str(expected or task.params.get("target") or task.params.get("name") or "")
            if not target or self.element_present is None:
                return VerificationOutcome(False, "no existe un detector de elementos disponible")
            found = self.waiter.until(
                lambda: self.element_present(target), timeout=timeout,
                description=f"el elemento {target}",
            )
            return VerificationOutcome(True, "elemento disponible", {"element": target, "match": found})
        if condition == "process":
            name = str(expected or task.params.get("process") or "").lower()
            try:
                import psutil
                self.waiter.until(
                    lambda: any(name in (p.info.get("name") or "").lower()
                                for p in psutil.process_iter(["name"])),
                    timeout=timeout, description=f"el proceso {name}",
                )
                return VerificationOutcome(True, "proceso disponible", {"process": name})
            except ImportError:
                return VerificationOutcome(False, "psutil no está disponible para esperar procesos")
        if condition in {
            "cursor_available", "mouse_available", "keyboard_available",
            "clipboard_available", "active_window_available",
        }:
            # TaskExecutor ya adquirió el lock dinámico de este recurso.
            return VerificationOutcome(True, f"recurso disponible: {condition}")
        if condition in {"ui_stable", "application_stable"}:
            observed = self.waiter.until_stable(
                self._stability_probe, timeout=timeout,
                interval=task.verification.poll_interval,
                description="la estabilidad de la interfaz",
            )
            return VerificationOutcome(True, "interfaz estable", {"state": str(observed)[:200]})
        if condition == "state_change":
            self.waiter.until(
                lambda: self._observable_state_changed(before), timeout=timeout,
                interval=task.verification.poll_interval,
                description="un cambio observable de estado",
            )
            return VerificationOutcome(True, "estado modificado")
        return VerificationOutcome(False, f"condición de espera no soportada: {condition}")

    def _stability_probe(self):
        titles = tuple(str(w.get("title", "")) for w in self.windows.list() if w.get("title"))
        signature = None
        if self.screen_signature:
            try:
                signature = self.screen_signature()
            except Exception:
                signature = None
        return self.windows.active_title(), titles, signature

    def _window_state_changed(self, before: Observation) -> bool:
        active = self.windows.active_title()
        titles = tuple(str(w.get("title", "")) for w in self.windows.list() if w.get("title"))
        return active != before.active_window or titles != before.windows

    def _observable_state_changed(self, before: Observation) -> bool:
        if self._window_state_changed(before):
            return True
        if before.screen_signature is not None and self.screen_signature and self.screen_changed:
            try:
                after = self.screen_signature()
                return after is not None and self.screen_changed(before.screen_signature, after)
            except Exception:
                return False
        return False

    def _desktop_available(self) -> bool:
        try:
            available = getattr(self.windows.desktop, "available", None)
            return bool(available()) if callable(available) else True
        except Exception:
            return False

    @staticmethod
    def _expected_path(task: AgentTask, detail: str) -> str:
        path = str(task.params.get("path", "")).strip()
        if path:
            return path
        # Los handlers de Office incluyen la ruta al final del detalle.
        for token in reversed(detail.split(" · ")):
            token = token.strip()
            if token and (os.path.isabs(token) or Path(token).suffix):
                return token
        return ""

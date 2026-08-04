"""Autonomía supervisada (FASE 8).

`core/autonomy.py` deja a YUE proponer y crear cosas por su cuenta. Para que eso
sea seguro cuando toca el PC, hace falta un filtro que revise CADA acción antes de
ejecutarla y decida: ¿se puede sola, hay que pedir permiso, o se bloquea?

Este módulo es ese filtro. Clasifica una acción propuesta en tres niveles:

    SEGURO      -> reversible e inocua: se puede ejecutar sin molestar.
    CONFIRMAR   -> tiene consecuencias (borra, envía, paga, cambia ajustes):
                   NO se ejecuta hasta que el usuario diga que sí.
    BLOQUEADO   -> peligrosa o irreversible a gran escala: no se hace nunca de
                   forma autónoma (formatear, borrar el sistema, etc.).

Además:
  - Listas de PERMITIDOS y PROTEGIDOS configurables (como el Modo Desarrollador).
  - Una cola de pendientes con aprobar/rechazar por token.
  - Un diario de decisiones (auditoría) para poder revisar qué hizo y qué frenó.

Diseño conservador: ante la duda, CONFIRMAR (no SEGURO). Solo librería estándar.
"""
from __future__ import annotations

import enum
import itertools
import re
import unicodedata
from dataclasses import dataclass, field


class RiskLevel(enum.IntEnum):
    SEGURO = 0
    CONFIRMAR = 1
    BLOQUEADO = 2


@dataclass
class Action:
    """Acción propuesta por la autonomía. `kind` es el verbo/tipo, `target` el objeto."""
    kind: str
    target: str = ""
    description: str = ""
    reversible: bool = True


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in text if not unicodedata.combining(c))


# Acciones inocuas y reversibles: se pueden hacer solas.
_SEGURO_KINDS = {
    "abrir", "leer", "buscar", "resumir", "mostrar", "consultar", "escuchar",
    "ver", "listar", "tomar_nota", "recordar", "saludar", "comentar", "nota",
}

# Acciones con consecuencias: exigen confirmación del usuario.
_CONFIRMAR_KINDS = {
    "borrar", "eliminar", "mover", "renombrar", "sobrescribir", "guardar_sobre",
    "enviar", "publicar", "responder_correo", "mandar_mensaje", "compartir",
    "comprar", "pagar", "transferir", "suscribir", "cambiar_ajuste",
    "instalar", "desinstalar", "descargar", "ejecutar_programa",
}

# Patrones peligrosos/irreversibles a gran escala: se bloquean siempre.
_BLOQUEADO_PATRONES = [
    r"\bformat(ear|o)\b",
    r"\brm\s+-rf\b",
    r"\bdel\s+/[sf]\b",
    r"\bborrar\s+(todo|el\s+disco|el\s+sistema|windows|c:\\?)\b",
    r"\beliminar\s+(todos\s+los\s+archivos|el\s+sistema|la\s+particion)\b",
    r"\bsystem32\b",
    r"\bdiskpart\b",
    r"\bmkfs\b",
    r"\bdrop\s+database\b",
    r"\bregistro\s+de\s+windows\b.*\bborrar\b",
    r"\bapagar\s+el\s+antivirus\b",
    r"\bdesactivar\s+(el\s+)?firewall\b",
]

# Objetivos sensibles: si una acción con consecuencias los toca, nunca es SEGURO
# aunque el verbo pareciera inocuo.
_TARGETS_SENSIBLES = [
    "contrasena", "password", "clave", ".env", "banco", "tarjeta", "billetera",
    "wallet", "correo", "email", "system32", "windows", "registro", "bios",
]


class AutonomySupervisor:
    def __init__(self, allowlist=None, protected=None):
        # Verbos que el usuario marcó como permitidos aunque normalmente pedirían
        # confirmación (baja a SEGURO). Y objetivos protegidos (suben a BLOQUEADO).
        self.allowlist = {(_norm(x)) for x in (allowlist or [])}
        self.protected = {(_norm(x)) for x in (protected or [])}
        self._pending: dict[int, Action] = {}
        self._ids = itertools.count(1)
        self._journal: list[dict] = []

    # ---- Clasificación ----------------------------------------------------
    def classify(self, action: Action) -> dict:
        kind = _norm(action.kind)
        target = _norm(action.target)
        blob = f"{kind} {target} {_norm(action.description)}"

        # 1) Objetivo protegido por el usuario -> BLOQUEADO.
        if any(pt and pt in blob for pt in self.protected):
            return self._decision(action, RiskLevel.BLOQUEADO,
                                   "toca algo que marcaste como protegido")

        # 2) Patrón peligroso/irreversible -> BLOQUEADO.
        if any(re.search(p, blob) for p in _BLOQUEADO_PATRONES):
            return self._decision(action, RiskLevel.BLOQUEADO,
                                   "acción peligrosa o irreversible a gran escala")

        # 3) Verbo permitido explícitamente -> SEGURO (salvo objetivo sensible).
        toca_sensible = any(s in blob for s in _TARGETS_SENSIBLES)
        if kind in self.allowlist and not toca_sensible:
            return self._decision(action, RiskLevel.SEGURO,
                                   "permitido por tu lista")

        # 4) Verbo con consecuencias -> CONFIRMAR.
        if kind in _CONFIRMAR_KINDS or toca_sensible or not action.reversible:
            motivo = ("tiene consecuencias y conviene tu visto bueno"
                      if not toca_sensible else
                      "toca información sensible; necesita tu confirmación")
            return self._decision(action, RiskLevel.CONFIRMAR, motivo)

        # 5) Verbo inocuo conocido -> SEGURO.
        if kind in _SEGURO_KINDS:
            return self._decision(action, RiskLevel.SEGURO, "acción inocua y reversible")

        # 6) Desconocido -> por prudencia, CONFIRMAR.
        return self._decision(action, RiskLevel.CONFIRMAR,
                              "acción no reconocida; mejor confirmar antes")

    def _decision(self, action: Action, level: RiskLevel, reason: str) -> dict:
        dec = {
            "level": level,
            "level_name": level.name,
            "reason": reason,
            "requires_confirmation": level == RiskLevel.CONFIRMAR,
            "blocked": level == RiskLevel.BLOQUEADO,
            "undo_hint": _undo_hint(action) if level != RiskLevel.SEGURO else "",
            "action": {"kind": action.kind, "target": action.target},
        }
        self._journal.append(dec)
        return dec

    # ---- Plan completo (dry-run) -----------------------------------------
    def dry_run(self, plan: list[Action]) -> dict:
        """Evalúa un plan entero SIN ejecutar. Solo procede solo si TODO es SEGURO."""
        decisiones = [self.classify(a) for a in plan]
        hay_bloqueado = any(d["blocked"] for d in decisiones)
        hay_confirmar = any(d["requires_confirmation"] for d in decisiones)
        if hay_bloqueado:
            veredicto = "detener"
        elif hay_confirmar:
            veredicto = "confirmar"
        else:
            veredicto = "proceder"
        return {
            "veredicto": veredicto,
            "puede_automatico": veredicto == "proceder",
            "decisiones": decisiones,
        }

    # ---- Cola de confirmación --------------------------------------------
    def queue_for_confirmation(self, action: Action) -> int:
        """Pone una acción a la espera del visto bueno del usuario. Devuelve un token."""
        tok = next(self._ids)
        self._pending[tok] = action
        return tok

    def approve(self, token: int) -> Action | None:
        """El usuario aprueba: saca la acción de la cola para que se ejecute."""
        act = self._pending.pop(token, None)
        if act is not None:
            self._journal.append({"level_name": "APROBADA_POR_USUARIO",
                                   "action": {"kind": act.kind, "target": act.target}})
        return act

    def reject(self, token: int) -> bool:
        """El usuario rechaza: la acción se descarta."""
        act = self._pending.pop(token, None)
        if act is not None:
            self._journal.append({"level_name": "RECHAZADA_POR_USUARIO",
                                   "action": {"kind": act.kind, "target": act.target}})
        return act is not None

    def pending(self) -> dict[int, Action]:
        return dict(self._pending)

    def journal(self) -> list[dict]:
        return list(self._journal)


def _undo_hint(action: Action) -> str:
    """Pista de cómo deshacer, para acompañar acciones que cambian algo."""
    k = _norm(action.kind)
    if k in ("borrar", "eliminar"):
        return "Se puede recuperar desde la papelera si no se vació."
    if k in ("mover", "renombrar"):
        return "Se puede volver a mover/renombrar al nombre anterior."
    if k in ("sobrescribir", "guardar_sobre"):
        return "Conviene guardar una copia antes de sobrescribir."
    if k in ("enviar", "publicar", "mandar_mensaje", "responder_correo", "compartir"):
        return "Una vez enviado no se puede des-enviar; revisa antes."
    if k in ("comprar", "pagar", "transferir"):
        return "Una transacción no se deshace sola; confírmala con cuidado."
    if k in ("instalar", "desinstalar"):
        return "Se puede revertir instalando/desinstalando de nuevo."
    return ""

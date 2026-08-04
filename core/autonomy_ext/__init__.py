"""Extensiones de autonomía de YUE (FASE 8).

Contiene el supervisor de autonomía: revisa cada acción propuesta y decide si es
segura, necesita confirmación o debe bloquearse, antes de tocar el PC. ADITIVO.
"""
from .supervisor import AutonomySupervisor, Action, RiskLevel

__all__ = ["AutonomySupervisor", "Action", "RiskLevel"]

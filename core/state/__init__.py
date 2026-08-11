"""Núcleo de estado de YUE — el cerebro central.

Reúne las piezas que dan orden a la aplicación:

  - EventBus         : que los módulos se avisen sin acoplarse.
  - ResourceManager  : un solo dueño por recurso físico (cámara, micro...).
  - YueStateManager  : única fuente de verdad + arbitraje del comportamiento.

Y la separación de estados (FASE 3), que es lo que evita que «tristeza»
signifique dos cosas distintas según quién lea el campo:

  - UserState         lo que YUE entiende del usuario   (observado)
  - YueBehaviorState  cómo decidió reaccionar YUE       (decidido)
  - SystemState       cómo está la máquina              (técnico)
  - YueGlobalState    las tres juntas, de forma coherente

El flujo completo:

    LOS SENSORES OBSERVAN    →  state_manager.observe(UserObservation(...))
    LOS MÓDULOS PROPONEN     →  state_manager.propose(YueProposal(...))
    EL STATE MANAGER DECIDE  →  arbitraje por prioridad + TTL
    EL RENDERER EJECUTA      →  AvatarRenderer (único que toca el avatar)

Todo es ADITIVO: se puede adoptar módulo a módulo sin romper lo existente.
"""
from .events import EventBus
from .resources import Resource, ResourceManager, Lease, ResourceBusy
from .state_manager import YueStateManager, YueState, Priority
from .models import (
    INITIATIVE_LEVELS, SOURCE_RELIABILITY, SOURCE_TTL,
    IDLE_PROPOSAL, SystemState, UserObservation, UserState,
    YueBehaviorState, YueGlobalState, YueProposal,
)
from .fusion import SensorFusion, effective_confidence
from .arbiter import ProposalArbiter
from .mapping import (
    avatar_pose, behavior_for_need, observation_from_companion,
    proposal_from_companion, user_state_from_companion,
)
from .renderer import AvatarRenderer, attach_renderer

__all__ = [
    "EventBus",
    "Resource", "ResourceManager", "Lease", "ResourceBusy",
    "YueStateManager", "YueState", "Priority",
    # --- estados separados ---
    "UserState", "YueBehaviorState", "SystemState", "YueGlobalState",
    "UserObservation", "YueProposal", "IDLE_PROPOSAL",
    "INITIATIVE_LEVELS", "SOURCE_RELIABILITY", "SOURCE_TTL",
    # --- fusión y arbitraje ---
    "SensorFusion", "effective_confidence", "ProposalArbiter",
    # --- traducción desde lo que ya existía ---
    "user_state_from_companion", "proposal_from_companion",
    "observation_from_companion", "behavior_for_need", "avatar_pose",
    # --- renderizado (único punto que toca el avatar) ---
    "AvatarRenderer", "attach_renderer",
]

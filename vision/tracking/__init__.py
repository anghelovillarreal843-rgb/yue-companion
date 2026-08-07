"""Seguimiento temporal: identidades estables y suavizado (puntos 7 y 10)."""
from vision.tracking.base_tracker import BaseTracker, Track, iou
from vision.tracking.hand_tracker import HandState, HandTracker
from vision.tracking.object_tracker import ObjectTracker, label_es
from vision.tracking.person_tracker import PersonSnapshot, PersonTracker
from vision.tracking.temporal_smoother import EMA, Hysteresis, MajorityVote, MotionTracker

__all__ = [
    "BaseTracker", "Track", "iou",
    "HandTracker", "HandState",
    "ObjectTracker", "label_es",
    "PersonTracker", "PersonSnapshot",
    "EMA", "Hysteresis", "MajorityVote", "MotionTracker",
]

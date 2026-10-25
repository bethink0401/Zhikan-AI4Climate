"""Shared type definitions for recommendation module.

These types are compatible with AERION's original definitions and the
target system's existing resolvers.
"""

from __future__ import annotations

from typing import Literal


UserGoal = Literal[
    "analysis",
    "explanation",
    "visualization",
    "governance_advice",
    "public_travel_advice",
    "health_protection",
    "report",
    "unknown",
]

Audience = Literal["citizen", "manager", "unknown"]

ConfidenceLabel = Literal["low", "medium", "high"]


__all__ = ["Audience", "ConfidenceLabel", "UserGoal"]

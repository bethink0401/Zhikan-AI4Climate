"""Recommendation composition for citizen-facing advice and governance guidance.

This module ports AERION's RecommendationComposer without LangGraph dependency,
providing evidence-based recommendations and model-authored governance copy for:
- Public travel advice (citizen)
- Health protection (citizen)
- Governance advice (manager)

All components use existing Agent results only; they never query data or
execute business Agents.
"""

from .composer import RecommendationComposer, RecommendationResult
from .evidence import (
    EvidenceKey,
    EvidenceResolution,
    EvidenceStatus,
    RecommendationEvidenceResolver,
)
from .prediction import FutureRiskAssessment, FutureRiskLevel, PredictionFusion
from .presentation import RecommendationPresentationBuilder
from .types import Audience, ConfidenceLabel, UserGoal

__all__ = [
    "Audience",
    "ConfidenceLabel",
    "EvidenceKey",
    "EvidenceResolution",
    "EvidenceStatus",
    "FutureRiskAssessment",
    "FutureRiskLevel",
    "PredictionFusion",
    "RecommendationComposer",
    "RecommendationEvidenceResolver",
    "RecommendationPresentationBuilder",
    "RecommendationResult",
    "UserGoal",
]

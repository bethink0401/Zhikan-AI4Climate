"""Deterministic decision about whether a turn needs expert retrieval."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping


_ALL_KNOWLEDGE_CATEGORIES = (
    "health_guidance", "air_quality_standard", "governance", "environmental_policy",
    "traffic_pollution", "meteorology_dispersion", "pollutant_mechanism", "other",
)


@dataclass(frozen=True, slots=True)
class KnowledgeNeedResolution:
    needs_rag: bool
    reason: str
    knowledge_topics: tuple[str, ...]
    categories: tuple[str, ...]
    scope: tuple[str, ...]
    query: str
    max_hits: int
    agent_facts_primary: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class KnowledgeNeedResolver:
    def __init__(self, *, enabled: bool = True, max_hits: int = 5) -> None:
        self.enabled = enabled
        self.max_hits = max(1, min(int(max_hits), 20))

    def resolve(
        self, user_input: str, *, user_goal: str,
        previous_knowledge_context: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
        model_decision: bool | None = None,
    ) -> KnowledgeNeedResolution:
        if not self.enabled:
            return self._no("rag_disabled")
        if model_decision is not True:
            return self._no("model_skipped_knowledge")
        has_facts = bool(context and context.get("agent_results"))
        return self._yes("model_selected_knowledge", (), _ALL_KNOWLEDGE_CATEGORIES,
                         user_input.strip(), has_facts)

    def _yes(
        self, reason: str, topics: tuple[str, ...], categories: tuple[str, ...],
        text: str, agent_facts_primary: bool,
    ) -> KnowledgeNeedResolution:
        cleaned = re.sub(r"[，。！？?、；;：:]", " ", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        query = " ".join(dict.fromkeys([*topics, cleaned[:120]]))[:240]
        return KnowledgeNeedResolution(
            True, reason, topics, tuple(dict.fromkeys(categories)), ("system", "user"),
            query, self.max_hits, agent_facts_primary,
        )

    def _no(self, reason: str) -> KnowledgeNeedResolution:
        return KnowledgeNeedResolution(False, reason, (), (), ("system", "user"), "", 0, True)

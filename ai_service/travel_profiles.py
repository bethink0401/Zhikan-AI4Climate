"""Resolve explicitly stated travel identities to supported planning profiles."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PROFILE_IDS = frozenset({"commuter", "parent_with_child", "elderly", "runner", "tourist"})

def explicit_travel_profile(question: str, plan: Mapping[str, Any] | None = None) -> str | None:
    """Accept model interpretation only with a verbatim identity from this turn."""
    plan = plan or {}
    profile = plan.get("travel_profile")
    quote = plan.get("travel_identity")
    if isinstance(profile, str) and profile in PROFILE_IDS and isinstance(quote, str) and quote.strip() and quote in question:
        return str(profile)
    return None

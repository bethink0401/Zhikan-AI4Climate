from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .spatial_agent import SpatialAgent

__all__ = ["SpatialAgent"]


def __getattr__(name: str) -> Any:
    if name == "SpatialAgent":
        from .spatial_agent import SpatialAgent

        return SpatialAgent
    raise AttributeError(name)

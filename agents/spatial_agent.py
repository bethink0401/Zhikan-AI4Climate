"""WeatherMulti-Agent entry point for the Spatial Agent."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if sys.path[0] != str(ROOT):
    sys.path.insert(0, str(ROOT))

from spatial_agent.agents.spatial_agent import SpatialAgent, main

__all__ = ["SpatialAgent"]


if __name__ == "__main__":
    main()

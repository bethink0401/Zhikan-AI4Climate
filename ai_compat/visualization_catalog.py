"""Read-only loader for target-system visualization data capabilities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).with_name("visualization_data_catalog.json")


@dataclass(frozen=True, slots=True)
class VisualizationMetric:
    key: str
    data_source: str
    source_agent: str
    field_path: str
    availability: str
    unit: str | None
    dimension: str
    semantic_type: str
    supported_view_types: tuple[str, ...]
    renderer_status: str
    limitations: tuple[str, ...]
    unavailable_reason: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "VisualizationMetric":
        return cls(
            key=str(value["key"]),
            data_source=str(value["dataSource"]),
            source_agent=str(value["sourceAgent"]),
            field_path=str(value["fieldPath"]),
            availability=str(value["availability"]),
            unit=str(value["unit"]) if value.get("unit") is not None else None,
            dimension=str(value["dimension"]),
            semantic_type=str(value["semanticType"]),
            supported_view_types=tuple(value.get("supportedViewTypes", ())),
            renderer_status=str(value.get("rendererStatus", "not_verified")),
            limitations=tuple(value.get("limitations", ())),
            unavailable_reason=(str(value["unavailableReason"]) if value.get("unavailableReason") else None),
        )


class VisualizationDataCatalog:
    def __init__(self, metrics: tuple[VisualizationMetric, ...], version: str) -> None:
        if not metrics:
            raise ValueError("visualization catalog must not be empty")
        self.version = version
        self._metrics = {item.key: item for item in metrics}
        if len(self._metrics) != len(metrics):
            raise ValueError("visualization catalog contains duplicate keys")

    @classmethod
    def load(cls, path: Path = CATALOG_PATH) -> "VisualizationDataCatalog":
        value = json.loads(path.read_text(encoding="utf-8"))
        metrics = tuple(VisualizationMetric.from_mapping(item) for item in value["metrics"])
        return cls(metrics=metrics, version=str(value["version"]))

    def require(self, key: str) -> VisualizationMetric:
        try:
            return self._metrics[key]
        except KeyError as exc:
            raise KeyError(f"unknown visualization metric: {key}") from exc

    def available(self) -> tuple[VisualizationMetric, ...]:
        return tuple(item for item in self._metrics.values() if item.availability in {"available", "conditional"})

    def unavailable(self) -> tuple[VisualizationMetric, ...]:
        return tuple(item for item in self._metrics.values() if item.availability == "unavailable")

    def all(self) -> tuple[VisualizationMetric, ...]:
        return tuple(self._metrics.values())

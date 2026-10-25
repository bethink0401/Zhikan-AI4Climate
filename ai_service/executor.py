"""Business execution boundary for the fixed seven-Agent CoordinatorMin chain."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlparse

import requests

from ai_compat.contracts import CanonicalAgentResults
from ai_compat.coordinator_adapter import (
    CoordinatorMinResultAdapter,
    CoordinatorResultValidationError,
)


class BusinessExecutionError(RuntimeError):
    pass


class ModelPlanningError(BusinessExecutionError):
    """No validated model action is available; business tools must not run."""


class BusinessTimeoutError(BusinessExecutionError):
    pass


class BusinessExecutor(Protocol):
    source_name: str

    def execute(self, task: Mapping[str, Any]) -> CanonicalAgentResults: ...


class CoordinatorMinExecutor:
    """Call one administrator-configured CoordinatorMin HTTP endpoint.

    The endpoint is constructor configuration and is never read from a chat
    request, preventing callers from selecting arbitrary internal URLs.
    """

    source_name = "target_coordinator_min_http"

    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 90.0,
        adapter: CoordinatorMinResultAdapter | None = None,
        http: requests.Session | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("CoordinatorMin endpoint must be an HTTP(S) URL")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds
        self._adapter = adapter or CoordinatorMinResultAdapter()
        self._http = http or requests.Session()

    def execute(self, task: Mapping[str, Any]) -> CanonicalAgentResults:
        request_task = {
            key: task.get(key)
            for key in ("task_id", "station_name", "station_id", "start_time", "end_time", "lat", "lon")
            if key in task
        }
        try:
            response = self._http.post(
                self._endpoint,
                json=request_task,
                timeout=self._timeout_seconds,
            )
        except requests.Timeout as exc:
            raise BusinessTimeoutError("CoordinatorMin request timed out") from exc
        except requests.RequestException as exc:
            raise BusinessExecutionError("CoordinatorMin service is unavailable") from exc
        if response.status_code >= 400:
            raise BusinessExecutionError(
                f"CoordinatorMin service returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise BusinessExecutionError("CoordinatorMin service returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise BusinessExecutionError("CoordinatorMin service returned a non-object response")
        if payload.get("status") == "error":
            raise BusinessExecutionError("CoordinatorMin service reported an analysis error")
        try:
            return self._adapter.adapt(payload)
        except CoordinatorResultValidationError as exc:
            raise BusinessExecutionError("CoordinatorMin response failed compatibility validation") from exc

"""Explicit access boundary; AI session identifiers are never identities."""

from __future__ import annotations

import ipaddress
from typing import Protocol

from flask import Request

from .config import KnowledgeConfig
from .exceptions import KnowledgeAccessDenied
from .models import KnowledgePrincipal


class PrincipalResolver(Protocol):
    def resolve(self, request: Request) -> KnowledgePrincipal: ...


class LocalDevelopmentPrincipalResolver:
    """Local-only fallback until the target system has real authentication.

    It intentionally ignores session_id, cookies, forwarded IP headers and
    caller-supplied owner fields. Deployments that expose the service beyond
    loopback must inject a real authenticated PrincipalResolver.
    """

    def __init__(self, config: KnowledgeConfig) -> None:
        self.config = config

    def resolve(self, request: Request) -> KnowledgePrincipal:
        address = (request.remote_addr or "").strip()
        try:
            local = ipaddress.ip_address(address).is_loopback
        except ValueError:
            local = False
        if self.config.local_development_only and not local:
            raise KnowledgeAccessDenied(
                "knowledge API is restricted to local development until authentication is configured"
            )
        return KnowledgePrincipal(
            principal_id="local-development",
            roles=("knowledge_user",),
            allowed_scopes=("system", "user"),
            local_development=True,
        )

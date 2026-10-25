"""AERION-compatible knowledge API mounted only by the standalone AI Service."""

from __future__ import annotations

import json
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from .access import LocalDevelopmentPrincipalResolver, PrincipalResolver
from .config import KnowledgeConfig
from .exceptions import (
    DocumentNotFound,
    DuplicateDocumentError,
    KnowledgeAccessDenied,
    KnowledgeBaseError,
    KnowledgeValidationError,
)
from .manager import KnowledgeBaseManager
from .models import CATEGORY_WHITELIST


def _error(code: str, message: str, status: int):
    # Keep AERION's top-level message while also exposing the target service's
    # structured error shape for new clients.
    return jsonify({
        "status": "error", "message": message,
        "error": {"code": code, "message": message, "retryable": status >= 500},
    }), status


def _list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    raise KnowledgeValidationError("filter must be a list")


def create_blueprint(
    manager_provider: Callable[[], KnowledgeBaseManager],
    *,
    config: KnowledgeConfig,
    principal_resolver: PrincipalResolver | None = None,
) -> Blueprint:
    blueprint = Blueprint("ai_knowledge", __name__)
    resolver = principal_resolver or LocalDevelopmentPrincipalResolver(config)

    def principal():
        if not config.enabled:
            raise KnowledgeAccessDenied("knowledge base is disabled")
        return resolver.resolve(request)

    def upload_metadata() -> dict[str, Any]:
        forbidden = [
            key for key in ("owner_id", "document_id", "page", "section", "doi")
            if request.form.get(key) not in (None, "")
        ]
        if forbidden:
            raise KnowledgeValidationError(
                f"server-derived or unsupported citation fields: {sorted(forbidden)}"
            )
        requested_scope = (request.form.get("scope") or "user").strip()
        requested_authority = (request.form.get("authority_level") or "user_uploaded").strip()
        if requested_scope != "user" or requested_authority != "user_uploaded":
            raise KnowledgeAccessDenied(
                "ordinary uploads are user documents; system authority requires an authenticated maintainer"
            )
        topics_value = request.form.get("topics")
        if topics_value:
            try:
                topics = json.loads(topics_value)
            except json.JSONDecodeError:
                topics = topics_value
        else:
            topics = []
        return {
            "title": request.form.get("title"), "source": request.form.get("source"),
            "author": request.form.get("author"), "year": request.form.get("year"),
            "category": request.form.get("category", "other"), "topics": topics,
            "scope": "user", "authority_level": "user_uploaded",
        }

    @blueprint.post("/documents")
    def upload_document():
        try:
            caller = principal()
            file = request.files.get("file")
            if file is None or not file.filename:
                raise KnowledgeValidationError("file is required")
            document = manager_provider().ingest_bytes(
                file.read(), file.filename, upload_metadata(), principal=caller
            )
            return jsonify({"status": document.status, "document": document.to_dict()}), 201
        except DuplicateDocumentError as exc:
            return jsonify({
                "status": "duplicate", "message": str(exc),
                "existing_document_id": exc.document_id,
            }), 409
        except KnowledgeAccessDenied as exc:
            return _error("knowledge_access_denied", str(exc), 403)
        except KnowledgeValidationError as exc:
            return _error("knowledge_invalid_request", str(exc), 400)
        except KnowledgeBaseError as exc:
            return _error("knowledge_unavailable", str(exc), 503)
        except Exception:
            return _error("knowledge_ingestion_failed", "knowledge ingestion failed", 500)

    @blueprint.get("/documents")
    def list_documents():
        try:
            documents = manager_provider().list_documents(
                principal=principal(), scope=request.args.get("scope"),
                category=request.args.get("category"), status=request.args.get("status"),
            )
            return jsonify({
                "status": "success", "documents": [item.to_dict() for item in documents],
                "categories": sorted(CATEGORY_WHITELIST), "access_mode": "local_development",
            })
        except KnowledgeAccessDenied as exc:
            return _error("knowledge_access_denied", str(exc), 403)
        except Exception:
            return _error("knowledge_unavailable", "knowledge document listing failed", 503)

    @blueprint.get("/documents/<document_id>")
    def get_document(document_id: str):
        try:
            document = manager_provider().get_document(document_id, principal=principal())
            return jsonify({"status": "success", "document": document.to_dict()})
        except DocumentNotFound:
            return _error("knowledge_document_not_found", "knowledge document not found", 404)
        except KnowledgeAccessDenied as exc:
            return _error("knowledge_access_denied", str(exc), 403)
        except Exception:
            return _error("knowledge_unavailable", "knowledge document lookup failed", 503)

    @blueprint.patch("/documents/<document_id>")
    def patch_document(document_id: str):
        try:
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                raise KnowledgeValidationError("JSON body must be an object")
            document = manager_provider().update_document(
                document_id, body, principal=principal()
            )
            return jsonify({"status": "success", "document": document.to_dict()})
        except DocumentNotFound:
            return _error("knowledge_document_not_found", "knowledge document not found", 404)
        except KnowledgeAccessDenied as exc:
            return _error("knowledge_access_denied", str(exc), 403)
        except (KnowledgeValidationError, ValueError) as exc:
            return _error("knowledge_invalid_request", str(exc), 400)
        except KnowledgeBaseError as exc:
            return _error("knowledge_unavailable", str(exc), 503)
        except Exception:
            return _error("knowledge_update_failed", "knowledge document update failed", 500)

    @blueprint.delete("/documents/<document_id>")
    def delete_document(document_id: str):
        try:
            manager_provider().delete_document(document_id, principal=principal())
            return jsonify({"status": "deleted", "document_id": document_id})
        except DocumentNotFound:
            return _error("knowledge_document_not_found", "knowledge document not found", 404)
        except KnowledgeAccessDenied as exc:
            return _error("knowledge_access_denied", str(exc), 403)
        except KnowledgeBaseError as exc:
            return _error("knowledge_delete_failed", str(exc), 503)
        except Exception:
            return _error("knowledge_delete_failed", "knowledge document deletion failed", 500)

    @blueprint.post("/search")
    def search():
        try:
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                raise KnowledgeValidationError("JSON body must be an object")
            unknown = set(body) - {
                "query", "top_k", "scope", "categories", "document_ids", "enabled_only",
            }
            if unknown:
                raise KnowledgeValidationError(f"unsupported search fields: {sorted(unknown)}")
            result = manager_provider().search(
                str(body.get("query") or ""), principal=principal(), top_k=body.get("top_k"),
                scope=_list(body.get("scope")), categories=_list(body.get("categories")),
                document_ids=_list(body.get("document_ids")),
                enabled_only=bool(body.get("enabled_only", True)),
            )
            return jsonify(result.to_dict())
        except KnowledgeAccessDenied as exc:
            return _error("knowledge_access_denied", str(exc), 403)
        except KnowledgeValidationError as exc:
            return _error("knowledge_invalid_request", str(exc), 400)
        except KnowledgeBaseError as exc:
            return _error("knowledge_unavailable", str(exc), 503)
        except Exception:
            return _error("knowledge_search_failed", "knowledge search failed", 500)

    return blueprint

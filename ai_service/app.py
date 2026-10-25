"""Standalone Flask entry point for ``/api/ai/v1/chat``."""

from __future__ import annotations

import logging
import json
import sys
from functools import lru_cache
from queue import Empty, Queue
from threading import Event, Thread
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, request, stream_with_context, copy_current_request_context

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_compat.contracts import ApiError, ErrorResponse  # noqa: E402
from ai_service.answering import EvidenceAnswerer, OpenAICompatibleAnswerModel  # noqa: E402
from ai_service.answer_generation import answer_generation, check_generation_active  # noqa: E402
from ai_service.config import AIServiceConfig  # noqa: E402
from ai_service.executor import (  # noqa: E402
    BusinessExecutionError,
    ModelPlanningError,
    BusinessTimeoutError,
    CoordinatorMinExecutor,
)
from ai_service.knowledge.api import create_blueprint as create_knowledge_blueprint  # noqa: E402
from ai_service.knowledge.config import KnowledgeConfig  # noqa: E402
from ai_service.knowledge.fusion import EvidenceFusion  # noqa: E402
from ai_service.knowledge.manager import KnowledgeBaseManager  # noqa: E402
from ai_service.knowledge.models import KnowledgePrincipal  # noqa: E402
from ai_service.knowledge.rag import KnowledgeRAGService  # noqa: E402
from ai_service.knowledge.resolver import KnowledgeNeedResolver  # noqa: E402
from ai_service.resolvers import TaskContextResolver  # noqa: E402
from ai_service.service import AIChatService  # noqa: E402
from ai_service.session_store import SQLiteSessionStore, SessionConflictError  # noqa: E402


logger = logging.getLogger(__name__)


def _error(code: str, message: str, status: int, *, retryable: bool = False) -> tuple[Any, int]:
    value = ErrorResponse(error=ApiError(code=code, message=message, retryable=retryable))
    return jsonify(value.to_dict()), status


def _default_service(
    config: AIServiceConfig,
    knowledge_config: KnowledgeConfig,
    knowledge_manager_provider: Callable[[], KnowledgeBaseManager],
) -> AIChatService:
    store = SQLiteSessionStore(
        config.session_db_path,
        ttl_seconds=config.session_ttl_seconds,
    )
    executor = CoordinatorMinExecutor(
        config.business_api_url,
        timeout_seconds=config.business_timeout_seconds,
    )
    model = None
    if config.llm_api_url and config.llm_model:
        model = OpenAICompatibleAnswerModel(
            config.llm_api_url,
            config.llm_model,
            api_key=config.llm_api_key,
            timeout_seconds=config.llm_timeout_seconds,
            max_context_chars=config.llm_max_context_chars,
        )
    return AIChatService(
        store=store,
        executor=executor,
        context_resolver=TaskContextResolver(ROOT / "data" / "station_mapping.json"),
        answerer=EvidenceAnswerer(model),
        knowledge_need_resolver=KnowledgeNeedResolver(enabled=knowledge_config.enabled),
        knowledge_retriever=KnowledgeRAGService(
            config=knowledge_config,
            manager_provider=knowledge_manager_provider,
            principal=KnowledgePrincipal(
                principal_id="local-development",
                roles=("knowledge_user",),
                allowed_scopes=("system", "user"),
                local_development=True,
            ),
        ),
        evidence_fusion=EvidenceFusion(),
        history_max_messages=config.history_max_messages,
        business_api_url=config.business_api_url,
    )


def create_app(
    *,
    config: AIServiceConfig | None = None,
    service: AIChatService | None = None,
    knowledge_config: KnowledgeConfig | None = None,
    knowledge_manager_provider: Callable[[], KnowledgeBaseManager] | None = None,
) -> Flask:
    resolved_config = config or AIServiceConfig.from_env()
    resolved_knowledge = knowledge_config or KnowledgeConfig.from_env(ROOT)
    if knowledge_manager_provider is None:
        @lru_cache(maxsize=1)
        def default_knowledge_manager() -> KnowledgeBaseManager:
            return KnowledgeBaseManager(resolved_knowledge)

        knowledge_manager_provider = default_knowledge_manager
    chat_service = service or _default_service(
        resolved_config, resolved_knowledge, knowledge_manager_provider
    )
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.config["MAX_CONTENT_LENGTH"] = max(
        64 * 1024, (resolved_knowledge.max_file_mb + 1) * 1024 * 1024
    )
    app.extensions["ai_chat_service"] = chat_service
    app.extensions["ai_service_config"] = resolved_config

    def health_payload() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "weather-multi-agent-ai",
            "contract_version": "1.0",
            "session_store": "sqlite",
            "langgraph": False,
            "rag": resolved_knowledge.enabled,
            "knowledge_access": "local_development_only"
            if resolved_knowledge.local_development_only else "external_identity_required",
            "visualization": True,
            "visualization_mode": "semantic_spec_only",
            "intent_routing": "llm_tools_only",
            "keyword_fallback": False,
        }

    @app.get("/health")
    def health() -> Any:
        return jsonify(health_payload())

    @app.get("/api/ai/v1/health")
    def versioned_health() -> Any:
        return jsonify(health_payload())

    @app.post("/api/ai/v1/chat")
    def chat() -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _error("invalid_request", "JSON object body is required", 400)
        session_id = body.get("session_id")
        user_input = body.get("user_input")
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 128:
            return _error("invalid_session_id", "session_id must be a non-empty string up to 128 characters", 400)
        if not isinstance(user_input, str) or not user_input.strip() or len(user_input) > 4000:
            return _error("invalid_user_input", "user_input must be a non-empty string up to 4000 characters", 400)
        request_context = body.get("request_context")
        page_context = body.get("page_context")
        if request_context is not None and not isinstance(request_context, dict):
            return _error("invalid_request_context", "request_context must be an object", 400)
        if page_context is not None and not isinstance(page_context, dict):
            return _error("invalid_page_context", "page_context must be an object", 400)
        try:
            response = chat_service.chat(session_id, user_input, request_context, page_context)
        except BusinessTimeoutError:
            return _error(
                "business_timeout",
                "业务分析服务超时，未生成新的环境分析结果。",
                504,
                retryable=True,
            )
        except ModelPlanningError as exc:
            return _error("model_planning_unavailable", str(exc), 502, retryable=True)
        except BusinessExecutionError:
            return _error(
                "business_unavailable",
                "业务分析服务当前不可用，未生成新的环境分析结果。",
                502,
                retryable=True,
            )
        except SessionConflictError:
            return _error(
                "session_conflict",
                "会话已被并发更新，请使用同一 session_id 重试当前请求。",
                409,
                retryable=True,
            )
        except ValueError as exc:
            return _error("invalid_request", str(exc), 400)
        except Exception:
            logger.exception("AI service request failed")
            return _error("internal_error", "AI 服务处理失败。", 500, retryable=True)
        return jsonify(response.to_dict())

    @app.post("/api/ai/v1/chat/stream")
    def chat_stream() -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _error("invalid_request", "JSON object body is required", 400)
        session_id = body.get("session_id")
        user_input = body.get("user_input")
        request_context = body.get("request_context")
        page_context = body.get("page_context")
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 128:
            return _error("invalid_session_id", "invalid session_id", 400)
        if not isinstance(user_input, str) or not user_input.strip() or len(user_input) > 4000:
            return _error("invalid_user_input", "invalid user_input", 400)
        if request_context is not None and not isinstance(request_context, dict):
            return _error("invalid_request_context", "request_context must be an object", 400)
        if page_context is not None and not isinstance(page_context, dict):
            return _error("invalid_page_context", "page_context must be an object", 400)

        def event(name: str, value: dict[str, Any]) -> str:
            return f"event: {name}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"

        @stream_with_context
        def generate():
            yield event("status", {"message": "正在准备分析数据…"})
            updates: Queue = Queue()
            cancelled = Event()

            @copy_current_request_context
            def run_chat():
                try:
                    with answer_generation(cancelled, lambda attempt: updates.put((
                            "status", {"message": "正在完善回答，请稍候…"}))):
                        check_generation_active()
                        result = chat_service.chat(session_id, user_input, request_context, page_context)
                        updates.put(("response", result))
                except Exception as exc:
                    updates.put(("error", exc))

            Thread(target=run_chat, daemon=True).start()
            try:
                while True:
                    try:
                        name, value = updates.get(timeout=5)
                    except Empty:
                        yield event("status", {"message": "正在生成回答，请稍候…"})
                        continue
                    if name == "status":
                        yield event(name, value)
                    elif name == "response":
                        yield event(name, value.to_dict())
                        break
                    else:
                        raise value
            except BusinessTimeoutError:
                yield event("error", {"code": "business_timeout", "message": "业务分析服务超时", "retryable": True})
            except ModelPlanningError as exc:
                yield event("error", {"code": "model_planning_unavailable", "message": str(exc), "retryable": True})
            except BusinessExecutionError:
                yield event("error", {"code": "business_unavailable", "message": "业务分析服务不可用", "retryable": True})
            except SessionConflictError:
                yield event("error", {"code": "session_conflict", "message": "会话并发冲突，请重试", "retryable": True})
            except ValueError as exc:
                yield event("error", {"code": "invalid_request", "message": str(exc),
                                      "status": 400, "retryable": False})
            except Exception:
                logger.exception("AI service streaming request failed")
                yield event("error", {"code": "internal_error", "message": "AI 服务处理失败", "retryable": True})
            finally:
                cancelled.set()

        return Response(generate(), mimetype="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })

    @app.get("/api/ai/v1/session/<session_id>/messages")
    def session_messages(session_id: str) -> Any:
        if not session_id or len(session_id) > 128:
            return _error("invalid_session_id", "invalid session_id", 400)
        session = chat_service.store.get(session_id)
        if session is None:
            return jsonify({"messages": [], "context": {}})
        return jsonify({
            "messages": session.client_messages,
            "context": {key: session.task_context.get(key) for key in
                        ("task_id", "station_id", "station_name", "start_time", "end_time")
                        if key in session.task_context},
        })

    app.register_blueprint(
        create_knowledge_blueprint(
            knowledge_manager_provider,
            config=resolved_knowledge,
        ),
        url_prefix="/api/knowledge",
    )

    @app.errorhandler(413)
    def request_too_large(_error: Exception) -> Any:
        return _error_response_for_size(resolved_knowledge.max_file_mb)

    return app


def _error_response_for_size(max_file_mb: int) -> tuple[Any, int]:
    return _error(
        "request_too_large",
        f"上传请求超过允许大小（文档上限 {max_file_mb} MB）。",
        413,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = AIServiceConfig.from_env()
    knowledge_config = KnowledgeConfig.from_env(ROOT)
    logger.info(f"Knowledge base enabled: {knowledge_config.enabled}")
    logger.info(f"Knowledge base storage: {knowledge_config.storage_path}")
    logger.info(
        "LLM answering enabled: %s; provider_host=%s; model=%s; api_key_loaded=%s",
        bool(config.llm_api_url and config.llm_model),
        urlparse(config.llm_api_url).hostname if config.llm_api_url else "none",
        config.llm_model or "none",
        bool(config.llm_api_key),
    )
    app = create_app(config=config, knowledge_config=knowledge_config)
    app.run(host=config.host, port=config.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()

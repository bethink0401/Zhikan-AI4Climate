"""Application service coordinating sessions, goals, business execution, and answers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import logging
from threading import RLock
from typing import Any

import requests

from ai_compat.contracts import ChatResponse
from tools.parcel_lifetime import load_parcel_rules

from .answering import EvidenceAnswerer, FAILED_ANSWERS
from .answer_generation import check_generation_active
from .dynamic_charts import DynamicChartPlanner
from .executor import BusinessExecutionError, BusinessExecutor, ModelPlanningError
from .knowledge.fusion import EvidenceFusion
from .knowledge.rag import KnowledgeContext, KnowledgeRAGService
from .knowledge.resolver import KnowledgeNeedResolver
from .page_evidence import resolve_travel_evidence
from .propagation_map import propagation_map_payload
from .travel_profiles import explicit_travel_profile
from .page_charts import travel_charts
from .named_travel import PlaceResolutionError, plan_named_trip, replan_travel_evidence
from .recommendation import (
    PredictionFusion,
    RecommendationComposer,
    RecommendationEvidenceResolver,
    RecommendationPresentationBuilder,
)
from .recommendation.governance import readable_advice_answer
from .resolvers import (
    ContinuationResolver,
    TaskContextResolver,
    UserGoalResolver,
)
from .session_store import SQLiteSessionStore, StoredSession
from .visualization import (
    VisualizationPlanResult,
    VisualizationPlanner,
    empty_visualization_context,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


logger = logging.getLogger(__name__)

_MODEL_GOALS = {
    "analysis", "explanation", "visualization", "governance_advice",
    "public_travel_advice", "health_protection", "report", "capability_intro", "unknown",
}
_MODEL_PAGES = {"overview", "weather", "restriction", "travel", "report", "none"}


class AIChatService:
    def __init__(
        self,
        *,
        store: SQLiteSessionStore,
        executor: BusinessExecutor,
        context_resolver: TaskContextResolver,
        goal_resolver: UserGoalResolver | None = None,
        continuation_resolver: ContinuationResolver | None = None,
        answerer: EvidenceAnswerer | None = None,
        visualization_planner: VisualizationPlanner | None = None,
        dynamic_chart_planner: DynamicChartPlanner | None = None,
        knowledge_need_resolver: KnowledgeNeedResolver | None = None,
        knowledge_retriever: KnowledgeRAGService | None = None,
        evidence_fusion: EvidenceFusion | None = None,
        recommendation_composer: RecommendationComposer | None = None,
        recommendation_evidence_resolver: RecommendationEvidenceResolver | None = None,
        recommendation_presentation_builder: RecommendationPresentationBuilder | None = None,
        prediction_fusion: PredictionFusion | None = None,
        history_max_messages: int = 24,
        business_api_url: str | None = None,
    ) -> None:
        self.store = store
        self.executor = executor
        self.context_resolver = context_resolver
        self.goal_resolver = goal_resolver or UserGoalResolver()
        self.continuation_resolver = continuation_resolver or ContinuationResolver()
        self.answerer = answerer or EvidenceAnswerer()
        self.visualization_planner = visualization_planner or VisualizationPlanner(model=getattr(self.answerer, "model", None))
        self.dynamic_chart_planner = dynamic_chart_planner or DynamicChartPlanner(
            getattr(self.answerer, "model", None)
        )
        self.knowledge_need_resolver = knowledge_need_resolver or KnowledgeNeedResolver(enabled=False)
        self.knowledge_retriever = knowledge_retriever
        self.evidence_fusion = evidence_fusion or EvidenceFusion()
        self.recommendation_composer = recommendation_composer or RecommendationComposer()
        self.recommendation_evidence_resolver = recommendation_evidence_resolver or RecommendationEvidenceResolver()
        self.recommendation_presentation_builder = (
            recommendation_presentation_builder or RecommendationPresentationBuilder()
        )
        self.prediction_fusion = prediction_fusion or PredictionFusion()
        self.history_max_messages = history_max_messages
        self.business_api_url = business_api_url
        self._locks: dict[str, RLock] = {}
        self._locks_guard = RLock()

    def _session_lock(self, session_id: str) -> RLock:
        with self._locks_guard:
            return self._locks.setdefault(session_id, RLock())

    def _plan_turn(self, question: str, page_context: Mapping[str, Any] | None,
                   session: StoredSession,
                   request_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        model = getattr(self.answerer, "model", None)
        planner = getattr(model, "plan_turn", None)
        if not callable(planner):
            raise ModelPlanningError("大模型未配置，无法决定业务操作；请配置模型服务后重试。")
        try:
            raw = planner(
                question=question,
                current_page=str((page_context or {}).get("page") or "overview"),
                previous_goal=session.last_user_goal,
                has_results=bool(session.canonical_agent_results),
                recent_history=session.history[-4:],
                pending_trip=session.task_context.get("pending_trip"),
                current_context={**session.task_context, "data_status": {
                    "parcel_model_current": bool(session.canonical_agent_results)
                        and not self._needs_parcel_refresh(session.canonical_agent_results),
                    "expected_parcel_version": load_parcel_rules()["version"],
                }},
                request_context=request_context,
                station_catalog=self.context_resolver.station_catalog(),
            )
        except Exception as exc:
            logger.warning("model turn planning failed: %s", type(exc).__name__)
            raise ModelPlanningError("大模型任务规划暂不可用，请稍后重试。") from exc
        if (not isinstance(raw, Mapping) or raw.get("goal") not in _MODEL_GOALS
                or raw.get("business_action") not in {"run", "reuse", "none"}
                or raw.get("visualization_action") not in {"auto", "none", "view", "reset"}
                or raw.get("travel_action") not in {"advice", "plan_route", "inspect_route", "none"}
                or any(not isinstance(raw.get(key), bool) for key in
                       ("use_knowledge", "use_recommendation", "requires_business_facts"))):
            raise ModelPlanningError("大模型返回的任务计划无效，请重试。")
        plan: dict[str, Any] = {key: raw[key] for key in (
            "business_action", "visualization_action", "requires_business_facts")}
        if isinstance(raw.get("task_updates"), Mapping):
            plan["task_updates"] = dict(raw["task_updates"])
        if isinstance(raw.get("refresh_data"), list):
            plan["refresh_data"] = [item for item in raw["refresh_data"]
                                    if isinstance(item, str) and item in {"hourly_air", "parcel"}]
        if isinstance(raw.get("clarification"), str):
            plan["clarification"] = raw["clarification"][:500]
        if raw.get("travel_action") in {"advice", "plan_route", "inspect_route", "none"}:
            plan["travel_action"] = raw["travel_action"]
        if raw.get("goal") in _MODEL_GOALS:
            plan["goal"] = raw["goal"]
        if raw.get("page") in _MODEL_PAGES:
            plan["page"] = raw["page"]
        for key in ("use_knowledge", "use_recommendation", "show_route_map", "show_propagation_map"):
            if isinstance(raw.get(key), bool):
                plan[key] = raw[key]
        identity = explicit_travel_profile(question, raw)
        if identity:
            plan["travel_profile"] = identity
            plan["travel_identity"] = raw.get("travel_identity", "")
        source = "".join(question.split())
        for key in ("origin", "destination"):
            value = raw.get(key)
            if isinstance(value, str) and 2 <= len(value) <= 80 and "".join(value.split()) in source:
                plan[key] = value.strip()
        if raw.get("trip_followup_role") in {"origin", "destination", "none"}:
            plan["trip_followup_role"] = raw["trip_followup_role"]
        return plan

    def chat(
        self,
        session_id: str,
        user_input: str,
        request_context: Mapping[str, Any] | None = None,
        page_context: Mapping[str, Any] | None = None,
    ) -> ChatResponse:
        normalized_id = session_id.strip()
        normalized_input = user_input.strip()
        if not normalized_id:
            raise ValueError("session_id is required")
        if not normalized_input:
            raise ValueError("user_input is required")

        with self._session_lock(normalized_id):
            session = self.store.get_or_create(normalized_id)
            if not session.visualization_context:
                session.visualization_context = empty_visualization_context()
            expected_version = session.version
            turn_plan = self._plan_turn(normalized_input, page_context, session, request_context)
            target_page = turn_plan.get("page")
            travel_action = turn_plan.get("travel_action")
            # With an available planner, only its tool decision can initiate a route.
            # Page location and keyword parsing must not override an advice decision.
            named_trip = None
            if travel_action not in {"advice", "none"}:
                if "origin" in turn_plan and "destination" in turn_plan:
                    named_trip = (turn_plan["origin"], turn_plan["destination"])
            pending = session.task_context.get("pending_trip")
            if travel_action in {"advice", "none"}:
                session.task_context.pop("pending_trip", None)
            if (travel_action not in {"advice", "none"} and not named_trip
                    and isinstance(pending, Mapping)):
                role = pending.get("role")
                choices = pending.get("candidates") or []
                chosen = normalized_input.strip("。；; ，,")
                # Exact candidate selection is authoritative even if the model
                # classifies a short follow-up as a fresh question.
                selected = chosen in choices
                model_selected = (turn_plan.get("trip_followup_role") == role
                                  and len(chosen) <= 80
                                  and not any(mark in chosen for mark in "？?"))
                if role in {"origin", "destination"} and (selected or model_selected):
                    named_trip = (chosen, str(pending["destination"])) if role == "origin" else (
                        str(pending["origin"]), chosen)
            explicit_profile = explicit_travel_profile(normalized_input, turn_plan)
            route_requested = travel_action in {"plan_route", "inspect_route"}
            if route_requested:
                if not self.business_api_url:
                    raise ValueError("出行证据服务尚未配置")
                travel_profile = explicit_profile or session.task_context.get("travel_profile")
                if isinstance(travel_profile, Mapping):
                    travel_profile = travel_profile.get("profile_id")
                travel_context = dict(page_context or {})
                if travel_profile:
                    travel_context["profile_id"] = travel_profile
                    if travel_profile != (page_context or {}).get("profile_id"):
                        travel_context.pop("route_id", None)
                try:
                    if (travel_action == "plan_route" and not named_trip
                            and not session.task_context.get("travel_evidence_id")
                            and not (page_context and page_context.get("page") == "travel"
                                     and page_context.get("mode") in {"live", "case", "default"})):
                        raise PlaceResolutionError(
                            "请明确写出起点和终点，例如“从旺角到油麻地”。")
                    if named_trip:
                        evidence_id = plan_named_trip(named_trip, self.business_api_url,
                                                      travel_context, request_context)
                        session.task_context.pop("pending_trip", None)
                        session.task_context["travel_evidence_id"] = evidence_id
                        selector: Mapping[str, Any] = {
                            "page": "travel", "mode": "live", "evidence_id": evidence_id}
                    elif (page_context and page_context.get("page") == "travel"
                          and page_context.get("mode") in {"case", "live", "default"}):
                        selector = travel_context
                    elif session.task_context.get("travel_evidence_id"):
                        selector = {"page": "travel", "mode": "live",
                                    "evidence_id": session.task_context["travel_evidence_id"]}
                    else:
                        raise PlaceResolutionError("请说明出行起点和终点，例如“从旺角到油麻地”，我会规划路线并展示地图。")
                    evidence = resolve_travel_evidence(selector, self.business_api_url)
                    actual_profile = (evidence.get("travel_profile") or {}).get("profile_id")
                    if travel_profile and actual_profile != travel_profile:
                        if selector.get("mode") in {"case", "default"}:
                            selector = {**selector, "profile_id": travel_profile, "route_id": ""}
                            evidence = resolve_travel_evidence(selector, self.business_api_url)
                        else:
                            evidence_id = replan_travel_evidence(
                                evidence, travel_profile, self.business_api_url)
                            selector = {"page": "travel", "mode": "live", "evidence_id": evidence_id}
                            evidence = resolve_travel_evidence(selector, self.business_api_url)
                            session.task_context["travel_evidence_id"] = evidence_id
                    if travel_profile:
                        session.task_context["travel_profile"] = travel_profile
                except PlaceResolutionError as exc:
                    if named_trip and exc.role in {"起点", "终点"}:
                        session.task_context["pending_trip"] = {
                            "origin": named_trip[0], "destination": named_trip[1],
                            "role": "origin" if exc.role == "起点" else "destination",
                            "candidates": exc.candidates,
                        }
                    if travel_profile:
                        session.task_context["travel_profile"] = travel_profile
                    answer = str(exc)
                    self._append_history(session, normalized_input, answer)
                    session.client_messages.extend((
                        {"role": "user", "content": normalized_input},
                        {"role": "assistant", "content": answer},
                    ))
                    session.client_messages = session.client_messages[-20:]
                    self.store.save(session, expected_version=expected_version)
                    return ChatResponse(
                        answer=answer, business_action="need_params", visualization_action="none",
                        route="travel", context={"page": "travel", "agent_results": {}},
                        agent_results={}, reason="place_needs_selection", user_goal="public_travel_advice",
                    )
                except requests.RequestException as exc:
                    raise BusinessExecutionError("出行证据服务当前不可用") from exc
                map_payload = evidence.pop("_route_map", None)
                answer = self.answerer.answer(
                    normalized_input, canonical=None, history=session.history,
                    user_goal="unknown", page_evidence=evidence,
                )
                travel_answer_available = answer not in FAILED_ANSWERS
                travel_knowledge = None
                travel_fused = None
                if turn_plan.get("use_knowledge") is True and self.knowledge_retriever is not None:
                    knowledge_need = self.knowledge_need_resolver.resolve(
                        normalized_input, user_goal="public_travel_advice",
                        context={"agent_results": {}}, model_decision=True)
                    if knowledge_need.needs_rag:
                        travel_knowledge = self.knowledge_retriever.retrieve(knowledge_need)
                        answer, fused = self.evidence_fusion.fuse(
                            base_answer=answer, agent_results={}, knowledge=travel_knowledge,
                            resolution=knowledge_need)
                        travel_fused = fused.to_dict()
                charts = (travel_charts(normalized_input, evidence,
                                        model=getattr(self.answerer, "model", None))
                          if turn_plan["visualization_action"] == "auto" and travel_answer_available else [])
                show_route_map = travel_answer_available and bool(map_payload) and (
                    bool(named_trip) or turn_plan.get("show_route_map") is True
                )
                content_blocks = ({"type": "text", "text": answer},)
                content_blocks += tuple({"type": "chart", "id": chart["id"], "chart": chart}
                                        for chart in charts)
                if show_route_map:
                    content_blocks += ({"type": "map", "id": "recommended-route", "map": map_payload},)
                self._append_history(session, normalized_input, answer)
                session.client_messages.extend((
                    {"role": "user", "content": normalized_input},
                    {"role": "assistant", "content": answer,
                     "content_blocks": list(content_blocks)},
                ))
                session.client_messages = session.client_messages[-20:]
                self.store.save(session, expected_version=expected_version)
                return ChatResponse(
                    answer=answer, business_action="fallback", visualization_action="none",
                    route="travel", context={"page": "travel", "source": evidence["source"],
                                             "evidence_id": selector.get("evidence_id"),
                                             "agent_results": {}}, agent_results={},
                    reason="trusted_page_evidence", user_goal="public_travel_advice",
                    content_blocks=content_blocks,
                    knowledge=travel_knowledge.to_dict() if travel_knowledge else None,
                    fused_evidence=travel_fused,
                )
            has_results = bool(session.canonical_agent_results)
            goal = self.goal_resolver.resolve(normalized_input, has_results=has_results,
                                              model_plan=turn_plan)
            context = self.context_resolver.resolve(
                normalized_input,
                request_context,
                session.task_context,
                model_updates=turn_plan.get("task_updates"),
            )
            decision = self.continuation_resolver.resolve(
                goal,
                context,
                has_results=has_results,
                model_action=turn_plan["business_action"],
            )

            if decision.invalidate_results:
                self._invalidate_analysis(session)
                has_results = False
            prior_travel_profile = explicit_profile or session.task_context.get("travel_profile")
            if isinstance(prior_travel_profile, Mapping):
                prior_travel_profile = prior_travel_profile.get("profile_id")
            prior_travel_evidence_id = session.task_context.get("travel_evidence_id")
            session.task_context = dict(context.task)
            if prior_travel_evidence_id:
                session.task_context["travel_evidence_id"] = prior_travel_evidence_id
            if prior_travel_profile:
                session.task_context["travel_profile"] = prior_travel_profile
            canonical = session.canonical_agent_results

            knowledge_kwargs = ({"model_decision": turn_plan["use_knowledge"]}
                                if "use_knowledge" in turn_plan else {})
            knowledge_need = self.knowledge_need_resolver.resolve(
                normalized_input,
                user_goal=goal.user_goal,
                previous_knowledge_context=session.knowledge_context,
                context={
                    "agent_results": (canonical or {}).get("agent_results", {})
                    if isinstance(canonical, Mapping) else {}
                },
                **knowledge_kwargs,
            )
            requests_business_facts = turn_plan["requires_business_facts"]
            is_recommendation_goal = turn_plan["use_recommendation"]
            knowledge_only = (
                knowledge_need.needs_rag
                and not has_results
                and not requests_business_facts
                and not is_recommendation_goal
            )
            effective_action = decision.action
            if knowledge_only:
                effective_action = "fallback"
            elif (
                knowledge_need.needs_rag and not has_results and requests_business_facts
                and not context.missing_fields and not context.errors
            ):
                effective_action = "run"
            elif (
                has_results and not context.missing_fields and not context.errors
                and ((requests_business_facts and self._needs_coordinate_refresh(canonical, context.task))
                     or ("hourly_air" in turn_plan.get("refresh_data", []) and self._needs_hourly_air_refresh(canonical))
                     or ("parcel" in turn_plan.get("refresh_data", []) and self._needs_parcel_refresh(canonical)))
            ):
                # Older persisted sessions contain only window aggregates.
                # Refresh once so an ordinary question can use newly available
                # observed hourly records without forcing a new conversation.
                effective_action = "run"

            if knowledge_only:
                route = "knowledge"
                business_action = "knowledge"
            elif effective_action == "run":
                task = dict(context.task)
                task.setdefault("task_id", self._task_id(normalized_id))
                canonical_result = self.executor.execute(task)
                canonical = canonical_result.to_dict()
                session.task_context = dict(canonical_result.task)
                session.canonical_agent_results = canonical
                session.evidence_metadata = self._evidence_metadata(canonical)
                session.analysis_generated_at = _now_iso()
                session.analysis_source = self.executor.source_name
                has_results = True
                route = self._route_for_goal(goal.user_goal, session.last_route)
                session.last_route = route
                business_action = "run"
            elif effective_action == "reuse":
                route = self._route_for_goal(goal.user_goal, session.last_route)
                business_action = "fallback"
            elif effective_action == "need_params":
                route = None
                business_action = "need_params"
            else:
                route = session.last_route
                business_action = "fallback"

            if goal.user_goal == "public_travel_advice":
                from tools.travel_advice_tools import load_travel_config, resolve_profile
                profile = resolve_profile(load_travel_config(), prior_travel_profile or
                                          (page_context or {}).get("profile_id"))
                session.task_context["travel_profile"] = profile
                if canonical:
                    canonical = {**canonical, "task": {**(canonical.get("task") or {}),
                                                       "travel_profile": profile}}

            visualization_plan = VisualizationPlanResult(
                "no_match", "none", None, None, None, None, (), "no_visualization_intent"
            )
            previous_dynamic_charts = list(session.visualization_context.get("dynamic_charts") or [])
            view_action = turn_plan["visualization_action"]
            if view_action == "reset":
                visualization_plan = self.visualization_planner._reset(session.visualization_context)
            elif effective_action != "need_params" and view_action == "view" and not turn_plan.get("show_propagation_map"):
                visualization_plan = self.visualization_planner.plan(
                    normalized_input, canonical=canonical,
                    visualization_context=session.visualization_context,
                    task_context=session.task_context)

            visualization_action = "none"
            visualization_payload: dict[str, Any] | None = None
            visualization_reason: str | None = None
            if visualization_plan.status in {"planned", "reset"}:
                previous_revision = int(session.visualization_context.get("revision", 0) or 0)
                revision = previous_revision + 1
                if visualization_plan.status == "reset":
                    session.visualization_context = {
                        **empty_visualization_context(),
                        "revision": revision,
                    }
                    visualization_action = "reset"
                else:
                    assert visualization_plan.spec is not None
                    spec_dict = visualization_plan.spec.to_dict()
                    session.visualization_context = {
                        "last_spec": spec_dict,
                        "last_view_id": visualization_plan.view_id,
                        "active_chart_type": visualization_plan.spec.chart_type,
                        "active_metrics": list(visualization_plan.spec.metrics),
                        "active_data_source": visualization_plan.spec.data_source,
                        "view_mode": visualization_plan.spec.view_type,
                        "revision": revision,
                        "data_provider": "canonical_agent_results",
                    }
                    visualization_action = visualization_plan.action
                visualization_payload = self._visualization_payload(visualization_plan, revision)
                visualization_reason = visualization_plan.reason
            elif visualization_plan.status == "invalid":
                visualization_reason = visualization_plan.reason
            elif visualization_plan.status == "no_match" and session.visualization_context.get("last_spec"):
                visualization_payload = self._unchanged_visualization_payload(session.visualization_context)

            knowledge_context = KnowledgeContext.not_used(knowledge_need)
            if knowledge_need.needs_rag and self.knowledge_retriever is not None:
                knowledge_context = self.knowledge_retriever.retrieve(knowledge_need)
                session.knowledge_context = knowledge_context.to_dict()

            if turn_plan.get("clarification"):
                answer = turn_plan["clarification"]
            elif effective_action == "need_params":
                missing = context.missing_fields or decision.missing_fields
                answer = "请补充完成分析所需参数：" + "、".join(missing or ("有效站点或时间范围",)) + "。"
            elif visualization_plan.status in {"planned", "reset"}:
                answer = self._visualization_answer(visualization_plan)
            elif visualization_plan.status == "invalid" and goal.user_goal == "visualization":
                answer = f"无法生成可执行可视化：{visualization_plan.reason}。原有图表状态未覆盖。"
            else:
                answer = self.answerer.answer(
                    normalized_input,
                    canonical=canonical,
                    history=session.history,
                    user_goal=goal.user_goal,
                    **({"focus_page": target_page}
                       if target_page in {"overview", "weather", "restriction", "report"}
                       else {"focus_page": page_context.get("page")} if page_context else {}),
                )

            recommendation_requested = (
                goal.user_goal in {"public_travel_advice", "health_protection", "governance_advice"}
                and has_results and turn_plan["use_recommendation"]
            )
            # The model can choose a direct answer instead of a recommendation
            # card. Its structured advice still needs rendering before fusion,
            # content blocks, or history receive it.
            if not recommendation_requested:
                answer = readable_advice_answer(answer)
            base_answer = answer
            fused_evidence: dict[str, Any] | None = None
            if knowledge_need.needs_rag:
                answer, fused = self.evidence_fusion.fuse(
                    base_answer=answer,
                    agent_results=(canonical or {}).get("agent_results", {})
                    if isinstance(canonical, Mapping) else {},
                    knowledge=knowledge_context,
                    resolution=knowledge_need,
                )
                fused_evidence = fused.to_dict()

            # Phase 5: Generate recommendation if user goal is recommendation-oriented
            recommendation_result_payload: dict[str, Any] | None = None
            recommendation_display_payload: dict[str, Any] | None = None
            if recommendation_requested:
                agent_results_envelope = (
                    canonical.get("agent_results") if isinstance(canonical, Mapping) else {}
                ) or {}
                
                # Assess evidence completeness
                evidence_resolution = self.recommendation_evidence_resolver.resolve(
                    recommendation_type=goal.user_goal,
                    agent_results=agent_results_envelope,
                    allow_enrichment=False,  # Never run Agents automatically
                )
                
                # Assess future risk if prediction evidence available
                future_risk = None
                if evidence_resolution.available_evidence and "prediction" in evidence_resolution.available_evidence:
                    future_risk = self.prediction_fusion.assess_from_agent_results(agent_results_envelope)
                
                # Compose recommendation
                recommendation_result = self.recommendation_composer.compose(
                    agent_results=agent_results_envelope,
                    context=session.task_context,
                    resolution=goal,
                    previous_recommendation=session.last_recommendation,
                    evidence_resolution=evidence_resolution,
                    future_risk=future_risk,
                )
                recommendation_result_payload = recommendation_result.to_dict()
                session.last_recommendation = recommendation_result_payload
                recommendation_display_payload = self.recommendation_presentation_builder.build(
                    recommendation=recommendation_result_payload,
                    agent_results=agent_results_envelope,
                    context=session.task_context,
                    explanation=base_answer,
                    knowledge=(
                        knowledge_context.to_dict()
                        if knowledge_need.needs_rag and knowledge_context.used
                        else None
                    ),
                )
                if goal.user_goal == "governance_advice":
                    recommendation_result_payload["summary"] = recommendation_display_payload["explanation"]
                # Preserve a readable response for older clients.  The new UI
                # consumes recommendation_display directly and does not parse
                # this Markdown string into sections.
                answer = str(recommendation_display_payload["markdown_fallback"])

            dynamic_charts: list[dict[str, Any]] = []
            if (canonical and effective_action != "need_params"
                    and visualization_plan.status != "reset"
                    and goal.user_goal not in {"capability_intro"}
                    and not knowledge_only and view_action == "auto" and base_answer not in FAILED_ANSWERS):
                dynamic_charts = self.dynamic_chart_planner.plan(
                    normalized_input, canonical, previous_dynamic_charts
                )
            if dynamic_charts:
                if goal.user_goal == "visualization":
                    answer = "已根据当前分析数据生成图表。你可以继续要求筛选时段、调整图形或修改其中一张。"
                saved_charts = {chart["id"]: chart for chart in previous_dynamic_charts}
                for chart in dynamic_charts:
                    saved_charts[chart["id"]] = chart
                session.visualization_context["dynamic_charts"] = list(saved_charts.values())[-10:]
            content_blocks = (
                ({"type": "text", "text": answer},)
                + tuple({"type": "chart", "id": chart["id"], "chart": chart}
                        for chart in dynamic_charts)
            ) if dynamic_charts else ()

            if (turn_plan.get("show_propagation_map") is True and view_action in {"auto", "view"}
                    and effective_action != "need_params" and not turn_plan.get("clarification")
                    and not knowledge_only and base_answer not in FAILED_ANSWERS):
                propagation_map = propagation_map_payload(canonical)
                if propagation_map:
                    if not content_blocks:
                        content_blocks = ({"type": "text", "text": answer},)
                    content_blocks += ({"type": "propagation_map", "id": "pollution-propagation",
                                        "map": propagation_map},)

            session.last_user_goal = goal.user_goal
            session.last_audience = goal.audience
            self._append_history(session, normalized_input, answer)
            session.client_messages.extend((
                {"role": "user", "content": normalized_input},
                {"role": "assistant", "content": answer,
                 "content_blocks": list(content_blocks),
                 "visualization_action": visualization_action,
                 "visualization": visualization_payload,
                 "recommendation_display": recommendation_display_payload},
            ))
            session.client_messages = session.client_messages[-20:]
            self.store.save(session, expected_version=expected_version)

            agent_results = (
                canonical.get("agent_results") if isinstance(canonical, Mapping) else {}
            ) or {}
            response_context = {
                **{key: session.task_context.get(key) for key in (
                    "task_id", "station_id", "station_name", "start_time", "end_time", "lat", "lon"
                ) if key in session.task_context},
                "agent_results": agent_results,
            }
            reason = decision.reason
            if knowledge_only:
                reason = knowledge_need.reason
            if visualization_reason is not None:
                reason = visualization_reason
            if effective_action == "run" and any(
                item.get("status") == "partial" for item in agent_results.values()
                if isinstance(item, Mapping)
            ):
                reason = "analysis_completed_with_partial_results"
            return ChatResponse(
                answer=answer,
                business_action=business_action,  # type: ignore[arg-type]
                visualization_action=visualization_action,  # type: ignore[arg-type]
                route="visualization" if visualization_action != "none" else route,
                context=response_context,
                agent_results=dict(agent_results),
                missing_fields=tuple(context.missing_fields or decision.missing_fields) if effective_action == "need_params" else (),
                reason=reason,
                visualization=visualization_payload,
                content_blocks=content_blocks,
                user_goal=goal.user_goal,
                audience=goal.audience,
                goal_reason=goal.reason,
                knowledge=knowledge_context.to_dict() if knowledge_need.needs_rag else None,
                fused_evidence=fused_evidence,
                recommendation_result=recommendation_result_payload,
                recommendation_display=recommendation_display_payload,
            )

    def _append_history(self, session: StoredSession, user_input: str, answer: str) -> None:
        check_generation_active()
        session.history.extend((
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": answer},
        ))
        if len(session.history) > self.history_max_messages:
            session.history = session.history[-self.history_max_messages :]

    @staticmethod
    def _invalidate_analysis(session: StoredSession) -> None:
        session.canonical_agent_results = None
        session.evidence_metadata = []
        session.analysis_generated_at = None
        session.analysis_source = None
        session.last_recommendation = None  # Phase 5: Clear recommendation when analysis invalidated
        # Keep the monotonic revision while removing the stale spec so a
        # frontend can discard a late response from the previous scope.
        previous_revision = int(session.visualization_context.get("revision", 0) or 0)
        session.visualization_context = {
            **empty_visualization_context(),
            "revision": previous_revision,
        }

    @staticmethod
    def _visualization_payload(plan: VisualizationPlanResult, revision: int) -> dict[str, Any]:
        return {
            "status": "reset" if plan.status == "reset" else "planned",
            "valid": True,
            "revision": revision,
            "view_id": plan.view_id,
            "spec": plan.spec.to_dict() if plan.spec is not None else None,
            "view_command": plan.command.to_dict() if plan.command is not None else None,
            "data": plan.data,
            "warnings": list(plan.warnings),
            "reason": plan.reason,
        }

    @staticmethod
    def _unchanged_visualization_payload(context: Mapping[str, Any]) -> dict[str, Any]:
        spec = context.get("last_spec")
        return {
            "status": "unchanged",
            "valid": True,
            "revision": int(context.get("revision", 0) or 0),
            "view_id": context.get("last_view_id"),
            "spec": spec,
            "view_command": {
                "action": "none",
                "view_id": context.get("last_view_id"),
                "spec": spec,
                "reason": "explanation_does_not_change_visualization",
            },
            "data": None,
            "warnings": [],
            "reason": "explanation_does_not_change_visualization",
        }

    @staticmethod
    def _visualization_answer(plan: VisualizationPlanResult) -> str:
        if plan.status == "reset":
            return "已返回默认地图视图，当前图表上下文已清除。"
        assert plan.spec is not None
        action = "更新" if plan.action == "update" else "生成"
        default_note = (
            "你未指定具体气象指标，已按真实可用数据生成逐小时综合气象图。"
            if plan.reason == "default_hourly_weather_composite"
            else ""
        )
        return f"已{action}{plan.spec.title}。{default_note}"

    @staticmethod
    def _task_id(session_id: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        safe = "".join(char for char in session_id if char.isalnum() or char in "-_")[:32]
        return f"ai-{safe or 'session'}-{stamp}"

    @staticmethod
    def _needs_hourly_air_refresh(canonical: Mapping[str, Any] | None, text: str = "") -> bool:
        if not isinstance(canonical, Mapping):
            return False
        air = (canonical.get("agent_results") or {}).get("AirQualityAgent") or {}
        result = air.get("result") or {}
        return air.get("status") == "available" and "hourly_series" not in result

    @staticmethod
    def _needs_coordinate_refresh(canonical: Mapping[str, Any] | None, task: Mapping[str, Any]) -> bool:
        if not canonical or task.get("lat") is None or task.get("lon") is None:
            return False
        old_task = canonical.get("task") or {}
        weather = (canonical.get("agent_results") or {}).get("MeteorologyAgent") or {}
        note = str((weather.get("data_quality") or {}).get("note") or "")
        return (old_task.get("lat") is None or old_task.get("lon") is None) and (
            "lat 和 lon 必须同时提供" in note)

    @staticmethod
    def _needs_parcel_refresh(canonical: Mapping[str, Any] | None, text: str = "") -> bool:
        if not isinstance(canonical, Mapping):
            return False
        weather = ((canonical.get("agent_results") or {}).get("MeteorologyAgent") or {}).get("result") or {}
        prediction = weather.get("pollution_path_prediction") or {}
        return bool(weather) and (prediction.get("lifetime_model") or {}).get("version") != load_parcel_rules()["version"]

    @staticmethod
    def _route_for_goal(goal: str, previous: str | None) -> str:
        return {
            "governance_advice": "decision",
            "report": "report",
            "public_travel_advice": "traffic",
            "health_protection": "air_quality",
            "analysis": "report",
        }.get(goal, previous or "report")

    @staticmethod
    def _evidence_metadata(canonical: Mapping[str, Any]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for agent, envelope in (canonical.get("agent_results") or {}).items():
            if not isinstance(envelope, Mapping):
                continue
            output.append({
                "agent": agent,
                "domain": envelope.get("domain"),
                "status": envelope.get("status"),
                "confidence": envelope.get("confidence"),
                "evidence_ids": [
                    item.get("evidence_id") for item in (envelope.get("evidence") or [])
                    if isinstance(item, Mapping) and item.get("evidence_id")
                ],
                "limitations": list(envelope.get("limitations") or []),
                "provenance": dict(envelope.get("provenance") or {}),
            })
        return output

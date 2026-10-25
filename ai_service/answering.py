"""Evidence-only response composition with an optional OpenAI-compatible model."""

from __future__ import annotations

import json
import logging
import re
import time
from uuid import uuid4
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Protocol
from urllib.parse import urlparse

import requests

from ai_service.recommendation.governance import (
    GOVERNANCE_UNAVAILABLE, parse_governance_answer, normalize_model_answer,
)
from ai_service.answer_generation import (
    AnswerGenerationCancelled, check_generation_active, wait_for_answer_retry,
)
from ai_service.identity import ASSISTANT_ROLE
from ai_service.route_labels import readable_route_names

MODEL_UNAVAILABLE_ANSWER = "大模型当前不可用，暂时无法生成回答，请稍后重试。"
ANSWER_STYLE_RETRY_FAILED = "这条回答未能按要求生成，请重试。"
ANSWER_VALIDATION_FAILED = "模型已响应，但回答中的数值或引用未通过校验，重写后仍未满足要求，请重试。"
MODEL_TIMEOUT_ANSWER = "模型响应超时，请稍后重试。"
MODEL_CONNECTION_FAILED = "AI 后端连接模型服务失败，请检查运行后端电脑的网络后重试。"
MODEL_AUTH_FAILED = "模型服务认证失败，请检查 AI 后端配置的 API 密钥和访问权限。"
MODEL_RATE_LIMITED = "模型服务暂时限流或额度受限，请稍后重试并检查服务额度。"
MODEL_FORMAT_FAILED = "模型已响应，但返回格式无法解析，请重试。"
FAILED_ANSWERS = frozenset({
    MODEL_UNAVAILABLE_ANSWER, ANSWER_STYLE_RETRY_FAILED, ANSWER_VALIDATION_FAILED,
    MODEL_TIMEOUT_ANSWER, MODEL_CONNECTION_FAILED, MODEL_AUTH_FAILED,
    MODEL_RATE_LIMITED, MODEL_FORMAT_FAILED,
})
_BLANKET_REFUSAL = re.compile(
    r"(?:证据|依据|资料|信息|数据)(?:不足|不够|有限|不充分)"
    r"|(?:没有|暂无|缺乏)(?:足够|充分)的?证据"
    r"|(?:不能|无法|没法|不能够|无法直接|不能直接|暂时无法|暂不能)"
    r"(?:准确|可靠|明确|定量|直接|进一步|为你|为您)?"
    r"(?:给出|提供|回答|判断|确定|比较|排序|推荐|计算|预测|评估|得出)"
    r"|(?:无法|不能)(?:按|逐)[^。；\n]{0,16}(?:比较|排序|评估)"
    r"|(?:这次|本次)(?:没有|未)(?:给出|提供|返回)"
    r"|(?:模块|数据源)(?:这次|本次|当前)?(?:没有|未)(?:提供|返回|给出)"
    r"|(?:数据|记录|吸入当量|暴露评分)(?:可用于|有助于)(?:进一步)?(?:细化|完善|补充)"
    r"|(?:已有数据|当前数据|现有结果)(?:支持的|能说明的|可说明的)(?:是|仅|只)"
    r"|需要(?:说明|强调|澄清)的是"
    r"|(?:不是|并非)(?:实际环境浓度|实测(?:污染)?轨迹|实测传播路径)"
    r"|(?:空间覆盖|影响范围)[^。；\n]{0,8}不代表[^。；\n]{0,8}(?:发生污染|污染已经发生)"
    r"|(?:没有|未)(?:匹配到|匹配|找到)[^。；\n]{0,16}(?:站点|监测站)"
    r"|(?:没有|暂无)[^。；\n]{0,16}站点观测[^。；\n]{0,24}(?:判定|结果)"
)

_DIRECT_ANSWER_STYLE = (
    "回答只包含与用户问题直接相关的结论、理由和具体行动，不写防御性段落。"
    "不要主动列举缺测字段、未提供的数据、分析能力限制、补数据计划或免责声明，"
    "不要写‘逐小时吸入当量可用于细化出发时间’之类收尾，也不要用‘已有数据支持的只是’压低结论。"
    "先综合本轮各模块已给出的时序、推荐时段、路线和评分，再选择最贴近问题的结果直接回答。"
    "问几点出发时，有推荐时段或逐小时比较就先给优选时段，再给简短理由；"
    "只比较了浓度就说污染浓度较低，不把浓度最低改称个人吸入量最少。"
    "没有时间排序时，用已知结果给出具体出行安排，不捏造最佳时刻或排序。"
    "不相关或未给出的指标直接略过；用户明确追问数据完整性、方法局限或某字段时再针对该问题解释。"
    "传播问题直接说明模拟传播方向、覆盖范围、沿途地点和变化趋势，不自动追加颜色原理、"
    "模拟与实测的区别、空间覆盖含义或站点验证缺项。用‘模拟传播路径’‘气团剩余比例’"
    "等准确名称即可，不再补写‘需要说明的是’或否定性说明。"
    "站点对照为空时略过该项，有对照结果且与问题相关时再说明结果。"
    "历史消息、模块 warnings、assumptions 中的限制性文字仅用于内部约束，不要照搬到回答。"
)


def _blanket_refusal(answer: str) -> bool:
    return isinstance(answer, str) and bool(_BLANKET_REFUSAL.search(re.sub(r"\*\*|__|`", "", answer)))


logger = logging.getLogger(__name__)

_UNVERIFIED_CITATION = re.compile(
    r"(?:\[\d+\]|参考文献|参考资料|doi\.org|https?://)", re.IGNORECASE
)
_FORBIDDEN_FACT_CLAIM = re.compile(
    r"(?:贡献率(?:为|是|约|最高|最大)|真实道路流量(?:为|是|约)|实测(?:污染)?轨迹|监测到的传播轨迹)",
    re.IGNORECASE,
)
# 否定前瞻：声明句（"……不是实测轨迹"）是合法的免责表述，不算越界断言。
_FORBIDDEN_NEGATIONS = ("不是", "并非", "不代表", "不属于", "不等于", "并非是")
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?%?")
_POSSIBLE_SECRET = re.compile(r"(?i)(?:bearer\s+)?sk-[a-z0-9_-]{8,}")

_AGENT_PAGE_ORDER = (
    "AirQualityAgent", "MeteorologyAgent", "TrafficAgent", "SpatialAgent",
    "SourceAgent", "DecisionAgent", "ReportAgent",
)
_PAGE_AGENT_GROUPS = {
    "overview": ("AirQualityAgent", "MeteorologyAgent", "TrafficAgent", "ReportAgent"),
    "weather": ("MeteorologyAgent", "SpatialAgent", "AirQualityAgent"),
    "restriction": ("SourceAgent", "TrafficAgent", "DecisionAgent", "SpatialAgent"),
    "report": ("ReportAgent", "DecisionAgent", "SourceAgent"),
}
_GEOMETRY_FIELDS = {"geometry", "affected_area_geojson", "path_geojson",
                    "uncertainty_corridor_geojson", "propagation_sector_geojson", "sector_steps"}


def _relevant_agent_order(question: str, focus_page: str | None) -> tuple[str, ...]:
    """Question intent takes precedence over the tab that happened to open chat."""
    selected = _PAGE_AGENT_GROUPS.get(focus_page or "", ())
    return selected + tuple(name for name in _AGENT_PAGE_ORDER if name not in selected)


def _compact_result_value(value: Any, key: str, question: str) -> Any:
    """Keep page facts while excluding map geometry and large raw evidence arrays."""
    if isinstance(value, Mapping):
        return {name: _compact_result_value(item, name, question)
                for name, item in value.items()
                if name not in _GEOMETRY_FIELDS and not (name == "report_text" and value.get("sections"))}
    if isinstance(value, list):
        limits = {"hourly_series": 48, "upwind_roads": 24, "sensitive_pois": 24,
                  "steps": 12, "forecast_hours": 12, "sections": 12}
        limit = limits.get(key, 24)
        return [_compact_result_value(item, key, question) for item in value[:limit]]
    if isinstance(value, str) and key == "content":
        return value[:1200]
    return value


def _page_agent_evidence(canonical: Mapping[str, Any], question: str,
                         focus_page: str | None) -> dict[str, Any]:
    available = canonical.get("agent_results") or {}
    if not isinstance(available, Mapping):
        return {}
    output: dict[str, Any] = {}
    order = list(_relevant_agent_order(question, focus_page))
    order.extend(name for name in available if name not in order)
    for name in order:
        envelope = available.get(name)
        if not isinstance(envelope, Mapping):
            continue
        result = envelope.get("result")
        compact: dict[str, Any] = {
            "status": envelope.get("status"),
            "result": _compact_result_value(result, "result", question)
            if isinstance(result, Mapping) else {},
        }
        if envelope.get("confidence") is not None:
            compact["confidence"] = envelope["confidence"]
        quality = envelope.get("data_quality")
        if isinstance(quality, Mapping):
            compact["data_quality"] = {key: quality[key] for key in ("source", "missing_rate", "note")
                                       if key in quality}
        provenance = envelope.get("provenance")
        if isinstance(provenance, Mapping) and isinstance(provenance.get("source_meta"), Mapping):
            compact["source_meta"] = _compact_result_value(provenance["source_meta"],
                                                           "source_meta", question)
        output[name] = compact
    return output


def _bounded_evidence_json(evidence: Mapping[str, Any], max_chars: int) -> str:
    """Fit whole JSON objects into the model context; never cut JSON mid-field."""
    payload = deepcopy(dict(evidence))
    payload["agent_results"] = dict(payload.get("agent_results") or {})
    current_page = payload.get("current_page")
    if isinstance(current_page, Mapping):
        payload["current_page"] = dict(current_page)

    def encoded() -> str:
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))

    result = encoded()
    if len(result) <= max_chars:
        return result
    # Raw hourly series and long lists are the first fields to reduce. The
    # scalar summaries, selected route, and each page's domain stay visible.
    for agent in payload["agent_results"].values():
        domain = agent.get("result") if isinstance(agent, Mapping) else None
        if not isinstance(domain, dict):
            continue
        for key in ("hourly_series", "upwind_roads", "sensitive_pois"):
            if isinstance(domain.get(key), list):
                domain[key] = domain[key][:8]
        for key in ("pollution_path_prediction", "wind_direction_forecast"):
            prediction = domain.get(key)
            if isinstance(prediction, dict):
                for series_key in ("steps", "forecast_hours"):
                    if isinstance(prediction.get(series_key), list):
                        prediction[series_key] = prediction[series_key][:6]
    page = payload.get("current_page")
    if isinstance(page, dict) and isinstance(page.get("route_options"), list):
        page["route_options"] = page["route_options"][:8]
    result = encoded()
    if len(result) <= max_chars:
        return result
    for name in reversed(tuple(payload["agent_results"])):
        if len(result) <= max_chars:
            break
        payload["agent_results"].pop(name)
        result = encoded()
    if len(result) <= max_chars:
        return result
    if isinstance(page, dict):
        for key in ("agent_evidence", "data_quality", "route_options", "departure_dose_curve",
                    "pollution_field", "uncertainty", "assumptions"):
            if len(result) <= max_chars:
                break
            page.pop(key, None)
            result = encoded()
    if len(result) <= max_chars:
        return result
    return json.dumps({"task": payload.get("task"), "warnings": ["证据超过模型上下文容量"]},
                      ensure_ascii=False, default=str)


@dataclass(frozen=True, slots=True)
class GroundingCheckResult:
    safe: bool
    rules: tuple[str, ...] = ()
    unsupported_numbers: tuple[str, ...] = ()


def _normalized_number(token: str) -> str:
    """Compare harmless formatting variants without authorizing calculations."""
    raw = token.rstrip("%")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _has_forbidden_claim(answer: str) -> bool:
    """语义越界断言检查；命中前缀带否定词（免责声明）的不算。"""
    for match in _FORBIDDEN_FACT_CLAIM.finditer(answer):
        prefix = answer[max(0, match.start() - 8):match.start()]
        if any(negation in prefix for negation in _FORBIDDEN_NEGATIONS):
            continue
        return True
    return False


def _safe_http_error_details(exc: requests.HTTPError) -> tuple[int | None, str, str]:
    """Return bounded provider diagnostics without headers or credentials."""
    response = exc.response
    if response is None:
        return None, "unknown", "empty_response"
    status = response.status_code
    provider_code = "unknown"
    provider_message = ""
    try:
        value = response.json()
        error = value.get("error") if isinstance(value, Mapping) else None
        if isinstance(error, Mapping):
            provider_code = str(error.get("code") or "unknown")
            provider_message = str(error.get("message") or "")
    except (TypeError, ValueError):
        provider_message = response.text or ""
    provider_code = re.sub(r"[^a-zA-Z0-9_.-]", "_", provider_code)[:80] or "unknown"
    provider_message = " ".join(provider_message.split())[:240] or "empty_response"
    provider_message = _POSSIBLE_SECRET.sub("[REDACTED]", provider_message)
    return status, provider_code, provider_message


class AnswerModel(Protocol):
    def answer(
        self,
        question: str,
        *,
        evidence: Mapping[str, Any],
        history: Sequence[Mapping[str, str]],
        user_goal: str,
    ) -> str: ...


class OpenAICompatibleAnswerModel:
    """Minimal HTTP model client; credentials only come from service config."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        *,
        api_key: str | None,
        timeout_seconds: float,
        max_context_chars: int,
        http: requests.Session | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_context_chars = max_context_chars
        self._http = http or requests.Session()

    @property
    def model_name(self) -> str:
        return self._model

    def extract_trip(self, question: str) -> tuple[str, str] | None:
        """Use the configured model to identify place names, never coordinates."""
        messages = [
            {"role": "system", "content": (
                "你是出行意图解析器。只返回一个 JSON 对象，不要 Markdown。"
                "用户明确给出出行起点和终点时，返回"
                '{"intent":"route","origin":"起点原文","destination":"终点原文"}；'
                "否则返回 {\"intent\":\"other\",\"origin\":\"\",\"destination\":\"\"}。"
                "地点必须是用户原话中的连续文字，不得猜测地点、坐标、日期或路线。"
                "解释当前路线、污染传播或指标变化的问题不是新的起终点规划。"
            )},
            {"role": "user", "content": question[:500]},
        ]
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict[str, Any] = {
            "model": self._model, "messages": messages, "temperature": 0,
            "max_tokens": 180, "stream": False,
        }
        if urlparse(self._endpoint).hostname == "api.siliconflow.cn":
            payload["enable_thinking"] = False
        check_generation_active()
        response = self._http.post(self._endpoint, headers=headers, json=payload,
                                   timeout=min(self._timeout, 20.0))
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("travel intent model returned non-text content")
        cleaned = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        value = json.loads(cleaned)
        if not isinstance(value, Mapping) or value.get("intent") != "route":
            return None
        origin, destination = value.get("origin"), value.get("destination")
        source = "".join(question.split())
        if not all(isinstance(item, str) and 2 <= len(item) <= 80
                   and "".join(item.split()) in source for item in (origin, destination)):
            raise ValueError("travel intent model returned unsupported place names")
        return origin.strip(), destination.strip()

    def plan_turn(self, *, question: str, current_page: str,
                  previous_goal: str, has_results: bool,
                  recent_history: Sequence[Mapping[str, str]] = (),
                  pending_trip: Mapping[str, Any] | None = None,
                  current_context: Mapping[str, Any] | None = None,
                  station_catalog: Sequence[str] = (),
                  request_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Select conversational tools; the service validates every selected action."""
        messages = [
            {"role": "system", "content": (
                "你是环境分析对话的任务规划器。根据用户语义选择一个工具并填写参数，不要 Markdown。"
                '格式：{"goal":"从允许值中选一个",'
                '"page":"从允许值中选一个",'
                '"use_knowledge":true或false,"use_recommendation":true或false,'
                '"show_route_map":true或false,"show_propagation_map":true或false,"origin":"","destination":"",'
                '"trip_followup_role":"origin|destination|none",'
                '"travel_profile":"","travel_identity":""}。'
                "goal 允许值：analysis、explanation、visualization、governance_advice、"
                "public_travel_advice、health_protection、report、capability_intro、unknown；"
                "page 允许值：overview、weather、restriction、travel、report、none。"
                "用户明确提及自己或本次出行对象的身份时，必须填写 travel_profile 和 travel_identity。"
                "travel_identity 必须逐字摘录本轮身份原话；没有提及身份时两者留空，不得从地点或历史猜测。"
                "travel_profile 仅允许 commuter（通勤/上班族）、parent_with_child（带孩子）、"
                "elderly（长者）、runner（跑步者）、tourist（游客），按明确身份选择最对应画像。"
                "否定、假设、引用他人的身份不算用户本次出行身份。"
                "根据用户真正的问题选择目标页面和是否调用工具；当前位置只是默认上下文。"
                "如果用户明确给出出行起点和终点，把用户原话中的地点分别填入 origin 和 destination；"
                "未给出两端就留空，不得猜测。"
                "如果 pending_trip 表示正在等待地点选择，且用户本轮只回复一个地点名称，"
                "按待补充的起点或终点填写 trip_followup_role；转换话题则填 none。"
                "不要把历史地点伪装成用户本轮原话中的 origin 或 destination。"
                "涉及外部研究、文献、原理、标准或需要专业知识支持时才用知识库；"
                "询问当前监测值或已有分析结果时不必检索。"
                "明确需要面向居民的出行、防护或面向管理者的治理建议时才用建议工具，"
                "并把 goal 设为对应类别。用户只问原因或数值时不要用建议工具。"
                "天气适不适合出门、穿衣雨具、避暑、户外活动、防污染等一般出行建议，调用 advise_travel，"
                "不需要起终点，即使当前页面为 travel 或之前问过路线。"
                "例如‘能给我一些出行建议吗’、‘根据天气给出行建议，不是规划路线’均调用 advise_travel。"
                "只有用户真正要求从一个地方到另一个地方的可通行路线时调用 plan_travel_route；"
                "没有起终点也可以调用该工具再询问，但不要为一般出行建议询问起终点。"
                "解释已有路线、比较页面路线或查看路线图时调用 inspect_travel_routes。"
                "用户纠正上一轮理解时，以本轮纠正为准，放弃此前的地点选择。"
                "健康防护必须调用 recommend_health_protection，治理措施调用 recommend_governance；"
                "分析站点数据调用 analyze_environment，解释已有数据调用 explain_environment；"
                "图表和地图操作调用 manage_visualization，报告调用 generate_environment_report；"
                "独立文献知识检索调用 retrieve_environment_knowledge，身份或功能介绍调用 introduce_assistant。"
                "advise_travel 仅用于出行安排，不能用于替代健康防护或治理。其他问题调用 answer_environment_question。"
                "需要地图解释具体出行路线时 show_route_map 为 true。"
                "询问污染往哪里传播、空间范围、沿途监测站，或明确要求传播图时，若地图有助理解，"
                "show_propagation_map 为 true；即使用户未说画图也可主动展示。传播图不是出行路线。"
                "只问一般扩散原理、纯数值或明确不要地图时为 false，不要机械地匹配关键词。"
                "传播地图复用可信业务轨迹，不由你生成坐标；需要它时 requires_business_facts 为 true，"
                "无分析结果则 business_action 选 run，visualization_action 选 auto 或 view。"
                "用户要求不画图、重置或仅返回文字时不展示传播地图。"
                "用户不说画图也可以把 goal 设为普通业务问题；图表由独立图表规划器判断。"
                "业务执行、检索、建议、图表、地图和数据刷新均由你根据完整语义决定，禁止按词语命中机械分类。"
                "business_action 必填 run/reuse/none：需要查询或重新分析站点事实用 run；已有结果可回答用 reuse；"
                "独立文献知识、功能介绍、问候、纯路线任务和不需要站点分析的问题用 none。"
                "没有已有结果且需要业务数据时选 run；已给站点时段只是参数，不等于请求执行分析。"
                "use_knowledge、use_recommendation 必填布尔值，不要对普通业务追问擅自追加建议或文献。"
                "visualization_action 必填 auto/none/view/reset：适合配图用 auto；用户拒绝图表用 none；"
                "需要创建或修改地图、主视图用 view；恢复默认视图或清除图表用 reset。"
                "requires_business_facts 表示本轮是否需要站点事实；refresh_data 可选 hourly_air/parcel，"
                "current_context.data_status.parcel_model_current 为 false，且本轮需要传播、气团寿命或沿途站点观测对照时，"
                "选择 refresh_data 中的 parcel 以更新旧版本数据。独立站点对照不代表因果验证或预测准确率。"
                "仅在本轮需要相应逐小时空气数据或传播寿命数据时填写，用于补齐旧会话证据。"
                "task_updates 只能包含用户本轮明确给出的 station_name/start_time/end_time，"
                "站点必须从 station_catalog 选，未给出的字段省略；没有匹配站点可填 null 以请求补充。"
                "结合 current_context/request_context 解释相对日期时段，绝不能自行更换用户未指定的参数。"
                "需要澄清模糊目标时 clarification 填一次性问题，并把 business_action 设为 none。"
                "不要生成业务事实、指标数值或坐标。"
            )},
            {"role": "user", "content": json.dumps({
                "question": question[:1000], "current_page": current_page,
                "previous_goal": previous_goal, "has_results": has_results,
                "recent_history": list(recent_history)[-4:],
                "pending_trip": dict(pending_trip) if pending_trip else None,
                "current_context": dict(current_context or {}),
                "request_context": dict(request_context or {}),
                "station_catalog": list(station_catalog),
                "local_date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            }, ensure_ascii=False)},
        ]
        properties = {
            "goal": {"type": "string", "enum": ["analysis", "explanation", "visualization",
                "governance_advice", "public_travel_advice", "health_protection", "report", "capability_intro", "unknown"]},
            "page": {"type": "string", "enum": ["overview", "weather", "restriction", "travel", "report", "none"]},
            "business_action": {"type": "string", "enum": ["run", "reuse", "none"]},
            "visualization_action": {"type": "string", "enum": ["auto", "none", "view", "reset"]},
            "requires_business_facts": {"type": "boolean"},
            "refresh_data": {"type": "array", "items": {"type": "string", "enum": ["hourly_air", "parcel"]}},
            "task_updates": {"type": "object", "properties": {
                key: {"type": ["string", "null"]} for key in ("station_name", "start_time", "end_time")},
                "additionalProperties": False},
            "clarification": {"type": "string"},
            "use_knowledge": {"type": "boolean"}, "use_recommendation": {"type": "boolean"},
            "show_route_map": {"type": "boolean"}, "show_propagation_map": {"type": "boolean"},
            "origin": {"type": "string"},
            "destination": {"type": "string"}, "trip_followup_role": {"type": "string"},
            "travel_profile": {"type": "string"}, "travel_identity": {"type": "string"},
        }
        actions = {
            "advise_travel": "基于天气、空气质量和用户身份给出出行建议，无需起终点。",
            "plan_travel_route": "规划实际出行路线，需要起终点；缺少时向用户询问。",
            "inspect_travel_routes": "解释或比较已有出行规划、路线、地图和案例证据。",
            "analyze_environment": "查询或分析所选站点时段的空气质量、天气、交通、污染来源或空间数据。",
            "explain_environment": "解释或比较已有数据，回答原因和指标含义，不自动追加建议。",
            "recommend_health_protection": "面向个人、儿童、长者或敏感人群，依据环境条件提供健康防护建议。",
            "recommend_governance": "面向管理部门提供污染控制、道路管控等治理建议。",
            "generate_environment_report": "将当前环境分析整理成报告或综合总结。",
            "manage_visualization": "创建、修改或恢复图表、地图和主视图，结合已有可视化上下文。",
            "retrieve_environment_knowledge": "检索研究、文献或专业知识，为独立知识问题寻找依据。",
            "introduce_assistant": "回答助手身份、真实配置的模型名称或平台功能。",
            "answer_environment_question": "处理问候、需要澄清的问题和其他不属于具体业务工具的问题。",
        }
        tool_goals = {
            "advise_travel": "public_travel_advice", "plan_travel_route": "public_travel_advice",
            "inspect_travel_routes": "public_travel_advice", "analyze_environment": "analysis",
            "explain_environment": "explanation", "recommend_health_protection": "health_protection",
            "recommend_governance": "governance_advice", "generate_environment_report": "report",
            "manage_visualization": "visualization", "retrieve_environment_knowledge": "explanation",
            "introduce_assistant": "capability_intro",
        }
        tools = [{"type": "function", "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": {
                **properties, **({"goal": {"type": "string", "enum": [tool_goals[name]]}}
                                  if name in tool_goals else {})},
                           "required": ["goal", "page", "business_action", "visualization_action",
                                        "requires_business_facts", "use_knowledge", "use_recommendation"],
                           "additionalProperties": False},
        }} for name, description in actions.items()]
        return self._small_json_plan(messages, max_tokens=650, tools=tools)

    def plan_visualization(self, *, question: str, catalog: list[dict[str, Any]],
                           previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._small_json_plan([
            {"role": "system", "content": (
                '根据用户语义和已有视图决定是否创建、修改或清除视图。只返回 JSON：'
                '{"action":"show|update|reset|none","metrics":["目录key"],"chart_type":"line|bar|scatter|radar|donut|composite"}。'
                '地图从目录选择 spatial 指标；只选真实可用目录项。拒绝出图时 action 为 none；恢复默认地图用 reset。'
                '追加、删除、修改必须结合 previous 返回修改后的完整指标集合，不得按词语命中机械分类。')},
            {"role": "user", "content": json.dumps({"question": question, "catalog": catalog,
                "previous": previous}, ensure_ascii=False)},
        ], max_tokens=300)

    def plan_page_charts(self, *, question: str,
                         catalog: list[dict[str, Any]]) -> dict[str, Any]:
        """Select page-specific metrics and chart types without supplying values."""
        messages = [
            {"role": "system", "content": (
                "你是环境与出行数据的自动图表规划器。只返回 JSON 对象，不要 Markdown。"
                '格式：{"focus":"travel或问题对应的语义目标","charts":[{"metric":"目录中的key","type":"目录中允许的图形","reason":"本图与问题的直接关系"}]}。'
                "最多两张图。用户无需明确要求画图：当图能帮助解释时间变化、路线比较、"
                "风险构成或空间影响时主动选择。纯定义、寒暄、证据不足或用户拒绝图表时返回空数组。"
                "默认零张，不要因为路线或时段数据存在就反复放同一张图。"
                "只选择直接回答本轮问题的图，每张说明 reason；普通解释不必画图。"
                "只能选目录中实际可用的指标与该指标允许的图形，不得生成数值或新指标。"
            )},
            {"role": "user", "content": json.dumps({
                "question": question[:1000], "catalog": catalog[:24],
            }, ensure_ascii=False)},
        ]
        value = self._small_json_plan(messages, max_tokens=400)
        value["_model_request"] = {
            "status": "success", "model": self._model, "provider_host": urlparse(self._endpoint).hostname,
            "request_id": uuid4().hex, "received_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        }
        logger.info("chart_model_response request_id=%s model=%s focus=%s metrics=%s",
                    value["_model_request"]["request_id"], self._model, value.get("focus"),
                    [item.get("metrics", item.get("metric")) for item in value.get("charts", []) if isinstance(item, Mapping)])
        return value

    def _small_json_plan(self, messages: list[dict[str, str]], *, max_tokens: int,
                         tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict[str, Any] = {
            "model": self._model, "messages": messages,
            "temperature": 0, "max_tokens": max_tokens, "stream": False,
        }
        if tools:
            payload.update(tools=tools, tool_choice="required", parallel_tool_calls=False)
        if urlparse(self._endpoint).hostname == "api.siliconflow.cn":
            payload["enable_thinking"] = False
        check_generation_active()
        response = self._http.post(self._endpoint, headers=headers, json=payload,
                                   timeout=min(self._timeout, 20.0))
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        if tools:
            calls = message.get("tool_calls") or []
            if len(calls) != 1:
                raise ValueError("turn planner must select exactly one tool")
            function = calls[0].get("function") or {}
            allowed = {tool["function"]["name"] for tool in tools}
            if function.get("name") not in allowed:
                raise ValueError("turn planner selected an unsupported tool")
            value = json.loads(function.get("arguments") or "{}")
            if not isinstance(value, dict):
                raise ValueError("turn tool arguments must be an object")
            selected = next(tool["function"] for tool in tools if tool["function"]["name"] == function["name"])
            goals = selected["parameters"]["properties"]["goal"].get("enum", [])
            if value.get("goal") not in goals:
                raise ValueError("model goal does not match selected tool")
            value["travel_action"] = {
                "advise_travel": "advice", "plan_travel_route": "plan_route",
                "inspect_travel_routes": "inspect_route",
            }.get(function["name"], "none")
            return value
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError("model planner returned non-text content")
        cleaned = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        value = json.loads(cleaned)
        if not isinstance(value, dict):
            raise ValueError("model planner returned a non-object")
        return value

    def plan_charts(self, *, question: str, catalog: list[dict[str, Any]],
                    previous: list[dict[str, Any]]) -> dict[str, Any]:
        """Ask for a bounded data plan, never executable chart code or values."""
        messages = [
            {"role": "system", "content": (
                "你是环境数据图表规划器。只返回 JSON 对象，不要 Markdown。格式："
                '{"focus":"本轮问题的语义目标","charts":[{"id":"new或已有图ID","title":"短标题",'
                '"type":"line|area|bar|scatter|pie|donut",'
                '"metrics":["目录中的key"],"time_filter":"all|morning|afternoon",'
                '"bucket_hours":1,"start_hour":0,"end_hour":24,"reason":"本图如何直接回答本轮问题"}]}。最多2张图。'
                "先识别本轮问题真正要回答什么，focus 仅允许 pollution_sources、pollutant_levels、weather、weather_risk、"
                "weather_pollution_relation、traffic、upwind_roads、propagation、travel、explicit_comparison、chart_edit、none。"
                "‘污染从哪来’及其各种问法属于 pollution_sources，只能选择 source.screening_scores；"
                "上风向道路匹配和气象风险即使有助分析，也不能代替来源比较图。没有来源数据就不画，不找邻近图凑数。"
                "问道路匹配用 upwind_roads，问气象风险用 weather_risk，问浓度水平用 pollutant_levels；"
                "只有用户明确同时比较多个领域时才能使用 explicit_comparison，不得为解释单一问题滥用它。"
                "每张图的指标必须符合 focus 及目录 meaning，不符合就返回空数组。"
                "start_hour/end_hour 为香港本地时的左闭右开范围；用户指定小时范围时填写。"
                "用户只会提出业务问题，不必提到‘图’或指定图形。你要主动判断图表是否能让答案更易理解："
                "涉及时间变化、峰值和异常时优先考虑时间序列；涉及两个逐时指标的关系时考虑散点图；"
                "涉及类别对比时考虑柱状图；涉及整体构成且确为比例时考虑饼图或环形图。"
                "例如‘污染什么时候最严重’可配污染物逐时图，‘风速和污染有关吗’可配散点图，"
                "‘哪些污染物偏高’可配污染物类别对比图。"
                "纯问候、术语定义、缺少相关数据或图表不能增加理解时返回空 charts。"
                "这是本轮唯一一次图表选择，目录已合并通用与页面指标，不会再有其他规划器补图。"
                "默认零张；只有能直接回答本轮问题并提供正文以外的信息，才选一张，确有不同视角才选第二张。"
                "不要因为某指标可用、属于当前页面或历史出现过就选择它。不要给每个问题都放气象风险、车型构成或污染物对比图。"
                "每张必须填写 reason，说明它与本轮问题的直接关系；说不清就不画。"
                "previous_charts 仅供用户明确修改、比较或指代旧图时参考，切换话题不得沿用旧图。"
                "可选指标有 allowed_types 时严格遵守；supports_time_transform 为 false 时不得筛选或聚合时段。"
                "用户明确不要图时 charts 必须为空。只能从目录选择指标，不得编造数据或计算值。"
                "散点图必须有两个逐时指标；饼图只能有一个类别指标。"
                "追问修改已有图时复用对应图 ID，未指定目标时修改最近一张。"
                "不可用或不合适的数据就不画。"
            )},
            {"role": "user", "content": json.dumps({
                "question": question, "catalog": catalog, "previous_charts": previous,
            }, ensure_ascii=False)},
        ]
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict[str, Any] = {
            "model": self._model, "messages": messages, "temperature": 0,
            "max_tokens": 1000, "stream": False,
        }
        if urlparse(self._endpoint).hostname == "api.siliconflow.cn":
            payload["enable_thinking"] = False
        check_generation_active()
        response = self._http.post(self._endpoint, headers=headers, json=payload,
                                   timeout=min(self._timeout, 25.0))
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("chart planner returned non-text content")
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:].removesuffix("```").strip()
        value = json.loads(content)
        if not isinstance(value, dict) or not isinstance(value.get("charts"), list):
            raise ValueError("chart planner returned an invalid plan")
        value["_model_request"] = {
            "status": "success", "model": self._model, "provider_host": urlparse(self._endpoint).hostname,
            "request_id": uuid4().hex, "received_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        }
        logger.info("chart_model_response request_id=%s model=%s focus=%s metrics=%s",
                    value["_model_request"]["request_id"], self._model, value.get("focus"),
                    [item.get("metrics", item.get("metric")) for item in value.get("charts", []) if isinstance(item, Mapping)])
        return value

    def answer(
        self,
        question: str,
        *,
        evidence: Mapping[str, Any],
        history: Sequence[Mapping[str, str]],
        user_goal: str,
        grounding_feedback: str = "",
    ) -> str:
        evidence_text = _bounded_evidence_json(evidence, self._max_context_chars)
        recommendation_mode = user_goal in {
            "public_travel_advice", "health_protection", "governance_advice"
        }
        recommendation_instruction = (
            "本轮的可执行建议会由结构化推荐层单独展示；你只需用一至两段通俗中文解释为什么这样建议。"
            "不要列建议清单，不要重复全部指标，不要扩展到其他受众或中长期规划。"
            if recommendation_mode else ""
        )
        if user_goal == "public_travel_advice":
            recommendation_instruction = (
                '请直接生成出行建议，只返回 JSON 对象：'
                '{"direct_advice":[{"title":"建议标题","detail":"行动及其天气依据"}],'
                '"explanation":"简短解释出行安排的依据"}。'
                "根据本次天气和空气质量证据选择相关建议，如雨具、衣物、防晒、户外活动和出发安排。"
                "必须按 task.travel_profile 中的角色身份和需求撰写，并简要说明采用的身份。"
                "用户未要求规划路线时直接给出天气出行建议，无需询问起终点，不得编造路线。"
                "建议内容和数量由本轮问题与证据决定，不套固定清单；事实和数值只能来自证据。"
                "可以使用一般出行常识解释行动；历史时段不能称作今天或未来预报。"
            )
        if user_goal == "health_protection":
            recommendation_instruction = (
                '直接根据本轮问题和已有环境证据撰写健康防护建议，只返回 JSON：'
                '{"direct_advice":[{"title":"行动标题","detail":"具体行动及其依据"}],'
                '"explanation":"为什么这样建议"}。'
                '按用户明确的身份、活动和敏感性提出建议，数量和详略遵守用户要求，不套固定清单。'
                '只用证据里的站点事实和数值，区分历史数据、一般防护常识与个体医疗诊断。'
            )
        if user_goal == "governance_advice":
            recommendation_instruction = (
                '本轮请直接生成城市治理建议，只返回 JSON 对象，格式为 '
                '{"direct_advice":[{"title":"措施标题","detail":"具体行动及其依据"}],'
                '"explanation":"治理优先级与措施选择的解释"}。'
                "措施和解释全部由你根据本次站点、时段及证据撰写，不使用固定措施清单。"
                "按本次污染、气象、交通、来源及空间影响证据确定措施优先级和适用对象，"
                "说明建议采取什么行动、为什么；数量与详略遵守用户要求。"
                "区分已观测事实与拟议措施，不虚构道路、排放源、敏感目标或治理成效。"
                "治理行动围绕已有来源筛查和道路信息安排排查，用‘来源筛查’准确标注判断性质。"
                "可以运用一般环境治理知识提出措施，但站点事实和数值只能来自证据。"
                "不要使用 Markdown 代码块，不要输出 JSON 之外的文字。"
            )
        messages: list[dict[str, str]] = [{
            "role": "system",
            "content": (
                f"你是本平台的{ASSISTANT_ROLE}，以清晰、专业、自然的方式帮助用户理解环境情况并形成行动建议。"
                "用户询问身份或模型时，可以依据 evidence_json.assistant 中的平台角色与模型名称直接回答，"
                "不要自称环境分析解释器，不要以业务范围为由拒绝回答身份问题，不得猜测未提供的模型版本。"
                "环境事实只能使用 evidence_json 中的数据；不得把交通压力说成真实流量，"
                "不得把来源评分说成贡献率，不得把模拟路径说成实测轨迹。"
                "禁止自行生成参考文献、文档编号、URL 或外部引用；文献引用只允许由后续受控知识层提供。"
                "回答先给出已有数据支持的直接结论，再说明相关原因，不要用笼统拒答或免责声明开头、结尾。"
                "不要使用‘证据不足’‘依据不足’‘证据有限’‘没有足够证据’这类概括性措辞，也不要复述这些禁用词。"
                "已提供来源评分、主判断、时序、路线或预测时，应围绕用户的问题直接使用它们，不得笼统否定已有结果。"
                f"{_DIRECT_ANSWER_STYLE}"
                "例如来源问题直接回答来源筛查结论及相关原因，不追加排放记录的缺测说明。"
                "不要以‘不能给出’‘无法提供’‘无法判断’‘不能排序’等能力否定句回答。"
                "用户询问最佳出发时间时，已有逐小时对比或推荐时段就直接说明优选时段和依据；"
                "仅有时段均值时，直接说明该分析窗口的情况和出行安排。"
                "没有数值或实测结果时不要编造；不能把来源可能性说成已确认的来源贡献率。"
                "回答优先简洁；用户要求多项页面数据或完整清单时，"
                "可以分组列出实际提供的字段，不要省略与问题直接相关的项目。"
                "必须优先遵守用户对句数、长度、通俗程度、主题范围和不罗列指标等表达要求；"
                "每次回答都应根据本轮问题识别用户关注的对象、比较结果或核心结论，"
                "用 Markdown **加粗** 突出这些重点，默认突出一至三处简短关键词或结论。"
                "重点必须与本轮问题直接相关，不要机械重复问题，不要整段加粗或加粗所有指标。"
                "解释类问题优先加粗因果或比较结论，建议类问题优先加粗应采取的具体行动；"
                "不要只加粗高、低等单字，也不要把通用免责声明当作回答重点。"
                "结构化 JSON 回答中，在 detail 和 explanation 的文字值内使用 **加粗**；"
                "title 保持纯文本，由界面统一强调。用户明确要求纯文本或不加粗时遵守用户要求。"
                "用户只问一个关系时，只解释该关系，不扩展出行建议、健康建议或治理方案。"
                "task 中的时间可能是历史时段；除非证据明确属于预测结果且用户询问预测，"
                "不得把历史结果称为当前状态、实时状态或未来预测。"
                "若 evidence_json.current_page 存在，优先按当前页面的推荐路线、时段和指标直接回答问题；"
                "不要把污染传播轨迹误认为可通行的出行路线。"
                "路线名称使用 display_name 等中文展示名，不输出 drive_0、transit_0、walk_0 等内部标识。"
                "传播页的设施数量必须使用 SpatialAgent.result.propagation_exposure；顶层 sensitive_poi_count 与 sensitive_poi_by_type 属于固定范围溯源治理，不能代替传播范围统计。"
                "以下是内部数据语义，用于准确选词，不作为每次回答的附加说明："
                "烟羽颜色对应所选污染物气团剩余比例，空间范围对应模型计算的模拟覆盖范围。"
                "station_validation 保存独立站点观测对照；引用时按各站 status 表述，"
                "保持观测对照与因果证明、预测准确率各自的含义，不虚构验证结果。"
                "用户主动问颜色含义、模拟原理或站点验证时，只围绕该问题解释。"
                "出行建议必须使用 current_page.travel_profile 对应的角色身份和规划结果，"
                "在回答中简要说明采用的身份，并结合该身份的步行耐受、时间弹性和环境敏感性解释建议；"
                "未声明身份时使用证据中的默认画像，不得自行换成其他角色。"
                f"{recommendation_instruction}"
                "所有阿拉伯数值必须逐字来自 evidence_json，不得自行计算、求和、平均、计数、换算、"
                "推导比例、预测未来数值或生成新的区间；需要分点时使用中文项目符号，不使用阿拉伯数字编号。"
            ),
        }]
        for item in history[-8:]:
            role, content = item.get("role"), item.get("content")
            if role in {"user", "assistant"} and isinstance(content, str):
                messages.append({"role": role, "content": content[:2000]})
        messages.append({
            "role": "user",
            "content": f"user_goal={user_goal}\nquestion={question}\n"
                       f"grounding_feedback={grounding_feedback}\nevidence_json={evidence_text}",
        })
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 1400,
            "stream": False,
        }
        # SiliconFlow's Qwen3 models otherwise enable a slower reasoning mode
        # whose non-streaming response may not contain the expected content.
        if urlparse(self._endpoint).hostname == "api.siliconflow.cn":
            payload["enable_thinking"] = False
        started = time.perf_counter()
        check_generation_active()
        response = self._http.post(
            self._endpoint,
            headers=headers,
            json=payload,
            timeout=self._timeout,
        )
        response.raise_for_status()
        value = response.json()
        content = value["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("LLM returned an empty answer")
        logger.info(
            "llm_http_success model=%s elapsed_ms=%d",
            self._model,
            round((time.perf_counter() - started) * 1000),
        )
        return content.strip()


class EvidenceAnswerer:
    """Use a model only with trusted evidence and reject invented citations."""

    def __init__(self, model: AnswerModel | None = None) -> None:
        self.model = model

    def answer(
        self,
        question: str,
        *,
        canonical: Mapping[str, Any] | None,
        history: Sequence[Mapping[str, str]],
        user_goal: str,
        page_evidence: Mapping[str, Any] | None = None,
        focus_page: str | None = None,
    ) -> str:
        canonical = canonical or {}
        agent_results = _page_agent_evidence(canonical, question, focus_page)
        evidence = {
            "assistant": {"role": ASSISTANT_ROLE, "model_name": getattr(self.model, "model_name", None)},
            "task": canonical.get("task") or {},
            "agent_results": agent_results,
            "warnings": canonical.get("warnings") or [],
        }
        if page_evidence:
            evidence["current_page"] = dict(page_evidence)
        if self.model is not None:
            started = time.perf_counter()
            try:
                native_model = isinstance(self.model, OpenAICompatibleAnswerModel)
                feedback = ""
                attempt = 0
                structured_advice = user_goal in {
                    "public_travel_advice", "health_protection", "governance_advice",
                }
                while True:
                    check_generation_active()
                    attempt += 1
                    kwargs = dict(evidence=evidence, history=history if attempt < 3 else (),
                                  user_goal=user_goal)
                    if native_model:
                        kwargs["grounding_feedback"] = feedback
                    try:
                        answer = self.model.answer(question, **kwargs)
                        check_generation_active()
                        if native_model:
                            answer = normalize_model_answer(answer, structured_advice=structured_advice)
                        elif user_goal == "governance_advice":
                            parse_governance_answer(answer)
                        grounding = self._check_model_answer(answer, evidence)
                        bad_style = _blanket_refusal(answer)
                        if grounding.safe and not bad_style:
                            return readable_route_names(answer)
                        reasons = list(grounding.rules)
                        if not grounding.safe:
                            logger.warning(
                                "llm_grounding_rejected model=%s rules=%s unsupported_numbers=%s",
                                getattr(self.model, "model_name", type(self.model).__name__),
                                ",".join(grounding.rules), ",".join(grounding.unsupported_numbers),
                            )
                        if bad_style:
                            reasons.append("answer_style")
                        numeric_instruction = (
                            "仅保留 evidence_json 中逐字出现的阿拉伯数值。"
                            if grounding.safe or attempt == 1 else
                            "用简短的定性描述回答，不输出任何阿拉伯数值或编号。"
                        )
                        feedback = (
                            "上一稿未满足回答要求，请重新回答同一个问题，直接输出完整合格的回答。"
                            f"违规类型：{','.join(reasons)}；"
                            f"无证据数值：{','.join(grounding.unsupported_numbers)}。"
                            f"{numeric_instruction}{_DIRECT_ANSWER_STYLE}"
                            "不输出参考文献或 URL，不虚构来源贡献率、实测轨迹或站点验证结果。"
                            "改写为本轮结论、理由和具体行动，不用补数据建议代替回答。"
                            "结构化建议遵守 direct_advice/explanation 结构，其他回答使用自然中文正文。"
                        )
                        if not native_model:
                            return ANSWER_STYLE_RETRY_FAILED if bad_style else ANSWER_VALIDATION_FAILED
                    except (ValueError, KeyError, TypeError, IndexError) as exc:
                        if not native_model:
                            raise
                        reasons = ["answer_format"]
                        feedback = (
                            "上一稿返回格式无效，请重新生成完整回答。"
                            "结构化建议只返回 JSON 对象，包含非空 direct_advice 列表及 explanation；"
                            "每条建议有 title 和 detail 字符串，不使用代码围栏。"
                            "普通解释只返回自然中文正文，不返回 JSON、字段名或代码块。"
                            "数值只能逐字来自 evidence_json。"
                            f"{_DIRECT_ANSWER_STYLE}"
                        )
                    logger.info("llm_answer_retry model=%s attempt=%d rules=%s",
                                getattr(self.model, "model_name", type(self.model).__name__),
                                attempt, ",".join(reasons))
                    wait_for_answer_retry(attempt)
            except AnswerGenerationCancelled:
                raise
            except Exception as exc:
                reason = (
                    "grounding_check_rejected"
                    if isinstance(exc, ValueError)
                    and str(exc) == "model answer failed evidence grounding checks"
                    else type(exc).__name__
                )
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                if isinstance(exc, requests.HTTPError):
                    status, provider_code, provider_message = _safe_http_error_details(exc)
                    logger.warning(
                        "llm_answer_fallback model=%s reason=HTTPError status=%s "
                        "provider_code=%s provider_message=%s elapsed_ms=%d",
                        getattr(self.model, "model_name", type(self.model).__name__),
                        status if status is not None else "unknown",
                        provider_code,
                        provider_message,
                        elapsed_ms,
                    )
                else:
                    logger.warning(
                        "llm_answer_fallback model=%s reason=%s elapsed_ms=%d",
                        getattr(self.model, "model_name", type(self.model).__name__),
                        reason,
                        elapsed_ms,
                    )
                if reason == "grounding_check_rejected":
                    return ANSWER_VALIDATION_FAILED
                if isinstance(exc, requests.Timeout):
                    return MODEL_TIMEOUT_ANSWER
                if isinstance(exc, requests.ConnectionError):
                    return MODEL_CONNECTION_FAILED
                if isinstance(exc, requests.HTTPError):
                    status = exc.response.status_code if exc.response is not None else None
                    if status in {401, 403}:
                        return MODEL_AUTH_FAILED
                    if status == 429:
                        return MODEL_RATE_LIMITED
                if isinstance(exc, (ValueError, KeyError, TypeError, IndexError)):
                    return MODEL_FORMAT_FAILED
        return MODEL_UNAVAILABLE_ANSWER

    @staticmethod
    def _model_answer_is_safe(answer: str, evidence: Mapping[str, Any]) -> bool:
        return EvidenceAnswerer._check_model_answer(answer, evidence).safe

    @staticmethod
    def _check_model_answer(
        answer: str, evidence: Mapping[str, Any]
    ) -> GroundingCheckResult:
        # Markdown emphasis must not hide a number or split a forbidden claim.
        answer = re.sub(r"\*\*|__|`", "", answer)
        rules: list[str] = []
        if _UNVERIFIED_CITATION.search(answer):
            rules.append("unverified_citation")
        if _has_forbidden_claim(answer):
            rules.append("forbidden_semantic_claim")
        evidence_text = json.dumps(evidence, ensure_ascii=False, default=str)
        evidence_numbers = {
            _normalized_number(item) for item in _NUMBER.findall(evidence_text)
        }
        unsupported: list[str] = []
        for token in _NUMBER.findall(answer):
            raw = _normalized_number(token)
            # Numeric transformations are deliberately not authorized in this
            # phase; any emitted Arabic number must occur in trusted evidence.
            if raw not in evidence_numbers and raw not in unsupported:
                unsupported.append(raw)
        if unsupported:
            rules.append("unsupported_numeric_value")
        return GroundingCheckResult(
            safe=not rules,
            rules=tuple(rules),
            unsupported_numbers=tuple(unsupported[:12]),
        )

    @staticmethod
    def _result(evidence: Mapping[str, Any], agent: str) -> Mapping[str, Any]:
        envelope = (evidence.get("agent_results") or {}).get(agent) or {}
        value = envelope.get("result") if isinstance(envelope, Mapping) else None
        return value if isinstance(value, Mapping) else {}

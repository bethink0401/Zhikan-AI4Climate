"""
AI 研判问答接口：POST /api/chat

把用户的自由提问交给大模型，结合当前分析链路的结构化上下文作答。
所有请求直接交给模型；LLM 不可达时返回 502，不启用关键词或本地规则应答。

模型配置复用 agents/coordinator.py 的同一套环境变量：
  COORDINATOR_API_KEY   SiliconFlow API Key
  COORDINATOR_BASE_URL  OpenAI 兼容接口地址（默认 https://api.siliconflow.cn/v1）
  COORDINATOR_MODEL     模型名（默认 Qwen/Qwen3-32B）

本文件刻意不引入 langchain：对话问答只是一次普通的 chat completions 调用，
用 requests 直连更轻，也不会把 coordinator 的重型依赖拖进 Flask 进程。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

# 复用 coordinator 的环境变量；未配置或模型不可用时明确报错
DEFAULT_API_KEY = os.getenv("COORDINATOR_API_KEY", "")  # 仅从环境变量读取，源码中不留任何凭证
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "Qwen/Qwen3-32B"

# 只带最近几轮历史，控制 token 用量
MAX_HISTORY_MESSAGES = 6
# (连接超时, 读取超时)：实测经代理到 SiliconFlow 单次请求延迟很高
# （1 个 token 也要 ~12 s），生成几百字会超过 1 分钟，读取超时给足余量
REQUEST_TIMEOUT = (15, 110)
MAX_ANSWER_TOKENS = 800

SYSTEM_PROMPT_TEMPLATE = """你是「智慧空气质量智能分析平台」的智能环境研判助手，负责解答关于当前分析任务、平台指标口径和污染治理的各类问题。
用户询问身份时，直接介绍你的平台角色；询问模型时，可以说明当前服务配置的模型名称为 {model_name}，不要猜测具体底层版本，不要以业务范围为由拒绝回答。

平台当前正在分析一个路边空气污染事件，下面是七 Agent 分析链路产出的结构化上下文（JSON）：

{context_json}

回答规则：
1. 只依据上述上下文里的数据回答，不要编造数字；上下文里没有的信息要明说"当前分析数据里没有"。
2. 用简体中文回答，简洁专业，默认不超过 200 字；用户要求展开分析时才写长。
3. 「污染来源可能性评分」是筛查评分（0~1，越高越可能是主要来源），不是贡献率，回答时注意口径。
4. 涉及治理建议时给出可操作的具体措施（分流引导、公交运力、健康防护提醒等）。
5. 用户问平台功能时，可介绍：综合态势、污染溯源与区域交通治理、恶劣天气与公里级传播预警、出行建议与路线推荐四个模块。
6. 每次回答都根据本轮问题，用 Markdown **加粗** 突出用户关注的对象、比较结果或核心结论，默认突出一至三处简短重点；不要机械重复问题、整段加粗或加粗所有指标。用户明确要求纯文本或不加粗时遵守用户要求。"""


def _build_messages(
    question: str, history: list[dict[str, Any]], context: dict[str, Any] | None
) -> list[dict[str, str]]:
    """组装 OpenAI 兼容接口的 messages：system(含上下文) + 截断后的历史 + 本次提问。"""
    context_json = json.dumps(context or {}, ensure_ascii=False)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.replace(
        "{model_name}", os.getenv("COORDINATOR_MODEL", DEFAULT_MODEL)
    ).replace("{context_json}", context_json)

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for item in history[-MAX_HISTORY_MESSAGES:]:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})
    return messages


def _call_llm(messages: list[dict[str, str]]) -> tuple[str, str]:
    """调用 OpenAI 兼容 chat completions，返回 (回答文本, 模型名)。失败抛异常。"""
    api_key = os.getenv("COORDINATOR_API_KEY", DEFAULT_API_KEY)
    base_url = os.getenv("COORDINATOR_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.getenv("COORDINATOR_MODEL", DEFAULT_MODEL)

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": MAX_ANSWER_TOKENS,
        "stream": False,
        # Qwen3 系列默认开启思考模式，非流式下会拖慢响应甚至报错，显式关掉
        "enable_thinking": False,
    }
    response = requests.post(
        f"{base_url}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    content = (data["choices"][0]["message"].get("content") or "").strip()
    if not content:
        raise ValueError("模型返回了空内容")
    return content, model


def create_blueprint() -> Blueprint:
    bp = Blueprint("chat_api", __name__)

    @bp.post("/chat")
    def chat():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        question = (body.get("question") or "").strip()
        if not question:
            return jsonify({"status": "error", "message": "question 不能为空"}), 400

        history = body.get("history") if isinstance(body.get("history"), list) else []
        context = body.get("context") if isinstance(body.get("context"), dict) else None

        try:
            answer, model = _call_llm(_build_messages(question, history, context))
        except requests.Timeout:
            logger.warning("chat: LLM 请求超时")
            return jsonify({"status": "error", "message": "AI 服务响应超时"}), 502
        except requests.RequestException as exc:
            logger.warning("chat: LLM 请求失败：%s", exc)
            return jsonify({"status": "error", "message": f"AI 服务不可达：{exc}"}), 502
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("chat: LLM 响应格式异常：%s", exc)
            return jsonify({"status": "error", "message": "AI 返回格式异常"}), 502

        return jsonify({"status": "ok", "answer": answer, "model": model})

    return bp

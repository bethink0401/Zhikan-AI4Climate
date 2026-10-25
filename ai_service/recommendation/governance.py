"""Validate model-authored governance copy without substituting canned advice."""

from __future__ import annotations

import json
import re
from typing import Any


GOVERNANCE_UNAVAILABLE = "城市治理建议暂未生成，请稍后重试。"


def _answer_json(answer: str) -> Any:
    text = answer.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    return json.loads(text)


def parse_governance_answer(answer: str) -> dict[str, Any]:
    value = _answer_json(answer)
    if not isinstance(value, dict):
        raise ValueError("governance answer must be an object")
    if "direct_advice" not in value and all(key in value for key in ("title", "detail", "explanation")):
        value = {"direct_advice": [{"title": value["title"], "detail": value["detail"]}],
                 "explanation": value["explanation"]}
    advice = value.get("direct_advice")
    explanation = value.get("explanation")
    if not isinstance(advice, list) or not advice:
        raise ValueError("governance answer requires advice")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError("governance answer requires an explanation")
    items = []
    for item in advice:
        if not isinstance(item, dict) or any(
            not isinstance(item.get(key), str) or not item[key].strip()
            for key in ("title", "detail")
        ):
            raise ValueError("governance advice requires a title and detail")
        items.append({"title": item["title"].strip(), "detail": item["detail"].strip()})
    return {"direct_advice": items, "explanation": explanation.strip()}


def readable_advice_answer(answer: str) -> str:
    """Render structured model copy when no recommendation card is requested."""
    try:
        generated = parse_governance_answer(answer)
    except (ValueError, TypeError):
        return answer
    sections = [f"**{item['title']}**\n\n{item['detail']}"
                for item in generated["direct_advice"]]
    sections.append(generated["explanation"])
    return "\n\n".join(sections)


def normalize_model_answer(answer: str, *, structured_advice: bool) -> str:
    """Accept known advice shapes and reject JSON that would leak into prose."""
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("empty_model_answer")
    text = answer.strip()
    looks_structured = text.startswith(("{", "[", "```"))
    if structured_advice or looks_structured:
        generated = parse_governance_answer(text)
        normalized = json.dumps(generated, ensure_ascii=False)
        return normalized if structured_advice else readable_advice_answer(normalized)
    return text

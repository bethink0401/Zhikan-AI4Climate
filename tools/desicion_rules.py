# tools/decision_rules.py

# 风险分数 → 建议等级 + 行动文案 的映射表
RISK_MAPPING = {
    "low": {
        "level": "low",
        "actions": ["建议维持常规空气质量监测，无需特殊管控。"]
    },
    "medium": {
        "level": "medium",
        "actions": ["建议发布健康提示，提醒敏感人群减少户外活动。"]
    },
    "high": {
        "level": "high",
        "actions": [
            "建议早高峰重点道路实施短时分流引导。",
            "建议增加公交运力以替代私家车出行。"
        ]
    },
    "very_high": {
        "level": "very_high",
        "actions": [
            "建议启动临时区域交通管制方案。",
            "建议高排放货车绕行避开上风向走廊。",
            "建议发布健康警报。"
        ]
    }
}

def get_decision_by_score(score):
    """根据风险分数返回对应的建议等级和行动"""
    if score >= 0.8:
        return RISK_MAPPING["very_high"]
    elif score >= 0.6:
        return RISK_MAPPING["high"]
    elif score >= 0.3:
        return RISK_MAPPING["medium"]
    else:
        return RISK_MAPPING["low"]
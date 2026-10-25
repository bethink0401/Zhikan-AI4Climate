"""Validate metric meaning against the model's declared question focus.

These rules inspect metric semantics, never keywords in the user's question.
"""
FOCI = frozenset({'pollution_sources', 'pollutant_levels', 'weather', 'weather_risk',
                  'weather_pollution_relation', 'traffic', 'upwind_roads', 'propagation',
                  'travel', 'explicit_comparison', 'chart_edit', 'none'})


def metric_meaning(key: str) -> str:
    if key == 'source.screening_scores':
        return '污染来源可能性筛查评分，直接比较交通源、局地积聚、区域传输等；不是贡献率。'
    if key == 'restriction.upwind_roads.match_score':
        return '道路位于上风向的空间匹配评分；不能直接回答污染来自哪个源，也不是道路排放量。'
    if key == 'weather.risk_factors':
        return '扩散不利气象条件评分；只能说明天气风险，不能直接判定污染来源。'
    if key.startswith('weather.propagation.'):
        return '模拟传播的寿命或覆盖目标，不是污染来源或实测传播。'
    return {'air_quality': '空气污染水平或变化', 'meteorology': '气象观测或变化',
            'traffic': '交通结构或压力', 'travel': '出行方案或出发时段对比'}.get(key.split('.')[0], key)


def relevant_metrics(focus: str, keys: list[str]) -> bool:
    if not keys or focus not in FOCI or focus == 'none':
        return False
    if focus in {'explicit_comparison', 'chart_edit'}:
        return True
    if focus == 'pollution_sources':
        return keys == ['source.screening_scores']
    if focus == 'weather_risk':
        return keys == ['weather.risk_factors']
    if focus == 'upwind_roads':
        return keys == ['restriction.upwind_roads.match_score']
    if focus == 'weather_pollution_relation':
        return (all(k.startswith(('air_quality.hourly.', 'meteorology.hourly.')) for k in keys)
                and any(k.startswith('air_quality.hourly.') for k in keys)
                and any(k.startswith('meteorology.hourly.') for k in keys))
    prefix = {'pollutant_levels': 'air_quality.', 'weather': 'meteorology.',
              'traffic': 'traffic.', 'propagation': 'weather.propagation.', 'travel': 'travel.'}[focus]
    return all(key.startswith(prefix) for key in keys)

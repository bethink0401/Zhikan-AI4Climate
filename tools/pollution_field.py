# -*- coding: utf-8 -*-
"""污染暴露场：把污染事件换算成出行路线上可比较的暴露指数。

模型
----
污染源有两类，统一按**点源集合**参与高斯烟羽扩散：

1. **监测站源**：异常监测站，强度取 AirQualityAgent 的 anomaly_score。
2. **交通走廊源**：把受监测交通走廊按等间隔采样成一组弱源，强度取交通压力分。
   走廊不是点，所以用沿线采样点近似线源；密集采样后叠加，自然形成
   "贴着走廊走暴露高、绕到平行街道暴露低"的横向梯度。

风速低于阈值时视为静风：不具备定向输送，改为各向同性积聚。

口径声明
--------
产出的是 **0-1 相对暴露指数**，用于路线之间的相对比较与"避开污染"的路径选择。
它不是浓度预测：没有化学反应、地形效应、垂直分层与真实源强的量纲。
所有证据与建议都必须以"相对指数""筛查级"表述，不得写成浓度值。
"""

from __future__ import annotations

import json
import math
import zlib
from pathlib import Path
from typing import Any, Iterable

from spatial_agent.tools.spatial_tools import bearing_deg, haversine_km

DEFAULT_RULES = Path(__file__).resolve().parents[1] / "config" / "pollution_rules.json"

EARTH_RADIUS_KM = 6371.0088

_RULES_CACHE: dict[str, dict[str, Any]] = {}
_GRID_CACHE: dict[str, dict[str, Any]] = {}

#: 网格缓存的容量上限。蒙特卡洛稳健性评估每抽样一次就换一组风速风向，
#: 每次都产生一个不同的签名；不设上限的话缓存会随请求数无界增长。
_GRID_CACHE_LIMIT = 8

#: 规则网格的节点数上限。起终点或污染源拉得很开时，外接框会覆盖很大范围，
#: 固定 0.1 km 格距会让节点数爆炸（全港范围约 40 万点），而蒙特卡洛稳健性
#: 评估每次换风都要重建一次网格。超过上限就按面积比放粗格距——插值取值
#: 对格距不敏感，粗到 0.2–0.3 km 也不影响路线间的相对比较。
_GRID_NODE_LIMIT = 40000


def load_pollution_rules(path: str | Path = DEFAULT_RULES) -> dict[str, Any]:
    """Load the pollution field configuration."""
    key = str(Path(path).resolve())
    if key not in _RULES_CACHE:
        with Path(path).open(encoding="utf-8") as handle:
            _RULES_CACHE[key] = json.load(handle)
    return _RULES_CACHE[key]


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


class PollutionField:
    """A screening-level relative exposure field for one wind situation."""

    def __init__(
        self,
        sources: Iterable[dict[str, Any]],
        *,
        wind_from_direction: float | None,
        wind_speed_ms: float | None,
        focus_points: Iterable[tuple[float, float]] = (),
        rules: dict[str, Any] | None = None,
    ):
        self.rules = rules or load_pollution_rules()
        self.sources = [
            {
                "name": str(source.get("name") or "未命名源"),
                "lon": float(source["lon"]),
                "lat": float(source["lat"]),
                "strength": clamp(float(source.get("strength", 0.7))),
                "kind": str(source.get("kind") or "point"),
            }
            for source in sources
            if source.get("lon") is not None and source.get("lat") is not None
        ]
        self.wind_from = (
            float(wind_from_direction) % 360.0 if wind_from_direction is not None else None
        )
        self.wind_speed = float(wind_speed_ms) if wind_speed_ms is not None else None
        self.wind_missing = self.wind_speed is None

        calm_rules = self.rules["calm"]
        self.calm_threshold = float(calm_rules["threshold_ms"])
        self.calm_transition = float(calm_rules["transition_ms"])
        # 静风积聚的结构性模型（V7）：双尺度。
        #   局部项：R0 高斯，保持近源陡梯度（走廊判别、绕行收益依赖它）；
        #   背景项：扩散展宽半径 R_eff = √(R0² + 2·K·t)，K（m²/s）与代表时长
        #   （小时）来自配置，由物理量纲导出；幅度按质量守恒缩放
        #   scale × (R0/R_eff)²——展宽不新增质量。旧版单尺度 0.35 km 把静风
        #   积聚压得过小，与扩散参考场差近一个量级。
        base_radius = float(calm_rules["accumulation_radius_km"])
        background = calm_rules.get("diffusion_background") or {}
        k_m2s = float(background.get("k_m2s", 0.0))
        reference_hours = float(background.get("hours", 0.0))
        self.calm_radius_effective_km = math.sqrt(
            base_radius ** 2 + 2.0 * (k_m2s * 1e-6) * (reference_hours * 3600.0))
        self.calm_local_radius_km = base_radius
        self.calm_background_scale = float(calm_rules["accumulation_scale"]) * (
            base_radius / self.calm_radius_effective_km) ** 2
        # V9.2 消融开关：两顶的独立缩放（默认 1.0 = 完整双尺度，行为不变）。
        # local_term_scale=0 → 仅背景扩散；background_term_scale=0 → 仅局部项。
        self.calm_local_term_scale = float(calm_rules.get("local_term_scale", 1.0))
        self.calm_background_term_scale = float(
            calm_rules.get("background_term_scale", 1.0))
        # 污染物实际输送方向 = 来风方向 + 180°
        self.transport_bearing = (
            None if self.wind_from is None else (self.wind_from + 180.0) % 360.0
        )
        self.calm_weight = self._calm_weight()
        # 对外语义：只有已经完全落到各向同性分支时才算"静风"。
        self.is_calm = self.calm_weight >= 0.999

        self._focus = [tuple(point) for point in focus_points]
        self._grid: dict[str, Any] | None = None

    def _calm_weight(self) -> float:
        """各向同性积聚分支的权重，1=完全积聚，0=完全定向烟羽。

        这是一个**平滑过渡**，不是开关。早先的实现用 ``wind < 阈值`` 做硬判断，
        导致风速从 0.49 升到 0.51 m/s（差 0.02，远小于任何风速测量误差）时，
        行程区暴露中位数从 0.636 掉到 0.090，差 7 倍。那是不连续，不是物理。

        没有风矢量（缺风速或风向）时无法做定向输送，退回纯集聚分支。
        """
        if self.wind_speed is None or self.wind_from is None:
            return 1.0
        if self.wind_speed <= self.calm_threshold:
            return 1.0
        if self.wind_speed >= self.calm_transition:
            return 0.0
        span = self.calm_transition - self.calm_threshold
        return (self.calm_transition - self.wind_speed) / span

    # ── 单点计算 ────────────────────────────────────────────────────────
    #
    # 场值 = calm_weight × Σ各向同性积聚项 + (1 − calm_weight) × Σ定向烟羽项
    #
    # 两个分支各有自己的标定系数（plume_scale / accumulation_scale），因为两者的
    # 求和结构完全不同：定向烟羽的横向高斯会把偏离风向的源几乎全部丢弃，只有少量
    # 源有效；各向同性分支把半径内的源全部计入。共用同一个系数会让静风分支的求和
    # 比有风分支大数倍，直接顶到 0-1 上限而失去区分度——实测正是如此。

    def _calm_term(self, source: dict[str, Any], distance: float) -> float:
        """各向同性积聚项：局部陡梯度高斯 + 扩散展宽背景（双尺度）。"""
        strength_scale = source["strength"] * float(
            self.rules["calm"]["accumulation_scale"])
        local = (strength_scale * self.calm_local_term_scale * math.exp(
            -(distance ** 2) / (2.0 * self.calm_local_radius_km ** 2)))
        background = (source["strength"] * self.calm_background_scale
                      * self.calm_background_term_scale * math.exp(
            -(distance ** 2) / (2.0 * self.calm_radius_effective_km ** 2)))
        return local + background

    def _plume_term(
        self, source: dict[str, Any], distance: float, lon: float, lat: float
    ) -> float:
        """定向高斯烟羽项。没有风向时返回 0。"""
        if self.transport_bearing is None:
            return 0.0

        plume = self.rules["plume"]
        scale = float(plume.get("plume_scale", 1.0))

        direction = bearing_deg(source["lon"], source["lat"], lon, lat)
        relative = (direction - self.transport_bearing + 180.0) % 360.0 - 180.0
        downwind_km = distance * math.cos(math.radians(relative))
        crosswind_km = distance * math.sin(math.radians(relative))

        if downwind_km < 0.0:
            # 上风向：只保留短距离回流，量级很小。
            fraction = float(plume["upwind_back_diffusion_fraction"])
            upwind_scale = float(plume["upwind_scale_km"])
            return (
                source["strength"]
                * scale
                * fraction
                * math.exp(-(abs(downwind_km) + abs(crosswind_km)) / upwind_scale)
            )

        crosswind = plume["crosswind_spread"]
        vertical = plume["vertical_spread"]
        sigma_y = float(crosswind["initial_km"]) + float(crosswind["coefficient"]) * (
            downwind_km ** float(crosswind["exponent"])
        )
        sigma_z = float(vertical["initial_km"]) + float(vertical["coefficient"]) * (
            downwind_km ** float(vertical["exponent"])
        )

        lateral = math.exp(-(crosswind_km ** 2) / (2.0 * sigma_y ** 2))
        # 扩散体积随 sigma 增长，浓度被稀释。
        dilution = (float(crosswind["initial_km"]) * float(vertical["initial_km"])) / (
            sigma_y * sigma_z
        )

        wind = max(self.wind_speed or 0.0, self.calm_threshold)
        travel_hours = downwind_km / (wind * 3.6)
        decay = 0.5 ** (travel_hours / float(plume["decay_half_life_hours"]))

        boost_rules = plume["stability_boost"]
        boost = clamp(
            float(plume["reference_wind_speed_ms"]) / wind,
            float(boost_rules["min"]),
            float(boost_rules["max"]),
        )

        return source["strength"] * scale * lateral * dilution * decay * boost

    def _source_index(self, source: dict[str, Any], lon: float, lat: float) -> float:
        """单个源在某点贡献的场值（两个分支已按权重混合）。"""
        distance = haversine_km(source["lon"], source["lat"], lon, lat)
        total = self.calm_weight * self._calm_term(source, distance)
        if self.calm_weight < 0.999:
            total += (1.0 - self.calm_weight) * self._plume_term(
                source, distance, lon, lat
            )
        return total

    def _plume_at(self, lon: float, lat: float) -> float:
        """解析式求某点的场值（不走网格，供校验与回退使用）。"""
        total = 0.0
        for source in self.sources:
            total += self._source_index(source, lon, lat)
        return clamp(total)

    # ── 规则网格（带缓存）────────────────────────────────────────────────

    def _grid_signature(self) -> str:
        """A stable key so repeated runs with the same inputs reuse the grid."""
        # 规则指纹必须入键：同风场不同烟羽参数的场，网格值完全不同，
        # 共用缓存会让"网格路径"与"解析路径"分叉（契约测试已捕获过）。
        rules_fingerprint = zlib.crc32(
            json.dumps(self.rules, sort_keys=True, default=str).encode("utf-8")
        )
        parts = [
            f"rules{rules_fingerprint}",
            f"{self.wind_from:.2f}" if self.wind_from is not None else "none",
            f"{self.wind_speed:.2f}" if self.wind_speed is not None else "none",
            f"{len(self.sources)}",
        ]
        for source in self.sources:
            parts.append(f"{source['lon']:.5f},{source['lat']:.5f},{source['strength']:.3f}")
        for point in self._focus:
            parts.append(f"{point[0]:.5f},{point[1]:.5f}")
        return "|".join(parts)

    def _build_grid(self) -> None:
        """Pre-compute the field on a regular grid so route queries stay cheap."""
        if not self.sources:
            self._grid = None
            return

        signature = self._grid_signature()
        cached = _GRID_CACHE.get(signature)
        if cached is not None:
            self._grid = cached
            return

        grid_rules = self.rules["grid"]
        margin = float(grid_rules["margin_km"])
        longitudes = [source["lon"] for source in self.sources] + [p[0] for p in self._focus]
        latitudes = [source["lat"] for source in self.sources] + [p[1] for p in self._focus]

        min_lon, max_lon = min(longitudes), max(longitudes)
        min_lat, max_lat = min(latitudes), max(latitudes)
        mean_lat = (min_lat + max_lat) / 2.0
        lon_scale = 111.32 * max(math.cos(math.radians(mean_lat)), 0.1)
        lon_margin = margin / lon_scale
        lat_margin = margin / 110.57

        min_lon -= lon_margin
        max_lon += lon_margin
        min_lat -= lat_margin
        max_lat += lat_margin

        cell_km = float(grid_rules["cell_km"])
        step_lat = cell_km / 110.57
        step_lon = cell_km / lon_scale

        n_lon = max(2, int((max_lon - min_lon) / step_lon) + 1)
        n_lat = max(2, int((max_lat - min_lat) / step_lat) + 1)

        # 节点数超上限时按 sqrt(面积比) 放粗格距，把重建成本约束在常数级
        overflow = (n_lon * n_lat) / _GRID_NODE_LIMIT
        if overflow > 1.0:
            shrink = math.sqrt(overflow)
            step_lon *= shrink
            step_lat *= shrink
            n_lon = max(2, int((max_lon - min_lon) / step_lon) + 1)
            n_lat = max(2, int((max_lat - min_lat) / step_lat) + 1)

        values = self._evaluate_grid(min_lon, min_lat, step_lon, step_lat, n_lon, n_lat)

        grid = {
            "min_lon": min_lon,
            "min_lat": min_lat,
            "step_lon": step_lon,
            "step_lat": step_lat,
            "n_lon": n_lon,
            "n_lat": n_lat,
            "values": values,
        }
        _GRID_CACHE[signature] = grid
        while len(_GRID_CACHE) > _GRID_CACHE_LIMIT:
            _GRID_CACHE.pop(next(iter(_GRID_CACHE)))
        self._grid = grid

    def _evaluate_grid(
        self,
        min_lon: float,
        min_lat: float,
        step_lon: float,
        step_lat: float,
        n_lon: int,
        n_lat: int,
    ) -> Any:
        """Evaluate the field on every grid node.

        两个分支各自累加后按 ``calm_weight`` 混合，与 ``_source_index`` 逐项对应；
        两条路径必须给出同一个场值，否则网格（正常路径）与解析式（回退路径）会分叉。

        This is the hot loop of the whole module: a few thousand grid nodes times
        a few hundred sources. numpy does it in well under a second, where the
        equivalent pure-Python loop takes double-digit seconds.
        """
        try:
            import numpy as np  # noqa: PLC0415 - 可选加速路径
        except ImportError:  # pragma: no cover - 无 numpy 时退回纯 Python
            return [
                [
                    self._plume_at(min_lon + column * step_lon, min_lat + row * step_lat)
                    for column in range(n_lon)
                ]
                for row in range(n_lat)
            ]

        lons = min_lon + np.arange(n_lon) * step_lon
        lats = min_lat + np.arange(n_lat) * step_lat
        grid_lon, grid_lat = np.meshgrid(lons, lats)
        phi2 = np.radians(grid_lat)
        calm_total = np.zeros((n_lat, n_lon), dtype=float)
        plume_total = np.zeros((n_lat, n_lon), dtype=float)

        calm_rules = self.rules["calm"]
        calm_radius_local = self.calm_local_radius_km
        calm_radius_background = self.calm_radius_effective_km
        calm_scale = float(calm_rules["accumulation_scale"])
        calm_background_scale = self.calm_background_scale

        plume = self.rules["plume"]
        crosswind = plume["crosswind_spread"]
        vertical = plume["vertical_spread"]
        sigma_y0 = float(crosswind["initial_km"])
        sigma_z0 = float(vertical["initial_km"])
        plume_scale = float(plume.get("plume_scale", 1.0))
        wind = max(self.wind_speed or 0.0, self.calm_threshold)
        boost_rules = plume["stability_boost"]
        boost = clamp(
            float(plume["reference_wind_speed_ms"]) / wind,
            float(boost_rules["min"]),
            float(boost_rules["max"]),
        )
        decay_per_km = math.log(0.5) / (
            wind * 3.6 * float(plume["decay_half_life_hours"])
        )
        back_fraction = float(plume["upwind_back_diffusion_fraction"])
        upwind_scale = float(plume["upwind_scale_km"])

        need_calm = self.calm_weight > 0.001
        need_plume = self.calm_weight < 0.999 and self.transport_bearing is not None

        for source in self.sources:
            phi1 = math.radians(source["lat"])
            dphi = np.radians(grid_lat - source["lat"])
            dlambda = np.radians(grid_lon - source["lon"])
            h = (
                np.sin(dphi / 2.0) ** 2
                + math.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2.0) ** 2
            )
            distance = 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0)))

            if need_calm:
                calm_total += (
                    source["strength"]
                    * calm_scale * self.calm_local_term_scale
                    * np.exp(-(distance ** 2) / (2.0 * calm_radius_local ** 2))
                    + source["strength"]
                    * calm_background_scale * self.calm_background_term_scale
                    * np.exp(-(distance ** 2) / (2.0 * calm_radius_background ** 2))
                )

            if not need_plume:
                continue

            y = np.sin(dlambda) * np.cos(phi2)
            x = (
                math.cos(phi1) * np.sin(phi2)
                - math.sin(phi1) * np.cos(phi2) * np.cos(dlambda)
            )
            bearing = (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0
            relative = np.radians(
                (bearing - self.transport_bearing + 180.0) % 360.0 - 180.0
            )
            downwind = distance * np.cos(relative)
            crosswind_offset = distance * np.sin(relative)

            safe_downwind = np.maximum(downwind, 0.0)
            sigma_y = sigma_y0 + float(crosswind["coefficient"]) * (
                safe_downwind ** float(crosswind["exponent"])
            )
            sigma_z = sigma_z0 + float(vertical["coefficient"]) * (
                safe_downwind ** float(vertical["exponent"])
            )
            lateral = np.exp(-(crosswind_offset ** 2) / (2.0 * sigma_y ** 2))
            dilution = (sigma_y0 * sigma_z0) / (sigma_y * sigma_z)
            decay = np.exp(decay_per_km * safe_downwind)

            downwind_plume = (
                source["strength"] * plume_scale * lateral * dilution * decay * boost
            )
            back_diffusion = (
                source["strength"]
                * plume_scale
                * back_fraction
                * np.exp(
                    -(np.abs(downwind) + np.abs(crosswind_offset)) / upwind_scale
                )
            )
            plume_total += np.where(downwind >= 0.0, downwind_plume, back_diffusion)

        total = self.calm_weight * calm_total + (1.0 - self.calm_weight) * plume_total
        return np.clip(total, 0.0, 1.0)

    def _sample_grid(self, lon: float, lat: float) -> float:
        """Bilinear interpolation of the pre-computed grid."""
        grid = self._grid
        if grid is None:
            return self._plume_at(lon, lat)

        x = (lon - grid["min_lon"]) / grid["step_lon"]
        y = (lat - grid["min_lat"]) / grid["step_lat"]
        if x < 0.0 or y < 0.0 or x > grid["n_lon"] - 1 or y > grid["n_lat"] - 1:
            return 0.0

        x0, y0 = int(x), int(y)
        x1, y1 = min(x0 + 1, grid["n_lon"] - 1), min(y0 + 1, grid["n_lat"] - 1)
        fx, fy = x - x0, y - y0
        values = grid["values"]
        bottom = values[y0][x0] * (1 - fx) + values[y0][x1] * fx
        top = values[y1][x0] * (1 - fx) + values[y1][x1] * fx
        return bottom * (1 - fy) + top * fy

    # ── 对外接口 ────────────────────────────────────────────────────────

    def index_at(self, lon: float, lat: float) -> float:
        """暴露指数（0-1）。"""
        if self._grid is None:
            self._build_grid()
        return round(clamp(self._sample_grid(lon, lat)), 4)

    def index_for_edges(self, edges: list[dict[str, Any]]) -> list[float]:
        """每条路段的暴露指数，取路段中点处的值。"""
        if self._grid is None:
            self._build_grid()
        return [
            self.index_at(edge["mid_lon"], edge["mid_lat"]) for edge in edges
        ]

    def sample_points(self, *, min_index: float | None = None) -> list[dict[str, Any]]:
        """Sample the field on the grid for map rendering."""
        grid = self._grid
        if grid is None:
            return []
        grid_rules = self.rules["grid"]
        threshold = (
            float(min_index)
            if min_index is not None
            else float(grid_rules["min_index"])
        )
        stride = max(1, int(grid_rules.get("render_stride", 1)))

        samples: list[dict[str, Any]] = []
        for row in range(0, grid["n_lat"], stride):
            lat = grid["min_lat"] + row * grid["step_lat"]
            for column in range(0, grid["n_lon"], stride):
                value = grid["values"][row][column]
                if value >= threshold:
                    samples.append(
                        {
                            "lon": round(grid["min_lon"] + column * grid["step_lon"], 6),
                            "lat": round(lat, 6),
                            "index": round(value, 4),
                        }
                    )
        return samples

    def peak_index(self) -> dict[str, Any] | None:
        """Highest exposure location on the grid."""
        grid = self._grid
        if grid is None or not self.sources:
            return None
        values = grid["values"]
        try:
            import numpy as np  # noqa: PLC0415 - 可选加速路径

            best_row, best_column = divmod(int(np.argmax(values)), grid["n_lon"])
            best_value = float(values[best_row][best_column])
        except ImportError:  # pragma: no cover - 无 numpy 时退回纯 Python
            best_row = best_column = 0
            best_value = -1.0
            for row, row_values in enumerate(values):
                for column, value in enumerate(row_values):
                    if float(value) > best_value:
                        best_value, best_row, best_column = float(value), row, column
        return {
            "lon": round(grid["min_lon"] + best_column * grid["step_lon"], 6),
            "lat": round(grid["min_lat"] + best_row * grid["step_lat"], 6),
            "index": round(best_value, 4),
        }

    def summary(self) -> dict[str, Any]:
        """Field-level metadata for the Agent output and the frontend."""
        if self._grid is None:
            self._build_grid()
        sources = self.sources
        return {
            "source_count": len(sources),
            "station_sources": [
                {
                    "name": source["name"],
                    "lon": round(source["lon"], 6),
                    "lat": round(source["lat"], 6),
                    "strength": round(source["strength"], 4),
                }
                for source in sources
                if source["kind"] == "station"
            ],
            "corridor_source_count": sum(1 for s in sources if s["kind"] == "corridor"),
            "corridor_source_strength": round(
                next((s["strength"] for s in sources if s["kind"] == "corridor"), 0.0), 4
            ),
            "wind_from_direction": self.wind_from,
            "transport_bearing": None
            if self.transport_bearing is None
            else round(self.transport_bearing, 1),
            "wind_speed_ms": self.wind_speed,
            "calm": self.is_calm,
            "calm_weight": round(self.calm_weight, 3),
            "mixed_regime": 0.001 < self.calm_weight < 0.999,
            "wind_missing": self.wind_missing,
            "peak": self.peak_index(),
            "index_meaning": "0-1 相对暴露指数，用于路线相对比较，不是浓度值",
            "assumptions": self.assumptions(),
        }

    def assumptions(self) -> list[str]:
        items = [
            "暴露指数为筛查级相对值，不含化学反应、地形效应与垂直分层",
            "烟羽按高斯形式沿主导风向下风向扩散，参数取自 config/pollution_rules.json",
            "交通走廊按沿线等间隔采样点近似线源，采样间隔见配置",
        ]
        if self.wind_missing:
            items.append("缺少风速，按各向同性积聚处理；此时无法判断定向输送")
        elif self.wind_from is None:
            items.append("缺少风向，按各向同性积聚处理；此时无法判断定向输送")
        elif self.is_calm:
            items.append(
                f"风速 {self.wind_speed:.2f} m/s 低于 {self.calm_threshold} m/s，"
                f"按各向同性积聚处理，不做定向输送"
            )
        elif 0.001 < self.calm_weight < 0.999:
            items.append(
                f"风速 {self.wind_speed:.2f} m/s 处于 {self.calm_threshold}–"
                f"{self.calm_transition} m/s 过渡区，场值由各向同性积聚项"
                f"（权重 {self.calm_weight:.2f}）与定向烟羽项加权合成"
            )
        else:
            items.append(
                f"输送方向取来风方向 {self.wind_from}° 的相反方向"
                f"（去向 {self.transport_bearing:.0f}°）"
            )
        return items


def sample_corridor_sources(
    edges: list[dict[str, Any]], config: dict[str, Any]
) -> tuple[list[dict[str, Any]], float]:
    """Thin the road network into evenly spaced corridor source points.

    Any segment with a monitoring station association counts as part of the
    emitting corridor, then the corridor is walked by cumulative length so the
    sample spacing is uniform regardless of how the source data is segmented.

    Returns the sampled points and the spacing actually used, in kilometres. The
    caller must scale each point's strength by that spacing: the discrete sum
    only approximates the underlying line-source integral when each weight is
    proportional to the arc length it represents. Without that, the field value
    would depend on how finely the corridor happens to be sampled.
    """
    strength_rules = config["source_strength"]
    target_interval = max(float(strength_rules["corridor_sample_km"]), 0.05)
    limit = max(int(strength_rules["corridor_max_points"]), 1)

    monitored = [edge for edge in edges if edge.get("near_stations")]
    if not monitored:
        return [], target_interval

    monitored.sort(key=lambda edge: (edge["road_id"], edge["mid_lon"]))
    total_km = sum(float(edge["length_km"]) for edge in monitored)
    # 走廊总长超过采样上限时按里程均匀放稀，覆盖范围不因排序而偏斜。
    interval = max(target_interval, total_km / limit)

    points: list[dict[str, Any]] = []
    accumulated = interval
    for edge in monitored:
        accumulated += float(edge["length_km"])
        if accumulated < interval:
            continue
        accumulated = 0.0
        label = edge.get("road_name_zh") or edge.get("road_name") or "受监测道路"
        points.append(
            {
                "name": f"走廊源 {label}",
                "lon": round(edge["mid_lon"], 6),
                "lat": round(edge["mid_lat"], 6),
            }
        )
        if len(points) >= limit:
            break
    return points, interval


def build_field_from_sources(
    *,
    stations: list[dict[str, Any]] | None,
    station_strength: float | None,
    corridor_edges: list[dict[str, Any]],
    corridor_strength: float | None,
    wind_from_direction: float | None,
    wind_speed_ms: float | None,
    focus_points: Iterable[tuple[float, float]] = (),
    rules: dict[str, Any] | None = None,
) -> PollutionField:
    """Assemble station sources and corridor sources into one exposure field."""
    config = rules or load_pollution_rules()
    strength_rules = config["source_strength"]

    sources: list[dict[str, Any]] = []
    for index, station in enumerate(stations or []):
        if station.get("lon") is None or station.get("lat") is None:
            continue
        sources.append(
            {
                "name": str(
                    station.get("name")
                    or station.get("station_name")
                    or station.get("station_id")
                    or f"监测站{index + 1}"
                ),
                "lon": float(station["lon"]),
                "lat": float(station["lat"]),
                "strength": clamp(
                    float(
                        station.get("strength")
                        if station.get("strength") is not None
                        else (
                            station_strength
                            if station_strength is not None
                            else strength_rules["default_station_strength"]
                        )
                    )
                ),
                "kind": "station",
            }
        )

    points, interval = sample_corridor_sources(corridor_edges, config)
    # 单点强度 = 每公里排放系数 × 该点代表的走廊长度 × 交通压力归一强度
    per_point_strength = (
        float(strength_rules["corridor_emission_per_km"])
        * interval
        * clamp(
            float(
                corridor_strength
                if corridor_strength is not None
                else strength_rules["default_corridor_strength"]
            )
        )
    )
    for point in points:
        sources.append(
            {
                "name": point["name"],
                "lon": point["lon"],
                "lat": point["lat"],
                "strength": per_point_strength,
                "kind": "corridor",
            }
        )

    return PollutionField(
        sources,
        wind_from_direction=wind_from_direction,
        wind_speed_ms=wind_speed_ms,
        focus_points=focus_points,
        rules=config,
    )


def avoidance_multipliers(
    edge_exposure: list[float], rules: dict[str, Any] | None = None
) -> list[float]:
    """Cost multipliers that bias a route search away from polluted segments.

    The curve is super-linear (exponent > 1), so a route strongly avoids the
    worst stretches while tolerating mildly exposed ones — which matches the
    everyday meaning of "avoid the pollution" better than a linear penalty.
    """
    config = rules or load_pollution_rules()
    weight = float(config["avoidance"]["cost_weight"])
    exponent = float(config["avoidance"]["exponent"])
    return [1.0 + weight * (max(value, 0.0) ** exponent) for value in edge_exposure]


__all__ = [
    "DEFAULT_RULES",
    "PollutionField",
    "avoidance_multipliers",
    "build_field_from_sources",
    "clamp",
    "load_pollution_rules",
    "sample_corridor_sources",
]

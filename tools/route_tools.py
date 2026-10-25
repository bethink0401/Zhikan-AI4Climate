"""Road-network routing built on the prepared Hong Kong road centreline dataset.

The processed dataset is a plain WGS84 GeoJSON road centreline network. This module
turns it into a routable graph and searches it with several alternative cost
functions, so the Travel Agent can offer more than one plausible route.

Design notes
------------
* No third-party dependencies. Graph construction and shortest paths use the
  standard library only, matching the rest of the repository.
* Vertices are snapped with a small tolerance (~1.5 m). The source data already
  shares coordinates at junctions, so a tight tolerance keeps the network
  connected without inventing shortcuts across parallel streets.
* Cost functions are deliberately simple and explainable. They are used to
  produce *plausible alternatives for screening purposes*, not to reproduce a
  commercial routing engine.
"""

from __future__ import annotations

import heapq
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from spatial_agent.tools.spatial_tools import angular_difference, bearing_deg, haversine_km

# ── 常量 ────────────────────────────────────────────────────────────────────

DEFAULT_ROADS = (
    Path(__file__).resolve().parents[1]
    / "spatial_agent"
    / "data"
    / "processed"
    / "road_network.geojson"
)

#: Vertex snapping tolerance in metres. Small on purpose: the source centreline
#: data already shares junction coordinates, so a tight tolerance is enough.
SNAP_TOLERANCE_M = 1.5

#: Only nodes within this distance of the requested origin/destination are used.
SNAP_MAX_KM = 1.5

#: Multiplier applied to already-used edges when searching for an alternative
#: route. Large enough to force a genuinely different street, small enough that
#: a detour is still preferred over an absurdly long way round.
DETOUR_PENALTY = 4.0

#: Alternatives sharing more than this fraction of edges are treated as duplicates.
ROUTE_SIMILARITY_LIMIT = 0.6

#: Vehicles prefer trunk roads over weaving through side streets.
DRIVE_TRUNK_ROAD_DISCOUNT = 0.8

#: Weight of the per-junction penalty applied when routing for vehicles.
DRIVE_JUNCTION_PENALTY = 0.01

#: 判定"路口"的最小节点度，以及把同一路口的多个节点合并的半径（米）。
#: 数据把一处交叉口拆成若干相邻节点，不合并会把同一个路口算成三四次等待。
JUNCTION_MIN_DEGREE = 3
JUNCTION_MERGE_M = 30.0

_ROAD_GRAPH_CACHE: dict[str, "RoadGraph"] = {}


# ── 数据结构 ────────────────────────────────────────────────────────────────

class RoadGraph:
    """An undirected, weighted road graph derived from the centreline dataset."""

    def __init__(self, coordinates: list[tuple[float, float]], edges: list[dict[str, Any]]):
        self.coordinates = coordinates
        self.edges = edges
        self.adjacency: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for index, edge in enumerate(edges):
            self.adjacency[edge["from_node"]].append((edge["to_node"], index))
            self.adjacency[edge["to_node"]].append((edge["from_node"], index))

    def __len__(self) -> int:
        return len(self.coordinates)

    def node_position(self, node_id: int) -> tuple[float, float]:
        return self.coordinates[node_id]

    def nearest_node(self, lon: float, lat: float) -> tuple[int, float]:
        """Return (node_id, distance_km) for the closest graph node."""
        best_id, best_distance = -1, float("inf")
        for node_id, (node_lon, node_lat) in enumerate(self.coordinates):
            distance = haversine_km(lon, lat, node_lon, node_lat)
            if distance < best_distance:
                best_id, best_distance = node_id, distance
        return best_id, best_distance

    def shortest_path(
        self,
        start_node: int,
        end_node: int,
        cost: Callable[[dict[str, Any]], float] | None = None,
        *,
        node_penalty: Callable[[int], float] | None = None,
        edge_penalty: list[float] | None = None,
    ) -> list[int] | None:
        """Dijkstra search. Returns the list of edge indices, or None if unreachable."""
        if start_node == end_node:
            return []
        cost_of = cost or (lambda edge: edge["length_km"])
        penalty_of = node_penalty or (lambda node_id: 1.0)

        best: dict[int, float] = {start_node: 0.0}
        previous: dict[int, tuple[int, int]] = {}
        queue: list[tuple[float, int]] = [(0.0, start_node)]
        settled: set[int] = set()

        while queue:
            distance, node = heapq.heappop(queue)
            if node in settled:
                continue
            settled.add(node)
            if node == end_node:
                break
            for neighbour, edge_index in self.adjacency[node]:
                if neighbour in settled:
                    continue
                step = cost_of(self.edges[edge_index]) * penalty_of(neighbour)
                if edge_penalty is not None:
                    step *= edge_penalty[edge_index]
                candidate = distance + step
                if candidate < best.get(neighbour, float("inf")):
                    best[neighbour] = candidate
                    previous[neighbour] = (node, edge_index)
                    heapq.heappush(queue, (candidate, neighbour))

        if end_node not in previous and end_node != start_node:
            return None

        route: list[int] = []
        cursor = end_node
        while cursor != start_node:
            parent, edge_index = previous[cursor]
            route.append(edge_index)
            cursor = parent
        route.reverse()
        return route


# ── 图构建 ──────────────────────────────────────────────────────────────────

#: 源数据用 "-99" 及其全角变体（-９９ / －９９……）标注**未命名路段**，
#: 中英文名字段都可能出现。不过滤的话会一路漏进 road_names，
#: 前端评级卡标题就变成"驾车干道（-９９）"。
_UNNAMED_NAME = re.compile(r"^[-–—―－‐][0-9０-９]+$")


def _clean_road_name(value: Any) -> str:
    """Strip and normalise a road name; sentinel/placeholder names become ''."""
    text = str(value or "").strip()
    if not text or text.lower() in {"unnamed road", "nan", "none", "null"}:
        return ""
    return "" if _UNNAMED_NAME.match(text) else text


def load_road_features(path: str | Path = DEFAULT_ROADS) -> list[dict[str, Any]]:
    """Read the centreline GeoJSON and normalise it into a flat feature list."""
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)

    features: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        geometry = feature.get("geometry") or {}
        geometry_type = geometry.get("type")
        if geometry_type == "LineString":
            lines = [geometry.get("coordinates") or []]
        elif geometry_type == "MultiLineString":
            lines = geometry.get("coordinates") or []
        else:
            continue

        properties = feature.get("properties") or {}
        name = _clean_road_name(properties.get("road_name"))
        near_stations = properties.get("near_stations") or []
        if isinstance(near_stations, str):
            near_stations = [part.strip() for part in near_stations.split(",") if part.strip()]

        # route_number 缺失时在源数据里是浮点 NaN，str(NaN) == 'nan' 是**真值**。
        # 不归一化的话全部路段都会被判成有编号的干线（实测 21774/21774 全中），
        # 干线判定和"干线打折"会整体失效。真实带编号的只有 1410 条（6.5%）。
        route_number = properties.get("route_number")
        route_number = "" if route_number is None else str(route_number).strip()
        if route_number.lower() in {"nan", "none", "null"}:
            route_number = ""

        for line in lines:
            if len(line) < 2:
                continue
            features.append(
                {
                    "road_id": str(properties.get("road_id") or properties.get("object_id") or ""),
                    "road_name": name,
                    "road_name_zh": _clean_road_name(properties.get("road_name_zh")),
                    "route_number": route_number,
                    "near_stations": [str(code).upper() for code in near_stations],
                    "has_name": bool(name),
                    "coordinates": line,
                }
            )
    return features


def build_road_graph(
    features: Iterable[dict[str, Any]],
    *,
    snap_tolerance_m: float = SNAP_TOLERANCE_M,
) -> RoadGraph:
    """Snap vertices onto shared nodes and build the weighted graph."""
    tolerance_deg = max(snap_tolerance_m, 0.5) / 111320.0
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    coordinates: list[tuple[float, float]] = []

    def bucket_key(lon: float, lat: float) -> tuple[int, int]:
        return (int(lon / tolerance_deg), int(lat / tolerance_deg))

    def resolve(lon: float, lat: float) -> int:
        key_x, key_y = bucket_key(lon, lat)
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                for node_id in buckets.get((key_x + offset_x, key_y + offset_y), []):
                    node_lon, node_lat = coordinates[node_id]
                    if haversine_km(lon, lat, node_lon, node_lat) * 1000.0 <= snap_tolerance_m:
                        return node_id
        node_id = len(coordinates)
        coordinates.append((lon, lat))
        buckets[(key_x, key_y)].append(node_id)
        return node_id

    edges: list[dict[str, Any]] = []
    #: The source dataset stores each street once per travel direction, so the
    #: same node pair can appear twice. Collapse those duplicates, otherwise a
    #: route search can "switch lane" between two identical edges and report a
    #: different-looking alternative that is physically the same street.
    deduplicated: dict[tuple[int, int], int] = {}
    for feature in features:
        line = feature["coordinates"]
        for first, second in zip(line, line[1:]):
            from_node = resolve(first[0], first[1])
            to_node = resolve(second[0], second[1])
            if from_node == to_node:
                continue
            length_km = haversine_km(first[0], first[1], second[0], second[1])
            if length_km <= 0:
                continue
            pair = (min(from_node, to_node), max(from_node, to_node))
            existing = deduplicated.get(pair)
            if existing is not None and edges[existing]["length_km"] <= length_km:
                continue
            edge = {
                "from_node": from_node,
                "to_node": to_node,
                "length_km": length_km,
                "road_id": feature["road_id"],
                "road_name": feature["road_name"],
                "road_name_zh": feature["road_name_zh"],
                "route_number": feature["route_number"],
                "near_stations": feature["near_stations"],
                "has_name": feature["has_name"],
                "mid_lon": (first[0] + second[0]) / 2.0,
                "mid_lat": (first[1] + second[1]) / 2.0,
            }
            if existing is None:
                deduplicated[pair] = len(edges)
                edges.append(edge)
            else:
                edges[existing] = edge

    return RoadGraph(coordinates, edges)


def get_road_graph(path: str | Path = DEFAULT_ROADS) -> RoadGraph:
    """Return a cached road graph, building it once per process."""
    key = str(Path(path).resolve())
    if key not in _ROAD_GRAPH_CACHE:
        _ROAD_GRAPH_CACHE[key] = build_road_graph(load_road_features(path))
    return _ROAD_GRAPH_CACHE[key]


# ── 成本函数 ────────────────────────────────────────────────────────────────

def make_cost_function(mode: str) -> Callable[[dict[str, Any]], float]:
    """Build an edge cost function for one travel mode.

    The cost is plain distance plus a trunk-road discount for vehicles. Pollution
    avoidance is **not** baked in here: it is supplied separately as an
    ``edge_multiplier`` derived from the exposure field (see
    ``tools/pollution_field.py``), so that the base cost stays a single, testable
    definition of "shortest" and pollution is an explicit, tunable overlay.

    Supported modes: ``walk`` and ``drive``.
    """
    def cost(edge: dict[str, Any]) -> float:
        base = edge["length_km"]
        if mode == "drive" and edge.get("route_number"):
            base *= DRIVE_TRUNK_ROAD_DISCOUNT
        return base

    return cost


def make_node_penalty(mode: str, graph: "RoadGraph") -> Callable[[int], float]:
    """Build a node cost multiplier. Vehicles are nudged away from junction-heavy weaving."""
    if mode != "drive":
        return lambda node_id: 1.0

    def penalty(node_id: int) -> float:
        return 1.0 + DRIVE_JUNCTION_PENALTY * len(graph.adjacency[node_id])

    return penalty


# ── 路径汇总 ────────────────────────────────────────────────────────────────

def path_geometry(
    graph: RoadGraph, edge_indices: list[int], start_node: int | None = None
) -> list[list[float]]:
    """Chain the edge endpoints into a single LineString coordinate list.

    Edges are undirected and store a fixed from/to orientation, so a path may
    traverse an edge backwards. The endpoints are therefore walked as a node
    sequence starting from ``start_node``; without that anchor the first edge
    can be drawn from the wrong end and the line doubles back on itself.
    """
    geometry: list[list[float]] = []
    current = start_node
    for edge_index in edge_indices:
        edge = graph.edges[edge_index]
        from_node, to_node = edge["from_node"], edge["to_node"]
        if current is None:
            current = from_node
        next_node = to_node if current == from_node else from_node
        if not geometry:
            position = graph.node_position(current)
            geometry.append([round(position[0], 6), round(position[1], 6)])
        position = graph.node_position(next_node)
        geometry.append([round(position[0], 6), round(position[1], 6)])
        current = next_node
    return geometry


def path_road_names(graph: RoadGraph, edge_indices: list[int], limit: int = 8) -> list[str]:
    """Ordered, de-duplicated list of road names along the path."""
    names: list[str] = []
    for edge_index in edge_indices:
        edge = graph.edges[edge_index]
        label = edge["road_name_zh"] or edge["road_name"]
        if not label or label.lower() == "unnamed road":
            continue
        if names and names[-1] == label:
            continue
        if label not in names:
            names.append(label)
        if len(names) >= limit:
            break
    return names


def path_exposure(
    graph: RoadGraph,
    edge_indices: list[int],
    *,
    edge_exposure: list[float] | None = None,
    high_threshold: float = 0.5,
) -> dict[str, Any]:
    """Summarise how polluted a path is.

    ``dose_index`` is the exposure integrated along the path (km × 0-1 index),
    which stands in for the inhaled dose: walking a longer but cleaner street can
    still lower it. ``mean_index`` makes routes of different length comparable.
    """
    total_km = 0.0
    hotspot_km = 0.0
    named_km = 0.0
    dose_index = 0.0
    peak_index = 0.0
    high_exposure_km = 0.0

    for edge_index in edge_indices:
        edge = graph.edges[edge_index]
        length = edge["length_km"]
        total_km += length
        if edge.get("near_stations"):
            hotspot_km += length
        if edge.get("has_name"):
            named_km += length
        if edge_exposure is not None:
            value = float(edge_exposure[edge_index])
            dose_index += value * length
            peak_index = max(peak_index, value)
            if value >= high_threshold:
                high_exposure_km += length

    return {
        "total_km": round(total_km, 3),
        "dose_index": round(dose_index, 4),
        "mean_index": round(dose_index / total_km, 4) if total_km else 0.0,
        "peak_index": round(peak_index, 4),
        "high_exposure_km": round(high_exposure_km, 3),
        "monitored_corridor_km": round(hotspot_km, 3),
        "named_road_km": round(named_km, 3),
        "monitored_corridor_ratio": round(hotspot_km / total_km, 3) if total_km else 0.0,
        "named_road_ratio": round(named_km / total_km, 3) if total_km else 0.0,
    }


def path_junctions(
    graph: RoadGraph,
    edge_indices: list[int],
    start_node: int | None = None,
    *,
    min_degree: int = JUNCTION_MIN_DEGREE,
    merge_m: float = JUNCTION_MERGE_M,
) -> int:
    """统计路径真正经过的**路口数**，用于估算路口等待时间。

    两个坑都在实测里踩到过：

    1. 只看相邻路段方位差，会把一片路口内部 3~10 m 的短噪声段也算成转向：
       沿弥敦道走 3 km 直路会被算出 26 次"转弯"。
    2. 数据把一处交叉口拆成多个相邻节点：3 km 的路段上有 51 个度 ≥ 3 的节点，
       合并 30 m 内的相邻节点后是 29 个，平均间距 103 m——这才是弥敦道实际的
       路口密度。

    因此按"度 ≥ 3 的节点 + 相邻合并"计数，不按方位差。步行过路口要等信号，
    直行过路口同样要等，所以不做转角过滤。
    """
    if len(edge_indices) < 2:
        return 0

    nodes: list[int] = []
    current = start_node
    for edge_index in edge_indices:
        edge = graph.edges[edge_index]
        from_node, to_node = edge["from_node"], edge["to_node"]
        if current is None:
            current = from_node
        next_node = to_node if current == from_node else from_node
        nodes.append((current, next_node))
        current = next_node

    counted: list[tuple[float, float]] = []
    for _, node in nodes[:-1]:
        if len(graph.adjacency[node]) < min_degree:
            continue
        position = graph.node_position(node)
        if counted:
            last_lon, last_lat = counted[-1]
            if haversine_km(position[0], position[1], last_lon, last_lat) * 1000.0 <= merge_m:
                continue
        counted.append(position)
    return len(counted)


def _summarise_path(
    graph: RoadGraph,
    edge_indices: list[int],
    mode: str,
    start_node: int,
    *,
    edge_exposure: list[float] | None = None,
    high_threshold: float = 0.5,
) -> dict[str, Any]:
    """Turn a list of edge indices into the route payload used downstream."""
    geometry = path_geometry(graph, edge_indices, start_node)
    distance_km = sum(graph.edges[index]["length_km"] for index in edge_indices)
    return {
        "mode": mode,
        "edge_count": len(edge_indices),
        "edge_indices": edge_indices,
        "distance_km": round(distance_km, 3),
        "geometry": geometry,
        "road_names": path_road_names(graph, edge_indices),
        "junctions": path_junctions(graph, edge_indices, start_node),
        "exposure": path_exposure(
            graph,
            edge_indices,
            edge_exposure=edge_exposure,
            high_threshold=high_threshold,
        ),
    }


def search_route(
    graph: RoadGraph,
    origin: tuple[float, float],
    destination: tuple[float, float],
    *,
    mode: str = "walk",
    edge_exposure: list[float] | None = None,
) -> dict[str, Any] | None:
    """Search one route between two WGS84 points and summarise it."""
    start_node, start_distance = graph.nearest_node(origin[0], origin[1])
    end_node, end_distance = graph.nearest_node(destination[0], destination[1])
    if start_distance > SNAP_MAX_KM or end_distance > SNAP_MAX_KM:
        return None

    cost = make_cost_function(mode)
    edge_indices = graph.shortest_path(
        start_node, end_node, cost, node_penalty=make_node_penalty(mode, graph)
    )
    if edge_indices is None:
        return None

    route = _summarise_path(graph, edge_indices, mode, start_node, edge_exposure=edge_exposure)
    if len(route["geometry"]) < 2:
        return None
    route["origin_snap_km"] = round(start_distance, 3)
    route["destination_snap_km"] = round(end_distance, 3)
    return route


def search_route_alternatives(
    graph: RoadGraph,
    origin: tuple[float, float],
    destination: tuple[float, float],
    *,
    mode: str = "walk",
    max_routes: int = 3,
    detour_penalty: float = DETOUR_PENALTY,
    edge_exposure: list[float] | None = None,
    edge_multiplier: list[float] | None = None,
    high_threshold: float = 0.5,
) -> list[dict[str, Any]]:
    """Return up to ``max_routes`` distinct routes between two points.

    The first route is the plain optimum for the given mode. Each further route
    re-runs the same search with the edges already used penalised, which forces
    the next-best genuinely different street. Candidates that overlap an earlier
    route too heavily are discarded instead of being reported twice.

    ``edge_multiplier`` biases the search itself (used to route *around*
    pollution); ``edge_exposure`` only annotates the result.
    """
    start_node, start_distance = graph.nearest_node(origin[0], origin[1])
    end_node, end_distance = graph.nearest_node(destination[0], destination[1])
    if start_distance > SNAP_MAX_KM or end_distance > SNAP_MAX_KM:
        return []

    cost = make_cost_function(mode)
    node_penalty = make_node_penalty(mode, graph)
    penalties = list(edge_multiplier) if edge_multiplier else [1.0] * len(graph.edges)
    routes: list[dict[str, Any]] = []
    seen_edge_sets: list[set[int]] = []
    seen_corridors: list[list[str]] = []
    max_attempts = max_routes + 4

    for _ in range(max_attempts):
        if len(routes) >= max_routes:
            break
        edge_indices = graph.shortest_path(
            start_node, end_node, cost,
            node_penalty=node_penalty,
            edge_penalty=penalties,
        )
        if edge_indices is None:
            break

        # Penalise what we just found before judging it, so the next attempt is
        # pushed away from this street even when the route itself is rejected.
        for edge_index in edge_indices:
            penalties[edge_index] *= detour_penalty

        edge_set = set(edge_indices)
        corridor_names = path_road_names(graph, edge_indices, limit=3)
        duplicate = False
        for previous in seen_edge_sets:
            union = len(previous | edge_set)
            overlap = len(previous & edge_set) / union if union else 1.0
            if overlap > ROUTE_SIMILARITY_LIMIT:
                duplicate = True
                break
        # Nathan Road is mapped as two separate carriageway centrelines, so a
        # route can "switch lane" onto the other carriageway and score as a
        # different edge set while still being the same street. Treat an
        # identical sequence of main roads as the same route.
        if not duplicate and any(corridor_names == previous for previous in seen_corridors):
            duplicate = True
        if duplicate:
            continue

        seen_edge_sets.append(edge_set)
        seen_corridors.append(corridor_names)
        route = _summarise_path(
            graph, edge_indices, mode, start_node,
            edge_exposure=edge_exposure, high_threshold=high_threshold,
        )
        if len(route["geometry"]) >= 2:
            route["origin_snap_km"] = round(start_distance, 3)
            route["destination_snap_km"] = round(end_distance, 3)
            route["alternative_index"] = len(routes)
            routes.append(route)

    return routes


def merge_route_options(
    primary: list[dict[str, Any]], extra: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Append routes from ``extra`` that are not already covered by ``primary``.

    Two routes count as the same when their main-road sequence matches, which is
    the same rule the alternative search uses internally.
    """
    merged = list(primary)
    known = [route.get("road_names", [])[:3] for route in merged]
    for route in extra:
        signature = route.get("road_names", [])[:3]
        if not signature or signature in known:
            continue
        known.append(signature)
        route = dict(route)
        route["alternative_index"] = len(merged)
        merged.append(route)
    return merged


def straight_line_km(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    """Great-circle distance between two WGS84 points, in kilometres."""
    return haversine_km(origin[0], origin[1], destination[0], destination[1])


# ── 轨道网络 ────────────────────────────────────────────────────────────────
#
# The prepared POI dataset contains every rail station as a point, but not the
# line topology. The network is therefore approximated by linking each station
# to its closest neighbours within ``RAIL_STATION_LINK_KM``. Adjacent stations
# on a real metro line sit roughly 0.8-1.5 km apart, so this recovers plausible
# line segments while keeping cross-city shortcuts out of the graph.
#
# This is a screening-level approximation. It is stated as an assumption in the
# Agent output and must not be presented as the official operating route.

DEFAULT_POIS = (
    Path(__file__).resolve().parents[1]
    / "spatial_agent"
    / "data"
    / "processed"
    / "poi_sensitive.geojson"
)

#: MTR 网络补充数据：缺失站点坐标 + 显式线路邻接（跨海段等邻近启发式连不上的站对）。
MTR_SUPPLEMENT = Path(__file__).resolve().parents[1] / "config" / "mtr_supplement.json"

#: Maximum distance between two stations for them to be treated as adjacent.
RAIL_STATION_LINK_KM = 1.6

#: How many neighbours each station is linked to.
RAIL_MAX_NEIGHBOURS = 3

#: Suffixes that mark an entrance rather than the station itself.
_ENTRANCE_MARKERS = ("進出口", "进出口", "出口", "入口")

#: Trailing exit codes such as ``-C1`` / ``-D2`` attached to a station name.
_ENTRANCE_CODE = re.compile(r"[-–—][A-Za-z]{0,2}\d{0,3}[A-Za-z]{0,2}\s*$")

_RAIL_STATIONS_CACHE: dict[str, list[dict[str, Any]]] = {}


def _strip_entrance(name: str) -> str:
    """Drop an entrance suffix such as ``-A進出口`` or ``-C1`` from a station name."""
    cleaned = name
    for marker in _ENTRANCE_MARKERS:
        index = cleaned.find(marker)
        if index > 0:
            cleaned = cleaned[:index]
    cleaned = cleaned.strip()
    # Repeat: names can carry both a marker and a trailing exit code.
    while True:
        trimmed = _ENTRANCE_CODE.sub("", cleaned).strip()
        if trimmed == cleaned or not trimmed:
            break
        cleaned = trimmed
    return cleaned.rstrip(" -–—").strip()


def load_rail_stations(path: str | Path = DEFAULT_POIS) -> list[dict[str, Any]]:
    """Load and de-duplicate rail stations from the prepared POI dataset."""
    key = str(Path(path).resolve())
    if key in _RAIL_STATIONS_CACHE:
        return _RAIL_STATIONS_CACHE[key]

    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)

    stations: dict[str, dict[str, Any]] = {}
    for feature in payload.get("features", []):
        properties = feature.get("properties") or {}
        if properties.get("poi_type") != "rail_transit":
            continue
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point" or not geometry.get("coordinates"):
            continue
        raw_name = properties.get("name_zh") or properties.get("name") or ""
        name = _strip_entrance(str(raw_name))
        # 数据源把港铁站统一写作"香港鐵路X站"，展示时简化为"港铁X站"更易读。
        name = name.replace("香港鐵路", "港铁").replace("香港铁路", "港铁")
        if not name or name in stations:
            continue
        lon, lat = geometry["coordinates"][:2]
        stations[name] = {
            "station_id": str(properties.get("poi_id") or name),
            "name": name,
            "lon": float(lon),
            "lat": float(lat),
        }

    # 合并补充站点（敏感 POI 子集缺过海核心站；同名站以补充文件坐标为准）
    if MTR_SUPPLEMENT.exists():
        try:
            supplement = json.loads(MTR_SUPPLEMENT.read_text(encoding="utf-8"))
            for station in supplement.get("stations") or []:
                name = str(station.get("name") or "").strip()
                if not name or not station.get("lat") or not station.get("lon"):
                    continue
                stations[name] = {
                    "station_id": f"mtr_{name}",
                    "name": name,
                    "lon": float(station["lon"]),
                    "lat": float(station["lat"]),
                }
        except (ValueError, OSError):
            pass

    result = list(stations.values())
    _RAIL_STATIONS_CACHE[key] = result
    return result


def build_rail_links(
    stations: list[dict[str, Any]],
    *,
    link_km: float = RAIL_STATION_LINK_KM,
    max_neighbours: int = RAIL_MAX_NEIGHBOURS,
) -> dict[str, list[tuple[str, float]]]:
    """Link each station to its nearest neighbours, forming an undirected graph."""
    # Deduplicate by name: both endpoints of a link register it, so the raw list
    # would otherwise carry each neighbour more than once.
    collected: dict[str, dict[str, float]] = {station["name"]: {} for station in stations}
    for station in stations:
        distances: list[tuple[float, str]] = []
        for other in stations:
            if other["name"] == station["name"]:
                continue
            distance = haversine_km(station["lon"], station["lat"], other["lon"], other["lat"])
            if distance <= link_km:
                distances.append((distance, other["name"]))
        distances.sort()
        for distance, name in distances[:max_neighbours]:
            collected[station["name"]][name] = distance
            collected[name][station["name"]] = distance

    # 显式线路邻接：跨海段等间距超阈值的相邻站对（如尖沙咀—金钟），启发式连不上。
    if MTR_SUPPLEMENT.exists():
        try:
            supplement = json.loads(MTR_SUPPLEMENT.read_text(encoding="utf-8"))
            for left, right in supplement.get("extra_links") or []:
                if left in collected and right in collected:
                    left_station = next(s for s in stations if s["name"] == left)
                    right_station = next(s for s in stations if s["name"] == right)
                    distance = haversine_km(left_station["lon"], left_station["lat"],
                                            right_station["lon"], right_station["lat"])
                    collected[left][right] = distance
                    collected[right][left] = distance
        except (ValueError, OSError, StopIteration):
            pass

    return {
        name: sorted(neighbours.items(), key=lambda item: item[1])
        for name, neighbours in collected.items()
    }


def nearest_rail_station(
    stations: list[dict[str, Any]], lon: float, lat: float
) -> tuple[dict[str, Any] | None, float]:
    """Return (station, distance_km) for the closest rail station."""
    best: dict[str, Any] | None = None
    best_distance = float("inf")
    for station in stations:
        distance = haversine_km(lon, lat, station["lon"], station["lat"])
        if distance < best_distance:
            best, best_distance = station, distance
    return best, best_distance


def search_rail_route(
    links: dict[str, list[tuple[str, float]]],
    origin_name: str,
    destination_name: str,
) -> list[str] | None:
    """Shortest station sequence between two named stations."""
    if origin_name not in links or destination_name not in links:
        return None
    best: dict[str, float] = {origin_name: 0.0}
    previous: dict[str, str] = {}
    queue: list[tuple[float, str]] = [(0.0, origin_name)]
    settled: set[str] = set()

    while queue:
        distance, name = heapq.heappop(queue)
        if name in settled:
            continue
        settled.add(name)
        if name == destination_name:
            break
        for neighbour, weight in links[name]:
            if neighbour in settled:
                continue
            candidate = distance + weight
            if candidate < best.get(neighbour, float("inf")):
                best[neighbour] = candidate
                previous[neighbour] = name
                heapq.heappush(queue, (candidate, neighbour))

    if destination_name not in previous and destination_name != origin_name:
        return None

    sequence = [destination_name]
    cursor = destination_name
    while cursor != origin_name:
        cursor = previous[cursor]
        sequence.append(cursor)
    sequence.reverse()
    return sequence


def stations_by_name(
    stations: list[dict[str, Any]], names: Iterable[str]
) -> list[dict[str, Any]]:
    """Resolve an ordered list of station names to station records."""
    index = {station["name"]: station for station in stations}
    resolved: list[dict[str, Any]] = []
    for name in names:
        station = index.get(name)
        if station:
            resolved.append(station)
    return resolved


def make_rail_segment(
    stations: list[dict[str, float]],
) -> dict[str, Any]:
    """Build a synthetic rail line geometry from an ordered list of station points."""
    geometry = [[float(station["lon"]), float(station["lat"])] for station in stations]
    distance_km = sum(
        haversine_km(first["lon"], first["lat"], second["lon"], second["lat"])
        for first, second in zip(stations, stations[1:])
    )
    return {
        "geometry": geometry,
        "distance_km": round(distance_km, 3),
        "station_count": len(stations),
        "stations": [station.get("name", "") for station in stations],
    }


def bearing_label(bearing: float) -> str:
    """Convert a bearing in degrees to a 16-point compass label."""
    labels = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    ]
    index = int(round((bearing % 360.0) / 22.5)) % 16
    return labels[index]


def round_geometry(geometry: list[list[float]], digits: int = 6) -> list[list[float]]:
    """Round coordinates for compact JSON output."""
    return [[round(point[0], digits), round(point[1], digits)] for point in geometry]


__all__ = [
    "DEFAULT_POIS",
    "DEFAULT_ROADS",
    "RoadGraph",
    "bearing_label",
    "build_rail_links",
    "build_road_graph",
    "get_road_graph",
    "load_rail_stations",
    "load_road_features",
    "make_cost_function",
    "make_node_penalty",
    "make_rail_segment",
    "merge_route_options",
    "nearest_rail_station",
    "path_exposure",
    "path_geometry",
    "path_road_names",
    "path_junctions",
    "round_geometry",
    "search_rail_route",
    "search_route",
    "search_route_alternatives",
    "stations_by_name",
    "straight_line_km",
]

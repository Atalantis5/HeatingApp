# -*- coding: utf-8 -*-
"""controller.py — 水力计算控制器（V4：NetworkX 动态拓扑寻优）

职责：
1) 图论解析（V4 新增）：把前端传来的 nodes/edges 构建为 networkx.DiGraph，
   自动识别热源（入度 0）/ 末端（出度 0），枚举全部“热源→末端”路径，
   以路径上所有管段总长度最大者为最不利环路 Loop1；其余路径与 Loop1 的交集
   即为各并联环路的共用管段，差集为独用管段。
2) 计算调度：复用 Part A（最不利环路迭代）/ Part B（并联环路平衡）物理流程，
   物理公式仍全部来自冻结的 engine.py；本文件不写死任何具体管段 ID / 环路列表。
3) 兼容旧版：若请求未携带 nodes/edges，则按旧版闭环语义处理（segments 顺序 +
   已废弃的 shared_segments 字段），保证既有客户端可用。

边界：
- 不修改 engine.py / data_loader.py（物理公式与查表口径冻结）；
- 本文件不直接读取 Excel / 数据库，不跨目录读写。
"""

from __future__ import annotations

import networkx as nx

import data_loader
import engine
from models import ProjectInput

# ---------------------------------------------------------------------------
# 常量（单一来源）
# ---------------------------------------------------------------------------
FLOW_CONVERSION_FACTOR = 0.86          # G = 0.86*Q/Δt（热水供暖单位换算系数）
SUPPLY_TEMPERATURE_C = 95.0            # 供水温度 tg，单位 ℃
RETURN_TEMPERATURE_C = 70.0            # 回水温度 th，单位 ℃
TEMPERATURE_DIFFERENCE_C = SUPPLY_TEMPERATURE_C - RETURN_TEMPERATURE_C  # Δt=25 K
DEFAULT_ALPHA = 0.5                    # 沿程阻力占比（重力循环系统经验值）
CONVERGENCE_MARGIN = 0.10              # Part A 富裕度下限（≥10% 视为收敛）
IMBALANCE_LIMIT = 0.15                 # 并联环路不平衡率限值（±15%）
DN_FLOOR = "DN15"                      # 缩径硬性底线
DN_CEILING = "DN100"                   # 升径上限
DN_ORDER = tuple(data_loader.DN_ORDER)
DN_INDEX = {dn: index for index, dn in enumerate(DN_ORDER)}

# Hardy-Cross 环状管网求解参数（V8.1）
LOOP_CONVERGENCE_TOLERANCE_PA = 0.1   # 每个基本回路的压力闭合差收敛阈值（Pa）
LOOP_MAX_ITERATIONS = 100             # Hardy-Cross 最大迭代次数
VIRTUAL_PRESSURE_NODE = "__PRESSURE_BOUNDARY__"  # 多热源等压边界虚拟节点


# ---------------------------------------------------------------------------
# 基础换算 / 管径步进 / 底线守卫
# ---------------------------------------------------------------------------
def heat_load_to_flow(Q: float) -> float:
    """热负荷 → 流量：G = 0.86 * Q / Δt（Δt=25 K，tg=95、th=70）。"""
    return FLOW_CONVERSION_FACTOR * Q / TEMPERATURE_DIFFERENCE_C


def _next_larger_dn(current_dn: str) -> str | None:
    """按 DN15→DN20→...→DN100 顺序升一档；已是 DN100 时返回 None（升径上限）。"""
    next_index = DN_INDEX[current_dn] + 1
    if next_index >= len(DN_ORDER):
        return None
    return DN_ORDER[next_index]


def _shrink_candidate(segment_id: int, current_dn: str, warnings: list[str]) -> str | None:
    """DN15 硬性拦截底线（缩径方向）。

    若当前管径已是 DN15，则禁止继续缩径并记录 warning；否则返回下一档较小管径
    （仅用于可行性判断，Part B 按契约不做自动缩径，因此不存在无限缩径路径）。
    """
    if current_dn == DN_FLOOR:
        warnings.append(f"管段{segment_id}：已达最小管径 DN15，禁止继续缩径")
        return None
    return DN_ORDER[DN_INDEX[current_dn] - 1]


# ---------------------------------------------------------------------------
# 单管段计算
# ---------------------------------------------------------------------------
def _segment_result(segment, G: float, dn: str, resistance: dict, shared_ids: set) -> dict:
    """把单个管段计算结果组装为 SegmentResult 兼容 dict（不 round）。"""
    return {
        "segment_id": segment.segment_id,
        "dn": dn,
        "Q": segment.Q,
        "G": G,
        "l": segment.l,
        "zeta_sum": segment.zeta_sum,
        "dn_locked": segment.dn_locked,
        "shared": segment.segment_id in shared_ids,
        "v": resistance["v"],
        "Rm": resistance["Rm"],
        "dPy": resistance["dPy"],
        "dPd": resistance["dPd"],
        "dPj": resistance["dPj"],
        "dP_total": resistance["dP_total"],
    }


def _calc_segment(segment, G: float, dn: str, project: ProjectInput) -> dict:
    """调用 engine 计算单管段阻力；动压头密度严格透传 project.rho_dynamic。"""
    return engine.compute_segment_resistance(
        segment.Q,
        G,
        segment.l,
        dn,
        segment.zeta_sum,
        rho=project.rho_dynamic,
    )


# ===========================================================================
# V4 图论解析（无任何写死的管段 ID / 环路列表）
# ===========================================================================
def _node_key(value) -> str:
    """节点 id 统一为字符串（前端画布 id 可能为 node-xxx）。"""
    if value is None or isinstance(value, bool):
        raise ValueError(f"非法节点 id：{value!r}")
    text = str(value).strip()
    if not text:
        raise ValueError("节点 id 不能为空")
    return text


def _edge_segment_id(edge: dict, project: ProjectInput) -> int:
    """从边的多种前端表示中解析对应管段 segment_id。

    支持顺序：edge.segment_id → edge.segmentId → edge.data.segmentId →
    edge.data.segment_id；若均缺失，则按 segments 的 source_node/target_node
    端点匹配回退。解析结果必须是 project.segments 中已存在的 int segment_id。
    """
    segment_by_str = {str(segment.segment_id): segment for segment in project.segments}
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    raw_candidates = [
        edge.get("segment_id"),
        edge.get("segmentId"),
        data.get("segmentId"),
        data.get("segment_id"),
    ]
    raw = next((candidate for candidate in raw_candidates if candidate is not None), None)
    if raw is not None:
        # 先按原样匹配
        if str(raw) in segment_by_str:
            return int(segment_by_str[str(raw)].segment_id)
        # 兼容 "管段 9" / "segment-9" 等文本：提取数字后匹配
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        if digits and digits in segment_by_str:
            return int(segment_by_str[digits].segment_id)
        raise ValueError(f"边引用的管段 {raw!r} 不在 segments 列表中")

    # 端点回退匹配：segments 带有 source_node/target_node 时可用
    source = _node_key(edge.get("source"))
    target = _node_key(edge.get("target"))
    matched = [
        segment
        for segment in project.segments
        if segment.source_node is not None
        and segment.target_node is not None
        and _node_key(segment.source_node) == source
        and _node_key(segment.target_node) == target
    ]
    if len(matched) == 1:
        return int(matched[0].segment_id)
    if len(matched) > 1:
        raise ValueError(f"边 {source}->{target} 可匹配多个管段，无法确定唯一管段")
    raise ValueError(f"边 {source}->{target} 缺少 segment_id 且无法按端点匹配管段")


def build_topology_graph(project: ProjectInput) -> tuple[nx.DiGraph, dict[str, object]]:
    """由前端 nodes/edges 构建有向图；返回 (DiGraph, segment_by_str)。

    建图规则：nodes 提供节点（id 必填）；edges 提供 source/target（并映射到管段）。
    校验：节点 id 唯一、边端点存在、同一管段不被重复使用、所有管段均接入拓扑。
    """
    segment_by_str = {str(segment.segment_id): segment for segment in project.segments}

    node_ids: list[str] = []
    for item in project.nodes:
        if not isinstance(item, dict) or "id" not in item:
            raise ValueError(f"nodes 元素必须是含 id 的字典：{item!r}")
        node_ids.append(_node_key(item["id"]))
    if len(node_ids) != len(set(node_ids)):
        duplicated = sorted({nid for nid in node_ids if node_ids.count(nid) > 1})
        raise ValueError(f"节点 id 必须唯一，重复 id：{duplicated!r}")

    graph = nx.DiGraph()
    graph.add_nodes_from(node_ids)

    used_segments: set[int] = set()
    for edge in project.edges:
        if not isinstance(edge, dict):
            raise ValueError(f"edges 元素必须是字典：{edge!r}")
        source = _node_key(edge.get("source"))
        target = _node_key(edge.get("target"))
        if source not in graph:
            raise ValueError(f"边起点节点不存在于 nodes：{source!r}")
        if target not in graph:
            raise ValueError(f"边终点节点不存在于 nodes：{target!r}")
        segment_id = _edge_segment_id(edge, project)
        if segment_id in used_segments:
            raise ValueError(f"管段 {segment_id} 被多条边重复使用（枝状管网要求每条管段唯一）")
        graph.add_edge(source, target, segment_id=segment_id)
        used_segments.add(segment_id)

    unused = sorted(
        str(segment.segment_id) for segment in project.segments if int(segment.segment_id) not in used_segments
    )
    if unused:
        raise ValueError(f"存在未接入拓扑边的管段：{unused}")

    return graph, segment_by_str


def analyze_tree_topology(project: ProjectInput) -> dict:
    """V4 核心：NetworkX 自动解析管网拓扑并寻找最不利环路。

    步骤：
    1) 建图（build_topology_graph）；
    2) 入度为 0 的节点为热源，出度为 0 的节点为末端；
    3) 枚举全部“热源→末端”路径，以路径上管段总长度最大者为最不利环路 Loop1；
    4) 其余路径与 Loop1 的交集为共用管段、差集为独用管段。

    返回 dict（与 models.TopologyAnalysisResult 对齐），并在控制台打印解析结果。
    """
    graph, segment_by_str = build_topology_graph(project)

    if graph.number_of_nodes() == 0:
        raise ValueError("拓扑图为空：nodes/edges 未提供有效节点")
    # 防御 1：当前系统仅支持枝状管网，遇到环直接给出自然语言错误
    if not nx.is_directed_acyclic_graph(graph):
        cycle_nodes = sorted({node for cycle in nx.simple_cycles(graph) for node in cycle})
        print(f"[topology] 检测到环状管网，涉及节点：{cycle_nodes[:8]}")
        raise ValueError("当前系统不支持环状管网，请检查是否有多余连线")

    sources = sorted(node for node in graph.nodes if graph.in_degree(node) == 0)
    sinks = sorted(node for node in graph.nodes if graph.out_degree(node) == 0)
    # 防御 2：单热源约束
    if len(sources) == 0:
        raise ValueError("未找到热源（入度为 0 的节点）：请检查管网是否缺少锅炉节点或连线方向")
    if len(sources) > 1:
        print(f"[topology] 检测到多个热源（入度 0）：{sources}")
        raise ValueError("检测到多个热源，当前仅支持单热源系统")
    # 防御 3：必须有末端
    if not sinks:
        raise ValueError("未找到末端节点（出度为 0）：请检查管网是否存在断路")

    path_records: list[dict] = []
    seen_paths: set[tuple[int, ...]] = set()
    for sink in sinks:
        for node_path in nx.all_simple_paths(graph, sources[0], sink):
            if len(node_path) < 2:
                continue
            segment_ids = [
                int(graph[source][target]["segment_id"])
                for source, target in zip(node_path, node_path[1:])
            ]
            key = tuple(segment_ids)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            path_records.append(
                {
                    "sink_node": sink,
                    "node_path": list(node_path),
                    "segment_ids": segment_ids,
                    "total_length": sum(float(segment_by_str[str(sid)].l) for sid in segment_ids),
                }
            )
    if not path_records:
        raise ValueError("未找到任何从热源到末端的连通路径，请检查拓扑连通性")

    # 稳定排序，保证同等长度时结果可复现
    path_records.sort(key=lambda record: (tuple(record["segment_ids"]), tuple(record["node_path"])))
    loop1_record = max(
        path_records,
        key=lambda record: (
            round(record["total_length"], 9),
            len(record["segment_ids"]),
            tuple(record["segment_ids"]),
        ),
    )
    loop1_ids = list(loop1_record["segment_ids"])
    loop1_id_set = set(loop1_ids)

    parallel_loops: list[dict] = []
    for record in path_records:
        if record is loop1_record:
            continue
        record_ids = list(record["segment_ids"])
        record_id_set = set(record_ids)
        shared = [sid for sid in loop1_ids if sid in record_id_set]
        dedicated = [sid for sid in record_ids if sid not in loop1_id_set]
        parallel_loops.append(
            {
                **record,
                "shared_segment_ids": shared,
                "dedicated_segment_ids": dedicated,
            }
        )

    topology = {
        "source_nodes": sources,
        "sink_nodes": sinks,
        "loop1": {
            **loop1_record,
            "shared_segment_ids": [],
            "dedicated_segment_ids": [],
        },
        "parallel_loops": parallel_loops,
    }

    # 契约要求：控制台打印自动找到的最不利路径与共用管段
    print(f"[topology] 自动识别热源（入度0）：{sources}；末端（出度0）：{sinks}")
    print(
        f"[topology] 自动寻优最不利环路 Loop1：{loop1_ids}，"
        f"总长度={loop1_record['total_length']:.2f} m（sink={loop1_record['sink_node']}）"
    )
    for loop in parallel_loops:
        print(
            f"[topology] 并联环路 sink={loop['sink_node']}："
            f"路径={loop['segment_ids']}；共用管段={loop['shared_segment_ids']}；"
            f"独用管段={loop['dedicated_segment_ids']}"
        )
    return topology


def validate_topology_graph(project: ProjectInput) -> dict:
    """供 /validate-topology 使用的图模式校验（结构性错误不抛异常，返回 errors 列表）。"""
    graph, _ = build_topology_graph(project)
    sources = sorted(node for node in graph.nodes if graph.in_degree(node) == 0)
    sinks = sorted(node for node in graph.nodes if graph.out_degree(node) == 0)
    is_dag = bool(nx.is_directed_acyclic_graph(graph))
    errors: list[str] = []
    if not is_dag:
        errors.append("当前系统不支持环状管网，请检查是否有多余连线")
    if len(sources) == 0:
        errors.append("未找到热源（入度为 0 的节点）：请检查管网是否缺少锅炉节点或连线方向")
    elif len(sources) > 1:
        errors.append("检测到多个热源，当前仅支持单热源系统")
    if not sinks:
        errors.append("未找到末端节点（出度为 0）：请检查管网是否存在断路")
    valid = not errors
    return {
        "valid": valid,
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "is_strongly_connected": bool(nx.is_strongly_connected(graph)),
        "closed_loop": False,
        "shared_subset_ok": True,
        "message": "图模式拓扑校验通过" if valid else "图模式拓扑校验失败",
        "errors": errors,
        "mode": "graph",
        "is_dag": is_dag,
        "source_nodes": sources,
        "sink_nodes": sinks,
    }


# ===========================================================================
# 计算调度：Part A（最不利环路）/ Part B（并联环路）
# ===========================================================================
def _run_loop_a(project: ProjectInput, ordered_segments: list, shared_ids: set) -> dict:
    """Part A：对给定环路管段序列执行 while 迭代（升径降阻）计算。"""
    dP_prime = engine.compute_driving_pressure(project.H, project.rho_h, project.rho_g, project.dPf)
    total_length = sum(segment.l for segment in ordered_segments)
    R_pj = engine.compute_average_Rpj(dP_prime, total_length, DEFAULT_ALPHA)

    # 管径状态：锁定段固定；未锁定段在首轮由 select_dn 选定，之后由调整逻辑驱动
    current_dns: dict[int, str | None] = {
        segment.segment_id: segment.dn_locked for segment in ordered_segments
    }
    adjustment_log: list[dict] = []
    warnings: list[str] = []
    segment_results: list[dict] = []
    sum_resistance = 0.0
    margin = 0.0
    iteration = 0
    status = "not_converged"

    while iteration < project.max_iter:
        iteration += 1
        segment_results = []
        sum_resistance = 0.0

        for segment in ordered_segments:
            G = heat_load_to_flow(segment.Q)
            dn = current_dns[segment.segment_id]
            if dn is None:
                dn = data_loader.select_dn(G, R_pj)["dn"]
                current_dns[segment.segment_id] = dn
            resistance = _calc_segment(segment, G, dn, project)
            sum_resistance += resistance["dP_total"]
            segment_results.append(_segment_result(segment, G, dn, resistance, shared_ids))

        margin = (dP_prime - sum_resistance) / dP_prime
        if margin >= CONVERGENCE_MARGIN:
            status = "converged"
            break

        # margin 不足：对未锁定段升一档（降低阻力），每轮每段最多升一档
        adjusted_any = False
        for segment in ordered_segments:
            if segment.dn_locked is not None:
                continue
            current_dn = current_dns[segment.segment_id]
            larger_dn = _next_larger_dn(current_dn)
            if larger_dn is None:
                continue
            covering_dns = set(data_loader.interpolate_pipe(heat_load_to_flow(segment.Q)).keys())
            if larger_dn not in covering_dns:
                continue
            current_dns[segment.segment_id] = larger_dn
            adjustment_log.append(
                {
                    "segment_id": segment.segment_id,
                    "from_dn": current_dn,
                    "to_dn": larger_dn,
                    "iteration": iteration,
                    "direction": "enlarge",
                    "reason": "margin<0.10，按 DN 序列升一档管径以降低阻力",
                }
            )
            adjusted_any = True

        if not adjusted_any:
            warnings.append(
                "所有未锁定管段已达 DN100 上限（或不存在未锁定管段），"
                "margin 仍 < 0.10；部分管段富余不足或系统需调节；"
                "status=not_converged，按契约停止（不会无限循环）"
            )
            status = "not_converged"
            break
    else:
        warnings.append(
            f"达到最大迭代次数 max_iter={project.max_iter}，margin 仍 < 0.10；"
            "部分管段富余不足或系统需调节；status=not_converged"
        )
        status = "not_converged"

    return {
        "dP_prime": dP_prime,
        "total_length": total_length,
        "R_pj": R_pj,
        "segment_results": segment_results,
        "sum_resistance": sum_resistance,
        "margin": margin,
        "status": status,
        "iteration": iteration,
        "adjustment_log": adjustment_log,
        "warnings": warnings,
    }


def _run_parallel_loop(
    project: ProjectInput,
    path_segments: list,
    loop_a: dict,
    shared_segment_ids: list[int],
    label: str,
    sink_node: str | None,
) -> dict:
    """Part B：对一条并联环路（末端路径）扣除共用段阻力后做独用段平衡计算。"""
    loop_a_results = loop_a["segment_results"]
    shared_set = {int(sid) for sid in shared_segment_ids}
    dP_prime = loop_a["dP_prime"]

    sum_shared = sum(
        float(item["dP_total"]) for item in loop_a_results if int(item["segment_id"]) in shared_set
    )
    available_pressure = dP_prime - sum_shared
    if available_pressure <= 0:
        raise ValueError(
            "Part B 资用压力非正："
            f"dP_prime={dP_prime!r}, sum_shared={sum_shared!r}，"
            "共用段阻力已超过作用压力，无法进行并联环路计算"
        )

    dedicated_segments = [
        segment for segment in path_segments if int(segment.segment_id) not in shared_set
    ]
    if not dedicated_segments:
        raise ValueError(f"并联环路 {label} 不存在独用管段，无法执行 Part B")

    L_specific = sum(segment.l for segment in dedicated_segments)
    R_pj_specific = engine.compute_average_Rpj(available_pressure, L_specific, DEFAULT_ALPHA)

    specific_results: list[dict] = []
    sum_resistance_specific = 0.0
    for segment in dedicated_segments:
        G = heat_load_to_flow(segment.Q)
        if segment.dn_locked is not None:
            dn = segment.dn_locked
        else:
            dn = data_loader.select_dn(G, R_pj_specific)["dn"]
        resistance = _calc_segment(segment, G, dn, project)
        sum_resistance_specific += resistance["dP_total"]
        specific_results.append(_segment_result(segment, G, dn, resistance, set()))

    imbalance = engine.check_imbalance(available_pressure, sum_resistance_specific, label)
    imbalance_warnings: list[str] = []
    if imbalance["status"] == "unbalanced":
        imbalance_warnings.append(
            "Part B 不平衡率 "
            f"{imbalance['imbalance_percent']!r}% 超出 ±15%：{imbalance['suggestion']}"
        )

    # DN15 硬性拦截底线：有压力富余且独用段已达 DN15 时，只能加装调节阀
    if imbalance["imbalance_ratio"] > IMBALANCE_LIMIT:
        shrinkable: list[str] = []
        for item in specific_results:
            candidate = _shrink_candidate(int(item["segment_id"]), str(item["dn"]), imbalance_warnings)
            if candidate is not None:
                shrinkable.append(candidate)
        if not shrinkable:
            imbalance_warnings.append("无法再缩径，需加装调节阀")

    return {
        "available_pressure": available_pressure,
        "sum_shared": sum_shared,
        "L_specific": L_specific,
        "R_pj_specific": R_pj_specific,
        "sum_resistance_specific": sum_resistance_specific,
        "imbalance_ratio": imbalance["imbalance_ratio"],
        "imbalance_percent": imbalance["imbalance_percent"],
        "status": imbalance["status"],
        "suggestion": imbalance["suggestion"],
        "segment_results": specific_results,
        "imbalance_warnings": imbalance_warnings,
        "shared_segment_ids": list(shared_segment_ids),
        "label": label,
        "sink_node": sink_node,
    }


# ===========================================================================
# V8.1：环状管网 Hardy-Cross 求解器
# ===========================================================================
def _node_type_map(project: ProjectInput) -> dict[str, str]:
    """读取前端 nodes 的节点类型（boiler / junction / radiator / return 等）。"""
    node_types: dict[str, str] = {}
    for item in project.nodes or []:
        if isinstance(item, dict) and item.get("id") is not None:
            node_types[_node_key(item["id"])] = str(item.get("type") or "").strip().lower()
    return node_types


def _detect_heat_sources(graph: nx.DiGraph, project: ProjectInput) -> list[str]:
    """识别热源节点：优先使用前端标记的 boiler 类型；否则回退到入度为 0 的节点。"""
    node_types = _node_type_map(project)
    explicit = sorted(
        node for node in graph.nodes
        if node_types.get(node) in ("boiler", "heat_source", "source", "热源")
    )
    if explicit:
        return explicit
    return sorted(node for node in graph.nodes if graph.in_degree(node) == 0)


def _loop_segment_records(graph: nx.DiGraph, project: ProjectInput) -> list[dict]:
    """把 graph 的每条边映射到管段，并赋予初始流量 heat_load_to_flow(Q)。"""
    segment_by_str = {str(segment.segment_id): segment for segment in project.segments}
    records: list[dict] = []
    seen_pairs: set[frozenset] = set()
    for source, target, data in graph.edges(data=True):
        segment_id = int(data["segment_id"])
        segment = segment_by_str.get(str(segment_id))
        if segment is None:
            raise ValueError(f"拓扑边引用的管段 {segment_id} 不在 segments 列表中")
        pair = frozenset((source, target))
        if pair in seen_pairs:
            raise ValueError("同一对节点之间存在多条并联管段，当前 Hardy-Cross 求解器暂不支持，请合并或调整拓扑")
        seen_pairs.add(pair)
        records.append(
            {
                "sid": segment_id,
                "u": source,
                "v": target,
                "segment": segment,
                "initial_flow": heat_load_to_flow(segment.Q),
                "dn": None,
            }
        )
    return records


def _loop_resistance(record: dict, flow_kg_h: float, project: ProjectInput) -> dict:
    """给定有符号流量，返回 engine 计算的阻力结果（流量取绝对值，方向另由回路符号处理）。"""
    magnitude = abs(float(flow_kg_h))
    safe_magnitude = max(magnitude, 1e-6)
    return _calc_segment(record["segment"], safe_magnitude, record["dn"], project)


def _loop_derivative(record: dict, flow_kg_h: float, project: ProjectInput) -> float:
    """d(dP_total)/d(|G|)，用中心差分基于冻结 engine 的阻力函数计算。"""
    magnitude = abs(float(flow_kg_h))
    step = max(magnitude * 1e-3, 1e-4)
    high = magnitude + step
    low = magnitude - step
    if low <= 1e-6:
        low = max(magnitude * 0.5, 1e-6)
        high = magnitude + (magnitude - low if magnitude > low else step)
        if high <= low:
            return 1e-6
    try:
        high_loss = _loop_resistance(record, high, project)["dP_total"]
        low_loss = _loop_resistance(record, low, project)["dP_total"]
    except ValueError:
        # 流量接近 0 时部分管径区间可能不在插值表覆盖范围；用正向差分兜底
        base_loss = _loop_resistance(record, max(magnitude, 1e-6), project)["dP_total"]
        return max(base_loss / max(magnitude, 1e-6), 1e-6)
    return max((high_loss - low_loss) / (high - low), 1e-6)


def solve_looped_network(graph: nx.DiGraph, project: ProjectInput) -> dict:
    """Hardy-Cross 环状管网流量平差求解器（V8.1）。

    步骤：
    1. 由 networkx.cycle_basis() 求所有基本回路；多热源时增加“等压虚拟节点”连接各热源，
       把热源间压差闭合条件转化为包含虚拟边的回路；
    2. 为每条管段赋予由热负荷换算的初始流量，并按初始流量选径（锁定段使用 dn_locked）；
    3. 对每个基本回路计算压力闭合差 F = Σ±dP_total，按 ΔG = -F / Σ(dP/d|G|) 修正回路流量；
    4. 直到所有回路 |F| < LOOP_CONVERGENCE_TOLERANCE_PA 或达到最大迭代次数。

    返回 dict 与 CalculationResponse 对齐（loop 模式无 branch 拓扑 / Part B）。
    """
    sources = _detect_heat_sources(graph, project)
    has_directed_cycle = not nx.is_directed_acyclic_graph(graph)
    if not sources:
        if has_directed_cycle:
            raise ValueError("未找到热源：当前环状管网没有标记锅炉节点或入度为 0 的热源，请检查拓扑")
        raise ValueError("未找到热源（入度为 0 的节点）：请检查管网是否缺少锅炉节点或连线方向")

    records = _loop_segment_records(graph, project)
    if not records:
        raise ValueError("管网没有可计算的管段，无法进行 Hardy-Cross 平差")

    # 平均比摩阻：沿用教材重力循环系统的计算口径
    dP_prime = engine.compute_driving_pressure(project.H, project.rho_h, project.rho_g, project.dPf)
    total_length = sum(segment.l for segment in project.segments)
    R_pj = engine.compute_average_Rpj(dP_prime, total_length, DEFAULT_ALPHA)

    # 选径：锁定段固定；未锁定段按初始流量选径并固定（Hardy-Cross 迭代过程中不改管径）
    for record in records:
        segment = record["segment"]
        initial_magnitude = max(abs(record["initial_flow"]), 1e-3)
        if segment.dn_locked is not None:
            record["dn"] = segment.dn_locked
        else:
            record["dn"] = data_loader.select_dn(initial_magnitude, R_pj)["dn"]
        # 预校验该管径能覆盖初始流量（engine 内部会再校验），避免迭代中途才失败
        _loop_resistance(record, record["initial_flow"], project)

    # 无向图 + 虚拟等压节点（多热源）
    undirected = nx.Graph()
    undirected.add_nodes_from(graph.nodes)
    pair_to_record: dict[frozenset, dict] = {}
    for record in records:
        undirected.add_edge(record["u"], record["v"])
        pair_to_record[frozenset((record["u"], record["v"]))] = record

    cycle_graph = undirected.copy()
    virtual_edges: set[frozenset] = set()
    if len(sources) > 1:
        cycle_graph.add_node(VIRTUAL_PRESSURE_NODE)
        for source in sources:
            cycle_graph.add_edge(VIRTUAL_PRESSURE_NODE, source)
            virtual_edges.add(frozenset((VIRTUAL_PRESSURE_NODE, source)))

    cycles = nx.cycle_basis(cycle_graph)
    if not cycles:
        raise ValueError("未找到基本回路：当前拓扑不是有效的环状管网，请检查连线")

    def build_cycle_terms(cycle_nodes: list[str]) -> list[tuple[int, int] | None]:
        terms: list[tuple[int, int] | None] = []
        count = len(cycle_nodes)
        for index in range(count):
            left = cycle_nodes[index]
            right = cycle_nodes[(index + 1) % count]
            pair = frozenset((left, right))
            if pair in virtual_edges:
                terms.append(None)  # 虚拟等压边界：阻力为 0
                continue
            record = pair_to_record.get(pair)
            if record is None:
                raise RuntimeError(f"回路边 {left}->{right} 未匹配到管段")
            orientation = 1 if (record["u"] == left and record["v"] == right) else -1
            terms.append((record["sid"], orientation))
        return terms

    cycle_terms = [build_cycle_terms(cycle) for cycle in cycles]
    flows: dict[int, float] = {record["sid"]: record["initial_flow"] for record in records}
    record_by_sid = {record["sid"]: record for record in records}

    def evaluate_cycle(terms: list[tuple[int, int] | None]) -> tuple[float, float]:
        imbalance = 0.0
        derivative_sum = 0.0
        for term in terms:
            if term is None:
                continue
            segment_id, orientation = term
            record = record_by_sid[segment_id]
            flow = flows[segment_id]
            resistance = _loop_resistance(record, flow, project)
            if orientation * flow >= 0:
                imbalance += resistance["dP_total"]
            else:
                imbalance -= resistance["dP_total"]
            derivative_sum += _loop_derivative(record, flow, project)
        return imbalance, derivative_sum

    converged = False
    iteration = 1
    for iteration in range(1, LOOP_MAX_ITERATIONS + 1):
        max_closure = 0.0
        corrections: list[tuple[list[tuple[int, int] | None], float]] = []
        for terms in cycle_terms:
            imbalance, derivative_sum = evaluate_cycle(terms)
            max_closure = max(max_closure, abs(imbalance))
            if derivative_sum > 1e-12 and abs(imbalance) > 1e-12:
                corrections.append((terms, -imbalance / derivative_sum))
        if max_closure <= LOOP_CONVERGENCE_TOLERANCE_PA:
            converged = True
            break
        for terms, delta_flow in corrections:
            for term in terms:
                if term is None:
                    continue
                segment_id, orientation = term
                flows[segment_id] += orientation * delta_flow

    # 迭代结束后的真实闭合差（用于结果展示）
    final_closure = 0.0
    for terms in cycle_terms:
        imbalance, _ = evaluate_cycle(terms)
        final_closure = max(final_closure, abs(imbalance))
    if final_closure <= LOOP_CONVERGENCE_TOLERANCE_PA:
        converged = True

    # 每根管段的最终计算结果（保持与 project.segments 相同的顺序）
    segment_results: list[dict] = []
    for segment in project.segments:
        segment_id = int(segment.segment_id)
        record = record_by_sid.get(segment_id)
        if record is None:
            raise RuntimeError(f"管段 {segment_id} 未参与环状管网求解")
        signed_flow = flows[segment_id]
        resistance = _loop_resistance(record, signed_flow, project)
        item = _segment_result(segment, abs(signed_flow), record["dn"], resistance, set())
        item["G"] = signed_flow  # 环状模式下保留有符号流量，便于识别反向流
        segment_results.append(item)

    # 以首个热源为根，沿生成树累加节点水头，取最大水头差作为“最远节点累计阻力”
    adjacency: dict[str, list[dict]] = {node: [] for node in graph.nodes}
    for record in records:
        adjacency[record["u"]].append(record)
        adjacency[record["v"]].append(record)
    heads: dict[str, float] = {sources[0]: 0.0}
    queue = [sources[0]]
    while queue:
        current = queue.pop(0)
        for record in adjacency[current]:
            other = record["v"] if record["u"] == current else record["u"]
            if other in heads:
                continue
            signed_flow = flows[record["sid"]]
            loss = _loop_resistance(record, signed_flow, project)["dP_total"]
            head_change = -loss if signed_flow >= 0 else loss  # 沿 u->v 方向流动时水头下降
            if record["u"] == current:
                heads[other] = heads[current] + head_change
            else:
                heads[other] = heads[current] - head_change
            queue.append(other)
    sum_resistance = max(heads.values()) - min(heads.values()) if heads else 0.0
    margin = (dP_prime - sum_resistance) / dP_prime if dP_prime > 0 else 0.0

    warnings: list[str] = [
        "环状管网 Hardy-Cross 求解完成："
        f"基本回路 {len(cycles)} 个，热源 {len(sources)} 个，"
        f"最大闭合差 {final_closure:.6f} Pa，迭代 {iteration} 次"
    ]
    if len(sources) > 1:
        warnings.append("多热源按等压边界处理（虚拟定压节点），迭代中保持各热源节点水头不变")
    if not converged:
        warnings.append(
            f"Hardy-Cross 在最大迭代次数 {LOOP_MAX_ITERATIONS} 内未达到闭合差阈值 "
            f"{LOOP_CONVERGENCE_TOLERANCE_PA} Pa，当前最大闭合差 {final_closure:.6f} Pa"
        )

    return {
        "segment_results": segment_results,
        "dP_prime": dP_prime,
        "total_length": total_length,
        "R_pj": R_pj,
        "sum_resistance": sum_resistance,
        "margin": margin,
        "status": "converged" if converged else "not_converged",
        "iteration": max(1, iteration),
        "adjustment_log": [],
        "imbalance_warnings": [],
        "warnings": warnings,
        "part_b": None,
        "parallel_loops": [],
        "topology": None,
    }


def _run_graph_mode(project: ProjectInput) -> dict:
    """V4 动态拓扑模式：自动寻优 Loop1 + 自动识别所有并联环路。"""
    topology = analyze_tree_topology(project)
    segment_by_str = {str(segment.segment_id): segment for segment in project.segments}

    loop1_ids = [int(sid) for sid in topology["loop1"]["segment_ids"]]
    loop1_segments = [segment_by_str[str(sid)] for sid in loop1_ids]

    union_shared: set[int] = set()
    for loop in topology["parallel_loops"]:
        union_shared.update(int(sid) for sid in loop["shared_segment_ids"])

    loop_a = _run_loop_a(project, loop1_segments, union_shared)

    parallel_results: list[dict] = []
    imbalance_warnings: list[str] = []
    for loop in topology["parallel_loops"]:
        path_segments = [segment_by_str[str(sid)] for sid in loop["segment_ids"]]
        parallel = _run_parallel_loop(
            project,
            path_segments,
            loop_a,
            loop["shared_segment_ids"],
            label=f"Loop@{loop['sink_node']}",
            sink_node=loop["sink_node"],
        )
        parallel_results.append(parallel)
        imbalance_warnings.extend(parallel["imbalance_warnings"])

    return {
        **loop_a,
        "imbalance_warnings": imbalance_warnings,
        "part_b": parallel_results[0] if parallel_results else None,
        "parallel_loops": parallel_results,
        "topology": topology,
    }


def _run_legacy_mode(project: ProjectInput) -> dict:
    """旧版闭环兼容模式：segments 顺序即最不利环路，shared_segments 为共用段。"""
    legacy_shared = {int(sid) for sid in project.shared_segments}
    loop_a = _run_loop_a(project, project.segments, legacy_shared)

    part_b = None
    imbalance_warnings: list[str] = []
    if project.shared_segments:
        shared_list = sorted(legacy_shared)
        part_b = _run_parallel_loop(
            project,
            project.segments,
            loop_a,
            shared_list,
            label=f"Part B shared_segments={shared_list}",
            sink_node=None,
        )
        imbalance_warnings = list(part_b["imbalance_warnings"])

    return {
        **loop_a,
        "imbalance_warnings": imbalance_warnings,
        "part_b": part_b,
        "parallel_loops": [],
        "topology": None,
    }


def run_hydraulic_calculation(project: ProjectInput) -> dict:
    """执行水力计算。

    - 图模式（nodes+edges 非空）：
      * 单热源枝状 DAG → 原 Part A / Part B 流程（V7 结果保持不变）；
      * 环状管网或多热源 → V8.1 Hardy-Cross 流量平差；
    - 若请求显式携带 nodes/edges 但拓扑为空或缺一边，说明拓扑不完整，直接抛 ValueError
      → HTTP 422；前端不得再把“不完整拓扑”静默改成空拓扑后回退旧算法。
    - 完全未携带 topology 字段的旧版请求仅作为显式 API 兼容入口保留（前端不再走此路径）。
    """
    topology_fields_supplied = "nodes" in project.model_fields_set or "edges" in project.model_fields_set
    if topology_fields_supplied and (not project.nodes or not project.edges):
        raise ValueError(
            "拓扑不完整：nodes 与 edges 必须同时提供，且所有管段必须对应一条边；"
            "已禁止静默回退旧算法。请检查画布上的孤立节点、断头连线或缺失管段。"
        )
    if bool(project.nodes) != bool(project.edges):
        raise ValueError(
            "拓扑不完整：nodes 与 edges 必须同时提供，且所有管段必须对应一条边；"
            "已禁止静默回退旧算法。请检查画布上的孤立节点、断头连线或缺失管段。"
        )
    if project.nodes and project.edges:
        graph, _ = build_topology_graph(project)
        sources = _detect_heat_sources(graph, project)
        has_directed_cycle = not nx.is_directed_acyclic_graph(graph)
        # V8.1：环状管网或多热源 → Hardy-Cross；单热源枝状 DAG → 原 Part A/Part B 流程
        if has_directed_cycle or len(sources) > 1:
            return solve_looped_network(graph, project)
        return _run_graph_mode(project)
    return _run_legacy_mode(project)

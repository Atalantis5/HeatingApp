# -*- coding: utf-8 -*-
"""topology.graph_builder — NetworkX 建图、最不利环路与应用层拓扑校验。"""
from __future__ import annotations

import networkx as nx

from models import ProjectInput

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


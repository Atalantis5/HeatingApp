# -*- coding: utf-8 -*-
"""controller.py — 水力计算统一调度入口（V9.0.2 结构拆分）。

具体算法位于:
- hydraulics/legacy_dag.py  —— 枝状 DAG 的 Part A / Part B；
- hydraulics/hardy_cross.py —— 环状/多热源 Hardy-Cross；
- topology/graph_builder.py —— NetworkX 建图与拓扑解析。

这里只做请求条件判断与模式分发，不复制任何计算公式。
"""
from __future__ import annotations

import networkx as nx

from hydraulics.common import (
    LOOP_CONVERGENCE_TOLERANCE_PA,
    LOOP_MAX_ITERATIONS,
    heat_load_to_flow,
)
from hydraulics.hardy_cross import solve_looped_network
from hydraulics.legacy_dag import _run_graph_mode, _run_legacy_mode
from models import ProjectInput
from topology.graph_builder import _detect_heat_sources, build_topology_graph, validate_topology_graph

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

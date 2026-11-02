# -*- coding: utf-8 -*-
"""api.calculation — /validate-topology 与 /calculate 路由。"""
from __future__ import annotations

import networkx as nx
from fastapi import APIRouter, Depends

import controller
from auth import get_current_user
from models import CalculationResponse, ErrorResponse, ProjectInput, TopologyResponse
from models_user import User

router = APIRouter()

ERROR_RESPONSES = {
    422: {"model": ErrorResponse, "description": "前置条件 / 输入校验失败"},
    500: {"model": ErrorResponse, "description": "后置条件违例或内部计算失败"},
}

# ---------------------------------------------------------------------------
# 拓扑校验
# ---------------------------------------------------------------------------
def build_closed_loop_graph(project: ProjectInput) -> nx.DiGraph:
    """旧版兼容：按 segments 输入顺序构造闭环有向图（相邻段连边 + 末段→首段闭合）。"""
    node_ids = [segment.segment_id for segment in project.segments]
    graph = nx.DiGraph()
    graph.add_nodes_from(node_ids)
    closing_tail = node_ids[1:] + node_ids[:1]
    graph.add_edges_from(zip(node_ids, closing_tail))
    return graph


def _validate_legacy_closed_loop(project: ProjectInput) -> TopologyResponse:
    """旧版兼容：segments 顺序闭环校验（保持既有客户端行为）。"""
    node_ids = [segment.segment_id for segment in project.segments]
    graph = build_closed_loop_graph(project)

    node_count = graph.number_of_nodes()
    edge_count = graph.number_of_edges()
    expected_count = len(project.segments)
    is_strongly_connected = bool(nx.is_strongly_connected(graph))
    closed_loop = bool(node_count >= 1 and edge_count == node_count)
    shared_subset_ok = set(project.shared_segments).issubset(set(node_ids))

    errors: list[str] = []
    if node_count != expected_count:
        errors.append(f"节点数与 segments 数量不一致：{node_count} != {expected_count}")
    if edge_count != expected_count:
        errors.append(f"边数与闭环要求不一致：{edge_count} != {expected_count}")
    if not is_strongly_connected:
        errors.append("闭环有向图不是强连通图")
    if not shared_subset_ok:
        errors.append("shared_segments 不是 segments 的子集")

    valid = not errors
    return TopologyResponse(
        valid=valid,
        node_count=node_count,
        edge_count=edge_count,
        is_strongly_connected=is_strongly_connected,
        closed_loop=closed_loop,
        shared_subset_ok=shared_subset_ok,
        message="闭环拓扑校验通过" if valid else "闭环拓扑校验失败",
        errors=errors,
        mode="legacy_closed_loop",
    )


@router.post("/validate-topology", response_model=TopologyResponse, responses=ERROR_RESPONSES)
def validate_topology(project: ProjectInput) -> TopologyResponse:
    """校验拓扑：图模式走 NetworkX DAG/单热源/末端校验；旧版走闭环强连通校验。"""
    if project.nodes and project.edges:
        return TopologyResponse(**controller.validate_topology_graph(project))
    return _validate_legacy_closed_loop(project)


# ---------------------------------------------------------------------------
# 水力计算
# ---------------------------------------------------------------------------
@router.post("/calculate", response_model=CalculationResponse, responses=ERROR_RESPONSES)
def calculate(
    project: ProjectInput,
    current_user: User = Depends(get_current_user),
) -> CalculationResponse:
    """执行 Part A 最不利环路 + Part B 并联环路计算（须登录）。"""
    result = controller.run_hydraulic_calculation(project)
    return CalculationResponse(**result)

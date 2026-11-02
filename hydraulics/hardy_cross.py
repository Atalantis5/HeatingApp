# -*- coding: utf-8 -*-
"""hydraulics.hardy_cross — 环状/多热源管网 Hardy-Cross 流量平差。"""
from __future__ import annotations

import networkx as nx

import data_loader
import engine
from models import ProjectInput

from hydraulics.common import (
    DEFAULT_ALPHA,
    LOOP_CONVERGENCE_TOLERANCE_PA,
    LOOP_MAX_ITERATIONS,
    VIRTUAL_PRESSURE_NODE,
    _calc_segment,
    _segment_result,
    heat_load_to_flow,
)
from topology.graph_builder import _detect_heat_sources

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

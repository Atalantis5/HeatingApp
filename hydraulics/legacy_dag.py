# -*- coding: utf-8 -*-
"""hydraulics.legacy_dag — 枝状 DAG（Loop1/Loop4）Part A/Part B 平差逻辑。"""
from __future__ import annotations

import data_loader
import engine
from models import ProjectInput

from hydraulics.common import (
    CONVERGENCE_MARGIN,
    DEFAULT_ALPHA,
    IMBALANCE_LIMIT,
    _calc_segment,
    _next_larger_dn,
    _segment_result,
    _shrink_candidate,
    heat_load_to_flow,
)
from topology.graph_builder import analyze_tree_topology

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

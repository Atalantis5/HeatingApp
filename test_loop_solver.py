# -*- coding: utf-8 -*-
"""test_loop_solver.py — V8.1 Hardy-Cross 环状管网求解器测试

覆盖：
1. 单环（1 个基本回路 + 1 个末端支路）Hardy-Cross 收敛性：
   - 返回 status=converged，闭合差 < 0.1 Pa；
   - 与“手工回路闭合方程 + 二分法”求解结果一致（流量误差 < 1e-3 kg/h）；
   - 节点流量守恒（末端支路流量 = 环内两股流入之和）。
2. 多热源（虚拟等压边界）收敛性：
   - 两个锅炉热源 → 管网含虚拟等压节点，仍能收敛；
   - warnings 中包含 Hardy-Cross 求解与多热源说明。

运行：pytest test_loop_solver.py -q
"""
from __future__ import annotations

import pytest

import engine
import controller
from models import ProjectInput

BASE = {"H": 3, "rho_h": 977.81, "rho_g": 961.92, "dPf": 350, "rho_dynamic": 983, "max_iter": 20}


def _segment(segment_id: int, Q: float, length: float, zeta_sum: float = 2.0) -> dict:
    return {
        "segment_id": segment_id,
        "Q": Q,
        "l": length,
        "zeta_sum": zeta_sum,
        "dn_locked": None,
    }


def _single_loop_payload() -> dict:
    return {
        **BASE,
        "nodes": [
            {"id": "B", "type": "boiler"},
            {"id": "A", "type": "junction"},
            {"id": "C", "type": "junction"},
            {"id": "D", "type": "radiator"},
        ],
        "edges": [
            {"id": "e1", "source": "B", "target": "A", "segment_id": 1},
            {"id": "e2", "source": "A", "target": "C", "segment_id": 2},
            {"id": "e3", "source": "C", "target": "B", "segment_id": 3},
            {"id": "e4", "source": "C", "target": "D", "segment_id": 4},
        ],
        "segments": [
            _segment(1, 3000, 20),
            _segment(2, 3000, 30),
            _segment(3, 1500, 10),
            _segment(4, 1500, 5),
        ],
    }


def _manual_loop_solution(dn_map: dict[int, str]) -> dict[int, float]:
    """独立手工求解：回路闭合方程 f1(q1) + f2(q2) - f3(|q3|) = 0。

    由热负荷换算得到初始流量关系：
      q1 = q2 = 103.2 + Δ，q3 = 51.6 + Δ，q4 = 51.6，
    用二分法求 Δ，使回路压力闭合差为 0（不使用 controller 的 Hardy-Cross 流程）。
    """
    q1 = q2 = controller.heat_load_to_flow(3000)  # 103.2 kg/h
    q3 = controller.heat_load_to_flow(1500)       # 51.6 kg/h
    q4 = q3

    def signed_head(flow: float, dn: str, length: float) -> float:
        magnitude = max(abs(flow), 1e-6)
        loss = engine.compute_segment_resistance(1000.0, magnitude, length, dn, 2.0, rho=983.0)["dP_total"]
        return loss if flow >= 0 else -loss

    def closure(delta: float) -> float:
        # 回路 B->A->C->B 的有符号水头闭合差（流量为负表示实际反向）
        return (
            signed_head(q1 + delta, dn_map[1], 20)
            + signed_head(q2 + delta, dn_map[2], 30)
            + signed_head(q3 + delta, dn_map[3], 10)
        )

    # 扫描搜索变号区间（流量可能反向，故按有符号水头计算）
    lower, upper = -90.0, 120.0
    step = 1.0
    left = lower
    right = lower + step
    while right <= upper:
        if closure(left) * closure(right) <= 0:
            break
        left, right = right, right + step
    else:
        raise AssertionError("手工二分法未找到回路闭合方程变号区间")
    low, high = left, right
    for _ in range(200):
        middle = (low + high) / 2
        if closure(low) * closure(middle) <= 0:
            high = middle
        else:
            low = middle
    delta = (low + high) / 2
    return {1: q1 + delta, 2: q2 + delta, 3: q3 + delta, 4: q4}


# ---------------------------------------------------------------------------
# [1] 单环收敛性与手工解对比
# ---------------------------------------------------------------------------
def test_single_loop_hardy_cross_matches_manual_solution():
    project = ProjectInput(**_single_loop_payload())
    result = controller.run_hydraulic_calculation(project)

    assert result["status"] == "converged"
    assert 1 <= result["iteration"] <= controller.LOOP_MAX_ITERATIONS
    assert any("Hardy-Cross" in warning for warning in result["warnings"])
    assert result["part_b"] is None and result["topology"] is None

    flows = {int(item["segment_id"]): float(item["G"]) for item in result["segment_results"]}
    dn_map = {int(item["segment_id"]): item["dn"] for item in result["segment_results"]}
    assert len(flows) == 4
    assert dn_map == {1: "DN20", 2: "DN20", 3: "DN15", 4: "DN15"}

    # 手工回路闭合差
    losses = {int(item["segment_id"]): item["dP_total"] for item in result["segment_results"]}
    closure = losses[1] + losses[2] - losses[3]
    assert abs(closure) < controller.LOOP_CONVERGENCE_TOLERANCE_PA, closure

    # 与手工二分法结果一致
    manual = _manual_loop_solution(dn_map)
    for segment_id in (1, 2, 3, 4):
        assert flows[segment_id] == pytest.approx(manual[segment_id], abs=1e-3)

    # 节点流量守恒：C 点流入 q2 + |q3| = 末端支路 q4
    assert flows[2] + (-flows[3]) == pytest.approx(flows[4], abs=1e-6)


# ---------------------------------------------------------------------------
# [2] 多热源（虚拟等压边界）
# ---------------------------------------------------------------------------
def test_multi_source_hardy_cross_converges():
    payload = {
        **BASE,
        "nodes": [
            {"id": "S1", "type": "boiler"},
            {"id": "S2", "type": "boiler"},
            {"id": "T", "type": "radiator"},
        ],
        "edges": [
            {"id": "e1", "source": "S1", "target": "T", "segment_id": 1},
            {"id": "e2", "source": "S2", "target": "T", "segment_id": 2},
        ],
        "segments": [_segment(1, 2000, 10), _segment(2, 2000, 20)],
    }
    result = controller.run_hydraulic_calculation(ProjectInput(**payload))
    assert result["status"] == "converged"
    assert any("Hardy-Cross" in warning for warning in result["warnings"])
    assert any("多热源" in warning for warning in result["warnings"])
    assert len(result["segment_results"]) == 2
    assert all(item["dn"] for item in result["segment_results"])

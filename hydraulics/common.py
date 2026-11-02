# -*- coding: utf-8 -*-
"""hydraulics.common — 水力计算共享常量、换算与单管段组装。"""
from __future__ import annotations

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


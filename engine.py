# -*- coding: utf-8 -*-
"""engine.py — 核心水力计算纯函数（05_engine/04 ProgramFile，task-3 交付）。

本模块只包含若干“单次公式求值”的纯函数，供后续计算管线调用。
实现约束（Lead 口径，验收第 2 条）：
- 函数体内不使用任何循环结构（含列表推导、生成器表达式）；每个函数都是一次性公式求值；
- 不包含 Web 路由层代码，也不包含数据模型框架代码；
- 每个函数都有前置条件检查（非法输入抛 ValueError）与后置条件检查
  （后置违例抛 RuntimeError，不做静默 clamp）；
- 计算结果不做 round，原样返回。

口径说明（重要，验收与报告依据）：
1) lookup_pipe 适配：task-2 仲裁后 data_loader.lookup_pipe(G, R_target) 要求给出
   目标比摩阻（R_target=None 已废弃），而 compute_segment_resistance 的 dn 由外部
   给定。因此先 data_loader.interpolate_pipe(G) 取该 dn 的插值 Rm 作为 R_target，
   再调用 data_loader.lookup_pipe(G, Rm_dn) 取回 v / Rm；随后后置断言
   lookup["dn"] == 传入 dn，不一致抛 RuntimeError。契约出处：
   schema_manifest.json 的 selection_contract（select_dn 语义与 lookup_pipe 兼容壳）
   以及 task-3 任务书“已由 Lead 判定的实现口径”。
2) 动压头密度口径：契约默认 rho=961.92 kg/m^3；而《默认模型的计算典例.md》第 25 行
   的动压头示例使用 rho=983（1/2 × 983 × 0.1² = 4.915 Pa）。本模块不替调用方决定
   密度口径，rho 由调用方显式传入；测试中 MD 锚点用 rho=983.0 复现，另有测试在默认
   rho=961.92 下按契约公式做自身一致性验证。
3) Q 参数：本步不做“热负荷 → 流量”换算（无供回水温差 Δt 参数），Q 仅保留接口语义
   并做“正的有限数”前置校验；本步计算所需的流量由 G 直接传入。
"""

from __future__ import annotations

import math

import data_loader

# 物理/工程常量（单一来源，便于审计）
GRAVITY_ACCELERATION = 9.81      # m/s^2
DEFAULT_RHO = 961.92             # kg/m^3，契约默认水密度
IMBALANCE_THRESHOLD = 0.15       # ±15% 不平衡率阈值


# ---------------------------------------------------------------------------
# 前置/后置检查辅助（无循环结构）
# ---------------------------------------------------------------------------
def _to_finite_float(value, name: str) -> float:
    """前置检查：把输入转成有限 float；非法输入抛 ValueError。"""
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是有限数值，不能是布尔值：{value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须可转换为数值：{value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数值：{value!r}")
    return number


def _require_positive(value, name: str) -> float:
    """前置检查：有限且 > 0。"""
    number = _to_finite_float(value, name)
    if number <= 0.0:
        raise ValueError(f"{name} 必须为正数：{value!r}")
    return number


def _require_nonnegative(value, name: str) -> float:
    """前置检查：有限且 >= 0。"""
    number = _to_finite_float(value, name)
    if number < 0.0:
        raise ValueError(f"{name} 必须为非负数：{value!r}")
    return number


def _require_text(value, name: str) -> str:
    """前置检查：非空字符串（去除首尾空白后）。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串：{value!r}")
    return value.strip()


def _ensure_positive(number: float, name: str) -> float:
    """后置检查：结果必须为正的有限数，否则 RuntimeError。"""
    if not math.isfinite(number) or number <= 0.0:
        raise RuntimeError(
            f"后置条件失败：{name} 必须为正的有限数，实际 {number!r}"
        )
    return number


def _ensure_nonnegative(number: float, name: str) -> float:
    """后置检查：结果必须为非负的有限数，否则 RuntimeError。"""
    if not math.isfinite(number) or number < 0.0:
        raise RuntimeError(
            f"后置条件失败：{name} 必须为非负的有限数，实际 {number!r}"
        )
    return number


def _ensure_close(actual: float, expected: float, name: str) -> float:
    """后置检查：结果须与公式重算值一致（不让误差/静默修正混入）。"""
    if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
        raise RuntimeError(
            f"后置条件失败：{name} 不一致，actual={actual!r}, expected={expected!r}"
        )
    return actual


# ---------------------------------------------------------------------------
# 1) 重力循环作用压力
# ---------------------------------------------------------------------------
def compute_driving_pressure(H, rho_h, rho_g, dPf) -> float:
    """计算重力循环系统作用压力。

    公式（MW）：ΔP' = 9.81 * H * (rho_h - rho_g) + dPf

    参数
    ----
    H : float
        锅炉到散热器的高度，单位 m；必须为正的有限数。
    rho_h : float
        回水密度（热媒冷却后），单位 kg/m^3；正的有限数，且必须 > rho_g。
    rho_g : float
        供水密度，单位 kg/m^3；正的有限数。
    dPf : float
        水冷却等附加压力，单位 Pa；有限且 >= 0。

    返回
    ----
    float：作用压力，单位 Pa（不做 round）。

    异常
    ----
    ValueError：任一参数非法，或前置条件 rho_h > rho_g 不满足。
    RuntimeError：后置条件失败（结果非正的有限数）。
    """
    height = _require_positive(H, "H")
    rho_hot = _require_positive(rho_h, "rho_h")
    rho_cold = _require_positive(rho_g, "rho_g")
    extra = _require_nonnegative(dPf, "dPf")

    if rho_hot <= rho_cold:
        raise ValueError(
            f"前置条件失败：rho_h 必须大于 rho_g，实际 rho_h={rho_hot!r}, rho_g={rho_cold!r}"
        )

    pressure = GRAVITY_ACCELERATION * height * (rho_hot - rho_cold) + extra
    return _ensure_positive(pressure, "driving_pressure")


# ---------------------------------------------------------------------------
# 2) 平均比摩阻
# ---------------------------------------------------------------------------
def compute_average_Rpj(dP_prime, total_length, alpha=0.5) -> float:
    """计算最不利环路 / 专用管段的平均比摩阻。

    公式（MW）：R_pj = alpha * dP_prime / total_length

    参数
    ----
    dP_prime : float
        环路作用压力，单位 Pa；必须为正的有限数。
    total_length : float
        环路总长度，单位 m；必须为正的有限数。
    alpha : float
        沿程阻力占比（重力循环系统经验值，教材给定 alpha=0.5），
        必须为有限的 (0, 1] 区间数值。

    返回
    ----
    float：平均比摩阻 R_pj，单位 Pa/m（不做 round）。

    异常
    ----
    ValueError：任一参数非法。
    RuntimeError：后置条件失败（结果非正的有限数）。
    """
    pressure = _require_positive(dP_prime, "dP_prime")
    length = _require_positive(total_length, "total_length")
    ratio = _to_finite_float(alpha, "alpha")
    if not 0.0 < ratio <= 1.0:
        raise ValueError(f"alpha 必须位于 (0, 1] 区间：{alpha!r}")

    average_rpj = ratio * pressure / length
    return _ensure_positive(average_rpj, "average_Rpj")


# ---------------------------------------------------------------------------
# 3) 管段阻力
# ---------------------------------------------------------------------------
def compute_segment_resistance(Q, G, l, dn, zeta_sum, rho=DEFAULT_RHO) -> dict:
    """计算单个管段的沿程阻力、动压头、局部阻力与总阻力。

    公式（MW）：
    - dPy = Rm * l
    - dPd = rho * v^2 / 2
    - dPj = zeta_sum * dPd
    - dP_total = dPy + dPj

    参数
    ----
    Q : float
        管段热负荷，单位 W。本步**不做**热负荷 → 流量换算（无供回水温差 Δt
        参数），Q 仅保留接口语义并做“正的有限数”前置校验，不参与本函数计算；
        本步阻力计算所需的流量直接由 G 传入。
    G : float
        管段流量，单位 kg/h；必须为正的有限数。
    l : float
        管段长度，单位 m；必须为正的有限数。
    dn : str
        外部给定的公称管径标签（如 "DN65"），必须是管道表中存在的 DN，
        且该 DN 组的流量区间必须覆盖 G；否则抛 ValueError。
    zeta_sum : float
        管段局部阻力系数之和 Σξ；有限且 >= 0（可为 0）。
    rho : float
        水密度，单位 kg/m^3；默认 961.92（契约默认值）；必须为正的有限数。

    lookup_pipe 适配（契约出处：schema_manifest.json 的 selection_contract 与
    task-3 任务书“已由 Lead 判定的实现口径”）
    ---------------------------------------------------------------
    task-2 仲裁后 data_loader.lookup_pipe(G, R_target) 要求传入目标比摩阻，
    R_target=None 已废弃；而本函数的 dn 由外部给定。为满足“必须调用
    lookup_pipe”的契约，实现采用两步适配：
    1. data_loader.interpolate_pipe(G) 取该 dn 组的插值 Rm，作为 R_target；
    2. data_loader.lookup_pipe(G, Rm_dn) 取回 v / Rm；
    3. 后置断言 lookup["dn"] == 传入 dn：不一致说明 dn 与插值候选不匹配，
       抛 RuntimeError（不静默改用其它管径）。

    返回
    ----
    dict：{dn, v, Rm, dPy, dPd, dPj, dP_total}，均为未 round 的原始数值；
    dn 为传入的管径标签，v 单位 m/s，Rm 单位 Pa/m，阻力单位 Pa。

    异常
    ----
    ValueError：任一参数非法，或 dn 不存在 / 其流量区间不覆盖 G。
    RuntimeError：后置条件失败（如 lookup_pipe 返回的 dn 与传入 dn 不一致）。
    """
    _require_positive(Q, "Q")  # 接口语义保留：本步不做 Q → G 换算
    flow = _require_positive(G, "G")
    length = _require_positive(l, "l")
    density = _require_positive(rho, "rho")
    zeta = _require_nonnegative(zeta_sum, "zeta_sum")
    dn_label = _require_text(dn, "dn")

    interpolated = data_loader.interpolate_pipe(flow)
    if dn_label not in interpolated:
        raise ValueError(
            f"前置条件失败：dn={dn_label!r} 不在覆盖 G={flow!r} 的候选管径中；"
            f"候选为 {sorted(interpolated.keys())!r}"
        )
    rm_target = interpolated[dn_label]["Rm"]

    lookup = data_loader.lookup_pipe(flow, rm_target)
    if lookup.get("dn") != dn_label:
        raise RuntimeError(
            "后置条件失败：data_loader.lookup_pipe 返回的 dn 与传入 dn 不一致，"
            f"lookup_dn={lookup.get('dn')!r}, given_dn={dn_label!r}"
        )
    velocity = _ensure_positive(lookup["v"], "v")
    rm_value = _ensure_positive(lookup["Rm"], "Rm")

    dPy = rm_value * length
    dPd = density * velocity * velocity / 2.0
    dPj = zeta * dPd
    dP_total = dPy + dPj

    _ensure_positive(dPy, "dPy")
    _ensure_nonnegative(dPd, "dPd")
    _ensure_nonnegative(dPj, "dPj")
    _ensure_positive(dP_total, "dP_total")
    _ensure_close(dP_total, dPy + dPj, "dP_total == dPy + dPj")
    _ensure_close(dPj, zeta * (density * velocity * velocity / 2.0), "dPj == zeta_sum * dPd")

    return {
        "dn": dn_label,
        "v": velocity,
        "Rm": rm_value,
        "dPy": dPy,
        "dPd": dPd,
        "dPj": dPj,
        "dP_total": dP_total,
    }


# ---------------------------------------------------------------------------
# 4) 并联环路不平衡率校验
# ---------------------------------------------------------------------------
def check_imbalance(available_pressure, sum_resistance, label) -> dict:
    """校验并联环路的不平衡率（只判定，不在函数内做任何缩径/改径动作）。

    公式（MD 第三步）：
    x = (available_pressure - sum_resistance) / available_pressure

    判定规则：
    - x > 0.15：status="unbalanced"，suggestion="缩径或加装调节阀"
    - x < -0.15：status="unbalanced"，suggestion="放大管径或重新设计"
      （扩展口径：本步未在 MD 典例中出现负向越限，但按对称性一并视为不平衡）
    - -0.15 <= x <= 0.15：status="balanced"

    本函数**绝不执行缩径**或其他管径调整，只返回判定结果与建议文本，
    供上层流程决定后续动作。

    参数
    ----
    available_pressure : float
        环路资用压力，单位 Pa；必须为正的有限数。
    sum_resistance : float
        环路计算阻力，单位 Pa；必须为非负的有限数。
    label : str
        环路标签（如 "Loop4"），用于结果回显；必须为非空字符串。

    返回
    ----
    dict：包含 label / imbalance_ratio（x，未 round）/ imbalance_percent（x*100，
    未 round）/ status / suggestion，并回显 available_pressure、sum_resistance、
    threshold（0.15）。

    异常
    ----
    ValueError：任一参数非法。
    RuntimeError：后置条件失败（如 status 与阈值判定不一致）。
    """
    available = _require_positive(available_pressure, "available_pressure")
    resistance = _require_nonnegative(sum_resistance, "sum_resistance")
    loop_label = _require_text(label, "label")

    ratio = (available - resistance) / available
    percent = ratio * 100.0

    if ratio > IMBALANCE_THRESHOLD:
        status = "unbalanced"
        suggestion = "缩径或加装调节阀"
    elif ratio < -IMBALANCE_THRESHOLD:
        status = "unbalanced"
        suggestion = "放大管径或重新设计"
    else:
        status = "balanced"
        suggestion = "不平衡率在 ±15% 以内"

    if not math.isfinite(ratio):
        raise RuntimeError(f"后置条件失败：imbalance_ratio 非有限数 {ratio!r}")
    if status not in ("balanced", "unbalanced"):
        raise RuntimeError(f"后置条件失败：status 非法 {status!r}")
    if (abs(ratio) > IMBALANCE_THRESHOLD) != (status == "unbalanced"):
        raise RuntimeError(
            f"后置条件失败：status={status!r} 与 |ratio|={abs(ratio)!r} 的阈值判定不一致"
        )
    if not suggestion:
        raise RuntimeError("后置条件失败：suggestion 不得为空")

    return {
        "label": loop_label,
        "imbalance_ratio": ratio,
        "imbalance_percent": percent,
        "status": status,
        "suggestion": suggestion,
        "available_pressure": available,
        "sum_resistance": resistance,
        "threshold": IMBALANCE_THRESHOLD,
    }

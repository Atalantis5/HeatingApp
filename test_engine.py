# -*- coding: utf-8 -*-
"""test_engine.py — engine.py 核心纯函数测试（05_engine/04 ProgramFile，task-3 交付）。

覆盖：
1) compute_driving_pressure：MD 典例作用压力锚点 817.6 Pa（精确式 817.6427 Pa）；
2) compute_average_Rpj：MD 典例 R_pj 锚点 4.95 Pa/m（由 817.6427 Pa 算得 4.95541）；
3) compute_segment_resistance（rho=983，MD L25 口径）：管段7 阻力锚点
   dPy≈35.85 / dPd≈4.91 / dPj≈29.49 / dP_total≈65.34 Pa；
4) compute_segment_resistance（默认 rho=961.92，契约口径）：按公式自洽到 1e-9，
   参考值 dPy≈35.766 / dPj≈28.817 / dP_total≈64.58 Pa；
5) lookup_pipe 适配（interpolate_pipe → R_target → lookup_pipe → dn 后置断言）
   与 dn 不一致时的 RuntimeError；
6) check_imbalance：MD 锚点 (633.5, 545.67) → x≈0.1386425、status="balanced"；
   越限（正向 / 负向）→ unbalanced 与建议文本；
7) 非法输入 ValueError、返回字段契约、engine.py 无循环/无 FastAPI/Pydantic 静态检查。

两种运行方式（均真实可运行，不硬依赖 pytest）：
- pytest（环境由 Lead 部署在 .pytest_env）：
  cd "05_engine/04 ProgramFile"
  PYTHONPATH="$PWD/.pytest_env" PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
    "<python>" -m pytest test_engine.py -p no:cacheprovider -v
- 直接运行：
  PYTHONPATH="$PWD/.pytest_env" PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
    "<python>" test_engine.py

口径说明（重要）：
- 动压头密度存在两个口径：MD 典例第 25 行用 rho=983（1/2×983×0.1²=4.915）；
  engine.py 契约默认 rho=961.92。因此 MD 锚点测试显式传 rho=983.0，
  另写默认 rho=961.92 的公式自洽测试；两口径结果差异仅来自密度项。
- lookup_pipe 适配：task-2 仲裁后 lookup_pipe(G, R_target) 需目标比摩阻；
  engine 以 interpolate_pipe(G) 取给定 dn 的插值 Rm 作 R_target，再调
  lookup_pipe 取回 v/Rm，并把返回 dn 与给定 dn 的一致性作为后置断言。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# 只允许 ProgramFile 下存在 2 个新文件：禁止生成 __pycache__。
sys.dont_write_bytecode = True

import data_loader as dl
import engine as eng

BASE_DIR = Path(__file__).resolve().parent
ENGINE_PATH = BASE_DIR / "engine.py"

# 锚点与容差（task-3 任务书口径）
DRIVING_PRESSURE_ANCHOR = 817.6          # MD L14~L16：467.6 + 350
DRIVING_PRESSURE_TOL = 0.05             # Pa
RPJ_ANCHOR = 4.95                        # MD L20
RPJ_TOL = 0.05                           # Pa/m
SEGMENT_RESISTANCE_TOL = 0.5             # Pa（管段7 阻力锚点，rho=983）
SELF_CONSISTENCY_TOL = 1e-9              # 公式自身一致性

# MD 典例行基础参数
MD_H = 3.0
MD_RHO_H = 977.81
MD_RHO_G = 961.92
MD_DPF = 350.0
MD_TOTAL_LENGTH = 82.5                   # MD L19：Σl=82.5 m
MD_ALPHA = 0.5

# 管段7（MD L31 汇总表；ρ=983 为 MD L25 动压头口径）
SEG7_G = 1286.2
SEG7_Q = 37400.0                         # MD L10：Q_7 = 37400 W
SEG7_L = 15.0
SEG7_DN = "DN65"
SEG7_ZETA = 6.0
SEG7_ANCHOR = {"dPy": 35.85, "dPd": 4.91, "dPj": 29.49, "dP_total": 65.34}
SEG7_RHO_MD = 983.0

# Loop4 不平衡率锚点（MD L58、L82、L85）
IMBALANCE_AVAILABLE = 633.5
IMBALANCE_RESISTANCE = 545.67


# ---------------------------------------------------------------------------
# 断言/日志辅助
# ---------------------------------------------------------------------------
def assert_close(label, actual, expected, tolerance):
    """数值断言：|actual - expected| <= tolerance，并打印 actual vs expected。"""
    actual_f = float(actual)
    expected_f = float(expected)
    deviation = abs(actual_f - expected_f)
    print(
        f"      {label}: actual={actual_f!r} expected={expected_f!r} "
        f"|偏差|={deviation!r} 容差={tolerance!r} -> {'PASS' if deviation <= tolerance else 'FAIL'}"
    )
    assert deviation <= tolerance, (
        f"{label} 偏差超容差：actual={actual_f!r}, expected={expected_f!r}, "
        f"|偏差|={deviation!r}, 容差={tolerance!r}"
    )


def assert_equal(label, actual, expected):
    """精确相等断言（字符串/键集合/公式重算等）。"""
    print(f"      {label}: actual={actual!r} expected={expected!r} -> {'PASS' if actual == expected else 'FAIL'}")
    assert actual == expected, f"{label} 必须相等：actual={actual!r}, expected={expected!r}"


def expect_value_error(case_name, func, *args, **kwargs):
    """断言调用抛 ValueError（前置条件失败）。"""
    try:
        func(*args, **kwargs)
    except ValueError as exc:
        print(f"      {case_name}: 抛出 ValueError（符合预期）-> {exc}")
        return
    raise AssertionError(f"{case_name} 应抛 ValueError，但未抛出")


def expect_runtime_error(case_name, func, *args, **kwargs):
    """断言调用抛 RuntimeError（后置条件失败）。"""
    try:
        func(*args, **kwargs)
    except RuntimeError as exc:
        print(f"      {case_name}: 抛出 RuntimeError（符合预期）-> {exc}")
        return
    raise AssertionError(f"{case_name} 应抛 RuntimeError，但未抛出")


# ---------------------------------------------------------------------------
# 1) 作用压力锚点
# ---------------------------------------------------------------------------
def test_compute_driving_pressure():
    print("  [1] compute_driving_pressure：MD 典例作用压力锚点")
    actual = eng.compute_driving_pressure(MD_H, MD_RHO_H, MD_RHO_G, MD_DPF)
    exact = 9.81 * MD_H * (MD_RHO_H - MD_RHO_G) + MD_DPF
    print(
        f"      输入: H={MD_H!r} m, rho_h={MD_RHO_H!r}, rho_g={MD_RHO_G!r}, "
        f"dPf={MD_DPF!r} Pa（MD L14~L16）"
    )
    assert_close("driving_pressure vs 精确式", actual, exact, SELF_CONSISTENCY_TOL)
    assert_close("driving_pressure vs MD 锚点 817.6 Pa", actual, DRIVING_PRESSURE_ANCHOR, DRIVING_PRESSURE_TOL)


# ---------------------------------------------------------------------------
# 2) 平均比摩阻锚点
# ---------------------------------------------------------------------------
def test_compute_average_Rpj():
    print("  [2] compute_average_Rpj：MD 典例 R_pj 锚点")
    driving_pressure = eng.compute_driving_pressure(MD_H, MD_RHO_H, MD_RHO_G, MD_DPF)
    actual = eng.compute_average_Rpj(driving_pressure, MD_TOTAL_LENGTH, alpha=MD_ALPHA)
    exact = MD_ALPHA * driving_pressure / MD_TOTAL_LENGTH
    print(
        f"      输入: dP'={driving_pressure!r} Pa, Σl={MD_TOTAL_LENGTH!r} m, "
        f"alpha={MD_ALPHA!r}（MD L19~L20）"
    )
    assert_close("average_Rpj vs 精确式", actual, exact, SELF_CONSISTENCY_TOL)
    assert_close("average_Rpj vs MD 锚点 4.95 Pa/m", actual, RPJ_ANCHOR, RPJ_TOL)
    # 契约锚点原文：直接输入 dP'=817.6 Pa、Σl=82.5 m、alpha=0.5 时应得 ≈4.95 Pa/m
    direct = eng.compute_average_Rpj(817.6, MD_TOTAL_LENGTH, alpha=MD_ALPHA)
    assert_close(
        "average_Rpj 直接输入 817.6 Pa vs MD 锚点 4.95 Pa/m",
        direct,
        RPJ_ANCHOR,
        RPJ_TOL,
    )


# ---------------------------------------------------------------------------
# 3) 管段7 阻力锚点（rho=983，MD L25 动压头口径）
# ---------------------------------------------------------------------------
def test_segment_resistance_md_anchor_rho983():
    print("  [3] compute_segment_resistance：管段7 MD 锚点（rho=983，MD L25 口径）")
    result = eng.compute_segment_resistance(
        Q=SEG7_Q,
        G=SEG7_G,
        l=SEG7_L,
        dn=SEG7_DN,
        zeta_sum=SEG7_ZETA,
        rho=SEG7_RHO_MD,
    )
    print(
        f"      输入: Q={SEG7_Q!r} W, G={SEG7_G!r} kg/h, l={SEG7_L!r} m, "
        f"dn={SEG7_DN!r}, zeta_sum={SEG7_ZETA!r}, rho={SEG7_RHO_MD!r}"
    )
    print(f"      实际: {result!r}")
    interpolated = dl.interpolate_pipe(SEG7_G)[SEG7_DN]
    print(
        f"      对照: data_loader.interpolate_pipe({SEG7_G!r})[{SEG7_DN!r}] -> "
        f"v={interpolated['v']!r}, Rm={interpolated['Rm']!r}（G_actual={interpolated['G_actual']!r}）"
    )
    assert_equal("dn（外部给定）", result["dn"], SEG7_DN)
    assert_close("v vs 插值值", result["v"], interpolated["v"], SELF_CONSISTENCY_TOL)
    assert_close("Rm vs 插值值", result["Rm"], interpolated["Rm"], SELF_CONSISTENCY_TOL)
    print(f"      MD L31 锚点: {SEG7_ANCHOR!r}（容差 {SEGMENT_RESISTANCE_TOL} Pa）")
    assert_close("dPy vs MD 锚点 35.85 Pa", result["dPy"], SEG7_ANCHOR["dPy"], SEGMENT_RESISTANCE_TOL)
    assert_close("dPd vs MD 锚点 4.91 Pa", result["dPd"], SEG7_ANCHOR["dPd"], SEGMENT_RESISTANCE_TOL)
    assert_close("dPj vs MD 锚点 29.49 Pa", result["dPj"], SEG7_ANCHOR["dPj"], SEGMENT_RESISTANCE_TOL)
    assert_close(
        "dP_total vs MD 锚点 65.34 Pa",
        result["dP_total"],
        SEG7_ANCHOR["dP_total"],
        SEGMENT_RESISTANCE_TOL,
    )
    # dPj 严格等于 zeta_sum * dPd（公式级检查）
    assert_close("dPj vs zeta_sum*dPd（公式）", result["dPj"], SEG7_ZETA * result["dPd"], 1e-12)


# ---------------------------------------------------------------------------
# 4) 管段7 默认 rho=961.92 的契约公式自洽测试
# ---------------------------------------------------------------------------
def test_segment_resistance_default_rho_self_consistency():
    print("  [4] compute_segment_resistance：默认 rho=961.92 下契约公式自洽（1e-9）")
    result = eng.compute_segment_resistance(
        Q=SEG7_Q,
        G=SEG7_G,
        l=SEG7_L,
        dn=SEG7_DN,
        zeta_sum=SEG7_ZETA,
        # 不传 rho，使用契约默认 961.92
    )
    density = 961.92
    expected_dPy = result["Rm"] * SEG7_L
    expected_dPd = density * result["v"] ** 2 / 2.0
    expected_dPj = SEG7_ZETA * expected_dPd
    expected_total = expected_dPy + expected_dPj
    print(f"      输入: 同管段7，rho 使用默认值 {density!r}（契约口径）")
    print(f"      实际: dPy={result['dPy']!r}, dPd={result['dPd']!r}, "
          f"dPj={result['dPj']!r}, dP_total={result['dP_total']!r}")
    print(f"      公式重算: dPy={expected_dPy!r}, dPd={expected_dPd!r}, "
          f"dPj={expected_dPj!r}, dP_total={expected_total!r}")
    assert_close("dPy 自洽（1e-9）", result["dPy"], expected_dPy, SELF_CONSISTENCY_TOL)
    assert_close("dPd 自洽（1e-9）", result["dPd"], expected_dPd, SELF_CONSISTENCY_TOL)
    assert_close("dPj 自洽（1e-9）", result["dPj"], expected_dPj, SELF_CONSISTENCY_TOL)
    assert_close("dP_total 自洽（1e-9）", result["dP_total"], expected_total, SELF_CONSISTENCY_TOL)
    # 与任务书参考值（约数）对照，避免口径写错
    assert_close("dPy vs 参考 35.766 Pa", result["dPy"], 35.766, 0.01)
    assert_close("dPj vs 参考 28.817 Pa", result["dPj"], 28.817, 0.1)
    assert_close("dP_total vs 参考 64.58 Pa", result["dP_total"], 64.58, 0.1)


# ---------------------------------------------------------------------------
# 5) lookup_pipe 适配（interpolate_pipe -> R_target -> lookup_pipe -> dn 断言）
# ---------------------------------------------------------------------------
def test_segment_resistance_lookup_pipe_adaptation():
    print("  [5] lookup_pipe 适配：interpolate_pipe 取 Rm 作 R_target，再调用 lookup_pipe")
    interpolated = dl.interpolate_pipe(SEG7_G)[SEG7_DN]
    recorded = []
    original_lookup = dl.lookup_pipe

    def recording_lookup(G_value, R_target):
        lookup_result = original_lookup(G_value, R_target)
        recorded.append({"G": G_value, "R_target": R_target, "result": lookup_result})
        return lookup_result

    dl.lookup_pipe = recording_lookup
    try:
        result = eng.compute_segment_resistance(
            Q=SEG7_Q, G=SEG7_G, l=SEG7_L, dn=SEG7_DN, zeta_sum=SEG7_ZETA, rho=SEG7_RHO_MD
        )
    finally:
        dl.lookup_pipe = original_lookup

    assert_equal("lookup_pipe 调用次数", len(recorded), 1)
    call = recorded[0]
    print(
        f"      实际调用: lookup_pipe(G={call['G']!r}, R_target={call['R_target']!r}) -> "
        f"dn={call['result']['dn']!r}, v={call['result']['v']!r}, Rm={call['result']['Rm']!r}"
    )
    assert_close("R_target == interpolate_pipe(G)[dn].Rm", call["R_target"], interpolated["Rm"], 0.0)
    assert_equal("lookup 返回 dn == 传入 dn", call["result"]["dn"], SEG7_DN)
    assert_close("最终 v == lookup 返回 v", result["v"], call["result"]["v"], 0.0)
    assert_close("最终 Rm == lookup 返回 Rm", result["Rm"], call["result"]["Rm"], 0.0)


def test_segment_resistance_runtime_error_on_dn_mismatch():
    print("  [6] 后置条件：lookup_pipe 返回 dn 与传入 dn 不一致 -> RuntimeError")
    original_lookup = dl.lookup_pipe

    def wrong_lookup(G_value, R_target):
        correct = original_lookup(G_value, R_target)
        return {
            "dn": "DN999",
            "G_actual": correct["G_actual"],
            "v": correct["v"],
            "Rm": correct["Rm"],
        }

    dl.lookup_pipe = wrong_lookup
    try:
        expect_runtime_error(
            "dn 不一致",
            eng.compute_segment_resistance,
            Q=SEG7_Q,
            G=SEG7_G,
            l=SEG7_L,
            dn=SEG7_DN,
            zeta_sum=SEG7_ZETA,
            rho=SEG7_RHO_MD,
        )
    finally:
        dl.lookup_pipe = original_lookup


# ---------------------------------------------------------------------------
# 6) 不平衡率判定
# ---------------------------------------------------------------------------
def test_check_imbalance_balanced():
    print("  [7] check_imbalance：MD 锚点 (633.5, 545.67) -> balanced")
    result = eng.check_imbalance(IMBALANCE_AVAILABLE, IMBALANCE_RESISTANCE, "Loop4")
    expected_ratio = (IMBALANCE_AVAILABLE - IMBALANCE_RESISTANCE) / IMBALANCE_AVAILABLE
    expected_percent = expected_ratio * 100.0
    print(f"      实际: {result!r}")
    print(
        f"      期望: imbalance_ratio={expected_ratio!r}, imbalance_percent={expected_percent!r}, "
        f"status='balanced'（MD L85：13.8% ≤ 15%）"
    )
    assert_close("imbalance_ratio vs 公式值", result["imbalance_ratio"], expected_ratio, SELF_CONSISTENCY_TOL)
    assert_close("imbalance_percent vs 公式值", result["imbalance_percent"], expected_percent, SELF_CONSISTENCY_TOL)
    assert_close("imbalance_ratio vs 参考 0.138666", result["imbalance_ratio"], 0.138666, 1e-3)
    assert_close("imbalance_percent vs 参考 13.8%", result["imbalance_percent"], 13.8, 0.1)
    assert_equal("status", result["status"], "balanced")
    assert_equal("label", result["label"], "Loop4")
    assert_equal("threshold", result["threshold"], 0.15)
    assert isinstance(result["suggestion"], str) and result["suggestion"], "suggestion 应为非空字符串"
    assert "dn" not in result and "new_dn" not in result, "check_imbalance 不得返回改径动作"
    print(f"      suggestion（仅供参考，函数内不执行缩径）={result['suggestion']!r}")


def test_check_imbalance_unbalanced():
    print("  [8] check_imbalance：越限（正向 / 负向 / 边界）")
    over = eng.check_imbalance(100.0, 80.0, "LoopOver")
    print(f"      正越限: ratio={over['imbalance_ratio']!r}, status={over['status']!r}, "
          f"suggestion={over['suggestion']!r}")
    assert_equal("正越限 ratio", over["imbalance_ratio"], 0.2)
    assert_equal("正越限 status", over["status"], "unbalanced")
    assert_equal("正越限 suggestion", over["suggestion"], "缩径或加装调节阀")
    assert "dn" not in over and "new_dn" not in over, "check_imbalance 不得返回改径动作"

    under = eng.check_imbalance(100.0, 120.0, "LoopUnder")
    print(f"      负越限: ratio={under['imbalance_ratio']!r}, status={under['status']!r}, "
          f"suggestion={under['suggestion']!r}")
    assert_equal("负越限 ratio", under["imbalance_ratio"], -0.2)
    assert_equal("负越限 status", under["status"], "unbalanced")
    assert_equal("负越限 suggestion", under["suggestion"], "放大管径或重新设计")

    boundary = eng.check_imbalance(100.0, 85.0, "LoopBoundary")
    print(f"      边界 x=0.15: ratio={boundary['imbalance_ratio']!r}, status={boundary['status']!r}")
    assert_equal("边界 x=0.15 视为 balanced（规则为 > 0.15 才越限）", boundary["status"], "balanced")


# ---------------------------------------------------------------------------
# 7) 非法输入 ValueError
# ---------------------------------------------------------------------------
def test_invalid_inputs_raise_value_error():
    print("  [9] 非法输入：前置条件失败一律 ValueError")
    expect_value_error("driving: H=0", eng.compute_driving_pressure, 0.0, MD_RHO_H, MD_RHO_G, MD_DPF)
    expect_value_error("driving: rho_h<=rho_g", eng.compute_driving_pressure, MD_H, 961.92, 977.81, MD_DPF)
    expect_value_error("driving: dPf<0", eng.compute_driving_pressure, MD_H, MD_RHO_H, MD_RHO_G, -1.0)
    expect_value_error("driving: H=NaN", eng.compute_driving_pressure, float("nan"), MD_RHO_H, MD_RHO_G, MD_DPF)
    expect_value_error("driving: H 非数值", eng.compute_driving_pressure, "abc", MD_RHO_H, MD_RHO_G, MD_DPF)

    expect_value_error("Rpj: dP'=0", eng.compute_average_Rpj, 0.0, MD_TOTAL_LENGTH)
    expect_value_error("Rpj: total_length=0", eng.compute_average_Rpj, 817.6427, 0.0)
    expect_value_error("Rpj: alpha=0", eng.compute_average_Rpj, 817.6427, MD_TOTAL_LENGTH, 0.0)
    expect_value_error("Rpj: alpha>1", eng.compute_average_Rpj, 817.6427, MD_TOTAL_LENGTH, 1.2)

    expect_value_error("segment: Q=0", eng.compute_segment_resistance, 0.0, SEG7_G, SEG7_L, SEG7_DN, SEG7_ZETA)
    expect_value_error("segment: G<0", eng.compute_segment_resistance, SEG7_Q, -5.0, SEG7_L, SEG7_DN, SEG7_ZETA)
    expect_value_error("segment: l=0", eng.compute_segment_resistance, SEG7_Q, SEG7_G, 0.0, SEG7_DN, SEG7_ZETA)
    expect_value_error("segment: dn 空串", eng.compute_segment_resistance, SEG7_Q, SEG7_G, SEG7_L, "", SEG7_ZETA)
    expect_value_error("segment: dn 不存在", eng.compute_segment_resistance, SEG7_Q, SEG7_G, SEG7_L, "DN999", SEG7_ZETA)
    expect_value_error(
        "segment: dn 区间不覆盖 G（DN20 最大 G=371 < 1286.2）",
        eng.compute_segment_resistance, SEG7_Q, SEG7_G, SEG7_L, "DN20", SEG7_ZETA,
    )
    expect_value_error("segment: zeta_sum<0", eng.compute_segment_resistance, SEG7_Q, SEG7_G, SEG7_L, SEG7_DN, -1.0)
    expect_value_error("segment: rho=0", eng.compute_segment_resistance, SEG7_Q, SEG7_G, SEG7_L, SEG7_DN, SEG7_ZETA, 0.0)

    expect_value_error("imbalance: available=0", eng.check_imbalance, 0.0, 545.67, "Loop4")
    expect_value_error("imbalance: sum<0", eng.check_imbalance, 633.5, -1.0, "Loop4")
    expect_value_error("imbalance: label 空串", eng.check_imbalance, 633.5, 545.67, "")
    expect_value_error("imbalance: label 非字符串", eng.check_imbalance, 633.5, 545.67, None)


# ---------------------------------------------------------------------------
# 8) 返回字段契约（不 round）
# ---------------------------------------------------------------------------
def test_return_contracts_no_rounding():
    print("  [10] 返回字段契约：键名齐全、数值未 round")
    segment = eng.compute_segment_resistance(SEG7_Q, SEG7_G, SEG7_L, SEG7_DN, SEG7_ZETA, rho=SEG7_RHO_MD)
    assert_equal(
        "segment 返回键集合",
        set(segment.keys()),
        {"dn", "v", "Rm", "dPy", "dPd", "dPj", "dP_total"},
    )
    for key in ("v", "Rm", "dPy", "dPd", "dPj", "dP_total"):
        assert isinstance(segment[key], float), f"{key} 应为 float，实际 {type(segment[key])!r}"
    assert_close("dP_total == dPy + dPj", segment["dP_total"], segment["dPy"] + segment["dPj"], 0.0)
    assert_close(
        "dPj == zeta_sum * dPd",
        segment["dPj"],
        SEG7_ZETA * segment["dPd"],
        1e-12,
    )
    # 未 round 证据：v 不等于保留 2 位小数的值
    assert segment["v"] != round(segment["v"], 2), "v 疑似被 round"

    imbalance = eng.check_imbalance(IMBALANCE_AVAILABLE, IMBALANCE_RESISTANCE, "Loop4")
    required_keys = {"label", "imbalance_ratio", "imbalance_percent", "status", "suggestion"}
    assert required_keys.issubset(set(imbalance.keys())), (
        f"imbalance 返回键缺少：{required_keys - set(imbalance.keys())!r}"
    )
    expected_ratio = (IMBALANCE_AVAILABLE - IMBALANCE_RESISTANCE) / IMBALANCE_AVAILABLE
    assert_equal("imbalance_ratio 未 round（与公式值精确相等）", imbalance["imbalance_ratio"], expected_ratio)


# ---------------------------------------------------------------------------
# 9) engine.py 静态约束：无循环 / 无 FastAPI / 无 Pydantic
# ---------------------------------------------------------------------------
def test_engine_source_constraints():
    print("  [11] engine.py 静态检查：无 while/for/列表推导；无 FastAPI/Pydantic")
    assert ENGINE_PATH.exists(), f"engine.py 不存在：{ENGINE_PATH}"
    source = ENGINE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    loop_nodes = []
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
        ):
            loop_nodes.append(type(node).__name__)
    print(f"      AST 中循环/推导节点：{loop_nodes!r}（应为空）")
    assert loop_nodes == [], f"engine.py 出现循环/推导结构：{loop_nodes!r}"
    lowered = source.lower()
    print(f"      含 'fastapi'：{'fastapi' in lowered}；含 'pydantic'：{'pydantic' in lowered}（应均为 False）")
    assert "fastapi" not in lowered, "engine.py 不得包含 FastAPI 路由代码"
    assert "pydantic" not in lowered, "engine.py 不得包含 Pydantic 模型"


# ---------------------------------------------------------------------------
# 直接运行入口（pytest 收集时不会执行 main）
# ---------------------------------------------------------------------------
def main() -> None:
    print("test_engine.py — engine.py 核心纯函数测试（task-3）")
    print(f"工作目录：{BASE_DIR}")
    print(f"pandas={dl.pd.__version__}, numpy={dl.np.__version__}")
    print("锚点容差：作用压力 0.05 Pa；Rpj 0.05 Pa/m；管段7 阻力 0.5 Pa；公式自洽 1e-9")
    tests = (
        test_compute_driving_pressure,
        test_compute_average_Rpj,
        test_segment_resistance_md_anchor_rho983,
        test_segment_resistance_default_rho_self_consistency,
        test_segment_resistance_lookup_pipe_adaptation,
        test_segment_resistance_runtime_error_on_dn_mismatch,
        test_check_imbalance_balanced,
        test_check_imbalance_unbalanced,
        test_invalid_inputs_raise_value_error,
        test_return_contracts_no_rounding,
        test_engine_source_constraints,
    )
    for test in tests:
        print()
        print("=" * 78)
        print(f"[{test.__name__}]")
        print("=" * 78)
        test()  # 断言失败直接抛出，进程非零退出（不 try/except 吞断言）
    print()
    print("=" * 78)
    print(f"汇总：{len(tests)} 个测试全部 PASS")
    print("口径提示：MD 锚点动压头用 rho=983（MD L25），契约默认 rho=961.92；"
          "管段20/管段7 选径口径见 data_loader 的 select_dn。")


if __name__ == "__main__":
    main()

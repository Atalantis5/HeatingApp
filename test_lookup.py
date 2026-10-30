# -*- coding: utf-8 -*-
"""test_lookup.py — task-2 仲裁后的数据加载 / 查表测试（05_engine/04 ProgramFile）。

测试对象：
- data_loader.py 的三张 Excel 加载；
- 插值函数 interpolate_pipe(G) 与选径函数 select_dn(G, R_pj_target)；
- 兼容壳 lookup_pipe(G, R_target)（R_target=None 必须抛 ValueError 并提示 select_dn）。

两种运行方式：
1) 直接运行（本步已实测）：
   cd "05_engine/04 ProgramFile"
   PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
     "C:/Users/Atis/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe" test_lookup.py
   输出 actual vs expected 明细；断言失败直接抛 AssertionError，进程非零退出（不 try/except 吞异常）。
2) pytest 收集（若环境已安装 pytest）：
   PYTHONDONTWRITEBYTECODE=1 "<python>" -m pytest test_lookup.py -p no:cacheprovider -q
   本文件所有 test_* 函数内部均为直接 assert，可被 pytest 收集。

仲裁口径（2026-10-30 用户仲裁，取代 task-1 的 1e-6 苛刻容差）：
A. 取值语义：采用按 G 线性插值结果（numpy.interp），不再要求数值等于 MD 表行原值；
B. 选径逻辑：select_dn 在所有覆盖 G 的 DN 组中，选“插值 Rm 与 R_pj_target 绝对
   偏差最小”的 DN（并列取 DN_ORDER 靠前 / 较小管径）；lookup_pipe 为兼容壳；
C. 测试容差：DN 必须 100% 相等；数值 |actual-expected| <= max(0.05, 0.02*|expected|)。

MD 锚点来源（05_engine/02 Calculation Sheet/默认模型的计算典例.md，只读）：
- 管段7 ：MD L20（R_pj≈4.95）/ L24 / L31，G=1286.2
           仲裁后期望：DN65, G_actual=1287, v=0.09993360995850623, Rm=2.384390041493776
           MD 表行原值（仅诊断对照，不作断言）：v=0.10, R=2.39
- 管段10：MD L20 / L33，G=639.7
           仲裁后期望：DN40, G_actual=712, v=0.13501554404145077, Rm=4.904580310880829
           MD 表行原值（仅诊断对照，不作断言）：v=0.15, R=4.50
- 管段20：MD L62（R_pj≈16.24）/ L68，G=419.6
           统一“最接近”规则期望：DN32, G_actual=345, v=0.1215606936416185, Rm=7.973526011560694
           MD 原选法（仅注释/诊断，不作断言）：DN25, v=0.20, R=32。
           MD L68 原文“比目标略大，缩小管径以消耗压力”表明这是工程微调；统一“最接近”
           规则会选 DN32（|7.9735-16.24|≈8.27 < |33.2544-16.24|≈17.01）。按仲裁，
           此处保留 MD 原选法说明，但不作为断言期望。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

# 只允许 ProgramFile 下存在 3 个新建文件：禁止 import data_loader 时生成 __pycache__。
sys.dont_write_bytecode = True

import data_loader as dl

TOL_ABS_MIN = 0.05
TOL_REL = 0.02

BASE_DIR = Path(__file__).resolve().parent
SHEET_DIR = BASE_DIR.parent / "02 Calculation Sheet"
MD_PATH = SHEET_DIR / "默认模型的计算典例.md"
MANIFEST_PATH = BASE_DIR / "schema_manifest.json"

# ---------------------------------------------------------------------------
# 仲裁后期望锚点（期望值 = “锁定 DN 后的插值结果”）
# md_original 仅用于注释/诊断对照，不参与任何数值断言。
# ---------------------------------------------------------------------------
MD_SELECT_ANCHORS = (
    # 管段7：MD L24 “管段7，G=1286.2，查表接近 DN65（G=1287, v=0.1, R=2.39）”；
    #         MD L31 汇总表行；MD L20 给出 Loop1 R_pj≈4.95。
    {
        "segment": 7,
        "md_lines": "MD L20/L24/L31",
        "G": 1286.2,
        "R_pj_target": 4.95,
        "dn": "DN65",
        "G_actual": 1287.0,
        "v": 0.09993360995850623,
        "Rm": 2.384390041493776,
        "md_original": {
            "dn": "DN65",
            "G_row": 1287.0,
            "v": 0.10,
            "Rm": 2.39,
            "note": "MD 表行原值，仅诊断对照；仲裁后数值期望改为插值结果",
        },
    },
    # 管段10：MD L33 “| 10 | 639.7 | 11 | DN40 | 0.15 | 4.50 | ...”；MD L20 R_pj≈4.95。
    {
        "segment": 10,
        "md_lines": "MD L20/L33",
        "G": 639.7,
        "R_pj_target": 4.95,
        "dn": "DN40",
        "G_actual": 712.0,
        "v": 0.13501554404145077,
        "Rm": 4.904580310880829,
        "md_original": {
            "dn": "DN40",
            "G_row": 712.0,
            "v": 0.15,
            "Rm": 4.50,
            "note": "MD 表行原值，仅诊断对照；仲裁后数值期望改为插值结果",
        },
    },
    # 管段20：MD L62 给出 R_pj≈16.24；MD L68 “管段20 (G=419.6)：选 DN25，查表 v=0.20, R=32
    #（比目标略大，缩小管径以消耗压力）”。MD 原选 DN25 属工程微调，与统一“最接近”
    # 规则不一致；按仲裁期望改为 DN32，MD 原选法仅保留说明、不作断言。
    {
        "segment": 20,
        "md_lines": "MD L62/L68",
        "G": 419.6,
        "R_pj_target": 16.24,
        "dn": "DN32",
        "G_actual": 345.0,
        "v": 0.1215606936416185,
        "Rm": 7.973526011560694,
        "md_original": {
            "dn": "DN25",
            "G_row": 412.0,
            "v": 0.20,
            "Rm": 32.0,
            "note": (
                "MD 原选法（工程微调：“比目标略大，缩小管径以消耗压力”）"
                "，与统一“最接近”规则不一致；按仲裁仅注释/诊断，不作断言期望"
            ),
        },
    },
)

# MD 第 15 行：“3层房屋，高度15m以下，水平距离30~50m”对应附加压力 350 Pa
MD_GRAVITY_ANCHOR = {
    "md_line": "MD L15",
    "section_keyword": "3层",
    "horizontal_column": "30～50",
    "value": 350.0,
}

# MD 原文行核验：(行号, 该行必须包含的文本片段)，证明锚点确实来自 MD。
MD_SOURCE_CHECKS = (
    (15, "350"),
    (20, "4.95"),
    (24, "1286.2"),
    (24, "DN65"),
    (24, "2.39"),
    (31, "| 7 | 1286.2 | 15 | DN65 | 0.10 | 2.39 |"),
    (33, "| 10 | 639.7 | 11 | DN40 | 0.15 | 4.50 |"),
    (62, "16.24"),
    (68, "419.6"),
    (68, "DN25"),
    (68, "R=32"),
)

EXPECTED_DN_GROUP_COUNTS = {
    "DN15": 12,
    "DN20": 12,
    "DN25": 11,
    "DN32": 13,
    "DN40": 13,
    "DN50": 13,
    "DN65": 13,
    "DN80": 12,
    "DN100": 12,
}


# ---------------------------------------------------------------------------
# 断言/日志辅助（pytest 下同样适用：内部均为直接 assert）
# ---------------------------------------------------------------------------
def numeric_tolerance(expected: float) -> float:
    """仲裁容差：max(0.05, 0.02*|expected|)。"""
    return max(TOL_ABS_MIN, TOL_REL * abs(float(expected)))


def assert_numeric(field: str, actual, expected) -> None:
    """数值断言：|actual - expected| <= max(0.05, 0.02*|expected|)。"""
    actual_f = float(actual)
    expected_f = float(expected)
    tolerance = numeric_tolerance(expected_f)
    deviation = abs(actual_f - expected_f)
    print(
        f"      {field}: actual={actual_f!r} expected={expected_f!r} "
        f"|偏差|={deviation!r} 容差={tolerance!r} -> {'PASS' if deviation <= tolerance else 'FAIL'}"
    )
    assert deviation <= tolerance, (
        f"{field} 数值偏差超容差：actual={actual_f!r}, expected={expected_f!r}, "
        f"|偏差|={deviation!r}, 容差=max(0.05, 0.02*|expected|)={tolerance!r}"
    )


def assert_exact(field: str, actual, expected) -> None:
    """精确相等断言（DN、G_actual 等要求 100% 相等）。"""
    print(f"      {field}: actual={actual!r} expected={expected!r} -> {'PASS' if actual == expected else 'FAIL'}")
    assert actual == expected, (
        f"{field} 必须 100% 相等：actual={actual!r}, expected={expected!r}"
    )


# ---------------------------------------------------------------------------
# 测试 1：MD 锚点来源核验
# ---------------------------------------------------------------------------
def test_md_anchor_source() -> None:
    print("  [1] 锚点来源核验：读取《默认模型的计算典例.md》原文行")
    assert MD_PATH.exists(), f"锚点来源 MD 不存在：{MD_PATH}"
    lines = MD_PATH.read_text(encoding="utf-8").splitlines()
    print(f"  文件：{MD_PATH}")
    print(f"  行数：{len(lines)}")
    for lineno, token in MD_SOURCE_CHECKS:
        shown = lines[lineno - 1].strip()[:88] if 1 <= lineno <= len(lines) else "<无此行>"
        found = 1 <= lineno <= len(lines) and token in lines[lineno - 1]
        print(
            f"  MD L{lineno:<3} 含 {token!r} -> {'PASS' if found else 'FAIL'}"
            f"  原文：{shown}"
        )
        assert 1 <= lineno <= len(lines), f"MD 行号越界：L{lineno}（MD 共 {len(lines)} 行）"
        assert found, f"MD L{lineno} 缺少锚点文本片段 {token!r}"


# ---------------------------------------------------------------------------
# 测试 2：三张 Excel 加载、行数、manifest 一致性
# ---------------------------------------------------------------------------
def test_table_loading() -> None:
    print("  [2] 三张 Excel 数据表加载与行数校验")

    pipe_table = dl.load_pipe_table()
    pipe_rows = len(pipe_table)
    group_counts = dl.pipe_group_row_counts()
    print(f"  热水采暖管道水力计算表：{pipe_rows} 行数据（列：{list(pipe_table.columns)}）")
    print(f"    DN 分组行数：{json.dumps(group_counts, ensure_ascii=False)}")
    assert pipe_rows > dl.PIPE_MIN_DATA_ROWS, (
        f"管道表数据行数={pipe_rows}，要求 > {dl.PIPE_MIN_DATA_ROWS}"
    )
    assert pipe_rows == 111, f"管道表数据行数应为 111，实际 {pipe_rows}"
    assert group_counts == EXPECTED_DN_GROUP_COUNTS, (
        f"DN 分组行数不符：actual={group_counts}, expected={EXPECTED_DN_GROUP_COUNTS}"
    )
    assert sum(group_counts.values()) == pipe_rows

    local_table = dl.load_local_loss_table()
    print(f"  局部阻力系数表：{len(local_table)} 行数据（列：{list(local_table.columns)}）")
    assert len(local_table) == 15, f"局部阻力系数表应有 15 行，实际 {len(local_table)}"
    assert int((local_table["名称"] == "三通").sum()) == 4, "三通名称合并单元格向下填充结果应为 4 行"

    gravity_table = dl.load_gravity_extra_pressure_table()
    print(f"  重力循环附加压力表：{len(gravity_table)} 行数据（列：{list(gravity_table.columns)}）")
    assert len(gravity_table) == 8, f"附加压力表应有 8 行，实际 {len(gravity_table)}"

    assert MANIFEST_PATH.exists(), f"manifest 不存在：{MANIFEST_PATH}"
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    print(f"  schema_manifest.json：json.load 成功，manifest_version={manifest.get('manifest_version')!r}")
    assert manifest["manifest_version"].startswith("2"), "manifest 应更新为 task-2 仲裁后的 2.x 版本"
    assert manifest["tables"]["pipe_hydraulic_table"]["data_row_count"] == pipe_rows
    assert manifest["tables"]["local_loss_coefficient_table"]["data_row_count"] == len(local_table)
    assert manifest["tables"]["gravity_extra_pressure_table"]["data_row_count"] == len(gravity_table)
    manifest_groups = {
        item["dn"]: item["row_count"]
        for item in manifest["tables"]["pipe_hydraulic_table"]["dn_groups"]
    }
    assert manifest_groups == group_counts, (
        f"manifest DN 分组行数与实际不符：{manifest_groups} != {group_counts}"
    )
    assert "select_dn" in manifest["selection_contract"]["primary_function"]
    manifest_anchor_dns = [item["expected"]["dn"] for item in manifest["anchors"]["select_dn_anchors"]]
    assert manifest_anchor_dns == ["DN65", "DN40", "DN32"], (
        f"manifest 锚点期望 DN 不符：{manifest_anchor_dns}"
    )
    assert "DN25" in manifest["anchors"]["md_original_selection_note"], (
        "manifest 应保留 MD 管段20 原选 DN25 的差异说明"
    )


# ---------------------------------------------------------------------------
# 测试 3：重力附加压力锚点（MD L15 -> 350 Pa）
# ---------------------------------------------------------------------------
def test_gravity_anchor() -> None:
    print("  [3] 重力附加压力表锚点（MD L15：3层/15m以下/水平距离30~50m -> 350 Pa）")
    gravity_table = dl.load_gravity_extra_pressure_table()
    column = MD_GRAVITY_ANCHOR["horizontal_column"]
    section_rows = gravity_table[
        gravity_table["房屋类型"].str.contains(MD_GRAVITY_ANCHOR["section_keyword"], regex=False)
    ]
    values = section_rows[column].to_numpy(dtype=float)
    print(f"  分节（含 {MD_GRAVITY_ANCHOR['section_keyword']!r}）的“{column}”列取值：{values.tolist()}")
    matched = section_rows[np.isclose(values, MD_GRAVITY_ANCHOR["value"], atol=1e-9, rtol=0.0)]
    assert len(matched) > 0, f"附加压力表 {column} 列未找到 {MD_GRAVITY_ANCHOR['value']} Pa"
    row = matched.iloc[0]
    print(
        f"  匹配行：房屋类型={row['房屋类型']!r}, 系统水平距离={row['系统水平距离(m)']!r}, "
        f"高度={row['锅炉到散热器的高度(m)']!r}, {column}={float(row[column])!r}"
    )
    assert_numeric(f"附加压力表 {column} 锚点值", float(row[column]), MD_GRAVITY_ANCHOR["value"])


# ---------------------------------------------------------------------------
# 测试 4：select_dn 三条锚点（DN 100% 相等 + 仲裁容差）
# ---------------------------------------------------------------------------
def test_select_dn_anchors() -> None:
    print("  [4] select_dn 锚点：DN 100% 相等；数值容差 max(0.05, 0.02*|expected|)")
    for anchor in MD_SELECT_ANCHORS:
        segment = anchor["segment"]
        result = dl.select_dn(anchor["G"], anchor["R_pj_target"])
        expected = {
            "dn": anchor["dn"],
            "G_actual": anchor["G_actual"],
            "v": anchor["v"],
            "Rm": anchor["Rm"],
        }
        print(
            f"  管段{segment}（锚点来源 {anchor['md_lines']}）："
            f"输入 G={anchor['G']!r}, R_pj_target={anchor['R_pj_target']!r}"
        )
        print(f"    期望（仲裁后·插值结果）: {json.dumps(expected, ensure_ascii=False)}")
        print(f"    实际: {json.dumps(result, ensure_ascii=False)}")
        assert_exact(f"管段{segment} dn", result["dn"], anchor["dn"])
        assert_exact(f"管段{segment} G_actual", result["G_actual"], anchor["G_actual"])
        assert_numeric(f"管段{segment} v", result["v"], anchor["v"])
        assert_numeric(f"管段{segment} Rm", result["Rm"], anchor["Rm"])
        original = anchor["md_original"]
        print(
            f"    [诊断·不参与断言] MD 原选法：DN={original['dn']!r}, "
            f"表行 G={original['G_row']!r}, 表行 v={original['v']!r}, 表行 R={original['Rm']!r}；"
            f"{original['note']}"
        )


# ---------------------------------------------------------------------------
# 测试 5：lookup_pipe 兼容壳
# ---------------------------------------------------------------------------
def test_lookup_pipe_compat_shell() -> None:
    print("  [5] lookup_pipe 兼容壳：R_target=None 显式报错；非 None 委托 select_dn")
    try:
        dl.lookup_pipe(1286.2)
    except ValueError as exc:
        message = str(exc)
        print(f"    lookup_pipe(1286.2) 抛 ValueError（符合预期）：{message}")
        assert "select_dn" in message, "ValueError 提示中应包含 select_dn，便于迁移"
    else:
        raise AssertionError("lookup_pipe(G, R_target=None) 未按仲裁抛 ValueError")

    for anchor in MD_SELECT_ANCHORS:
        shell = dl.lookup_pipe(anchor["G"], anchor["R_pj_target"])
        direct = dl.select_dn(anchor["G"], anchor["R_pj_target"])
        print(
            f"    管段{anchor['segment']}: lookup_pipe({anchor['G']!r}, {anchor['R_pj_target']!r}) "
            f"== select_dn(...) -> {shell == direct}; result={json.dumps(shell, ensure_ascii=False)}"
        )
        assert shell == direct, (
            f"兼容壳未正确委托 select_dn：lookup_pipe={shell!r}, select_dn={direct!r}"
        )


# ---------------------------------------------------------------------------
# 测试 6：插值函数与选径函数分离
# ---------------------------------------------------------------------------
def test_interpolate_select_separation() -> None:
    print("  [6] 插值函数 interpolate_pipe 与选径函数 select_dn 分离性校验")
    interpolated = dl.interpolate_pipe(1286.2)
    print(f"    interpolate_pipe(1286.2) 覆盖 DN 组：{list(interpolated.keys())}")
    for dn, item in interpolated.items():
        print(f"      {dn}: {json.dumps(item, ensure_ascii=False)}")
        assert set(item.keys()) == {"G_actual", "v", "Rm"}, (
            f"插值函数每组结果字段应为 G_actual/v/Rm，实际 {set(item.keys())}"
        )

    assert isinstance(interpolated, dict) and len(interpolated) >= 2, (
        "G=1286.2 应被多个 DN 组覆盖，interpolate_pipe 应返回多个候选"
    )
    assert "dn" not in interpolated, "插值函数不应包含选径结果 dn"

    # 选径函数：在所有候选内取 |Rm - R_pj_target| 最小者（并列取 DN_ORDER 靠前）
    target = 4.95
    candidate_dns = list(interpolated.keys())
    distances = {dn: abs(interpolated[dn]["Rm"] - target) for dn in candidate_dns}
    expected_dn = min(candidate_dns, key=lambda dn: (distances[dn], candidate_dns.index(dn)))
    selected = dl.select_dn(1286.2, target)
    print(f"    候选 |Rm-{target}|：{json.dumps(distances, ensure_ascii=False)}")
    print(f"    期望 DN（最小偏差，并列取小管径）={expected_dn!r}；select_dn 实际={selected['dn']!r}")
    assert selected["dn"] == expected_dn, (
        f"select_dn 选径不是最小偏差 DN：actual={selected['dn']!r}, expected={expected_dn!r}"
    )
    assert selected["dn"] == "DN65"


# ---------------------------------------------------------------------------
# 测试 7：select_dn 返回字段契约
# ---------------------------------------------------------------------------
def test_return_contract() -> None:
    print("  [7] select_dn 返回字段契约校验")
    result = dl.select_dn(419.6, 16.24)
    print(f"    select_dn(419.6, 16.24) -> {json.dumps(result, ensure_ascii=False)}")
    assert set(result.keys()) == {"dn", "G_actual", "v", "Rm"}, (
        f"返回字段应为 dn/G_actual/v/Rm，实际 {set(result.keys())}"
    )
    assert isinstance(result["dn"], str) and result["dn"] in dl.DN_ORDER
    assert isinstance(result["G_actual"], float)
    assert isinstance(result["v"], float)
    assert isinstance(result["Rm"], float)
    group = dl.get_pipe_group(result["dn"])
    assert bool(np.isclose(group["G"], result["G_actual"]).any()), (
        f"G_actual={result['G_actual']!r} 不是 {result['dn']} 组的表行 G"
    )


# ---------------------------------------------------------------------------
# 直接运行入口（pytest 收集时不会执行 main）
# ---------------------------------------------------------------------------
def main() -> None:
    print("test_lookup.py — task-2 仲裁后数据加载/查表测试")
    print(f"工作目录：{BASE_DIR}")
    print(f"源表目录：{SHEET_DIR}")
    print(f"pandas={dl.pd.__version__}, numpy={np.__version__}")
    print("容差：DN 100% 相等；数值 |actual-expected| <= max(0.05, 0.02*|expected|)")
    tests = (
        test_md_anchor_source,
        test_table_loading,
        test_gravity_anchor,
        test_select_dn_anchors,
        test_lookup_pipe_compat_shell,
        test_interpolate_select_separation,
        test_return_contract,
    )
    for test in tests:
        print()
        print("=" * 78)
        print(f"[{test.__name__}]")
        print("=" * 78)
        test()  # 断言失败时直接抛出，进程非零退出，不 try/except 吞异常
    print()
    print("=" * 78)
    print(
        f"汇总：{len(tests)} 个测试全部 PASS"
        "（select_dn 三条锚点 DN 100% 相等；数值容差 max(0.05, 0.02*|expected|)）"
    )
    print("说明：MD 管段20 原选 DN25 属工程微调（MD L68），按仲裁不作断言期望；"
          "统一最接近规则的实际输出为 DN32，见测试 [4] 诊断行。")


if __name__ == "__main__":
    main()

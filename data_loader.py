# -*- coding: utf-8 -*-
"""数据表加载与管道查表模块（05_engine/04 ProgramFile/data_loader.py）。

task-2 仲裁后语义（2026-10-30 用户仲裁，取代 task-1 的契约冲突版本）：
A. 取值语义：采用按 G 线性插值结果（numpy.interp），不再要求与 MD 表行原值逐位相等；
B. 选径逻辑彻底重构为“插值函数 + 选径函数”两段：
   - interpolate_pipe(G)：在流量区间覆盖 G 的各 DN 组内按 G 对 v / Rm 线性插值，
     返回 {dn: {"G_actual", "v", "Rm"}}（仅插值、不选径）；
   - select_dn(G, R_pj_target)：在所有覆盖 G 的 DN 组中，选取插值 Rm 与
     R_pj_target 绝对偏差最小的 DN（并列时取 DN_ORDER 靠前 / 较小管径），
     返回 {"dn", "G_actual", "v", "Rm"}（v/Rm 为插值值，G_actual 为该 DN 组内
     最近表行的 G）；
   - lookup_pipe 保留为兼容壳：R_target 为 None 时显式 raise ValueError 并提示
     改用 select_dn；R_target 非 None 时委托 select_dn(G, R_target)。
C. 测试容差：DN 必须 100% 相等；数值 |actual-expected| <= max(0.05, 0.02*|expected|)。

与 MD 典例的已知差异（按仲裁仅记录、不作断言）：
MD 管段20 原选 DN25（查表 v=0.20, R=32；MD 第 68 行注明“比目标略大，缩小管径
以消耗压力”，属工程微调）。统一“最接近 R_pj_target”规则下该输入得到 DN32
（插值 Rm≈7.9735），差异说明见 schema_manifest.json 的
anchors.md_original_selection_note 与 known_conflict.seg20_note。

职责边界（严格）：本模块只做 pandas 读取、numpy 插值、表行定位与 DN 选择；
不写任何水力计算式；不手写二分查找或逐行循环比较（插值/定位全部交给 numpy）。

公开接口：
- load_pipe_table()                    -> DataFrame（列名保持 Excel 原始表头）
- load_local_loss_table()              -> DataFrame（名称 / 形式 / ξ）
- load_gravity_extra_pressure_table()  -> DataFrame（附加压力表整理为长表）
- get_pipe_group(dn)                   -> dict（某 DN 组的 G/v/Rm numpy 数组）
- interpolate_pipe(G)                  -> dict（插值函数，不选径）
- select_dn(G, R_pj_target)            -> dict（主选径接口，含 dn/G_actual/v/Rm）
- lookup_pipe(G, R_target=None)        -> 兼容壳（None 时报错，非 None 委托 select_dn）
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 路径与常量（相对本文件定位；源目录只读，绝不写入）
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
SHEET_DIR = BASE_DIR.parent / "02 Calculation Sheet"

PIPE_XLSX = SHEET_DIR / "热水采暖管道水力计算表.xlsx"
LOCAL_LOSS_XLSX = SHEET_DIR / "局部阻力系数.xlsx"
GRAVITY_XLSX = SHEET_DIR / "重力循环上供下回热水供暖系统附加压力.xlsx"
SCHEMA_MANIFEST_PATH = BASE_DIR / "schema_manifest.json"

# 管道水力计算表（Sheet1）
PIPE_SHEET = "Sheet1"
PIPE_TITLE_ROW = 0        # 0-based：第 1 行为合并标题行
PIPE_TITLE_CELL = "管径水力计算参数表"
PIPE_HEADER_ROW = 1        # 0-based：第 2 行为表头
PIPE_DATA_START_ROW = 2    # 0-based：第 3 行起为数据
PIPE_MIN_DATA_ROWS = 100   # 任务书要求：数据行数必须 > 100
PIPE_COL_DN = "管径"
PIPE_COL_G = "流量 G (kg/h)"
PIPE_COL_V = "流速 v (m/s)"
PIPE_COL_RM = "比摩阻 R (Pa/m)"
PIPE_COLUMNS = (PIPE_COL_DN, PIPE_COL_G, PIPE_COL_V, PIPE_COL_RM)
DN_ORDER = ("DN15", "DN20", "DN25", "DN32", "DN40",
            "DN50", "DN65", "DN80", "DN100")

# 局部阻力系数表（Sheet1）
LOCAL_LOSS_SHEET = "Sheet1"
LOCAL_LOSS_HEADER_ROW = 0  # 0-based：表头即首行
LOCAL_LOSS_COLUMNS = ("名称", "形式", "ξ")
LOCAL_LOSS_DATA_ROW_COUNT = 15

# 重力循环附加压力表（Sheet1）：标题行 0；两层表头行 1~2；分节标签行 3、8
GRAVITY_SHEET = "Sheet1"
GRAVITY_TITLE_ROW = 0
GRAVITY_HEADER_ROWS = (1, 2)
GRAVITY_SECTION_ROWS = ((3, 4, 8), (8, 9, 13))  # (分节标签行, 数据起, 数据止(不含))
GRAVITY_HORIZONTAL_COLUMNS = ("<10", "10～20", "20～30",
                              "30～50", "50～75", "75～100")
GRAVITY_DATA_ROW_COUNT = 8
GRAVITY_COL_SECTION = "房屋类型"
GRAVITY_COL_SYSTEM_DISTANCE = "系统水平距离(m)"
GRAVITY_COL_HEIGHT = "锅炉到散热器的高度(m)"


# ---------------------------------------------------------------------------
# 管道水力计算表
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _pipe_table_cached() -> pd.DataFrame:
    """读取管道水力计算表并完成合并单元格/表头处理（结果缓存）。"""
    if not PIPE_XLSX.exists():
        raise FileNotFoundError(f"未找到源文件：{PIPE_XLSX}")

    # header=None 读取原始网格：第 1 行为标题（A1:D1 合并），第 2 行为列名。
    raw = pd.read_excel(PIPE_XLSX, sheet_name=PIPE_SHEET, header=None)
    if raw.shape[0] <= PIPE_DATA_START_ROW or raw.shape[1] < len(PIPE_COLUMNS):
        raise ValueError(f"管道表结构异常：shape={raw.shape}")

    title = str(raw.iloc[PIPE_TITLE_ROW, 0]).strip()
    if title != PIPE_TITLE_CELL:
        raise ValueError(f"管道表标题不符：{title!r} != {PIPE_TITLE_CELL!r}")

    header = [str(x).strip() for x in raw.iloc[PIPE_HEADER_ROW].tolist()[:4]]
    if tuple(header) != PIPE_COLUMNS:
        raise ValueError(f"管道表表头不符：{header!r} != {list(PIPE_COLUMNS)!r}")

    data = raw.iloc[PIPE_DATA_START_ROW:, :4].copy()
    data.columns = list(PIPE_COLUMNS)

    # 合并单元格处理：管径标签仅组首行有值（A3:A14、A15:A26…），向下填充。
    data[PIPE_COL_DN] = data[PIPE_COL_DN].ffill()
    data = data[data[PIPE_COL_G].notna()].copy()
    data[PIPE_COL_DN] = data[PIPE_COL_DN].astype(str).str.strip()
    for col in (PIPE_COL_G, PIPE_COL_V, PIPE_COL_RM):
        data[col] = pd.to_numeric(data[col], errors="raise")
        if data[col].isna().any():
            raise ValueError(f"管道表列 {col!r} 存在空单元格")

    unknown = sorted(set(data[PIPE_COL_DN]) - set(DN_ORDER))
    if unknown:
        raise ValueError(f"管道表出现未登记管径标签：{unknown}")
    missing = [dn for dn in DN_ORDER if dn not in set(data[PIPE_COL_DN])]
    if missing:
        raise ValueError(f"管道表缺少 DN 分组：{missing}")

    # 任务书第 3 步：数据行数必须 > 100，否则停止。
    if len(data) <= PIPE_MIN_DATA_ROWS:
        raise ValueError(
            f"管道表数据行数={len(data)}，未满足 “> {PIPE_MIN_DATA_ROWS}”，按任务书停止。"
        )
    return data.reset_index(drop=True)


def load_pipe_table() -> pd.DataFrame:
    """返回管道水力计算表（列名保留 Excel 原始表头，管径列已向下填充）。"""
    return _pipe_table_cached().copy()


def get_pipe_group(dn: str) -> dict:
    """返回指定 DN 组的 numpy 数组：{"dn", "G", "v", "Rm", "rows"}（G 升序）。"""
    dn = str(dn).strip()
    table = _pipe_table_cached()
    sub = table[table[PIPE_COL_DN] == dn]
    if sub.empty:
        raise KeyError(f"管道表中不存在管径 {dn!r}；可选：{list(DN_ORDER)}")
    sub = sub.sort_values(PIPE_COL_G, kind="stable")
    g = sub[PIPE_COL_G].to_numpy(dtype=float)
    if not np.all(np.diff(g) > 0):
        raise ValueError(f"{dn} 组流量 G 非严格递增，无法使用 np.interp")
    return {
        "dn": dn,
        "G": g,
        "v": sub[PIPE_COL_V].to_numpy(dtype=float),
        "Rm": sub[PIPE_COL_RM].to_numpy(dtype=float),
        "rows": int(sub.shape[0]),
    }


# ---------------------------------------------------------------------------
# 局部阻力系数表
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _local_loss_cached() -> pd.DataFrame:
    """读取局部阻力系数表（结果缓存）。"""
    if not LOCAL_LOSS_XLSX.exists():
        raise FileNotFoundError(f"未找到源文件：{LOCAL_LOSS_XLSX}")
    raw = pd.read_excel(LOCAL_LOSS_XLSX, sheet_name=LOCAL_LOSS_SHEET, header=None)
    if raw.shape[1] < 3:
        raise ValueError(f"局部阻力系数表结构异常：shape={raw.shape}")

    header = [str(x).strip() for x in raw.iloc[LOCAL_LOSS_HEADER_ROW].tolist()[:3]]
    if tuple(header) != LOCAL_LOSS_COLUMNS:
        raise ValueError(f"局部阻力系数表表头不符：{header!r}")

    data = raw.iloc[LOCAL_LOSS_HEADER_ROW + 1:, :3].copy()
    data.columns = list(LOCAL_LOSS_COLUMNS)

    # 合并单元格处理：A2:A3、A4:A5、A6:A7、A9:A10、A11:A14，名称标签仅在组首行
    # 有值，向下填充；“形式”列中的 B11:B14 属于三通的合并空单元格（原表未标注
    # 形式），保持空白，不做向下填充。
    data[LOCAL_LOSS_COLUMNS[0]] = data[LOCAL_LOSS_COLUMNS[0]].ffill()
    for col in LOCAL_LOSS_COLUMNS[:2]:
        data[col] = data[col].map(
            lambda x: x.strip() if isinstance(x, str) else x
        )
    data[LOCAL_LOSS_COLUMNS[2]] = pd.to_numeric(
        data[LOCAL_LOSS_COLUMNS[2]], errors="raise"
    )
    if data[LOCAL_LOSS_COLUMNS[2]].isna().any():
        raise ValueError("局部阻力系数表 ξ 列存在空单元格")
    return data.reset_index(drop=True)


def load_local_loss_table() -> pd.DataFrame:
    """返回局部阻力系数表（名称/形式/ξ；合并的名称标签已向下填充）。"""
    return _local_loss_cached().copy()


# ---------------------------------------------------------------------------
# 重力循环附加压力表
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _gravity_cached() -> pd.DataFrame:
    """读取重力循环附加压力表并整理为长表（结果缓存）。"""
    if not GRAVITY_XLSX.exists():
        raise FileNotFoundError(f"未找到源文件：{GRAVITY_XLSX}")
    raw = pd.read_excel(GRAVITY_XLSX, sheet_name=GRAVITY_SHEET, header=None)
    if raw.shape[0] < 13 or raw.shape[1] < 8:
        raise ValueError(f"附加压力表结构异常：shape={raw.shape}")

    # 合并单元格处理：A1:H1 标题；A2:A3 / B2:B3 为纵向表头；C2:H2 为横向组标题
    # （“自总立管至计算立管之间的水平距离(m)”）；A4:H4、A9:H9 为分节标签
    # （“未保温的明装立管(1)1层或2层的房屋”、“（2）3层或4层的房屋”）。
    title = str(raw.iloc[GRAVITY_TITLE_ROW, 0]).strip()
    if not title:
        raise ValueError("附加压力表标题行为空")

    horizontal_header = [
        str(x).strip() for x in raw.iloc[GRAVITY_HEADER_ROWS[1], 2:8].tolist()
    ]
    if tuple(horizontal_header) != GRAVITY_HORIZONTAL_COLUMNS:
        raise ValueError(f"附加压力表横向表头不符：{horizontal_header!r}")

    records = []
    for label_row, start, end in GRAVITY_SECTION_ROWS:
        section = str(raw.iloc[label_row, 0]).strip()
        if not section:
            raise ValueError(f"附加压力表分节标签行为空：row={label_row}")
        for r in range(start, end):
            record = {
                GRAVITY_COL_SECTION: section,
                GRAVITY_COL_SYSTEM_DISTANCE: str(raw.iloc[r, 0]).strip(),
                GRAVITY_COL_HEIGHT: str(raw.iloc[r, 1]).strip(),
            }
            for offset, col in enumerate(GRAVITY_HORIZONTAL_COLUMNS):
                value = raw.iloc[r, 2 + offset]
                record[col] = None if pd.isna(value) else float(value)
            records.append(record)

    data = pd.DataFrame.from_records(records)
    if len(data) != GRAVITY_DATA_ROW_COUNT:
        raise ValueError(
            f"附加压力表数据行数={len(data)}，期望 {GRAVITY_DATA_ROW_COUNT}"
        )
    return data


def load_gravity_extra_pressure_table() -> pd.DataFrame:
    """返回重力循环附加压力表长表（房屋类型 / 系统水平距离 / 高度 / 6 档水平距离值）。"""
    return _gravity_cached().copy()


# ---------------------------------------------------------------------------
# 查表：插值函数（不选径）
# ---------------------------------------------------------------------------
def _as_positive_float(value, name: str) -> float:
    """把输入转成正的有限 float；类型/取值不合法时抛 TypeError / ValueError。"""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} 必须是数值：{value!r}") from exc
    if not np.isfinite(number) or number <= 0:
        raise ValueError(f"{name} 必须是正的有限数：{value!r}")
    return number


def _as_finite_float(value, name: str) -> float:
    """把输入转成有限 float；类型/取值不合法时抛 TypeError / ValueError。"""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} 必须是数值：{value!r}") from exc
    if not np.isfinite(number):
        raise ValueError(f"{name} 必须是有限数：{value!r}")
    return number


def interpolate_pipe(G_target: float) -> dict:
    """插值函数（只插值、不选径）。

    对每个“流量区间覆盖 G_target”的 DN 组：
    - 使用 numpy.interp 在该组内按 G 对 v 与 Rm 做线性插值；
    - 使用 numpy.searchsorted 定位组内最接近 G_target 的表行，取该表行的 G 作为
      G_actual（数据出处标注，不参与插值）。

    返回
    ----
    dict[str, dict]，键为按 DN_ORDER 排列的 DN 标签，值为
    {"G_actual": float, "v": float, "Rm": float}。若没有任何 DN 组覆盖
    G_target，抛 ValueError。
    """
    G = _as_positive_float(G_target, "G_target")
    interpolated: dict[str, dict] = {}
    for dn in DN_ORDER:
        group = get_pipe_group(dn)
        g = group["G"]
        if G < g[0] or G > g[-1]:
            # 该 DN 组的表行流量区间不覆盖 G_target，跳过（覆盖判定用 numpy 比较）。
            continue

        # 组内按 G 线性插值 v 与 Rm（跨 DN 组不插值，逐组使用 np.interp）。
        v_value = float(np.interp(G, g, group["v"]))
        rm_value = float(np.interp(G, g, group["Rm"]))

        # 组内最近 G 行定位：np.searchsorted 找插入点，再在左右邻居中取最近；
        # 全程使用 numpy，无手写二分查找 / 逐行循环比较。
        position = int(np.searchsorted(g, G, side="left"))
        position = int(np.clip(position, 1, g.size - 1))
        left_g = float(g[position - 1])
        right_g = float(g[position])
        nearest_g = left_g if abs(left_g - G) <= abs(right_g - G) else right_g

        interpolated[dn] = {"G_actual": nearest_g, "v": v_value, "Rm": rm_value}

    if not interpolated:
        raise ValueError(
            f"G_target={G} 超出管道表全部 DN 组的流量区间 "
            f"[{DN_ORDER[0]}..{DN_ORDER[-1]}]，无法插值"
        )
    return interpolated


# ---------------------------------------------------------------------------
# 查表：选径函数（主接口）
# ---------------------------------------------------------------------------
def select_dn(G: float, R_pj_target: float) -> dict:
    """选径主接口（task-2 仲裁后的统一“最接近”规则）。

    在“流量区间覆盖 G”的全部 DN 组中，选取**插值 Rm 与 R_pj_target 绝对偏差
    最小**的 DN；若多个 DN 并列（距离相同），取 DN_ORDER 靠前者（较小管径）。
    v / Rm 返回该 DN 组的插值值；G_actual 返回该 DN 组内最接近 G 的表行流量。

    参数
    ----
    G : float
        目标流量，单位 kg/h（必须为正的有限数）。
    R_pj_target : float
        目标平均比摩阻，单位 Pa/m（必须为有限数）。

    返回
    ----
    dict，包含且仅包含：dn / G_actual / v / Rm。
    """
    target = _as_finite_float(R_pj_target, "R_pj_target")
    candidates = interpolate_pipe(G)  # 插值函数：所有覆盖 G 的 DN 组
    dns = list(candidates.keys())     # 按 DN_ORDER 顺序（小管径在前）
    rms = np.array([candidates[dn]["Rm"] for dn in dns], dtype=float)
    distances = np.abs(rms - target)
    # np.argmin 返回首个最小值的索引 -> 并列时自然取 DN_ORDER 靠前者（较小管径）。
    chosen_dn = dns[int(np.argmin(distances))]
    chosen = candidates[chosen_dn]
    return {
        "dn": chosen_dn,
        "G_actual": chosen["G_actual"],
        "v": chosen["v"],
        "Rm": chosen["Rm"],
    }


def lookup_pipe(G_target: float, R_target: float | None = None) -> dict:
    """兼容壳（task-2 仲裁后）。

    - R_target 为 None：按仲裁废弃“最小覆盖管径”语义，显式 raise ValueError，
      并在错误信息中提示改用 select_dn(G, R_pj_target)；
    - R_target 非 None：委托 select_dn(G_target, R_target)。
    """
    if R_target is None:
        raise ValueError(
            "lookup_pipe(G, R_target=None) 已按 task-2 仲裁废弃："
            "“覆盖该 G 的最小管径”语义不再使用；"
            "请改用 select_dn(G, R_pj_target)，例如 select_dn(G, 4.95)。"
        )
    return select_dn(G_target, R_target)


def table_data_row_counts() -> dict:
    """返回三张表的数据行数（不含标题/表头/分节标签行）。"""
    return {
        "pipe_hydraulic_table": int(load_pipe_table().shape[0]),
        "local_loss_coefficient_table": int(load_local_loss_table().shape[0]),
        "gravity_extra_pressure_table": int(load_gravity_extra_pressure_table().shape[0]),
    }


def pipe_group_row_counts() -> dict:
    """返回管道表各 DN 分组的数据行数（按 DN_ORDER 顺序）。"""
    table = load_pipe_table()
    counts = table.groupby(PIPE_COL_DN, sort=False).size()
    return {dn: int(counts.get(dn, 0)) for dn in DN_ORDER}


if __name__ == "__main__":
    print("三张表数据行数：")
    print(json.dumps(table_data_row_counts(), ensure_ascii=False, indent=2))
    print("管道表 DN 分组行数：")
    print(json.dumps(pipe_group_row_counts(), ensure_ascii=False, indent=2))
    print("插值与选径分离示例：")
    for g_value, r_target in ((1286.2, 4.95), (639.7, 4.95), (419.6, 16.24)):
        result = select_dn(g_value, r_target)
        print(
            f"  select_dn({g_value}, {r_target}) -> "
            + json.dumps(result, ensure_ascii=False)
        )
    try:
        lookup_pipe(1286.2)
    except ValueError as exc:
        print(f"兼容壳 lookup_pipe(G, R_target=None) 抛 ValueError：{exc}")

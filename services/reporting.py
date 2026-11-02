# -*- coding: utf-8 -*-
"""services.reporting — Markdown / Excel 报表构造（从 main.py 原样迁移）。"""
from __future__ import annotations

from io import BytesIO

import pandas as pd
from openpyxl.styles import Font

from models import ProjectInput

# ---------------------------------------------------------------------------
# 后处理导出（V5）：Markdown / Excel
# ---------------------------------------------------------------------------
EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# V8.5 品牌标识：Markdown 与 Excel 报表顶部统一加注
REPORT_BRAND_LINE = "> 本报表由 [Atalantis 暖通工作台] 生成"

# 管段结果列的中文表头（Markdown / Excel 共用，保证“能直接用表格工具渲染”）
SEGMENT_COLUMNS = {
    "segment_id": "管段",
    "dn": "管径",
    "Q": "热负荷Q(W)",
    "G": "流量G(kg/h)",
    "l": "长度l(m)",
    "zeta_sum": "局部阻力Σξ",
    "shared": "共用段",
    "v": "流速v(m/s)",
    "Rm": "比摩阻Rm(Pa/m)",
    "dPy": "沿程阻力dPy(Pa)",
    "dPd": "动压头dPd(Pa)",
    "dPj": "局部阻力dPj(Pa)",
    "dP_total": "总阻力dP(Pa)",
}


def _segment_dataframe(segment_results: list[dict]) -> pd.DataFrame:
    """把后端 segment_results 转为 pandas.DataFrame（中文列名，数值保持全精度）。"""
    frame = pd.DataFrame(segment_results)
    if frame.empty:
        return pd.DataFrame(columns=list(SEGMENT_COLUMNS.values()))
    frame = frame[list(SEGMENT_COLUMNS.keys())].rename(columns=SEGMENT_COLUMNS)
    frame["共用段"] = frame["共用段"].map(lambda value: "是" if value else "否")
    return frame


def _parallel_loop_dataframe(result: dict) -> pd.DataFrame:
    """并联环路汇总 DataFrame（图模式取 parallel_loops；旧版兼容取 part_b）。"""
    parallel_loops = result.get("parallel_loops") or []
    if not parallel_loops and result.get("part_b"):
        parallel_loops = [result["part_b"]]
    rows = [
        {
            "环路": loop.get("label") or loop.get("sink_node") or "并联环路",
            "资用压力(Pa)": loop["available_pressure"],
            "共用段阻力(Pa)": loop["sum_shared"],
            "独用段总阻力(Pa)": loop["sum_resistance_specific"],
            "不平衡率(%)": loop["imbalance_percent"],
            "状态": loop["status"],
            "建议": loop["suggestion"],
        }
        for loop in parallel_loops
    ]
    return pd.DataFrame(rows)


def _warnings_dataframe(result: dict) -> pd.DataFrame:
    warnings = result.get("warnings") or []
    return pd.DataFrame(
        [{"序号": index + 1, "异常诊断": warning} for index, warning in enumerate(warnings)]
    ) if warnings else pd.DataFrame(columns=["序号", "异常诊断"])


def _topology_dataframe(result: dict) -> pd.DataFrame:
    topology = result.get("topology")
    if not topology:
        return pd.DataFrame(columns=["环路", "管段顺序", "总长度(m)", "共用管段", "独用管段"])
    loop1 = topology["loop1"]
    rows = [
        {
            "环路": f"Loop1（最不利环路，sink={loop1['sink_node']}）",
            "管段顺序": " → ".join(str(sid) for sid in loop1["segment_ids"]),
            "总长度(m)": loop1["total_length"],
            "共用管段": "",
            "独用管段": "",
        }
    ]
    for loop in topology.get("parallel_loops") or []:
        rows.append(
            {
                "环路": f"并联环路（sink={loop['sink_node']}）",
                "管段顺序": " → ".join(str(sid) for sid in loop["segment_ids"]),
                "总长度(m)": loop["total_length"],
                "共用管段": ", ".join(str(sid) for sid in loop["shared_segment_ids"]),
                "独用管段": ", ".join(str(sid) for sid in loop["dedicated_segment_ids"]),
            }
        )
    return pd.DataFrame(rows)


def build_markdown_report(project: ProjectInput, result: dict, project_name: str = "") -> str:
    """生成 Markdown 报表：核心结论 + 管段表格（pandas.to_markdown）+ 拓扑 + 异常诊断。"""
    title = project_name.strip() or "未命名案例"
    topology = result.get("topology")
    loop1 = topology["loop1"] if topology else None
    part_b = result.get("part_b")

    lines: list[str] = [
        REPORT_BRAND_LINE,
        "",
        f"# {title}_水力计算报表",
        "",
        "## 一、计算摘要",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 作用压力 dP′ | {result['dP_prime']:.4f} Pa |",
        f"| 最不利环路总阻力 ΣdP | {result['sum_resistance']:.4f} Pa |",
        f"| 系统富裕度 margin | {result['margin'] * 100:.2f}% |",
        f"| 最不利环路总长度 | {result['total_length']:.2f} m |",
        f"| 平均比摩阻 R_pj | {result['R_pj']:.4f} Pa/m |",
        f"| 迭代次数 | {result['iteration']} |",
        f"| 计算状态 | {result['status']} |",
    ]
    if topology:
        lines.extend(
            [
                f"| 自动识别热源节点 | {', '.join(topology['source_nodes'])} |",
                f"| 自动识别末端节点 | {', '.join(topology['sink_nodes'])} |",
                f"| 自动寻优最不利环路 Loop1 | {' → '.join(str(sid) for sid in loop1['segment_ids'])} |",
            ]
        )
    lines.extend(
        [
            "",
            "## 二、最不利环路管段计算表",
            "",
            _segment_dataframe(result["segment_results"]).to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 三、并联环路汇总",
            "",
        ]
    )

    parallel_frame = _parallel_loop_dataframe(result)
    if parallel_frame.empty:
        lines.append("无并联环路（未提供共用管段或多末端拓扑）。")
    else:
        lines.append(parallel_frame.to_markdown(index=False, floatfmt=".4f"))

    if topology:
        lines.extend(
            [
                "",
                "## 四、NetworkX 自动拓扑解析",
                "",
                _topology_dataframe(result).to_markdown(index=False, floatfmt=".4f"),
            ]
        )

    lines.extend(["", "## 五、异常诊断日志", ""])
    warnings = result.get("warnings") or []
    if warnings:
        lines.extend(f"{index + 1}. {warning}" for index, warning in enumerate(warnings))
    else:
        lines.append("无异常诊断记录。")

    lines.extend(["", "---", "", "本报表由后端水力计算接口自动生成，数值全部来自 engine.py 的计算结果。", ""])
    return "\n".join(lines)


def build_excel_workbook(project: ProjectInput, result: dict, project_name: str = "") -> bytes:
    """生成 Excel 工作簿字节流（pandas + openpyxl，多工作表）。"""
    title = project_name.strip() or "未命名案例"
    summary_rows: list[dict] = [
        {"指标": "案例名称", "数值": title},
        {"指标": "作用压力 dP′(Pa)", "数值": result["dP_prime"]},
        {"指标": "最不利环路总阻力 ΣdP(Pa)", "数值": result["sum_resistance"]},
        {"指标": "系统富裕度 margin", "数值": result["margin"]},
        {"指标": "最不利环路总长度(m)", "数值": result["total_length"]},
        {"指标": "平均比摩阻 R_pj(Pa/m)", "数值": result["R_pj"]},
        {"指标": "迭代次数", "数值": result["iteration"]},
        {"指标": "计算状态", "数值": result["status"]},
    ]
    topology = result.get("topology")
    if topology:
        summary_rows.extend(
            [
                {"指标": "自动识别热源节点", "数值": ", ".join(topology["source_nodes"])},
                {"指标": "自动识别末端节点", "数值": ", ".join(topology["sink_nodes"])},
                {
                    "指标": "最不利环路 Loop1 管段",
                    "数值": " → ".join(str(sid) for sid in topology["loop1"]["segment_ids"]),
                },
            ]
        )

    parallel_detail_rows: list[dict] = []
    for loop in result.get("parallel_loops") or []:
        for item in loop["segment_results"]:
            parallel_detail_rows.append(
                {"环路": loop.get("label") or loop.get("sink_node") or "并联环路", **item}
            )
    if not parallel_detail_rows and result.get("part_b"):
        for item in result["part_b"]["segment_results"]:
            parallel_detail_rows.append({"环路": result["part_b"].get("label") or "并联环路", **item})

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        summary_frame = pd.DataFrame(summary_rows)
        summary_frame.to_excel(writer, sheet_name="计算摘要", index=False, startrow=2)
        summary_sheet = writer.sheets["计算摘要"]
        summary_sheet["A1"] = REPORT_BRAND_LINE
        summary_sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=2)
        summary_sheet["A1"].font = Font(bold=True, color="1D39C4")
        _segment_dataframe(result["segment_results"]).to_excel(
            writer, sheet_name="最不利环路管段", index=False
        )
        parallel_frame = _parallel_loop_dataframe(result)
        if parallel_frame.empty:
            parallel_frame = pd.DataFrame([{"说明": "无并联环路"}])
        parallel_frame.to_excel(writer, sheet_name="并联环路汇总", index=False)
        if parallel_detail_rows:
            pd.DataFrame(parallel_detail_rows).rename(columns=SEGMENT_COLUMNS).to_excel(
                writer, sheet_name="并联环路明细", index=False
            )
        _topology_dataframe(result).to_excel(writer, sheet_name="拓扑解析", index=False)
        _warnings_dataframe(result).to_excel(writer, sheet_name="异常诊断", index=False)
    return buffer.getvalue()


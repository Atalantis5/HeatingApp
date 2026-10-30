/**
 * SegmentTable.jsx — 管段参数表（React + Ant Design Table）
 *
 * 任务边界（05_engine/04 ProgramFile 前端，第四步）：
 * - 计算剥离：本组件【不包含任何水力计算逻辑】（无插值、无阻力/压差公式），
 *   计算列 G/v/Rm/dPy/dPj/dP_total 仅展示 App 从后端 /calculate 返回的 segment_results；
 * - 数据读取：不读取 Excel / 数据库；
 * - 请求剥离：本组件【无 fetch / axios】，所有数据由父组件 App 通过 props 传入；
 * - Q、l 可编辑（InputNumber，失焦触发 onBlurValidate 由父组件做输入校验）；
 *   dn_locked 可编辑（Select，含“自动”选项 = null，交后端选径）；
 * - 行校验标红：segmentErrors 包含的 segment_id 整行浅红；
 * - 后端预警匹配：warnings 字符串数组按 segment_id 精准匹配，命中行显示红色
 *   WarningOutlined 图标，Tooltip 展示完整 warning 文本。
 */

import React, { useMemo } from "react";
import { Table, InputNumber, Select, Tooltip, Typography } from "antd";
import { WarningOutlined } from "@ant-design/icons";

export const DN_OPTIONS = [
  { value: "", label: "自动" },
  { value: "DN15", label: "DN15" },
  { value: "DN20", label: "DN20" },
  { value: "DN25", label: "DN25" },
  { value: "DN32", label: "DN32" },
  { value: "DN40", label: "DN40" },
  { value: "DN50", label: "DN50" },
  { value: "DN65", label: "DN65" },
  { value: "DN80", label: "DN80" },
  { value: "DN100", label: "DN100" },
];

/** 计算列（只读展示；值为 null/undefined 时显示占位符，不做任何计算）。 */
export const RESULT_COLUMNS = [
  { key: "G", title: "G (kg/h)" },
  { key: "v", title: "v (m/s)" },
  { key: "Rm", title: "Rm (Pa/m)" },
  { key: "dPy", title: "dPy (Pa)" },
  { key: "dPj", title: "dPj (Pa)" },
  { key: "dP_total", title: "dP_total (Pa)" },
];

/**
 * 在 warnings 中查找与指定 segment_id 关联的预警。
 * 精准匹配：优先匹配「管段<id>」（避免 管段2 误匹配 管段21）；再兼容 segment 写法。
 * @param {string[]} warnings 后端返回的 warnings
 * @param {number|string} segmentId 当前管段号
 * @returns {string|null} 命中的完整 warning 原文；未命中返回 null
 */
export function findWarningForSegment(warnings, segmentId) {
  if (!Array.isArray(warnings) || warnings.length === 0) return null;
  const id = Number(segmentId);
  if (!Number.isInteger(id) || id < 1) return null;
  const segmentPattern = new RegExp(`管段\\s*${id}(?!\\d)`);
  const segmentEnPattern = new RegExp(`segment(?:[_\\s-]?id)?\\s*[:=#]?\\s*${id}(?!\\d)`, "i");
  for (const warning of warnings) {
    if (typeof warning !== "string" || warning.length === 0) continue;
    if (segmentPattern.test(warning) || segmentEnPattern.test(warning)) return warning;
  }
  return null;
}

/** 展示计算列：只读取字段值，不做任何计算；空值显示占位符。 */
function renderResult(value) {
  if (value === null || value === undefined || value === "") {
    return <Typography.Text type="secondary">—</Typography.Text>;
  }
  return <span>{String(value)}</span>;
}

export default function SegmentTable({
  segments = [],
  segmentErrors = [],
  warnings = [],
  onChange,
  onBlurValidate,
  disabled = false,
}) {
  const errorIdSet = useMemo(
    () => new Set((segmentErrors || []).map((id) => Number(id))),
    [segmentErrors],
  );

  const columns = useMemo(() => {
    const editableColumn = (dataIndex, title, width, onChangeHandler, extraProps = {}) => ({
      title,
      dataIndex,
      key: dataIndex,
      width,
      align: "right",
      render: (_, record) => (
        <InputNumber
          min={0}
          disabled={disabled}
          value={record[dataIndex]}
          className="segment-number-input"
          style={{ width: "100%" }}
          onChange={(value) => onChange?.(record.segment_id, dataIndex, value)}
          onBlur={() => onBlurValidate?.(record.segment_id)}
          {...extraProps}
        />
      ),
    });

    return [
      {
        title: "管段号",
        dataIndex: "segment_id",
        key: "segment_id",
        width: 82,
        fixed: "left",
        render: (value) => <span className="segment-id-cell">管段 {String(value)}</span>,
      },
      editableColumn("Q", "Q (W)", 112),
      editableColumn("l", "l (m)", 100),
      {
        title: "管径 dn_locked",
        dataIndex: "dn_locked",
        key: "dn_locked",
        width: 140,
        render: (_, record) => (
          <Select
            disabled={disabled}
            value={record.dn_locked || ""}
            options={DN_OPTIONS}
            style={{ width: "100%" }}
            onChange={(value) => onChange?.(record.segment_id, "dn_locked", value || null)}
            onBlur={() => onBlurValidate?.(record.segment_id)}
          />
        ),
      },
      ...RESULT_COLUMNS.map(({ key, title }) => ({
        key,
        dataIndex: key,
        title,
        width: key === "G" ? 108 : 104,
        align: "right",
        render: (value) => renderResult(value),
      })),
      {
        title: "预警",
        key: "warning",
        width: 64,
        align: "center",
        render: (_, record) => {
          const matchedWarning = findWarningForSegment(warnings, record.segment_id);
          if (!matchedWarning) return null;
          return (
            <Tooltip title={matchedWarning} color="red">
              <WarningOutlined
                data-testid={`segment-warning-${record.segment_id}`}
                style={{ color: "#ff4d4f", fontSize: 17 }}
              />
            </Tooltip>
          );
        },
      },
    ];
  }, [disabled, onChange, onBlurValidate, warnings]);

  const rowClassName = (record) =>
    errorIdSet.has(Number(record.segment_id)) ? "segment-row-error" : "";

  const onRow = (record) => {
    if (!errorIdSet.has(Number(record.segment_id))) return {};
    return { style: { backgroundColor: "#fff1f0" } };
  };

  return (
    <Table
      rowKey="segment_id"
      size="middle"
      rowHoverable
      pagination={false}
      columns={columns}
      dataSource={segments}
      rowClassName={rowClassName}
      onRow={onRow}
      scroll={{ x: 1120, y: 268 }}
      className="segment-table"
      data-testid="segment-table"
    />
  );
}

/**
 * Dashboard.jsx — V8.2 结果决策看板（视觉升级版）
 *
 * 数据来源不变：只读取 Store 中的 calculationResult（全部来自后端 /calculate），
 * 不包含任何水力计算公式，仅做展示与阈值着色：
 * - 顶部三张指标卡片（Card + 图标 + 统一字号）：系统富裕度（≥10% 绿 / <10% 红）、
 *   最不利环路总阻力、作用压力；
 * - 中部：各并联环路不平衡率进度条（合格绿色、>15% 红色）；
 * - 底部：warnings → 异常诊断日志，文本与后端完全一致。
 */
import React from "react";
import { Card, Empty, Progress, Statistic, Tag, Typography } from "antd";
import {
  DashboardOutlined,
  HeatMapOutlined,
  RiseOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from "@ant-design/icons";

import { useProjectStore } from "../store/useProjectStore";

const MARGIN_THRESHOLD = 0.10;      // 系统富裕度阈值 10%（仅 UI 阈值着色）
const IMBALANCE_LIMIT = 15;         // 并联环路不平衡率限值 15%（仅 UI 阈值着色）

function SectionTitle({ children, extra }) {
  return (
    <div className="dashboard-section-title">
      <span>{children}</span>
      {extra ? <span className="dashboard-section-extra">{extra}</span> : null}
    </div>
  );
}

export default function Dashboard() {
  const calculationResult = useProjectStore((state) => state.currentProject.calculationResult);

  if (!calculationResult) {
    return (
      <Card
        size="small"
        className="dashboard-card dashboard-card-empty"
        title={
          <span className="panel-title">
            <DashboardOutlined /> 结果决策看板
          </span>
        }
        data-testid="dashboard"
      >
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="尚未计算：请填写管段参数后点击「开始计算」"
          data-testid="dashboard-empty"
        />
      </Card>
    );
  }

  const warnings = calculationResult.warnings || [];
  const parallelLoops =
    calculationResult.parallel_loops && calculationResult.parallel_loops.length > 0
      ? calculationResult.parallel_loops
      : calculationResult.part_b
        ? [calculationResult.part_b]
        : [];

  const margin = Number(calculationResult.margin);
  const marginLevel = margin >= MARGIN_THRESHOLD ? "success" : "warning";
  const marginColor = marginLevel === "success" ? "#16a34a" : "#dc2626";

  return (
    <Card
      size="small"
      className="dashboard-card"
      title={
        <span className="panel-title">
          <DashboardOutlined /> 结果决策看板
        </span>
      }
      extra={<Tag color="blue">数据来自 /calculate</Tag>}
      data-testid="dashboard"
    >
      {/* 顶部三张核心指标卡片 */}
      <div className="dashboard-stats">
        <Card size="small" className="stat-tile">
          <div className="stat-tile-label">
            <RiseOutlined /> 系统富裕度
          </div>
          <div data-testid="stat-margin" data-level={marginLevel}>
            <Statistic
              value={margin * 100}
              precision={2}
              suffix="%"
              valueStyle={{ color: marginColor, fontSize: 22, fontWeight: 700 }}
            />
          </div>
        </Card>
        <Card size="small" className="stat-tile">
          <div className="stat-tile-label">
            <HeatMapOutlined /> 最不利环路总阻力
          </div>
          <div data-testid="stat-sum-resistance">
            <Statistic
              value={calculationResult.sum_resistance}
              precision={2}
              suffix="Pa"
              valueStyle={{ color: "#0f172a", fontSize: 22, fontWeight: 700 }}
            />
          </div>
        </Card>
        <Card size="small" className="stat-tile">
          <div className="stat-tile-label">
            <ThunderboltOutlined /> 作用压力
          </div>
          <div data-testid="stat-dp-prime">
            <Statistic
              value={calculationResult.dP_prime}
              precision={2}
              suffix="Pa"
              valueStyle={{ color: "#0f172a", fontSize: 22, fontWeight: 700 }}
            />
          </div>
        </Card>
      </div>

      {/* 并联环路不平衡率 */}
      <SectionTitle extra={`限值 ±${IMBALANCE_LIMIT}%`}>并联环路不平衡率</SectionTitle>
      {parallelLoops.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="无并联环路数据"
          data-testid="imbalance-empty"
          className="dashboard-empty-inline"
        />
      ) : (
        <div className="dashboard-loops" data-testid="imbalance-chart">
          {parallelLoops.map((loop, index) => {
            const imbalancePercent = Number(loop.imbalance_percent);
            const overLimit = imbalancePercent > IMBALANCE_LIMIT;
            const progressPercent = Math.min(Math.max(imbalancePercent, 0), 100);
            return (
              <div
                key={`${loop.label || loop.sink_node || "loop"}-${index}`}
                className="dashboard-loop-row"
                data-testid="imbalance-row"
                data-over-limit={overLimit ? "true" : "false"}
                data-imbalance={imbalancePercent}
              >
                <div className="dashboard-loop-header">
                  <Tag color={overLimit ? "red" : "green"} className="dashboard-loop-tag">
                    {loop.label || loop.sink_node || `并联环路 ${index + 1}`}
                  </Tag>
                  <span className={`dashboard-loop-value ${overLimit ? "is-danger" : "is-ok"}`}>
                    {Number.isFinite(imbalancePercent) ? imbalancePercent.toFixed(2) : "—"}%
                  </span>
                  <span className="dashboard-loop-hint">{overLimit ? "超限 >15%" : "合格"}</span>
                </div>
                <Progress
                  data-testid="imbalance-progress"
                  percent={Number(progressPercent.toFixed(2))}
                  status={overLimit ? "exception" : "success"}
                  strokeColor={overLimit ? "#dc2626" : "#16a34a"}
                  showInfo={false}
                  size="small"
                />
              </div>
            );
          })}
        </div>
      )}

      {/* 异常诊断日志 */}
      <SectionTitle extra={`${warnings.length} 条`}>异常诊断日志</SectionTitle>
      {warnings.length === 0 ? (
        <Typography.Text type="secondary" data-testid="diagnosis-empty">
          无异常诊断记录
        </Typography.Text>
      ) : (
        <ul data-testid="diagnosis-warnings" className="dashboard-warnings">
          {warnings.map((warning, index) => (
            <li key={`${index}-${warning}`} data-testid="diagnosis-warning">
              <WarningOutlined className="dashboard-warning-icon" />
              <Typography.Text type="warning">{warning}</Typography.Text>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/**
 * Workbench.jsx — 登录后的主工作台：案例栏 + 画布 + 参数表 + 决策看板。
 *
 * V9.0.2 起业务逻辑按领域拆到 hooks：
 * - useTopologyValidation：前端预检 + 后端拓扑校验；
 * - useCalculation：/calculate 与竞态/中止；
 * - useExport：Markdown/Excel 导出。
 * 本组件只负责布局、表格编辑与展示状态。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, App as AntdApp, Card, Layout, Space, Spin, Tag, Typography } from "antd";
import {
  ApartmentOutlined,
  CheckCircleFilled,
  DatabaseOutlined,
  WarningFilled,
} from "@ant-design/icons";

import SegmentTable from "../../SegmentTable";
import Dashboard from "../Dashboard";
import Sidebar from "../Sidebar";
import TopologyEditor from "../TopologyEditor";
import WorkbenchHeader from "./WorkbenchHeader";
import { APP_VERSION } from "../../theme";
import { useCalculation } from "../../hooks/useCalculation";
import { useExport } from "../../hooks/useExport";
import { useTopologyValidation } from "../../hooks/useTopologyValidation";
import { useProjectStore } from "../../store/useProjectStore";

const { Content, Sider, Footer } = Layout;

export default function Workbench() {
  const { message } = AntdApp.useApp();

  // ---------------- Zustand Store ----------------
  const currentProject = useProjectStore((state) => state.currentProject);
  const updateSegment = useProjectStore((state) => state.updateSegment);
  const setCalculationResult = useProjectStore((state) => state.setCalculationResult);
  const markEdgeErrors = useProjectStore((state) => state.markEdgeErrors);
  const buildCalculationPayload = useProjectStore((state) => state.buildCalculationPayload);
  const initializeProjects = useProjectStore((state) => state.initializeProjects);
  const projectsLoading = useProjectStore((state) => state.projectsLoading);
  const projectLoading = useProjectStore((state) => state.projectLoading);
  const projectsList = useProjectStore((state) => state.projectsList);
  const storeError = useProjectStore((state) => state.error);

  // 纯 UI 瞬时状态
  const [segmentErrors, setSegmentErrors] = useState([]);

  const segments = currentProject.segments;
  const calcResult = currentProject.calculationResult;
  const calculationNotConverged = calcResult?.status === "not_converged";

  // V6.2：页面加载时从后端 SQLite 拉取案例列表并自动加载上次活跃/第一个项目
  useEffect(() => {
    initializeProjects();
  }, [initializeProjects]);

  // 校验/计算/导出三段逻辑分别由 hooks 承担
  const {
    topologyErrors,
    validating,
    validateTopologyBeforeRequest,
    handleValidateTopology,
  } = useTopologyValidation({
    currentProject,
    setSegmentErrors,
    markEdgeErrors,
    buildCalculationPayload,
    message,
  });

  const { calculating, handleCalculate } = useCalculation({
    currentProject,
    setSegmentErrors,
    markEdgeErrors,
    buildCalculationPayload,
    validateTopologyBeforeRequest,
    message,
  });

  const { exporting, handleCopyMarkdown, handleExportExcel } = useExport({
    currentProject,
    buildCalculationPayload,
    calculationNotConverged,
    message,
  });

  /**
   * 表格数据 = Store.segments 按顺序映射为「管段 1..N」显示序号，
   * 并合并后端计算结果。结果必须按后端 segment_id 查找，禁止使用数组下标，
   * 否则删除/新增管段后旧结果会错位到别的行。
   */
  const tableSegments = useMemo(() => {
    const results = calcResult?.segment_results || [];
    const resultBySegmentId = new Map(
      results.map((item) => [Number(item?.segment_id), item]),
    );
    return (segments || []).map((segment, index) => {
      const base = { ...segment, segment_id: index + 1 };
      const backendResult = resultBySegmentId.get(Number(base.segment_id));
      if (!backendResult) return base;
      return {
        ...base,
        G: backendResult.G,
        v: backendResult.v,
        Rm: backendResult.Rm,
        dPy: backendResult.dPy,
        dPj: backendResult.dPj,
        dP_total: backendResult.dP_total,
      };
    });
  }, [segments, calcResult]);

  /** 表格编辑：显示序号 → Store 中的源管段（Q/l/dn_locked） */
  const handleSegmentChange = useCallback(
    (displaySegmentId, field, value) => {
      const index = Number(displaySegmentId) - 1;
      const target = segments[index];
      if (!target) return;
      updateSegment(target.segment_id, field, value);
      if (calcResult) setCalculationResult(null);
    },
    [segments, updateSegment, setCalculationResult, calcResult],
  );

  /** 表格失焦校验：仅做输入完整性检查（Q/l 必须不小于前端下限 0.1），不做水力计算 */
  const handleRowBlur = useCallback(
    (displaySegmentId) => {
      const index = Number(displaySegmentId) - 1;
      const row = segments[index];
      const invalid = !row || !(Number(row.Q) >= 0.1) || !(Number(row.l) >= 0.1);
      setSegmentErrors((errors) => {
        const hasError = errors.includes(displaySegmentId);
        if (invalid && !hasError) return [...errors, displaySegmentId];
        if (!invalid && hasError) return errors.filter((id) => id !== displaySegmentId);
        return errors;
      });
    },
    [segments],
  );

  const topologyStatus = topologyErrors.length > 0
    ? { color: "error", icon: <WarningFilled />, text: "拓扑异常" }
    : calcResult
      ? calculationNotConverged
        ? { color: "error", icon: <WarningFilled />, text: "计算未收敛" }
        : { color: "success", icon: <CheckCircleFilled />, text: "计算完成" }
      : { color: "default", icon: null, text: "待计算" };

  return (
    <Layout className="app-shell">
      {/* 左侧固定案例管理栏 */}
      <Sider width={272} className="app-sider" theme="light">
        <div className="app-brand">
          <div className="app-brand-mark">
            <ApartmentOutlined />
          </div>
          <div className="app-brand-text">
            <div className="app-brand-title" data-testid="brand-title">HeatingApp_CZY</div>
            <div className="app-brand-subtitle">陈致远 暖通计算工作台</div>
          </div>
          <Tag className="app-brand-version">{APP_VERSION}</Tag>
        </div>
        <div className="app-sider-scroll">
          <Sidebar />
        </div>
      </Sider>

      <Layout className="app-main">
        <WorkbenchHeader
          currentProject={currentProject}
          topologyStatus={topologyStatus}
          projectsLoading={projectsLoading}
          projectLoading={projectLoading}
          validating={validating}
          calculating={calculating}
          exporting={exporting}
          calcResult={calcResult}
          calculationNotConverged={calculationNotConverged}
          onValidate={handleValidateTopology}
          onCalculate={handleCalculate}
          onCopyMarkdown={handleCopyMarkdown}
          onExportExcel={handleExportExcel}
        />

        <Content className="app-content">
          {calculationNotConverged ? (
            <Alert
              className="app-error-alert"
              type="error"
              showIcon
              data-testid="not-converged-banner"
              message="计算未收敛：结果不可保存或导出"
              description={`Hardy-Cross 已达最大迭代次数仍未满足 0.1 Pa 闭合差。请检查环状管网拓扑、局部阻力与管径后重新计算。${
                Array.isArray(calcResult?.warnings) && calcResult.warnings.length > 0
                  ? ` ${calcResult.warnings[0]}`
                  : ""
              }`}
            />
          ) : null}

          {storeError ? (
            <Alert
              className="app-error-alert"
              type="error"
              showIcon
              message="后端数据库连接失败"
              description={storeError}
              data-testid="db-error-alert"
            />
          ) : null}

          {!projectsLoading && projectsList.length === 0 ? (
            <Alert
              className="app-empty-alert"
              type="info"
              showIcon
              message="当前账号暂无工程案例"
              description="请点击左侧「新建案例」创建第一个案例，然后开始编辑拓扑、填写管段参数并计算。"
              data-testid="empty-project-hint"
            />
          ) : null}

          <div className="app-loading-shell">
            <Spin spinning={projectsLoading || projectLoading} tip="正在从 SQLite 加载案例…" className="app-loading-spin">
              <div className="workspace-grid">
                {/* 主画布 */}
                <Card
                  className="panel-card canvas-card"
                  title={
                    <span className="panel-title">
                      <ApartmentOutlined /> 拓扑建模
                    </span>
                  }
                  extra={
                    <Space size={6}>
                      <Tag>节点 {currentProject.nodes.length}</Tag>
                      <Tag>连线 {currentProject.edges.length}</Tag>
                      <Tag color="blue">管段 {segments.length}</Tag>
                    </Space>
                  }
                >
                  <TopologyEditor validationErrors={topologyErrors} />
                </Card>

                {/* 右侧：管段参数 + 决策看板 */}
                <div className="side-panel">
                  <Card
                    className="panel-card table-card"
                    title={
                      <span className="panel-title">
                        <DatabaseOutlined /> 管段参数
                      </span>
                    }
                    extra={<Tag color="blue">{segments.length} 段</Tag>}
                  >
                    <SegmentTable
                      segments={tableSegments}
                      segmentErrors={segmentErrors}
                      warnings={calcResult?.warnings || []}
                      onChange={handleSegmentChange}
                      onBlurValidate={handleRowBlur}
                      disabled={calculating}
                    />
                  </Card>
                  <div className="dashboard-panel">
                    <Dashboard />
                  </div>
                </div>
              </div>
            </Spin>
          </div>
        </Content>

        <Footer className="app-footer">
          <span className="app-footer-note" data-testid="app-footer-copyright">
            <CheckCircleFilled /> © 2026 陈致远 保留所有权利
          </span>
          <span className="app-footer-version">{APP_VERSION}</span>
        </Footer>
      </Layout>
    </Layout>
  );
}

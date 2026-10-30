/**
 * App.jsx — V5.0 可视化拓扑编辑 + 结果决策看板 + 报表导出
 *
 * 布局：左侧 Sidebar（案例管理） | 中间 TopologyEditor（拖拽画布） | 右侧 SegmentTable（管段参数）
 *      下方：结果决策看板 Dashboard（统计卡片 / 不平衡率进度条 / 异常诊断日志）
 *
 * 联动关系：
 * - TopologyEditor 读取 Store 的 nodes / edges；
 * - 画布连线成功 → Store.addSegment 自动生成管段（管段 N，Q/l/Σξ=0，dn_locked=null）；
 * - 画布删除连线 → Store.removeSegment 按 edge id 同步删除管段；
 * - 表格展示 Store.segments（显示序号映射为 管段 1..N），编辑 Q/l/dn_locked 写回 Store；
 * - 计算结果仍全部来自后端 /calculate，前端不包含任何水力公式。
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  ConfigProvider,
  Layout,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  ApartmentOutlined,
  CheckCircleFilled,
  CopyOutlined,
  DatabaseOutlined,
  FileExcelOutlined,
  PlayCircleOutlined,
  SafetyCertificateOutlined,
  WarningFilled,
} from "@ant-design/icons";
import zhCN from "antd/locale/zh_CN";

import Login from "./pages/Login";
import SegmentTable from "./SegmentTable";
import Dashboard from "./components/Dashboard";
import Sidebar from "./components/Sidebar";
import TopologyEditor from "./components/TopologyEditor";
import { APP_VERSION, engineeringTheme } from "./theme";
import { getAuthToken } from "./utils/request";
import { validateTopology } from "./utils/topologyValidator";
import { useProjectStore } from "./store/useProjectStore";

const { Header, Content, Sider, Footer } = Layout;

/**
 * 复制文本到剪贴板：优先 navigator.clipboard，失败时回退到隐藏 textarea 方案。
 */
async function copyTextToClipboard(text) {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

/**
 * 接口前缀：统一走 Vite 开发代理 /api（见 vite.config.js），浏览器同源请求规避 CORS。
 */
export const API_BASE = "/api";

/** 从 422 详情中提取涉及的管段序号（后端 detail 形如“管段2：已达最小管径 DN15…”） */
export function extractSegmentIds(detail) {
  const ids = new Set();
  const text = typeof detail === "string" ? detail : JSON.stringify(detail ?? "");
  const patterns = [/管段\s*(\d+)/g, /segment[_ ]?id["'\s:=]*(\d+)/gi];
  for (const pattern of patterns) {
    let match = pattern.exec(text);
    while (match !== null) {
      ids.add(Number(match[1]));
      match = pattern.exec(text);
    }
  }
  return Array.from(ids);
}

function Workbench() {
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
  const [validating, setValidating] = useState(false);
  const [calculating, setCalculating] = useState(false);
  const [exporting, setExporting] = useState(null); // "markdown" | "excel" | null
  const [topologyErrors, setTopologyErrors] = useState([]); // 前端拓扑预校验错误

  const segments = currentProject.segments;
  const calcResult = currentProject.calculationResult;

  // V6.2：页面加载时从后端 SQLite 拉取案例列表并自动加载上次活跃/第一个项目
  useEffect(() => {
    initializeProjects();
  }, [initializeProjects]);


  // 切换案例时清空前端拓扑校验警告
  useEffect(() => {
    setTopologyErrors([]);
  }, [currentProject.id]);

  /** 点击计算/校验前的统一前端拓扑预校验（失败直接弹错并中止请求） */
  const validateTopologyBeforeRequest = useCallback(() => {
    const validation = validateTopology(currentProject.nodes, currentProject.edges);
    if (!validation.valid) {
      setTopologyErrors(validation.errors);
      message.error(validation.errors[0] || "拓扑校验未通过，请检查管网连接");
      return false;
    }
    setTopologyErrors([]);
    return true;
  }, [currentProject.nodes, currentProject.edges]);

  /**
   * 表格数据 = Store.segments 按顺序映射为「管段 1..N」显示序号，
   * 并合并后端计算结果（按 payload 顺序 index 对应，前端不做任何计算）。
   */
  const tableSegments = useMemo(() => {
    const results = calcResult?.segment_results || [];
    return (segments || []).map((segment, index) => {
      const backendResult = results[index];
      const base = { ...segment, segment_id: index + 1 };
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

  /** 表格失焦校验：仅做输入完整性检查（Q/l 必须为正数），不做水力计算 */
  const handleRowBlur = useCallback(
    (displaySegmentId) => {
      const index = Number(displaySegmentId) - 1;
      const row = segments[index];
      const invalid = !row || !(Number(row.Q) > 0) || !(Number(row.l) > 0);
      setSegmentErrors((errors) => {
        const hasError = errors.includes(displaySegmentId);
        if (invalid && !hasError) return [...errors, displaySegmentId];
        if (!invalid && hasError) return errors.filter((id) => id !== displaySegmentId);
        return errors;
      });
    },
    [segments],
  );

  /** 提交校验：POST /validate-topology；422 时解析 detail、标红行与拓扑边 */
  const handleValidateTopology = useCallback(async () => {
    if (!validateTopologyBeforeRequest()) return;
    setValidating(true);
    try {
      const response = await fetch(`${API_BASE}/validate-topology`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildCalculationPayload()),
      });
      if (response.status === 422) {
        const errorBody = await response.json();
        const detail = errorBody?.detail ?? "拓扑校验失败（422）";
        const failedIds = extractSegmentIds(detail);
        if (typeof detail === "string") setTopologyErrors([detail]);
        message.error(typeof detail === "string" ? detail : "拓扑校验失败，请检查输入");
        setSegmentErrors(failedIds);
        markEdgeErrors(failedIds);
        return;
      }
      if (!response.ok) {
        message.error(`拓扑校验请求失败：HTTP ${response.status}`);
        return;
      }
      const result = await response.json();
      const backendErrors = Array.isArray(result?.errors) ? result.errors : [];
      if (result?.valid) {
        setTopologyErrors([]);
        message.success("拓扑校验通过");
      } else {
        setTopologyErrors(backendErrors);
        message.error(backendErrors[0] || result?.message || "拓扑校验未通过，请检查拓扑");
      }
      setSegmentErrors([]);
      markEdgeErrors([]);
    } finally {
      setValidating(false);
    }
  }, [buildCalculationPayload, markEdgeErrors, validateTopologyBeforeRequest]);

  /** 开始计算：POST /calculate；结果全部来源于后端，写入 Store 后渲染 */
  const handleCalculate = useCallback(async () => {
    if (!validateTopologyBeforeRequest()) return;
    setCalculating(true);
    try {
      const response = await fetch(`${API_BASE}/calculate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildCalculationPayload()),
      });
      if (response.status === 422) {
        const errorBody = await response.json();
        const detail = errorBody?.detail ?? "前置条件失败（422）";
        const failedIds = extractSegmentIds(detail);
        message.error(typeof detail === "string" ? detail : "计算输入校验失败，请检查参数");
        setSegmentErrors(failedIds);
        markEdgeErrors(failedIds);
        return;
      }
      if (!response.ok) {
        message.error(`计算请求失败：HTTP ${response.status}`);
        return;
      }
      const result = await response.json();
      setCalculationResult(result);
      setSegmentErrors([]);
      message.success("计算完成（结果已写入当前案例，点击左侧「保存当前案例」可持久化）");
    } finally {
      setCalculating(false);
    }
  }, [buildCalculationPayload, setCalculationResult, markEdgeErrors, validateTopologyBeforeRequest]);

  /** 复制 Markdown 报表：POST /export/markdown → navigator.clipboard.writeText */
  const handleCopyMarkdown = useCallback(async () => {
    setExporting("markdown");
    try {
      const response = await fetch(
        `${API_BASE}/export/markdown?project_name=${encodeURIComponent(currentProject.name)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildCalculationPayload()),
        },
      );
      if (!response.ok) {
        const detail = await response.text();
        message.error(`Markdown 导出失败：HTTP ${response.status} ${detail.slice(0, 200)}`);
        return;
      }
      const markdown = await response.text();
      await copyTextToClipboard(markdown);
      message.success("Markdown 报表已复制到剪贴板");
    } finally {
      setExporting(null);
    }
  }, [buildCalculationPayload, currentProject.name]);

  /** 导出 Excel 报表：POST /export/excel → Blob 触发浏览器下载 */
  const handleExportExcel = useCallback(async () => {
    setExporting("excel");
    try {
      const response = await fetch(
        `${API_BASE}/export/excel?project_name=${encodeURIComponent(currentProject.name)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildCalculationPayload()),
        },
      );
      if (!response.ok) {
        const detail = await response.text();
        message.error(`Excel 导出失败：HTTP ${response.status} ${detail.slice(0, 200)}`);
        return;
      }
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = `${currentProject.name || "案例"}_水力计算表.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      message.success("Excel 报表已开始下载");
    } finally {
      setExporting(null);
    }
  }, [buildCalculationPayload, currentProject.name]);

  const topologyStatus = topologyErrors.length > 0
    ? { color: "error", icon: <WarningFilled />, text: "拓扑异常" }
    : calcResult
      ? { color: calcResult.status === "converged" ? "success" : "warning", icon: <CheckCircleFilled />, text: "计算完成" }
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
          {/* 顶部：项目标题 / 状态 / 操作 */}
          <Header className="app-header">
            <div className="app-header-left">
              <div className="app-header-eyebrow">GRAVITY HEATING · HYDRAULIC CALCULATION</div>
              <div className="app-header-title-row">
                <Typography.Title level={4} className="app-header-title">
                  重力循环双管热水供暖管网水力计算
                </Typography.Title>
                <Tag
                  color="blue"
                  icon={<DatabaseOutlined />}
                  className="app-project-tag"
                  data-testid="current-project-title"
                >
                  当前案例：{currentProject.name}
                </Tag>
                <Tag color={topologyStatus.color} icon={topologyStatus.icon} className="app-status-tag">
                  {topologyStatus.text}
                </Tag>
                {projectsLoading || projectLoading ? (
                  <Tag color="processing" data-testid="db-loading-tag" className="app-status-tag">
                    正在从 SQLite 加载…
                  </Tag>
                ) : null}
              </div>
            </div>

            <Space className="app-header-actions" size={8} wrap>
              <Button
                icon={<SafetyCertificateOutlined />}
                loading={validating}
                onClick={handleValidateTopology}
                data-testid="btn-validate"
              >
                提交校验
              </Button>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                loading={calculating}
                onClick={handleCalculate}
                data-testid="btn-calculate"
              >
                开始计算
              </Button>
              <Button
                data-testid="btn-copy-markdown"
                icon={<CopyOutlined />}
                loading={exporting === "markdown"}
                disabled={!calcResult || Boolean(exporting)}
                onClick={handleCopyMarkdown}
              >
                复制报表
              </Button>
              <Button
                data-testid="btn-export-excel"
                icon={<FileExcelOutlined />}
                loading={exporting === "excel"}
                disabled={!calcResult || Boolean(exporting)}
                onClick={handleExportExcel}
              >
                导出 Excel
              </Button>
            </Space>
          </Header>

          <Content className="app-content">
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

/**
 * 应用根组件：设计系统 Theme + 中文 locale + Ant Design App 上下文 + 登录路由守卫。
 * 本地无 token 时只渲染 <Login />；登录成功后切回原管理系统 Layout。
 */
export default function App() {
  const [authToken, setAuthToken] = useState(() => getAuthToken());

  return (
    <ConfigProvider theme={engineeringTheme} locale={zhCN}>
      <AntdApp>
        {authToken ? <Workbench /> : <Login onLogin={(token) => setAuthToken(token)} />}
      </AntdApp>
    </ConfigProvider>
  );
}

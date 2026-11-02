/**
 * WorkbenchHeader.jsx — 工作台顶部：标题、当前案例、状态与四个操作按钮。
 *
 * 纯展示组件；校验/计算/导出状态与回调由 Workbench 传入。
 */
import { Button, Layout, Space, Tag, Typography } from "antd";
import {
  CopyOutlined,
  DatabaseOutlined,
  FileExcelOutlined,
  PlayCircleOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";

const { Header } = Layout;

export default function WorkbenchHeader({
  currentProject,
  topologyStatus,
  projectsLoading,
  projectLoading,
  validating,
  calculating,
  exporting,
  calcResult,
  calculationNotConverged,
  onValidate,
  onCalculate,
  onCopyMarkdown,
  onExportExcel,
}) {
  return (
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
          onClick={onValidate}
          data-testid="btn-validate"
        >
          提交校验
        </Button>
        <Button
          type="primary"
          icon={<PlayCircleOutlined />}
          loading={calculating}
          onClick={onCalculate}
          data-testid="btn-calculate"
        >
          开始计算
        </Button>
        <Button
          data-testid="btn-copy-markdown"
          icon={<CopyOutlined />}
          loading={exporting === "markdown"}
          disabled={!calcResult || Boolean(exporting) || calculationNotConverged}
          onClick={onCopyMarkdown}
        >
          复制报表
        </Button>
        <Button
          data-testid="btn-export-excel"
          icon={<FileExcelOutlined />}
          loading={exporting === "excel"}
          disabled={!calcResult || Boolean(exporting) || calculationNotConverged}
          onClick={onExportExcel}
        >
          导出 Excel
        </Button>
      </Space>
    </Header>
  );
}

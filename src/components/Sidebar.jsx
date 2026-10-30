/**
 * Sidebar.jsx — V6.2 案例管理侧边栏（后端 SQLite API）
 *
 * 功能：
 * - 新建案例（POST /projects，立即写入 SQLite）
 * - 修改当前案例名称 / 保存当前案例（PUT /projects/{id}）
 * - 案例列表（GET /projects）加载、克隆（GET+POST）、删除（DELETE）
 *
 * 边界：本组件不直接发 fetch，只调用 Zustand Store 的异步 action；
 *       Loading 状态同样来自 Store（projectsLoading/projectLoading/saving）。
 */
import React, { useEffect, useState } from "react";
import {
  App as AntdApp,
  Button,
  Card,
  Divider,
  Empty,
  Input,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  CopyOutlined,
  DeleteOutlined,
  FolderOpenOutlined,
  LogoutOutlined,
  PlusOutlined,
  SaveOutlined,
} from "@ant-design/icons";

import { useProjectStore } from "../store/useProjectStore";
import { clearAuthToken } from "../utils/request";

function formatTime(timestamp) {
  if (!timestamp) return "—";
  try {
    return new Date(timestamp).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return "—";
  }
}

export default function Sidebar() {
  const { message } = AntdApp.useApp();
  const currentProject = useProjectStore((state) => state.currentProject);
  const projectsList = useProjectStore((state) => state.projectsList);
  const projectsLoading = useProjectStore((state) => state.projectsLoading);
  const projectLoading = useProjectStore((state) => state.projectLoading);
  const saving = useProjectStore((state) => state.saving);
  const newProject = useProjectStore((state) => state.newProject);
  const setProjectName = useProjectStore((state) => state.setProjectName);
  const saveCurrentProject = useProjectStore((state) => state.saveCurrentProject);
  const loadProject = useProjectStore((state) => state.loadProject);
  const deleteProject = useProjectStore((state) => state.deleteProject);
  const cloneProject = useProjectStore((state) => state.cloneProject);

  const [newProjectName, setNewProjectName] = useState("");
  const [nameDraft, setNameDraft] = useState(currentProject.name);

  const handleLogout = () => {
    clearAuthToken();
    window.location.reload();
  };

  // 切换/加载/新建/克隆案例后，同步“当前案例名称”输入框
  useEffect(() => {
    setNameDraft(currentProject.name);
  }, [currentProject.id, currentProject.name]);

  const handleCreate = async () => {
    const name = newProjectName.trim() || "未命名案例";
    try {
      const created = await newProject(name);
      setNewProjectName("");
      message.success(`已新建案例「${created.name}」并保存到 SQLite 数据库`);
    } catch (error) {
      message.error(`新建案例失败：${error.message}`);
    }
  };

  const handleRename = () => {
    if (nameDraft.trim() && nameDraft.trim() !== currentProject.name) {
      setProjectName(nameDraft.trim());
    }
  };

  const handleSave = async () => {
    try {
      const saved = await saveCurrentProject();
      message.success(`案例「${saved.name}」已保存到 SQLite 数据库`);
    } catch (error) {
      message.error(`保存案例失败：${error.message}`);
    }
  };

  const handleLoad = async (project) => {
    try {
      await loadProject(project.id);
      message.success(`已从数据库加载案例「${project.name}」`);
    } catch (error) {
      message.error(`加载案例失败：${error.message}`);
    }
  };

  const handleClone = async (project) => {
    try {
      const cloned = await cloneProject(project.id);
      message.success(`已克隆为「${cloned.name}」并写入数据库`);
    } catch (error) {
      message.error(`克隆案例失败：${error.message}`);
    }
  };

  const handleDelete = async (project) => {
    try {
      await deleteProject(project.id);
      message.success(`已从数据库删除案例「${project.name}」`);
    } catch (error) {
      message.error(`删除案例失败：${error.message}`);
    }
  };

  const currentSaved = projectsList.find((project) => project.id === currentProject.id);
  const isUnsaved = !currentSaved || String(currentSaved.updatedAt) !== String(currentProject.updatedAt);

  return (
    <div
      data-testid="sidebar"
      className="sidebar-root"
      style={{ height: "100%", overflowY: "auto" }}
    >
      <div className="sidebar-heading-row">
        <Typography.Title level={5} className="sidebar-heading">
          📁 案例管理
        </Typography.Title>
        <Tag color="blue" className="sidebar-heading-tag">SQLite</Tag>
      </div>
      <Typography.Text className="sidebar-heading-hint">
        案例与计算结果保存于本地数据库
      </Typography.Text>
      <Button
        data-testid="btn-logout"
        block
        size="small"
        icon={<LogoutOutlined />}
        onClick={handleLogout}
        style={{
          marginBottom: 12,
          color: "#fecaca",
          borderColor: "rgba(248, 113, 113, 0.45)",
          background: "rgba(127, 29, 29, 0.28)",
        }}
      >
        退出登录
      </Button>

      {/* 新建案例 */}
      <Card size="small" title="新建案例" style={{ marginBottom: 12 }}>
        <Space direction="vertical" style={{ width: "100%" }} size={8}>
          <Input
            data-testid="new-project-name"
            placeholder="输入新案例名称（默认：未命名案例）"
            value={newProjectName}
            maxLength={40}
            onChange={(event) => setNewProjectName(event.target.value)}
            onPressEnter={handleCreate}
          />
          <Button
            type="primary"
            icon={<PlusOutlined />}
            block
            loading={saving}
            onClick={handleCreate}
            data-testid="btn-new-project"
          >
            新建案例
          </Button>
        </Space>
      </Card>

      {/* 当前案例 */}
      <Card
        size="small"
        title={
          <Space>
            <span>当前案例</span>
            {isUnsaved ? <Tag color="orange">未保存</Tag> : <Tag color="green">已保存</Tag>}
          </Space>
        }
        style={{ marginBottom: 12 }}
      >
        <Spin spinning={projectLoading}>
          <Space direction="vertical" style={{ width: "100%" }} size={8}>
            <Input
              data-testid="current-project-name"
              value={nameDraft}
              maxLength={40}
              onChange={(event) => setNameDraft(event.target.value)}
              onBlur={handleRename}
              onPressEnter={handleRename}
              placeholder="当前案例名称"
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              更新时间：{formatTime(currentProject.updatedAt)} · 管段数：{currentProject.segments.length}
            </Typography.Text>
            <Button
              type="primary"
              ghost
              icon={<SaveOutlined />}
              block
              loading={saving}
              onClick={handleSave}
              data-testid="btn-save-project"
            >
              保存当前案例
            </Button>
          </Space>
        </Spin>
      </Card>

      <Divider style={{ margin: "12px 0" }}>
        数据库案例（{projectsList.length}）
      </Divider>

      <Spin spinning={projectsLoading || projectLoading}>
        {projectsList.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              projectsLoading
                ? "正在从后端加载案例…"
                : "当前账号暂无案例，请点击上方「新建案例」"
            }
          />
        ) : (
          <Space direction="vertical" style={{ width: "100%" }} size={8}>
            {projectsList.map((project) => {
              const active = project.id === currentProject.id;
              return (
                <Card
                  key={project.id}
                  size="small"
                  data-testid={`project-card-${project.id}`}
                  style={{
                    borderColor: active ? "#1677ff" : undefined,
                    background: active ? "#f0f7ff" : undefined,
                  }}
                  title={
                    <Space size={4}>
                      <Tooltip title={project.name}>
                        <span
                          data-testid={`project-name-${project.id}`}
                          style={{
                            maxWidth: 150,
                            display: "inline-block",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {project.name}
                        </span>
                      </Tooltip>
                      {active ? <Tag color="blue">当前</Tag> : null}
                    </Space>
                  }
                  extra={
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      {formatTime(project.updatedAt)}
                    </Typography.Text>
                  }
                >
                  <Space size={4} wrap>
                    <Button
                      size="small"
                      icon={<FolderOpenOutlined />}
                      onClick={() => handleLoad(project)}
                      data-testid={`btn-load-${project.id}`}
                    >
                      加载
                    </Button>
                    <Button
                      size="small"
                      icon={<CopyOutlined />}
                      onClick={() => handleClone(project)}
                      data-testid={`btn-clone-${project.id}`}
                    >
                      克隆
                    </Button>
                    <Button
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={() => handleDelete(project)}
                      data-testid={`btn-delete-${project.id}`}
                    >
                      删除
                    </Button>
                  </Space>
                </Card>
              );
            })}
          </Space>
        )}
      </Spin>
    </div>
  );
}

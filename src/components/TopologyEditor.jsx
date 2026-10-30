/**
 * TopologyEditor.jsx — V3 可视化拓扑编辑器（React Flow）
 *
 * 能力：
 * - 接收 Store 中的 nodes / edges（受控模式，所有更新通过 Store 的不可变 action）；
 * - 节点面板拖拽入画布：HTML5 DnD + screenToFlowPosition 计算落点 → Store.addNode；
 * - 连线：onConnect → Store.addSegment（自动生成 “管段 N”，Q/l/Σξ=0，dn_locked=null）；
 * - 删除连线：React Flow 删除变更 → Store.removeSegment（按 edge id 同步删除管段）；
 * - 拖拽节点移动：只调用 Store.updateNodePosition（仅 x/y，不动管段数据）；
 * - 一键自动排版：调用 Store.autoLayout（Dagre TB），只重算 nodes 坐标。
 *
 * 边界：不包含任何水力计算、不发请求；节点/管段数据全部来自 useProjectStore。
 */
import React, { useCallback, useEffect, useMemo, useRef } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MiniMap,
  Panel,
  Position,
  ReactFlowProvider,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
} from "reactflow";
import "reactflow/dist/style.css";
import { Alert, Button, Tag, Typography } from "antd";
import { DeploymentUnitOutlined, PartitionOutlined } from "@ant-design/icons";

import NodePalette, { NODE_DRAG_MIME } from "./NodePalette";
import CycleFlowMap, { layoutCycleNodes } from "./CycleFlowMap";
import { NODE_TYPE_DEFS } from "../store/useProjectStore";
import { analyzeLoopTopology } from "../utils/loopValidator";
import { useProjectStore } from "../store/useProjectStore";

/** 工程节点：按锅炉 / 散热器 / 立管类型显示不同图标与配色（仅视觉，data 不变） */
function EquipmentNode({ data }) {
  const def = NODE_TYPE_DEFS[data?.nodeType] || { icon: "⚙️", color: "#64748b", label: "节点" };
  return (
    <div className="equipment-node">
      <span
        className="equipment-node-icon"
        style={{ background: `${def.color}18`, color: def.color, border: `1px solid ${def.color}33` }}
      >
        {def.icon || "⚙️"}
      </span>
      <span className="equipment-node-label">{data?.label || def.label}</span>
      <Handle type="target" position={Position.Top} className="equipment-handle" />
      <Handle type="source" position={Position.Bottom} className="equipment-handle" />
    </div>
  );
}

const NODE_TYPES = { default: EquipmentNode };

function TopologyEditorInner({ validationErrors = [] }) {
  const nodes = useProjectStore((state) => state.currentProject.nodes);
  const edges = useProjectStore((state) => state.currentProject.edges);
  const segments = useProjectStore((state) => state.currentProject.segments);
  const addNode = useProjectStore((state) => state.addNode);
  const updateNodePosition = useProjectStore((state) => state.updateNodePosition);
  const addSegment = useProjectStore((state) => state.addSegment);
  const removeSegment = useProjectStore((state) => state.removeSegment);
  const setNodes = useProjectStore((state) => state.setNodes);
  const setEdges = useProjectStore((state) => state.setEdges);
  const autoLayout = useProjectStore((state) => state.autoLayout);
  const projectId = useProjectStore((state) => state.currentProject.id);

  const wrapperRef = useRef(null);
  const { screenToFlowPosition, fitView } = useReactFlow();
  const loopInfo = useMemo(() => analyzeLoopTopology(nodes, edges), [nodes, edges]);

  // 切换/首次加载案例后自动适配视图（仅调整视口，不修改节点坐标）
  useEffect(() => {
    if (!projectId || nodes.length === 0) return undefined;
    const timer = window.setTimeout(() => {
      fitView({ padding: 0.18, duration: 260 });
    }, 150);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  /** 节点变更：位置 → updateNodePosition（只动 x/y）；其它（尺寸/选中）→ setNodes（不可变） */
  const handleNodesChange = useCallback(
    (changes) => {
      const positionChanges = changes.filter((change) => change.type === "position" && change.position);
      const otherChanges = changes.filter((change) => change.type !== "position");
      positionChanges.forEach((change) => updateNodePosition(change.id, change.position));
      if (otherChanges.length > 0) {
        setNodes(applyNodeChanges(otherChanges, nodes));
      }
    },
    [nodes, setNodes, updateNodePosition],
  );

  /** 边变更：删除 → removeSegment（按 edge id 删除管段）；其它（选中）→ setEdges */
  const handleEdgesChange = useCallback(
    (changes) => {
      const removedIds = changes.filter((change) => change.type === "remove").map((change) => change.id);
      removedIds.forEach((edgeId) => removeSegment(edgeId));
      const otherChanges = changes.filter((change) => change.type !== "remove");
      if (otherChanges.length > 0) {
        setEdges(applyEdgeChanges(otherChanges, edges));
      }
    },
    [edges, removeSegment, setEdges],
  );

  /** 连线成功：触发 Store.addSegment 自动生成管段 */
  const handleConnect = useCallback(
    (connection) => {
      if (connection?.source && connection?.target) {
        addSegment(connection.source, connection.target);
      }
    },
    [addSegment],
  );

  /**
   * 客户端坐标 → React Flow 画布坐标。
   *
   * React Flow v11 的 screenToFlowPosition 接收 client 视口坐标 {x, y}；
   * 为兼容不同版本/宿主，同时附带 clientX/clientY 字段。pywebview 的
   * WebView2 中窗口可能带原生边框，这里始终用浏览器事件坐标，而不用
   * screenX/screenY（后者会包含窗口/标题栏偏移）。
   */
  const flowPositionFromClient = useCallback(
    (clientX, clientY) => {
      const rect = wrapperRef.current?.getBoundingClientRect();
      const fallbackX = rect ? rect.left + rect.width / 2 : 0;
      const fallbackY = rect ? rect.top + rect.height / 2 : 0;
      const x = Number.isFinite(Number(clientX)) ? Number(clientX) : fallbackX;
      const y = Number.isFinite(Number(clientY)) ? Number(clientY) : fallbackY;
      return screenToFlowPosition({ x, y, clientX: x, clientY: y });
    },
    [screenToFlowPosition],
  );

  /** 浏览器原生 HTML5 drop：读取 dataTransfer；失败时返回 null */
  const readDroppedNodeType = useCallback((event) => {
    try {
      const raw =
        event.dataTransfer?.getData(NODE_DRAG_MIME) ||
        event.dataTransfer?.getData("text/plain") ||
        "";
      return raw && NODE_TYPE_DEFS[raw] ? raw : null;
    } catch {
      // pywebview/WebView2 宿主可能阻止 dataTransfer.getData；由点击/指针降级接管
      return null;
    }
  }, []);

  /** 浏览器 HTML5 drag 入画布 */
  const handleDrop = useCallback(
    (event) => {
      event.preventDefault();
      event.stopPropagation();
      const nodeType = readDroppedNodeType(event);
      if (!nodeType) return;
      const native = event.nativeEvent || event;
      addNode(
        nodeType,
        flowPositionFromClient(
          event.clientX ?? native.clientX,
          event.clientY ?? native.clientY,
        ),
      );
    },
    [addNode, flowPositionFromClient, readDroppedNodeType],
  );

  const handleDragOver = useCallback((event) => {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  }, []);

  const handleDragEnter = useCallback((event) => {
    event.preventDefault();
  }, []);

  /** pywebview 降级：点击节点 → 直接在画布中央生成（多次添加时轻微错开） */
  const handleQuickAdd = useCallback(
    (nodeType) => {
      if (!NODE_TYPE_DEFS[nodeType]) return;
      const rect = wrapperRef.current?.getBoundingClientRect();
      if (!rect) return;
      const index = nodes.length;
      const offsetX = (index % 5) * 26;
      const offsetY = (Math.floor(index / 5) % 5) * 24;
      const position = flowPositionFromClient(
        rect.left + rect.width / 2 + offsetX,
        rect.top + rect.height / 2 + offsetY,
      );
      addNode(nodeType, position);
    },
    [addNode, flowPositionFromClient, nodes.length],
  );

  /** pywebview 降级：自绘鼠标拖拽松手点 → 生成节点（只接受画布内落点） */
  const handlePalettePointerDrop = useCallback(
    (nodeType, point) => {
      if (!NODE_TYPE_DEFS[nodeType]) return;
      const rect = wrapperRef.current?.getBoundingClientRect();
      if (!rect) return;
      const clientX = Number(point?.clientX);
      const clientY = Number(point?.clientY);
      if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return;
      if (
        clientX < rect.left ||
        clientX > rect.right ||
        clientY < rect.top ||
        clientY > rect.bottom
      ) {
        return; // 未落在画布内：忽略，不把节点生成到视口外
      }
      addNode(nodeType, flowPositionFromClient(clientX, clientY));
    },
    [addNode, flowPositionFromClient],
  );

  /** 一键自动排版（Dagre TB）：只更新节点坐标，不改变连接关系与管段数据 */
  const handleAutoLayout = useCallback(() => {
    const layoutedCount = autoLayout();
    if (layoutedCount > 0) {
      window.requestAnimationFrame(() => fitView({ padding: 0.2, duration: 200 }));
    }
  }, [autoLayout, fitView]);

  /** 环状布局（V8.1）：把节点均匀排布在圆周上，使环路清晰可见且不重叠 */
  const handleCycleLayout = useCallback(() => {
    if (nodes.length === 0) return;
    const layoutedNodes = layoutCycleNodes(nodes, edges);
    setNodes(layoutedNodes);
    window.requestAnimationFrame(() => fitView({ padding: 0.2, duration: 200 }));
  }, [nodes, edges, setNodes, fitView]);

  return (
    <div
      style={{ display: "flex", flexDirection: "column", width: "100%", height: "100%", minHeight: 0 }}
    >
      {/* 前端拓扑预校验失败提示（由 App 在点击计算/校验前注入） */}
      {validationErrors.length > 0 ? (
        <Alert
          data-testid="topology-validation-alert"
          type="error"
          showIcon
          style={{ marginBottom: 8 }}
          message="拓扑校验未通过，请先修正以下问题"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {validationErrors.map((error, index) => (
                <li key={`${index}-${error}`}>{error}</li>
              ))}
            </ul>
          }
        />
      ) : null}
      {/* 画布上方：一键自动排版（Dagre TB） */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          marginBottom: 8,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Button
            data-testid="btn-auto-layout"
            type="dashed"
            icon={<PartitionOutlined />}
            onClick={handleAutoLayout}
            disabled={nodes.length === 0}
          >
            一键自动排版
          </Button>
          {loopInfo.hasCycle ? (
            <Button
              data-testid="btn-cycle-layout"
              type="dashed"
              icon={<DeploymentUnitOutlined />}
              onClick={handleCycleLayout}
              disabled={nodes.length === 0}
            >
              环状布局
            </Button>
          ) : null}
        </div>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {loopInfo.hasCycle
            ? `检测到环状管网（热源 ${loopInfo.heatSourceCount} 个）：后端将使用 Hardy-Cross 求解；可点击「环状布局」按圆周排布`
            : `Dagre 分层排版（TB，节点 {150}×{40}）：仅调整节点坐标，不改变连接与管段数据`}
        </Typography.Text>
      </div>
      {loopInfo.hasCycle ? (
        <div style={{ marginBottom: 8 }}>
          <CycleFlowMap nodes={nodes} edges={edges} height={170} />
        </div>
      ) : null}
      <NodePalette onQuickAdd={handleQuickAdd} onPointerDrop={handlePalettePointerDrop} />
      <div
        ref={wrapperRef}
        data-testid="topology-editor"
        className="topology-canvas"
        style={{
          position: "relative",
          flex: 1,
          minHeight: 360,
          border: "1px solid #e8edf5",
          borderRadius: 8,
          overflow: "hidden",
          background: "#fbfdff",
        }}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragEnter={handleDragEnter}
      >
        <ReactFlow
          nodeTypes={NODE_TYPES}
          nodes={nodes}
          edges={edges}
          onNodesChange={handleNodesChange}
          onEdgesChange={handleEdgesChange}
          onConnect={handleConnect}
          deleteKeyCode={["Backspace", "Delete"]}
          proOptions={{ hideAttribution: true }}
          minZoom={0.2}
        >
          <Background variant="dots" gap={20} size={1.4} color="#c9d6e8" />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable style={{ background: "#fff" }} />
          <Panel position="top-right">
            <Tag color="blue" className="canvas-counter-tag">
              节点 {nodes.length} · 连线 {edges.length} · 管段 {segments.length}
            </Tag>
          </Panel>
          {nodes.length === 0 ? (
            <Panel position="top-left">
              <Typography.Text type="secondary">
                从上方节点面板拖拽（桌面端可点击/拖动）「锅炉 / 散热器 / 立管节点」到此处开始绘制拓扑
              </Typography.Text>
            </Panel>
          ) : null}
        </ReactFlow>
      </div>
    </div>
  );
}

export default function TopologyEditor({ validationErrors = [] }) {
  return (
    <ReactFlowProvider>
      <TopologyEditorInner validationErrors={validationErrors} />
    </ReactFlowProvider>
  );
}

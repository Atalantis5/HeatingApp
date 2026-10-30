/**
 * CycleFlowMap.jsx — 环状拓扑可视化 / 环形布局（V8.1）
 *
 * 组成：
 * - layoutCycleNodes(nodes, edges)：纯函数环形布局，把节点均匀放在圆周上，
 *   返回“仅替换 position、其余字段保留”的新 nodes 数组，供 TopologyEditor 一键应用；
 * - <CycleFlowMap />：环状管网缩略示意图（SVG），用于在拓扑编辑器上方直观查看环路，
 *   不依赖重型图表库，也不会与主 React Flow 画布争抢交互。
 *
 * 说明：本组件只做布局与展示，不包含任何水力计算。
 */
import React from "react";

export const CYCLE_LAYOUT_CENTER = { x: 420, y: 320 };

/** 环形布局：节点按 id 排序后均匀分布在圆上；radius 随节点数自适应，避免重叠 */
export function layoutCycleNodes(nodes = [], edges = []) {
  if (!Array.isArray(nodes) || nodes.length === 0) return [];
  const ordered = [...nodes].sort((left, right) => String(left.id).localeCompare(String(right.id)));
  const radius = Math.max(180, Math.min(460, ordered.length * 46 + 120));
  const center = CYCLE_LAYOUT_CENTER;
  return ordered.map((node, index) => {
    const angle = (2 * Math.PI * index) / ordered.length - Math.PI / 2;
    return {
      ...node,
      position: {
        x: Math.round((center.x + radius * Math.cos(angle) - 75) * 100) / 100,
        y: Math.round((center.y + radius * Math.sin(angle) - 20) * 100) / 100,
      },
    };
  });
}

/**
 * 环状拓扑 SVG 缩略图：
 * - 环形坐标 → 自动缩放到给定画布；
 * - 连线用线段 + 箭头表示流向；
 * - 节点显示短标签，环路在圆周上清晰可辨且不重叠。
 */
export default function CycleFlowMap({ nodes = [], edges = [], height = 210 }) {
  if (!Array.isArray(nodes) || nodes.length === 0) {
    return (
      <div
        data-testid="cycle-flow-map"
        style={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          border: "1px dashed #d9d9d9",
          borderRadius: 8,
          color: "#999",
          fontSize: 12,
          background: "#fafafa",
        }}
      >
        暂无环状拓扑
      </div>
    );
  }

  const positioned = layoutCycleNodes(nodes, edges);
  const width = 480;
  const padding = 34;
  const xs = positioned.map((node) => node.position.x);
  const ys = positioned.map((node) => node.position.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = Math.max(maxX - minX, 1);
  const spanY = Math.max(maxY - minY, 1);
  const scale = Math.min((width - padding * 2) / spanX, (height - padding * 2) / spanY);
  const offsetX = (width - spanX * scale) / 2;
  const offsetY = (height - spanY * scale) / 2;
  const project = (node) => ({
    x: offsetX + (node.position.x - minX) * scale,
    y: offsetY + (node.position.y - minY) * scale,
  });
  const positionById = new Map(positioned.map((node) => [String(node.id), project(node)]));

  return (
    <div
      data-testid="cycle-flow-map"
      style={{
        height,
        border: "1px solid #f0f0f0",
        borderRadius: 8,
        background: "#fafcff",
        overflow: "hidden",
      }}
    >
      <svg width="100%" height="100%" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="环状管网布局预览">
        <defs>
          <marker id="cycle-flow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#8c8c8c" />
          </marker>
        </defs>
        {edges.map((edge, index) => {
          const source = positionById.get(String(edge.source));
          const target = positionById.get(String(edge.target));
          if (!source || !target) return null;
          return (
            <line
              key={edge.id || `cycle-edge-${index}`}
              x1={source.x}
              y1={source.y}
              x2={target.x}
              y2={target.y}
              stroke="#8c8c8c"
              strokeWidth={1.4}
              markerEnd="url(#cycle-flow-arrow)"
            />
          );
        })}
        {positioned.map((node) => {
          const point = positionById.get(String(node.id));
          const label = String(node.data?.label ?? node.id);
          const shortLabel = label.length > 9 ? `${label.slice(0, 9)}…` : label;
          return (
            <g key={node.id} data-testid={`cycle-map-node-${node.id}`}>
              <circle cx={point.x} cy={point.y} r={12} fill="#e6f4ff" stroke="#1677ff" strokeWidth={1.6} />
              <text x={point.x} y={point.y + 26} textAnchor="middle" fontSize={10} fill="#595959">
                {shortLabel}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

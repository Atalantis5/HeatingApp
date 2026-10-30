/**
 * TopologyView.jsx — 管网拓扑与连通性视图（React + React Flow）
 *
 * 任务边界（05_engine/04 ProgramFile 前端，第四步）：
 * - 计算剥离：本组件不包含任何水力计算逻辑，仅做拓扑图渲染；
 * - 数据读取：不读取 Excel / 数据库，不发送请求；
 * - 拓扑数据 topoData = { nodes, edges } 由父组件 App 传入（App 依据后端
 *   /validate-topology 的校验结果注入 edge.data.status = 'error'）；
 * - 错误边描红：edge.data.status === 'error' 时 stroke = #f87171；
 * - 状态更新约束：严禁原地 mutate nodes/edges；本组件用 useMemo +
 *   buildStyledEdges(...) 基于展开符创建全新数组/对象。
 */

import React, { useMemo } from "react";
import ReactFlow, { Background, Controls } from "reactflow";
import "reactflow/dist/style.css";

export const ERROR_EDGE_STROKE = "#f87171";

/**
 * 根据边的 data.status 生成新的边数组（不可变更新：输入数组/对象不被修改）。
 * - 错误边：stroke = #f87171，strokeWidth = 2；
 * - 正常边：清空错误描红（保留调用方原有 style 的其他字段）。
 * @param {Array<{id:string, source:string, target:string, style?:object, data?:object}>} edges
 * @returns {Array} 全新的边数组（元素亦为新对象）
 */
export function buildStyledEdges(edges) {
  if (!Array.isArray(edges)) return [];
  return edges.map((edge) => {
    const baseStyle = edge && edge.style ? { ...edge.style } : {};
    const isError = Boolean(edge && edge.data && edge.data.status === "error");
    if (isError) {
      baseStyle.stroke = ERROR_EDGE_STROKE;
      baseStyle.strokeWidth = 2;
    } else {
      delete baseStyle.stroke;
      delete baseStyle.strokeWidth;
    }
    return {
      ...edge,
      style: baseStyle,
      data: edge && edge.data ? { ...edge.data } : edge?.data,
    };
  });
}

export default function TopologyView({ topoData = { nodes: [], edges: [] }, height = 420 }) {
  // 不可变派生：nodes / edges 均创建新数组，绝不原地修改
  const nodes = useMemo(
    () => (Array.isArray(topoData.nodes) ? topoData.nodes.map((node) => ({ ...node })) : []),
    [topoData.nodes],
  );
  const edges = useMemo(() => buildStyledEdges(topoData.edges), [topoData.edges]);

  return (
    <div
      data-testid="topology-view"
      style={{ height, width: "100%", border: "1px solid #f0f0f0", borderRadius: 8, overflow: "hidden" }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        minZoom={0.2}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

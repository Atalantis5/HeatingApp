/**
 * layout.js — Dagre 自动排版算法封装（V6.1）
 *
 * 职责：把 React Flow 的 nodes（含当前 x/y）与 edges 交给 Dagre 做有向分层排版，
 * 返回“只替换 position、其它字段原样保留”的全新 nodes 数组。
 *
 * 约定：
 * - 节点默认尺寸 width=150、height=40（React Flow 的 position 指左上角，Dagre 输出中心点，
 *   因此回填时减去半宽/半高）；
 * - 方向 TB（从上到下）；
 * - 不修改输入数组/对象（避免 React Flow state mutation 警告）；
 * - 只处理存在的节点端点，孤立节点也会得到一个合法坐标；
 * - 不改变连接关系，只改变坐标。
 */
import { graphlib, layout } from "@dagrejs/dagre";

export const NODE_WIDTH = 150;
export const NODE_HEIGHT = 40;

const GRAPH_CONFIG = {
  rankdir: "TB", // 从上到下
  nodesep: 60,   // 同层节点水平间距
  ranksep: 80,   // 层间距
  marginx: 24,
  marginy: 24,
};

/** 保留两位小数，避免 Dagre 浮点坐标造成无意义抖动 */
function roundCoordinate(value) {
  return Math.round(Number(value) * 100) / 100;
}

/**
 * 使用 Dagre 计算节点坐标。
 *
 * @param {Array} nodes 当前 React Flow 节点数组（只读）
 * @param {Array} edges 当前 React Flow 边数组（只读）
 * @returns {Array} 新 nodes 数组（position 为 Dagre 结果，其它字段浅拷贝保留）
 */
export function layoutWithDagre(nodes = [], edges = []) {
  if (!Array.isArray(nodes) || nodes.length === 0) {
    return [];
  }

  const graph = new graphlib.Graph({ directed: true });
  graph.setGraph(GRAPH_CONFIG);
  graph.setDefaultEdgeLabel(() => ({}));

  const nodeIds = new Set(nodes.map((node) => String(node.id)));
  nodes.forEach((node) => {
    graph.setNode(String(node.id), { width: NODE_WIDTH, height: NODE_HEIGHT });
  });

  edges.forEach((edge) => {
    const source = String(edge.source);
    const target = String(edge.target);
    if (!nodeIds.has(source) || !nodeIds.has(target) || source === target) return;
    graph.setEdge(source, target);
  });

  layout(graph);

  return nodes.map((node) => {
    const positioned = graph.node(String(node.id));
    if (!positioned) {
      return { ...node };
    }
    return {
      ...node,
      position: {
        x: roundCoordinate(positioned.x - NODE_WIDTH / 2),
        y: roundCoordinate(positioned.y - NODE_HEIGHT / 2),
      },
    };
  });
}

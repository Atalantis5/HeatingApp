/**
 * loopValidator.js — 前端环状拓扑分析/校验工具（V8.1）
 *
 * V8.1 起系统支持环状管网与多热源：后端使用 Hardy-Cross 进行流量平差。
 * 本工具用于前端：
 * - 识别有向环（环状管网特征）与环上节点；
 * - 统计热源数量、判断无向连通性；
 * - 为 TopologyEditor 提供“是否显示环状布局 / 环状布局按钮”的判定，
 *   并在拓扑不满足环状计算前提时给出自然语言提示。
 *
 * 纯函数：不修改 nodes / edges，不发请求，不做水力计算。
 */
import { isBoilerNode } from "./topologyValidator";

/** 无向邻接表 */
export function buildUndirectedAdjacency(nodes = [], edges = []) {
  const adjacency = new Map();
  nodes.forEach((node) => adjacency.set(String(node.id), new Set()));
  edges.forEach((edge) => {
    const source = String(edge.source);
    const target = String(edge.target);
    if (!adjacency.has(source) || !adjacency.has(target)) return;
    adjacency.get(source).add(target);
    adjacency.get(target).add(source);
  });
  return adjacency;
}

/** 无向连通性（返回未连通的节点 id 列表） */
export function findDisconnectedNodes(nodes = [], edges = []) {
  if (nodes.length === 0) return [];
  const adjacency = buildUndirectedAdjacency(nodes, edges);
  const visited = new Set();
  const queue = [String(nodes[0].id)];
  while (queue.length > 0) {
    const current = queue.shift();
    if (visited.has(current)) continue;
    visited.add(current);
    for (const next of adjacency.get(current) || []) {
      if (!visited.has(next)) queue.push(next);
    }
  }
  return nodes.map((node) => String(node.id)).filter((id) => !visited.has(id));
}

/**
 * 有向 DFS 环检测：返回是否成环 + 环上节点 id 集合。
 * 三色标记：0=未访问、1=当前栈中、2=已完成；遇到栈中节点即命中环。
 */
export function detectDirectedCycles(nodes = [], edges = []) {
  const nodeIds = nodes.map((node) => String(node.id));
  const adjacency = new Map(nodeIds.map((id) => [id, []]));
  edges.forEach((edge) => {
    const source = String(edge.source);
    const target = String(edge.target);
    if (adjacency.has(source) && adjacency.has(target)) {
      adjacency.get(source).push(target);
    }
  });
  const state = new Map();
  const stack = [];
  const cycleNodes = new Set();
  let hasCycle = false;

  const visit = (nodeId) => {
    state.set(nodeId, 1);
    stack.push(nodeId);
    for (const next of adjacency.get(nodeId) || []) {
      const nextState = state.get(next) ?? 0;
      if (nextState === 0) {
        if (visit(next)) return true;
      } else if (nextState === 1) {
        hasCycle = true;
        const startIndex = stack.lastIndexOf(next);
        if (startIndex >= 0) {
          stack.slice(startIndex).forEach((id) => cycleNodes.add(id));
        }
      }
    }
    stack.pop();
    state.set(nodeId, 2);
    return false;
  };

  for (const nodeId of nodeIds) {
    if ((state.get(nodeId) ?? 0) === 0) visit(nodeId);
  }
  return { hasCycle, cycleNodeIds: Array.from(cycleNodes) };
}

/**
 * 环状拓扑综合分析（供 TopologyEditor 使用）。
 * @returns {{hasCycle:boolean, cycleNodeIds:string[], heatSourceCount:number,
 *            disconnectedNodeIds:string[], connected:boolean, canUseLoopLayout:boolean}}
 */
export function analyzeLoopTopology(nodes = [], edges = []) {
  const { hasCycle, cycleNodeIds } = detectDirectedCycles(nodes, edges);
  const heatSourceCount = nodes.filter((node) => isBoilerNode(node)).length;
  const disconnectedNodeIds = findDisconnectedNodes(nodes, edges);
  return {
    hasCycle,
    cycleNodeIds,
    heatSourceCount,
    disconnectedNodeIds,
    connected: disconnectedNodeIds.length === 0,
    canUseLoopLayout: hasCycle && heatSourceCount >= 1 && disconnectedNodeIds.length === 0,
  };
}

/**
 * 环状拓扑前置校验：允许存在环与多热源，但要求无向连通且至少一个热源。
 * @returns {{valid:boolean, errors:string[], mode:"tree"|"looped"}}
 */
export function validateLoopedTopology(nodes = [], edges = []) {
  const analysis = analyzeLoopTopology(nodes, edges);
  const errors = [];
  if (!analysis.hasCycle) {
    return { valid: true, errors, mode: "tree" };
  }
  if (analysis.heatSourceCount === 0) {
    errors.push("环状管网中未找到锅炉/热源节点：请至少保留一个热源作为定压边界");
  }
  if (!analysis.connected) {
    errors.push("环状管网不连通：存在无法连通的节点，请检查拓扑连接");
  }
  return { valid: errors.length === 0, errors, mode: "looped" };
}

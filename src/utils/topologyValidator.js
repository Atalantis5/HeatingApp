/**
 * topologyValidator.js — 前端拓扑预校验（V7.1）
 *
 * 在点击「开始计算 / 提交校验」之前，先在浏览器内做图论合法性检查，
 * 失败时返回人类可读的自然语言错误列表（由 App 用 message.error 弹出并中止请求）。
 *
 * 校验规则（与后端 controller 的防御逻辑对应）：
 * 1. 至少存在一个“锅炉”节点（热源；V8.1 起支持多热源）；
 * 2. 不能有孤立节点（无任何连线）；
 * 3. 不能有断路：除热源外，任一节点入度为 0（没有上游供热路径）；
 * 4. 环状管网在 V8.1 起允许存在（后端 Hardy-Cross 求解），此处仅记录 hasCycle 供环状布局使用；
 * 5. 管网必须连通（无向连通；环状/多热源不要求所有节点沿有向边可达）。
 *
 * 纯函数：不修改传入的 nodes / edges，不发起请求，不做任何水力计算。
 */

/** 判断节点是否为锅炉（兼容默认模型 nodeType=boiler，以及 label 含“锅炉”的节点） */
export function isBoilerNode(node) {
  const nodeType = node?.data?.nodeType ?? node?.type;
  if (nodeType === "boiler") return true;
  const label = String(node?.data?.label ?? node?.label ?? "");
  return label.includes("锅炉");
}

function nodeLabel(node) {
  return String(node?.data?.label ?? node?.label ?? node?.id ?? "未命名节点");
}

function segmentLabel(edge) {
  const raw = edge?.data?.segmentId ?? edge?.data?.segment_id ?? edge?.segment_id ?? edge?.id;
  if (raw === null || raw === undefined || raw === "") return String(edge?.id ?? "未知管段");
  const text = String(raw);
  return /^\d+$/.test(text) ? `管段${text}` : text;
}

/** 有向图 DFS 环检测（三色标记） */
function hasDirectedCycle(nodeIds, edges) {
  const adjacency = new Map(nodeIds.map((id) => [id, []]));
  edges.forEach((edge) => adjacency.get(edge.source)?.push(edge.target));
  const state = new Map(); // 0=未访问 1=访问中 2=已完成
  const visit = (nodeId) => {
    state.set(nodeId, 1);
    for (const next of adjacency.get(nodeId) || []) {
      const nextState = state.get(next) ?? 0;
      if (nextState === 1) return true;
      if (nextState === 0 && visit(next)) return true;
    }
    state.set(nodeId, 2);
    return false;
  };
  for (const nodeId of nodeIds) {
    if ((state.get(nodeId) ?? 0) === 0 && visit(nodeId)) return true;
  }
  return false;
}

/**
 * 校验拓扑。
 *
 * @param {Array} nodes React Flow / Store 节点数组
 * @param {Array} edges React Flow / Store 边数组
 * @returns {{valid:boolean, errors:string[], boilerCount:number, hasCycle:boolean}}
 */
export function validateTopology(nodes = [], edges = []) {
  const errors = [];
  const pushError = (message) => {
    if (!errors.includes(message)) errors.push(message);
  };

  const nodeList = Array.isArray(nodes) ? nodes : [];
  const edgeList = Array.isArray(edges) ? edges : [];

  if (nodeList.length === 0) {
    return {
      valid: false,
      errors: ["画布为空：请先拖入锅炉、立管/散热器等节点并连线后再计算"],
      boilerCount: 0,
      hasCycle: false,
    };
  }

  const nodeById = new Map();
  nodeList.forEach((node) => {
    const nodeId = String(node.id);
    if (nodeById.has(nodeId)) {
      pushError(`节点 id 重复：「${nodeLabel(node)}」(${nodeId})，请删除重复节点`);
      return;
    }
    nodeById.set(nodeId, node);
  });
  const nodeIds = Array.from(nodeById.keys());

  // 端点必须存在；忽略/拒绝悬空连线
  const validEdges = [];
  edgeList.forEach((edge) => {
    const source = String(edge?.source ?? "");
    const target = String(edge?.target ?? "");
    if (!nodeById.has(source) || !nodeById.has(target)) {
      pushError(`连线 ${edge?.id ?? ""} 的端点不在画布中（${source || "?"} → ${target || "?"}），请删除后重新连接`);
      return;
    }
    validEdges.push({ ...edge, source, target });
  });

  // 度数 / 入度 / 出度 / 出边
  const inDegree = new Map(nodeIds.map((id) => [id, 0]));
  const outDegree = new Map(nodeIds.map((id) => [id, 0]));
  const undirectedDegree = new Map(nodeIds.map((id) => [id, 0]));
  const outgoingEdges = new Map(nodeIds.map((id) => [id, []]));
  validEdges.forEach((edge) => {
    inDegree.set(edge.target, (inDegree.get(edge.target) || 0) + 1);
    outDegree.set(edge.source, (outDegree.get(edge.source) || 0) + 1);
    undirectedDegree.set(edge.source, (undirectedDegree.get(edge.source) || 0) + 1);
    undirectedDegree.set(edge.target, (undirectedDegree.get(edge.target) || 0) + 1);
    outgoingEdges.get(edge.source)?.push(edge);
  });

  // 1) 热源：V8.1 支持环状管网与多热源，只要求至少存在一个锅炉/热源节点
  const boilerNodes = nodeList.filter((node) => isBoilerNode(node));
  if (boilerNodes.length === 0) {
    pushError("未找到锅炉节点：请至少设置一个锅炉节点作为热源，并连接管网");
  }

  // 2) 孤立节点
  const isolatedIds = [];
  nodeList.forEach((node) => {
    const nodeId = String(node.id);
    if ((undirectedDegree.get(nodeId) || 0) === 0) {
      isolatedIds.push(nodeId);
      pushError(`节点「${nodeLabel(node)}」是孤立节点（没有任何连线），请连接它或删除该节点`);
    }
  });

  // 3) 断头路：非锅炉节点入度为 0
  const missingUpstreamIds = [];
  if (nodeList.length > 0) {
    nodeList.forEach((node) => {
      const nodeId = String(node.id);
      if (boilerNodes.some((boiler) => String(boiler.id) === nodeId)) return;
      if ((inDegree.get(nodeId) || 0) === 0 && !isolatedIds.includes(nodeId)) {
        missingUpstreamIds.push(nodeId);
        const segments = (outgoingEdges.get(nodeId) || []).map(segmentLabel).join("、");
        const segmentText = segments ? `${segments} 的起点` : "该节点";
        pushError(`节点「${nodeLabel(node)}」没有上游节点：${segmentText}缺少上游连接，请检查拓扑`);
      }
    });
  }

  // 4) 环状管网：V8.1 已支持，由后端 Hardy-Cross 求解；这里只记录是否有环用于布局提示
  const hasCycle = hasDirectedCycle(nodeIds, validEdges);

  // 5) 连通性（无向连通即可；环状/多热源不要求所有节点沿有向边可达）
  if (nodeIds.length > 0) {
    const undirectedAdjacency = new Map(nodeIds.map((id) => [id, new Set()]));
    validEdges.forEach((edge) => {
      undirectedAdjacency.get(edge.source)?.add(edge.target);
      undirectedAdjacency.get(edge.target)?.add(edge.source);
    });
    const visited = new Set();
    const queue = [nodeIds[0]];
    while (queue.length > 0) {
      const current = queue.shift();
      if (visited.has(current)) continue;
      visited.add(current);
      for (const next of undirectedAdjacency.get(current) || []) {
        if (!visited.has(next)) queue.push(next);
      }
    }
    const disconnected = nodeIds.filter((id) => !visited.has(id));
    if (disconnected.length > 0) {
      const first = nodeById.get(disconnected[0]);
      pushError(`管网不连通：节点「${nodeLabel(first)}」所在的连接部分与其余管网断开，请检查拓扑`);
    }
  }

  return {
    valid: errors.length === 0,
    errors,
    boilerCount: boilerNodes.length,
    hasCycle,
  };
}

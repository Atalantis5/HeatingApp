/**
 * useProjectStore.js — V6.2 案例状态管理（Zustand + 后端 SQLite API）
 *
 * 数据来源：FastAPI /projects REST API（SQLite projects.db），彻底废弃浏览器本地存储业务数据（所有案例读写均走后端 SQLite API）。
 * - 初始化：GET /projects 拉取列表，自动加载上次活跃（sessionStorage 仅记 id）或第一个项目；
 *   若列表为空，则自动重建一个空白案例（避免后端 404/空库时白屏）。
 * - 新建 / 保存 / 删除 / 克隆：全部为异步 fetch 请求，返回 Promise；组件需 await。
 * - 拓扑编辑 / Dagre 排版 / 计算 payload 组装仍在本地完成，但只有点击“保存”才写回后端。
 *
 * 本 Store 不包含任何水力计算；结果全部来自 /calculate。
 */
import { create } from "zustand";
import { nanoid } from "nanoid";

import { layoutWithDagre } from "../utils/layout";

import { fetchWithAuth } from "../utils/request";

const API_BASE = "/api";

/** sessionStorage 仅保存“上次活跃案例 id”（UI 偏好，非业务数据） */
const LAST_ACTIVE_KEY = "gravity-heating-last-active-project";

/** 默认工程参数（输入值，来自《默认模型的计算典例》，非计算结果） */
export const PROJECT_DEFAULTS = {
  H: 3,
  rho_h: 977.81,
  rho_g: 961.92,
  dPf: 350,
  rho_dynamic: 983,
  max_iter: 20,
  shared_segments: [],
};

/** 可拖拽节点类型定义（NodePalette 使用） */
export const NODE_TYPE_DEFS = {
  boiler: { label: "锅炉", color: "#fa8c16", icon: "🔥" },
  radiator: { label: "散热器", color: "#1677ff", icon: "♨️" },
  riser: { label: "立管节点", color: "#52c41a", icon: "↕️" },
};

/** 前端空案例（id=null 表示尚未从后端加载到任何案例） */
export const EMPTY_PROJECT = {
  id: null,
  name: "加载中…",
  nodes: [],
  edges: [],
  segments: [],
  calculationResult: null,
  createdAt: null,
  updatedAt: null,
};

export const DEFAULT_PROJECT = { ...EMPTY_PROJECT, name: "默认案例（空白拓扑）" };

// ---------------------------------------------------------------------------
// 本地辅助函数（拓扑编辑，与后端无关）
// ---------------------------------------------------------------------------
function parseSegmentNumber(segmentId) {
  const match = String(segmentId ?? "").match(/(\d+)/);
  return match ? Number(match[1]) : 0;
}

function nextSegmentId(segments) {
  const maxNumber = (segments || []).reduce((max, segment) => {
    return Math.max(max, parseSegmentNumber(segment.segment_id));
  }, 0);
  return `管段 ${maxNumber + 1}`;
}

function makeNodeStyle(nodeType) {
  const def = NODE_TYPE_DEFS[nodeType] || { color: "#8c8c8c" };
  return {
    fontSize: 12,
    fontWeight: 600,
    padding: "6px 10px",
    borderRadius: 8,
    border: `1px solid ${def.color}`,
    background: `${def.color}1a`,
    color: def.color,
    minWidth: 92,
    textAlign: "center",
  };
}

function projectData(project) {
  return {
    nodes: project.nodes || [],
    edges: project.edges || [],
    segments: project.segments || [],
    calculationResult: project.calculationResult ?? null,
  };
}

function normalizeSummary(row) {
  return { id: row.id, name: row.name, updatedAt: row.updated_at ?? null };
}

function normalizeDetail(row) {
  const data = row.data || {};
  return {
    id: row.id,
    name: row.name,
    nodes: data.nodes || [],
    edges: data.edges || [],
    segments: data.segments || [],
    calculationResult: data.calculationResult ?? null,
    createdAt: row.created_at ?? null,
    updatedAt: row.updated_at ?? null,
  };
}

function getLastActiveId() {
  try {
    return window.sessionStorage.getItem(LAST_ACTIVE_KEY);
  } catch (error) {
    console.warn("[useProjectStore] 读取 sessionStorage 失败：", error);
    return null;
  }
}

function setLastActiveId(projectId) {
  try {
    if (projectId) window.sessionStorage.setItem(LAST_ACTIVE_KEY, projectId);
    else window.sessionStorage.removeItem(LAST_ACTIVE_KEY);
  } catch (error) {
    console.warn("[useProjectStore] 写入 sessionStorage 失败：", error);
  }
}

/** 统一 HTTP 请求：失败抛错（不吞掉任何后端错误） */
async function apiRequest(path, options = {}) {
  const response = await fetchWithAuth(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    const error = new Error(`后端请求失败 HTTP ${response.status}：${text.slice(0, 300)}`);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
}

async function fetchProjectList() {
  const rows = await apiRequest("/projects");
  return rows.map(normalizeSummary);
}

async function fetchProjectDetail(projectId) {
  return normalizeDetail(await apiRequest(`/projects/${projectId}`));
}

const EMPTY_DATA = { nodes: [], edges: [], segments: [], calculationResult: null };

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------
export const useProjectStore = create((set, get) => ({
  // ---------------- state ----------------
  currentProject: EMPTY_PROJECT,
  projectsList: [],
  projectsLoading: false, // 列表加载
  projectLoading: false,  // 单个案例加载/切换
  saving: false,          // 保存中
  error: null,            // 网络/后端错误提示

  // ---------------- 初始化 / 同步 ----------------
  /** 页面初始化：GET /projects → 自动加载 lastActive / 第一个项目；
   * 空列表（如新注册用户）不自动创建案例，而是提示用户点击「新建案例」，
   * 以实现 V8.3 多租户空数据体验并避免隐式写入。 */
  initializeProjects: async () => {
    set({ projectsLoading: true, error: null });
    try {
      let list = await fetchProjectList();
      if (list.length === 0) {
        setLastActiveId(null);
        set({
          projectsList: [],
          currentProject: { ...DEFAULT_PROJECT, name: "未选择案例", id: null, createdAt: null, updatedAt: null },
        });
        return null;
      }
      set({ projectsList: list });
      const lastActiveId = getLastActiveId();
      const target = list.find((project) => project.id === lastActiveId) || list[0];
      return await get().loadProject(target.id);
    } catch (error) {
      console.error("[useProjectStore] 初始化案例列表失败：", error);
      set({ error: error.message });
      return null;
    } finally {
      set({ projectsLoading: false });
    }
  },

  /** 重新拉取案例列表 */
  refreshProjects: async () => {
    const list = await fetchProjectList();
    set({ projectsList: list });
    return list;
  },

  /** 加载案例完整数据（GET /projects/{id}） */
  loadProject: async (projectId) => {
    set({ projectLoading: true, error: null });
    try {
      const project = await fetchProjectDetail(projectId);
      set({ currentProject: project });
      setLastActiveId(project.id);
      return project;
    } catch (error) {
      console.error("[useProjectStore] 加载案例失败：", error);
      set({ error: error.message });
      throw error;
    } finally {
      set({ projectLoading: false });
    }
  },

  /** 新建案例（POST /projects，立即持久化到 SQLite） */
  newProject: async (name) => {
    const trimmed = name && String(name).trim() ? String(name).trim() : "未命名案例";
    set({ saving: true, error: null });
    try {
      const created = normalizeDetail(
        await apiRequest("/projects", {
          method: "POST",
          body: JSON.stringify({ name: trimmed, data: EMPTY_DATA }),
        }),
      );
      await get().refreshProjects();
      set({ currentProject: created });
      setLastActiveId(created.id);
      return created;
    } finally {
      set({ saving: false });
    }
  },

  /** 本地重命名（保存时统一 PUT 到后端） */
  setProjectName: (name) => {
    const trimmed = String(name ?? "").trim();
    set((state) => ({
      currentProject: {
        ...state.currentProject,
        name: trimmed || state.currentProject.name,
        updatedAt: Date.now(),
      },
    }));
  },

  /** 保存当前案例（PUT /projects/{id}，覆盖 nodes/edges/segments） */
  saveCurrentProject: async () => {
    const { currentProject } = get();
    if (!currentProject.id) {
      throw new Error("当前没有可保存的案例（尚未从后端加载）");
    }
    set({ saving: true, error: null });
    try {
      const saved = normalizeDetail(
        await apiRequest(`/projects/${currentProject.id}`, {
          method: "PUT",
          body: JSON.stringify({ name: currentProject.name, data: projectData(currentProject) }),
        }),
      );
      await get().refreshProjects();
      set({ currentProject: saved });
      setLastActiveId(saved.id);
      return saved;
    } catch (error) {
      console.error("[useProjectStore] 保存案例失败：", error);
      set({ error: error.message });
      throw error;
    } finally {
      set({ saving: false });
    }
  },

  /** 删除案例（DELETE /projects/{id}） */
  deleteProject: async (projectId) => {
    await apiRequest(`/projects/${projectId}`, { method: "DELETE" });
    const list = await get().refreshProjects();
    if (get().currentProject.id === projectId) {
      if (list.length > 0) {
        await get().loadProject(list[0].id);
      } else {
        set({ currentProject: { ...DEFAULT_PROJECT } });
        setLastActiveId(null);
      }
    }
    return true;
  },

  /** 克隆案例（GET 源 → POST 副本） */
  cloneProject: async (projectId) => {
    const source = await fetchProjectDetail(projectId);
    const cloned = normalizeDetail(
      await apiRequest("/projects", {
        method: "POST",
        body: JSON.stringify({ name: `${source.name}（副本）`, data: projectData(source) }),
      }),
    );
    await get().refreshProjects();
    set({ currentProject: cloned });
    setLastActiveId(cloned.id);
    return cloned;
  },

  /** 保存后端计算结果（全部来自 /calculate） */
  setCalculationResult: (result) => {
    set((state) => ({
      currentProject: { ...state.currentProject, calculationResult: result, updatedAt: Date.now() },
    }));
  },

  /** 给拓扑边注入错误状态（/validate-topology 422 后描红；不可变更新） */
  markEdgeErrors: (failedIds) => {
    const failed = new Set((failedIds || []).map(Number));
    set((state) => ({
      currentProject: {
        ...state.currentProject,
        nodes: state.currentProject.nodes.map((node) => ({ ...node })),
        edges: state.currentProject.edges.map((edge) => ({
          ...edge,
          data: {
            ...(edge.data || {}),
            status: failed.has(Number(edge.data?.segmentId)) ? "error" : "ok",
          },
        })),
      },
    }));
  },

  // ---------------- 计算 / 导出共用的请求 payload（V5） ----------------
  /**
   * 组装 /calculate、/export/markdown、/export/excel 共用的请求 payload。
   * 保证导出请求发送的是“当前案例当前参数”，且与计算请求完全一致。
   */
  buildCalculationPayload: () => {
    const { currentProject } = get();
    const segments = currentProject.segments || [];
    const projectNodes = (currentProject.nodes || []).map((node) => ({
      id: String(node.id),
      type: node.data?.nodeType || node.type || "junction",
    }));
    const projectEdges = (currentProject.edges || [])
      .map((edge) => {
        const segmentIndex = segments.findIndex((segment) => segment.edge_id === edge.id);
        return {
          id: String(edge.id),
          source: String(edge.source),
          target: String(edge.target),
          segment_id: segmentIndex >= 0 ? segmentIndex + 1 : null,
        };
      })
      .filter((edge) => edge.segment_id !== null);
    const hasCompleteTopology = projectNodes.length > 0 && projectEdges.length === segments.length;

    return {
      ...PROJECT_DEFAULTS,
      nodes: hasCompleteTopology ? projectNodes : [],
      edges: hasCompleteTopology ? projectEdges : [],
      segments: segments.map((segment, index) => ({
        segment_id: index + 1, // 后端 SegmentInput 要求 int 且按顺序计算
        Q: segment.Q,
        l: segment.l,
        zeta_sum: segment.zeta_sum,
        dn_locked: segment.dn_locked || null,
        source_node: segment.source_node ? String(segment.source_node) : null,
        target_node: segment.target_node ? String(segment.target_node) : null,
        edge_id: segment.edge_id ? String(segment.edge_id) : null,
      })),
    };
  },

  // ---------------- 拓扑画布 actions（V3） ----------------
  /** 拖拽节点入库：id 形如 node-<nanoid>，data.label 为 锅炉/散热器/立管节点 */
  addNode: (nodeType, position) => {
    const def = NODE_TYPE_DEFS[nodeType];
    if (!def) throw new Error(`未知节点类型：${nodeType}`);
    const node = {
      id: `node-${nanoid()}`,
      type: "default",
      position: { x: Number(position?.x ?? 0), y: Number(position?.y ?? 0) },
      data: { label: def.label, nodeType },
      style: makeNodeStyle(nodeType),
    };
    set((state) => ({
      currentProject: {
        ...state.currentProject,
        nodes: [...state.currentProject.nodes, node],
        updatedAt: Date.now(),
      },
    }));
    return node;
  },

  /** 节点拖拽移动：只更新 x/y，不改变管段数据 */
  updateNodePosition: (nodeId, position) => {
    set((state) => ({
      currentProject: {
        ...state.currentProject,
        nodes: state.currentProject.nodes.map((node) =>
          node.id === nodeId
            ? { ...node, position: { x: Number(position?.x ?? node.position.x), y: Number(position?.y ?? node.position.y) } }
            : node,
        ),
      },
    }));
  },

  /**
   * 一键自动排版（V6.1）：调用 Dagre 重算全部节点坐标，
   * 只替换 nodes 的 position，不改连接关系、不改管段数据。
   * @returns {number} 完成排版的节点数量（0 表示画布为空）
   */
  autoLayout: () => {
    let layoutedCount = 0;
    set((state) => {
      const nodes = state.currentProject.nodes || [];
      if (nodes.length === 0) return state;
      const layoutedNodes = layoutWithDagre(nodes, state.currentProject.edges || []);
      layoutedCount = layoutedNodes.length;
      return {
        currentProject: {
          ...state.currentProject,
          nodes: layoutedNodes,
          updatedAt: Date.now(),
        },
      };
    });
    return layoutedCount;
  },

  /** 删除节点：同步删除相连连线及其管段 */
  removeNode: (nodeId) => {
    set((state) => {
      const removedEdges = state.currentProject.edges.filter(
        (edge) => edge.source === nodeId || edge.target === nodeId,
      );
      const removedEdgeIds = new Set(removedEdges.map((edge) => edge.id));
      return {
        currentProject: {
          ...state.currentProject,
          nodes: state.currentProject.nodes.filter((node) => node.id !== nodeId),
          edges: state.currentProject.edges.filter((edge) => !removedEdgeIds.has(edge.id)),
          segments: state.currentProject.segments.filter((segment) => !removedEdgeIds.has(segment.edge_id)),
          updatedAt: Date.now(),
        },
      };
    });
  },

  /**
   * 连线成功 → 自动生成新管段（核心联动）：
   *   边 id = edge-<nanoid>；管段 id = “管段 N”（按当前最大编号自增）；
   *   Q/l/Σξ 初始为 0，dn_locked 为 null（表格显示“自动”）。
   */
  addSegment: (sourceNodeId, targetNodeId) => {
    const edgeId = `edge-${nanoid()}`;
    let created = null;
    set((state) => {
      const segmentId = nextSegmentId(state.currentProject.segments);
      const edge = {
        id: edgeId,
        source: sourceNodeId,
        target: targetNodeId,
        type: "smoothstep",
        data: { status: "ok", segmentId },
      };
      created = {
        edge,
        segment: {
          segment_id: segmentId,
          source_node: sourceNodeId,
          target_node: targetNodeId,
          edge_id: edgeId,
          Q: 0,
          l: 0,
          zeta_sum: 0,
          dn_locked: null,
        },
      };
      return {
        currentProject: {
          ...state.currentProject,
          edges: [...state.currentProject.edges, created.edge],
          segments: [...state.currentProject.segments, created.segment],
          updatedAt: Date.now(),
        },
      };
    });
    return created;
  },

  /** 删除连线 → 根据 edge id 同步删除对应管段 */
  removeSegment: (edgeId) => {
    set((state) => ({
      currentProject: {
        ...state.currentProject,
        edges: state.currentProject.edges.filter((edge) => edge.id !== edgeId),
        segments: state.currentProject.segments.filter((segment) => segment.edge_id !== edgeId),
        updatedAt: Date.now(),
      },
    }));
  },

  /** 应用 React Flow 的节点变更（尺寸/选中；位置变更由 updateNodePosition 负责） */
  setNodes: (nextNodes) => {
    set((state) => ({
      currentProject: { ...state.currentProject, nodes: nextNodes, updatedAt: Date.now() },
    }));
  },

  /** 应用 React Flow 的边变更（选中状态等；删除由 removeSegment 负责） */
  setEdges: (nextEdges) => {
    set((state) => ({
      currentProject: { ...state.currentProject, edges: nextEdges, updatedAt: Date.now() },
    }));
  },

  /** 编辑当前案例某管段的输入（Q / l / dn_locked 等） */
  updateSegment: (segmentId, field, value) => {
    set((state) => {
      const segments = state.currentProject.segments.map((segment) =>
        String(segment.segment_id) === String(segmentId) ? { ...segment, [field]: value } : segment,
      );
      return {
        currentProject: { ...state.currentProject, segments, updatedAt: Date.now() },
      };
    });
  },
}));

// 仅开发环境：把 Store 挂到 window 便于调试与 E2E 断言（生产构建不受影响）
if (typeof window !== "undefined" && Boolean(import.meta.env && import.meta.env.DEV)) {
  window.__projectStore = useProjectStore;
}

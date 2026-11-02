/**
 * projects.js — 案例 REST API 与后端 DTO 归一化。
 *
 * Store 只保留状态更新；HTTP 请求、错误抛出与字段映射集中在此文件。
 */
import { fetchWithAuth } from "../utils/request";

export const API_BASE = "/api";

function normalizeSummary(row) {
  return { id: row.id, name: row.name, updatedAt: row.updated_at ?? null };
}

export function normalizeDetail(row) {
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

export async function fetchProjectList() {
  const rows = await apiRequest("/projects");
  return rows.map(normalizeSummary);
}

export async function fetchProjectDetail(projectId) {
  return normalizeDetail(await apiRequest(`/projects/${projectId}`));
}

export async function createProjectRecord(payload) {
  return normalizeDetail(await apiRequest("/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  }));
}

export async function updateProjectRecord(projectId, payload) {
  return normalizeDetail(await apiRequest(`/projects/${projectId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  }));
}

export async function deleteProjectRecord(projectId) {
  return apiRequest(`/projects/${projectId}`, { method: "DELETE" });
}

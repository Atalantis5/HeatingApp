/**
 * calculation.js — 校验 / 计算 / 报表导出的 HTTP 边界。
 *
 * 返回原始 Response，由 hooks 决定 422、非收敛与错误提示策略；
 * 这样前端仍不包含任何水力公式。
 */
import { fetchWithAuth } from "../utils/request";

const API_BASE = "/api";

export function postValidateTopology(payload) {
  return fetchWithAuth(`${API_BASE}/validate-topology`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function postCalculate(payload, signal) {
  return fetchWithAuth(`${API_BASE}/calculate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}

export function postMarkdown(projectName, payload) {
  return fetchWithAuth(
    `${API_BASE}/export/markdown?project_name=${encodeURIComponent(projectName)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export function postExcel(projectName, payload) {
  return fetchWithAuth(
    `${API_BASE}/export/excel?project_name=${encodeURIComponent(projectName)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

/**
 * request.js — V8.3 全局请求拦截（JWT Bearer Token）
 *
 * - fetchWithAuth：自动从 localStorage 读取 token，注入 Authorization: Bearer <token>；
 * - 后端返回 401：自动清除本地 token 并刷新到登录页（根 App 会重新渲染 <Login />）；
 * - 登录/注册接口传 `{ redirectOnUnauthorized: false }`，避免 401 拦截造成循环跳转。
 */

export const AUTH_TOKEN_KEY = "gravity-heating-auth-token";

export function getAuthToken() {
  try {
    return window.localStorage.getItem(AUTH_TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setAuthToken(token) {
  try {
    window.localStorage.setItem(AUTH_TOKEN_KEY, token);
  } catch {
    /* localStorage 不可用时保持内存态由调用方处理 */
  }
}

export function clearAuthToken() {
  try {
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

let redirectingToLogin = false;

/** 401 统一处理：清除 token，整页刷新让 App 路由守卫渲染登录页。 */
export function handleUnauthorized() {
  clearAuthToken();
  if (redirectingToLogin) return;
  redirectingToLogin = true;
  if (typeof window !== "undefined") {
    // 使用 reload 而非 location.href 修改，保证 SPA 当前路径下也能回到 Login 守卫。
    window.location.reload();
  }
}

/**
 * 带鉴权的 fetch 封装。
 * @param {RequestInfo|URL} input
 * @param {RequestInit} [init]
 * @param {{redirectOnUnauthorized?: boolean}} [options]
 * @returns {Promise<Response>}
 */
export async function fetchWithAuth(input, init = {}, options = {}) {
  const headers = new Headers(init.headers || {});
  const token = getAuthToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(input, { ...init, headers });
  if (response.status === 401 && options.redirectOnUnauthorized !== false) {
    handleUnauthorized();
  }
  return response;
}

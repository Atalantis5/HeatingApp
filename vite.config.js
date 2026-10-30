/**
 * vite.config.js — 前端工程配置与 CORS 解决层
 *
 * 背景：冻结的 FastAPI 后端（05_engine/04 ProgramFile/main.py）未配置 CORSMiddleware，
 * 浏览器跨域直连 http://127.0.0.1:8000 会在 OPTIONS 预检阶段被拦截（405，无 CORS 头）。
 *
 * 解决：前端统一请求同源前缀 /api，由 Vite 开发服务器代理转发到后端并去掉 /api 前缀；
 * 浏览器视角全部为同源请求，因此不触发任何 CORS 预检。
 */
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND_ORIGIN = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    // Windows 下忽略工具/测试产生的临时文件，避免文件监视器 EBUSY 崩溃（不影响业务源码监听）
    watch: {
      ignored: ["**/.*.tmpdir/**", "**/*.tmp", "**/.verify/**", "**/.api_env/**"],
    },
    proxy: {
      "/api": {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
        // 去掉 /api 前缀：/api/calculate → /calculate
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    proxy: {
      "/api": {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});

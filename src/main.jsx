/**
 * src/main.jsx — Vite 前端入口
 *
 * 仅做 React 挂载与样式基线；所有计算均来自后端 /api/calculate（经 Vite 代理）。
 */
import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN}>
      <App />
    </ConfigProvider>
  </React.StrictMode>,
);

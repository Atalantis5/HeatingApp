/**
 * App.jsx — 应用根组件：设计系统 Theme + 中文 locale + Ant Design 上下文 + 登录守卫。
 *
 * V9.0.2 起工作台业务逻辑与布局已迁移：
 * - components/workbench/Workbench.jsx：布局、表格编辑与展示；
 * - hooks/useTopologyValidation.js、useCalculation.js、useExport.js：业务交互；
 * - components/workbench/WorkbenchHeader.jsx：顶部操作区。
 */
import { useState } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";

import Login from "./pages/Login";
import Workbench from "./components/workbench/Workbench";
import { engineeringTheme } from "./theme";
import { getAuthToken } from "./utils/request";

/**
 * 应用根组件：本地无 token 时只渲染 <Login />；登录成功后切回原管理系统 Layout。
 */
export default function App() {
  const [authToken, setAuthToken] = useState(() => getAuthToken());

  return (
    <ConfigProvider theme={engineeringTheme} locale={zhCN}>
      <AntdApp>
        {authToken ? <Workbench /> : <Login onLogin={(token) => setAuthToken(token)} />}
      </AntdApp>
    </ConfigProvider>
  );
}

/**
 * theme.js — V8.2 统一设计系统（Ant Design ConfigProvider 主题）
 *
 * 设计语言：
 * - 主色：工程蓝 #2f54eb（辅以信息蓝 #1890ff）
 * - 背景：#f0f2f5 浅灰，白色卡片承载内容
 * - 圆角：4px（大卡片 8px），弱化阴影、强调边界与层次
 * - 数字使用等宽字体，便于工程数据对齐阅读
 *
 * 注意：这里只包含视觉 token，不包含任何业务/请求/水力计算逻辑。
 */

export const ENGINEERING_BLUE = "#2f54eb";
export const ENGINEERING_BLUE_LIGHT = "#eef2ff";
export const APP_BG = "#f0f2f5";

export const engineeringTheme = {
  token: {
    colorPrimary: ENGINEERING_BLUE,
    colorInfo: "#1890ff",
    colorSuccess: "#16a34a",
    colorWarning: "#d97706",
    colorError: "#dc2626",
    colorBgLayout: APP_BG,
    colorBgContainer: "#ffffff",
    colorBorder: "#e5e9f2",
    colorBorderSecondary: "#eef2f8",
    colorText: "#1e293b",
    colorTextSecondary: "#64748b",
    colorTextTertiary: "#94a3b8",
    borderRadius: 4,
    borderRadiusLG: 8,
    borderRadiusSM: 4,
    fontSize: 14,
    controlHeight: 34,
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif',
    boxShadowTertiary: "0 1px 2px rgba(15, 40, 90, 0.06)",
  },
  components: {
    Layout: {
      headerBg: "#ffffff",
      headerHeight: 72,
      headerPadding: "0 20px",
      siderBg: "#ffffff",
      bodyBg: APP_BG,
      footerBg: APP_BG,
      footerPadding: "10px 20px",
    },
    Card: {
      headerBg: "#ffffff",
      headerFontSize: 14,
      headerHeight: 46,
      paddingLG: 14,
      boxShadowTertiary: "0 2px 8px rgba(15, 40, 90, 0.06)",
    },
    Button: {
      controlHeight: 34,
      primaryShadow: "0 3px 8px rgba(47, 84, 235, 0.22)",
      defaultShadow: "none",
      borderRadius: 4,
    },
    Table: {
      headerBg: "#f7f9fc",
      headerColor: "#334155",
      headerSplitColor: "#e8edf5",
      borderColor: "#eef2f8",
      rowHoverBg: "#f0f7ff",
      cellPaddingBlock: 8,
      cellPaddingInline: 8,
      fontSize: 13,
    },
    Tag: {
      defaultBg: "#f4f6fb",
      defaultColor: "#475569",
      borderRadiusSM: 10,
    },
    Progress: {
      defaultColor: ENGINEERING_BLUE,
      remainingColor: "#e8edf5",
    },
    InputNumber: {
      borderRadius: 4,
    },
    Select: {
      borderRadius: 4,
    },
    Statistic: {
      contentFontSize: 22,
      titleFontSize: 12,
    },
    Divider: {
      fontSize: 12,
      colorText: "#64748b",
    },
  },
};

export const APP_VERSION = "V8.5";

/**
 * NodePalette.jsx — 节点面板（V8.5 拖拽双兼容）
 *
 * 浏览器（Vite/Edge）：保留原生 HTML5 Drag and Drop：
 *   dragstart 写入 dataTransfer（application/reactflow），TopologyEditor.onDrop 读取并生成节点。
 * pywebview/WebView2：WinForms WebView2 宿主默认未启用 HTML5 拖放/可能拦截 dataTransfer，
 *   因此自动降级为：
 *   - 点击节点 → 在画布中央生成节点；
 *   - 按住鼠标移动 → 自绘拖拽幽灵，松开时在画布落点生成节点（不依赖原生 drag 事件）。
 *
 * 边界：本组件不包含任何水力计算、不发请求；节点数据直接来自 useProjectStore。
 */
import React, { useCallback, useEffect, useRef } from "react";
import { Card, Space, Tag, Typography } from "antd";

import { NODE_TYPE_DEFS } from "../store/useProjectStore";

/** 拖拽数据 MIME 类型（与 TopologyEditor.onDrop 约定一致） */
export const NODE_DRAG_MIME = "application/reactflow";

const PALETTE_TYPES = ["boiler", "radiator", "riser"];

/** pywebview 会注入 window.pywebview；浏览器中不存在该对象 */
export function isDesktopRuntime() {
  return typeof window !== "undefined" && Boolean(window.pywebview);
}

export default function NodePalette({ onQuickAdd, onPointerDrop }) {
  const desktopMode = isDesktopRuntime();
  const dragStateRef = useRef(null);
  const suppressClickRef = useRef(false);

  // 卸载时清理遗留的 document 监听（正常 mouseup 也会清理）
  useEffect(() => {
    return () => {
      const state = dragStateRef.current;
      if (state?.cleanup) state.cleanup();
      dragStateRef.current = null;
    };
  }, []);

  const handleDragStart = useCallback((event, nodeType) => {
    if (!event.dataTransfer) return;
    event.dataTransfer.setData(NODE_DRAG_MIME, nodeType);
    event.dataTransfer.setData("text/plain", nodeType);
    event.dataTransfer.effectAllowed = "move";
  }, []);

  const handleClick = useCallback(
    (nodeType) => {
      if (suppressClickRef.current) return;
      onQuickAdd?.(nodeType);
    },
    [onQuickAdd],
  );

  const handleKeyDown = useCallback(
    (event, nodeType) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        handleClick(nodeType);
      }
    },
    [handleClick],
  );

  /** pywebview 降级：鼠标拖拽 + 自定义幽灵（不依赖 HTML5 drag/drop） */
  const handleMouseDown = useCallback(
    (event, nodeType) => {
      if (!desktopMode || event.button !== 0) return;
      event.preventDefault();
      const def = NODE_TYPE_DEFS[nodeType];
      const startX = event.clientX;
      const startY = event.clientY;
      let ghost = null;
      let moved = false;

      const moveGhost = (moveEvent) => {
        if (!ghost) return;
        ghost.style.left = `${moveEvent.clientX}px`;
        ghost.style.top = `${moveEvent.clientY}px`;
      };

      const cleanup = () => {
        document.removeEventListener("mousemove", handleMove, true);
        document.removeEventListener("mouseup", handleUp, true);
        if (ghost) {
          ghost.remove();
          ghost = null;
        }
        suppressClickRef.current = true;
        window.setTimeout(() => {
          suppressClickRef.current = false;
        }, 120);
        dragStateRef.current = null;
      };

      const handleMove = (moveEvent) => {
        const distance = Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY);
        if (!moved && distance < 5) return;
        if (!moved) {
          moved = true;
          ghost = document.createElement("div");
          ghost.textContent = `${def.icon || "⚙️"} ${def.label || nodeType}`;
          Object.assign(ghost.style, {
            position: "fixed",
            left: `${moveEvent.clientX}px`,
            top: `${moveEvent.clientY}px`,
            transform: "translate(-50%, -50%)",
            zIndex: 9999,
            pointerEvents: "none",
            padding: "8px 14px",
            borderRadius: "8px",
            background: "#fff",
            border: `1px solid ${def.color || "#2f54eb"}`,
            color: def.color || "#2f54eb",
            fontWeight: "600",
            fontSize: "12px",
            boxShadow: "0 8px 24px rgba(15, 40, 90, 0.18)",
          });
          document.body.appendChild(ghost);
        }
        moveGhost(moveEvent);
        moveEvent.preventDefault();
      };

      const handleUp = (upEvent) => {
        const wasMoved = moved;
        cleanup();
        if (wasMoved) {
          onPointerDrop?.(nodeType, { clientX: upEvent.clientX, clientY: upEvent.clientY });
        }
      };

      dragStateRef.current = { cleanup };
      document.addEventListener("mousemove", handleMove, true);
      document.addEventListener("mouseup", handleUp, true);
    },
    [desktopMode, onPointerDrop],
  );

  return (
    <Card
      size="small"
      data-testid="node-palette"
      className="node-palette-card"
      title={desktopMode ? "节点面板 · 点击或拖动至高亮画布" : "节点面板 · 拖拽至画布"}
      style={{ marginBottom: 10 }}
    >
      <Space size={12} wrap>
        {PALETTE_TYPES.map((nodeType) => {
          const def = NODE_TYPE_DEFS[nodeType];
          return (
            <div
              key={nodeType}
              draggable={!desktopMode}
              role="button"
              tabIndex={0}
              className="palette-item"
              data-testid={`palette-${nodeType}`}
              data-node-type={nodeType}
              data-add-mode={desktopMode ? "pointer" : "html5-drag"}
              onDragStart={(event) => handleDragStart(event, nodeType)}
              onMouseDown={(event) => handleMouseDown(event, nodeType)}
              onClick={() => handleClick(nodeType)}
              onKeyDown={(event) => handleKeyDown(event, nodeType)}
              style={{
                cursor: desktopMode ? "pointer" : "grab",
                userSelect: "none",
                padding: "8px 16px",
                borderRadius: 8,
                border: `1px solid ${def.color}`,
                background: `${def.color}1a`,
                color: def.color,
                fontWeight: 600,
                textAlign: "center",
                minWidth: 96,
              }}
              title={
                desktopMode
                  ? `点击「${def.label}」添加到画布中央，或按住拖到画布任意位置`
                  : `拖拽「${def.label}」到右侧画布`
              }
            >
              <div style={{ fontSize: 18 }}>{def.icon}</div>
              <div>{def.label}</div>
            </div>
          );
        })}
      </Space>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: "10px 0 0" }}>
        {desktopMode
          ? "桌面端支持点击节点直接添加，或按住节点拖动到画布任意位置；"
          : "拖入节点后，在画布上从节点边缘的锚点拖拽连线；"}
        <Tag color="blue">连线成功即自动生成管段</Tag>
        （编号按当前数量自增，Q/l/Σξ 初始为 0，管径默认「自动」）。选中连线按 Delete/Backspace 可删除并同步移除管段。
      </Typography.Paragraph>
    </Card>
  );
}

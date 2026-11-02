/**
 * useExport.js — Markdown 复制与 Excel 下载。
 *
 * 报表请求与计算请求使用同一 buildCalculationPayload，保证“所见即所导”；
 * 非收敛结果禁止导出（与 WorkbenchHeader 的 disabled 状态一致）。
 */
import { useCallback, useState } from "react";

import { postExcel, postMarkdown } from "../api/calculation";
import { copyTextToClipboard } from "../utils/clipboard";

export function useExport({
  currentProject,
  buildCalculationPayload,
  calculationNotConverged,
  message,
}) {
  const [exporting, setExporting] = useState(null); // "markdown" | "excel" | null

  /** 复制 Markdown 报表：POST /export/markdown → navigator.clipboard.writeText */
  const handleCopyMarkdown = useCallback(async () => {
    if (calculationNotConverged) {
      message.error("计算未收敛，已禁止复制报表");
      return;
    }
    setExporting("markdown");
    try {
      const response = await postMarkdown(currentProject.name, buildCalculationPayload());
      if (!response.ok) {
        const detail = await response.text();
        message.error(`Markdown 导出失败：HTTP ${response.status} ${detail.slice(0, 200)}`);
        return;
      }
      const markdown = await response.text();
      await copyTextToClipboard(markdown);
      message.success("Markdown 报表已复制到剪贴板");
    } finally {
      setExporting(null);
    }
  }, [buildCalculationPayload, currentProject.name, calculationNotConverged, message]);

  /** 导出 Excel 报表：POST /export/excel → Blob 触发浏览器下载 */
  const handleExportExcel = useCallback(async () => {
    if (calculationNotConverged) {
      message.error("计算未收敛，已禁止导出 Excel");
      return;
    }
    setExporting("excel");
    try {
      const response = await postExcel(currentProject.name, buildCalculationPayload());
      if (!response.ok) {
        const detail = await response.text();
        message.error(`Excel 导出失败：HTTP ${response.status} ${detail.slice(0, 200)}`);
        return;
      }
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = `${currentProject.name || "案例"}_水力计算表.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      message.success("Excel 报表已开始下载");
    } finally {
      setExporting(null);
    }
  }, [buildCalculationPayload, currentProject.name, calculationNotConverged, message]);

  return { exporting, handleCopyMarkdown, handleExportExcel };
}

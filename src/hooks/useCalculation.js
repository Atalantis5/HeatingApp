/**
 * useCalculation.js — POST /calculate 请求、竞态防护与结果写入。
 *
 * 保持原 Workbench 的 H4 行为：中止上一请求；响应回来时若项目已切换则丢弃；
 * 所有结果仍由后端返回，前端不含任何水力公式。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { postCalculate } from "../api/calculation";
import { useProjectStore } from "../store/useProjectStore";
import { extractSegmentErrorIds } from "../utils/apiErrors";

export function useCalculation({
  currentProject,
  setSegmentErrors,
  markEdgeErrors,
  buildCalculationPayload,
  validateTopologyBeforeRequest,
  message,
}) {
  const [calculating, setCalculating] = useState(false);
  const calculateAbortRef = useRef(null);

  // 切换案例时中止上一个案例尚未返回的计算请求
  useEffect(() => {
    return () => {
      calculateAbortRef.current?.abort();
    };
  }, [currentProject.id]);

  /** 开始计算：POST /calculate；结果全部来源于后端，写入 Store 后渲染。 */
  const handleCalculate = useCallback(async () => {
    if (!validateTopologyBeforeRequest()) return;
    const requestProjectId = useProjectStore.getState().currentProject.id;
    if (!requestProjectId) {
      message.error("当前案例尚未加载完成，无法计算");
      return;
    }
    const isSameProject = () => useProjectStore.getState().currentProject.id === requestProjectId;

    // H4：取消上一请求；若响应回来时项目 id 已变化，直接丢弃，绝不串项目写结果。
    calculateAbortRef.current?.abort();
    const controller = new AbortController();
    calculateAbortRef.current = controller;
    setCalculating(true);
    try {
      const response = await postCalculate(buildCalculationPayload(), controller.signal);
      if (!isSameProject()) return;
      if (response.status === 422) {
        const errorBody = await response.json();
        if (!isSameProject()) return;
        const detail = errorBody?.detail ?? "前置条件失败（422）";
        const failedIds = extractSegmentErrorIds(errorBody);
        message.error(typeof detail === "string" ? detail : "计算输入校验失败，请检查参数");
        setSegmentErrors(failedIds);
        markEdgeErrors(failedIds);
        return;
      }
      if (!response.ok) {
        message.error(`计算请求失败：HTTP ${response.status}`);
        return;
      }
      const result = await response.json();
      if (!isSameProject()) return;
      useProjectStore.getState().setCalculationResult(result);
      setSegmentErrors([]);
      if (result?.status === "not_converged") {
        message.warning("计算未收敛：结果已标记为不可保存/导出，请检查管网或参数");
      } else {
        message.success("计算完成（结果已写入当前案例，点击左侧「保存当前案例」可持久化）");
      }
    } catch (error) {
      if (error?.name === "AbortError") return;
      if (isSameProject()) message.error(`计算请求失败：${error.message}`);
    } finally {
      if (calculateAbortRef.current === controller) {
        calculateAbortRef.current = null;
        setCalculating(false);
      }
    }
  }, [
    buildCalculationPayload,
    markEdgeErrors,
    validateTopologyBeforeRequest,
    message,
    setSegmentErrors,
  ]);

  return { calculating, handleCalculate };
}

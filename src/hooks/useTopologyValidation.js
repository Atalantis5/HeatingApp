/**
 * useTopologyValidation.js — 校验前拓扑预检 + POST /validate-topology。
 *
 * 负责：前端拓扑校验、422 行/边描红、后端校验结果提示；
 * 不负责计算与导出（见 useCalculation / useExport）。
 */
import { useCallback, useEffect, useState } from "react";

import { postValidateTopology } from "../api/calculation";
import { extractSegmentErrorIds } from "../utils/apiErrors";
import { validateTopology } from "../utils/topologyValidator";

export function useTopologyValidation({
  currentProject,
  setSegmentErrors,
  markEdgeErrors,
  buildCalculationPayload,
  message,
}) {
  const [topologyErrors, setTopologyErrors] = useState([]);
  const [validating, setValidating] = useState(false);

  // 切换案例时清空前端拓扑校验警告
  useEffect(() => {
    setTopologyErrors([]);
  }, [currentProject.id]);

  /** 点击计算/校验前的统一前端拓扑预校验（失败直接弹错并中止请求） */
  const validateTopologyBeforeRequest = useCallback(() => {
    const validation = validateTopology(currentProject.nodes, currentProject.edges);
    if (!validation.valid) {
      setTopologyErrors(validation.errors);
      message.error(validation.errors[0] || "拓扑校验未通过，请检查管网连接");
      return false;
    }
    setTopologyErrors([]);
    return true;
  }, [currentProject.nodes, currentProject.edges, message]);

  /** 提交校验：POST /validate-topology；422 时解析 detail、标红行与拓扑边 */
  const handleValidateTopology = useCallback(async () => {
    if (!validateTopologyBeforeRequest()) return;
    setValidating(true);
    try {
      const response = await postValidateTopology(buildCalculationPayload());
      if (response.status === 422) {
        const errorBody = await response.json();
        const detail = errorBody?.detail ?? "拓扑校验失败（422）";
        const failedIds = extractSegmentErrorIds(errorBody);
        if (typeof detail === "string") setTopologyErrors([detail]);
        message.error(typeof detail === "string" ? detail : "拓扑校验失败，请检查输入");
        setSegmentErrors(failedIds);
        markEdgeErrors(failedIds);
        return;
      }
      if (!response.ok) {
        message.error(`拓扑校验请求失败：HTTP ${response.status}`);
        return;
      }
      const result = await response.json();
      const backendErrors = Array.isArray(result?.errors) ? result.errors : [];
      if (result?.valid) {
        setTopologyErrors([]);
        message.success("拓扑校验通过");
      } else {
        setTopologyErrors(backendErrors);
        message.error(backendErrors[0] || result?.message || "拓扑校验未通过，请检查拓扑");
      }
      setSegmentErrors([]);
      markEdgeErrors([]);
    } catch (error) {
      message.error(`拓扑校验请求失败：${error.message}`);
    } finally {
      setValidating(false);
    }
  }, [buildCalculationPayload, markEdgeErrors, validateTopologyBeforeRequest, message, setSegmentErrors]);

  return {
    topologyErrors,
    validating,
    validateTopologyBeforeRequest,
    handleValidateTopology,
  };
}

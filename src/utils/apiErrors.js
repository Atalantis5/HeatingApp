/**
 * apiErrors.js — 将后端 422 响应解析为可标红的管段序号。
 *
 * 优先使用 V9.0.1 结构化 segment_errors；向后兼容旧版 detail 文本。
 */

/** 从 422 详情中提取涉及的管段序号（后端 detail 形如“管段2：已达最小管径 DN15…”） */
export function extractSegmentIds(detail) {
  const ids = new Set();
  const text = typeof detail === "string" ? detail : JSON.stringify(detail ?? "");
  const patterns = [/管段\s*(\d+)/g, /segment[_ ]?id["'\s:=]*(\d+)/gi];
  for (const pattern of patterns) {
    let match = pattern.exec(text);
    while (match !== null) {
      ids.add(Number(match[1]));
      match = pattern.exec(text);
    }
  }
  return Array.from(ids);
}

/** 从后端结构化 422 响应中提取 segment_id；没有结构体时回退到旧 detail 文本解析。 */
export function extractSegmentErrorIds(errorBody) {
  const structured = errorBody?.segment_errors;
  if (Array.isArray(structured) && structured.length > 0) {
    return Array.from(
      new Set(
        structured
          .map((item) => Number(item?.segment_id))
          .filter((id) => Number.isInteger(id) && id > 0),
      ),
    );
  }
  return extractSegmentIds(errorBody?.detail);
}

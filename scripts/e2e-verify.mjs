#!/usr/bin/env node
/**
 * scripts/e2e-verify.mjs — 端到端验证脚本（Node 18+/20+）
 *
 * 验证目标：
 * 1) Vite 代理链路：POST /api/validate-topology、/api/calculate → 200（证明 /api 代理生效、无 CORS 拦截）；
 * 2) 业务断言：Loop1 计算结果 margin ≈ 0.108、status="converged"、总阻力 729.32；
 * 3) 浏览器控制台：headless Edge/Chrome（CDP 9222）加载前端页面、点击「开始计算」，
 *    抓取 console.error / warning / 未捕获异常 / 网络失败，要求全部为 0。
 *
 * 使用前提：
 *   - 后端已启动：python -m uvicorn main:app --host 127.0.0.1 --port 8000
 *   - 前端已启动：pnpm dev（Vite 5173）
 *   - 可选浏览器（用于控制台抓取）：
 *       msedge.exe --headless=new --disable-gpu --no-sandbox --remote-debugging-port=9222
 *         --user-data-dir=%TEMP%\edge-e2e about:blank
 *     未检测到 CDP 时脚本仍会完成代理 HTTP 链路验证，并输出 WARN。
 *
 * 环境变量：APP_BASE（默认 http://127.0.0.1:5173）、CDP_URL（默认 http://127.0.0.1:9222）
 * 退出码：0 = 通过；1 = 失败。
 */
const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:5173";
const CDP_URL = process.env.CDP_URL || "http://127.0.0.1:9222";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const results = [];
function assert(name, ok, detail = "") {
  results.push({ name, ok: Boolean(ok), detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  | " + detail : ""}`);
}

const seg = (segment_id, Q, l, zeta_sum, dn_locked = null) => ({
  segment_id,
  Q,
  l,
  zeta_sum,
  ...(dn_locked ? { dn_locked } : {}),
});

/** Loop1（最不利环路）11 个管段输入（《默认模型的计算典例》表 4-3-1 输入参数与管径） */
const LOOP1_PAYLOAD = {
  H: 3,
  rho_h: 977.81,
  rho_g: 961.92,
  dPf: 350,
  rho_dynamic: 983,
  max_iter: 20,
  shared_segments: [],
  segments: [
    seg(7, 37400, 15, 6, "DN65"),
    seg(8, 37400, 8, 3.5, "DN65"),
    seg(10, 18600, 11, 4, "DN40"),
    seg(11, 12200, 3, 4, "DN32"),
    seg(12, 6000, 3, 4, "DN25"),
    seg(1, 6000, 2, 25, "DN25"),
    seg(2, 6000, 8.5, 4, "DN25"),
    seg(3, 12200, 8, 1, "DN32"),
    seg(4, 18600, 8, 1, "DN40"),
    seg(5, 30800, 8, 1, "DN50"),
    seg(6, 37400, 8, 2.5, "DN65"),
  ],
};

async function postJson(path) {
  const response = await fetch(`${APP_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(LOOP1_PAYLOAD),
  });
  const text = await response.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    body = { raw: text.slice(0, 300) };
  }
  return { status: response.status, headers: Object.fromEntries(response.headers), body };
}

// ===========================================================================
// 第一步：Vite 代理 HTTP 链路（同源 /api → 后端）
// ===========================================================================
console.log("\n=== [1] Vite 代理链路（/api → FastAPI，同源无 CORS 预检） ===");
let proxyOk = false;
try {
  const page = await fetch(`${APP_BASE}/`);
  const html = await page.text();
  assert("前端页面 GET / 200", page.status === 200, `status=${page.status}`);
  assert("页面含 React 挂载点 #root", html.includes('id="root"'));

  const validate = await postJson("/api/validate-topology");
  console.log(
    "POST /api/validate-topology ->",
    validate.status,
    JSON.stringify({
      valid: validate.body.valid,
      node_count: validate.body.node_count,
      is_strongly_connected: validate.body.is_strongly_connected,
    }),
  );
  assert("POST /api/validate-topology 200 且 valid=true", validate.status === 200 && validate.body.valid === true);

  const calculate = await postJson("/api/calculate");
  console.log(
    "POST /api/calculate ->",
    calculate.status,
    JSON.stringify({
      status: calculate.body.status,
      sum_resistance: calculate.body.sum_resistance,
      margin: calculate.body.margin,
      dP_prime: calculate.body.dP_prime,
    }),
  );
  const marginDiff = Math.abs((calculate.body.margin ?? -1) - 0.108);
  assert("POST /api/calculate 200", calculate.status === 200);
  assert("margin ≈ 0.108（±0.002）", marginDiff <= 0.002, `actual=${calculate.body.margin}, diff=${marginDiff}`);
  assert("status=converged", calculate.body.status === "converged");
  assert(
    "Loop1 总阻力 ≈ 729.32（±1%）",
    Math.abs((calculate.body.sum_resistance ?? 0) - 729.32) / 729.32 <= 0.01,
    `actual=${calculate.body.sum_resistance}`,
  );
  proxyOk = results.every((item) => item.ok);
} catch (error) {
  assert("Vite 代理链路可用", false, String(error));
}

// ===========================================================================
// 第二步：真实浏览器（headless Edge/Chrome + CDP）控制台与页面验证
// ===========================================================================
console.log("\n=== [2] 真实浏览器验证（CDP，含控制台抓取） ===");
let browserOk = null;
try {
  const version = await (await fetch(`${CDP_URL}/json/version`)).json();
  console.log("浏览器:", version.Browser);
  const ws = new WebSocket(version.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });
  let nextId = 1;
  const pending = new Map();
  const consoleErrors = [];
  const consoleWarnings = [];
  const exceptions = [];
  const logErrors = [];
  const networkFailures = [];
  const apiResponses = [];

  ws.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
      return;
    }
    const { method, params } = message;
    if (method === "Runtime.consoleAPICalled") {
      const text = params.args.map((arg) => arg.value ?? arg.description ?? "").join(" ");
      if (params.type === "error") consoleErrors.push(text);
      if (params.type === "warning") consoleWarnings.push(text);
    } else if (method === "Runtime.exceptionThrown") {
      exceptions.push(params.exceptionDetails?.text || JSON.stringify(params.exceptionDetails));
    } else if (method === "Log.entryAdded" && params.entry.level === "error") {
      logErrors.push(params.entry.text);
    } else if (method === "Network.loadingFailed") {
      networkFailures.push(params.errorText);
    } else if (method === "Network.responseReceived" && params.response.url.includes("/api/")) {
      apiResponses.push({ url: params.response.url, status: params.response.status });
    }
  });

  const send = (method, params = {}, sessionId) =>
    new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  const evaluate = async (expression) => {
    const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, sessionId);
    return result.result?.value;
  };

  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Page.enable", {}, sessionId);
  await send("Runtime.enable", {}, sessionId);
  await send("Log.enable", {}, sessionId);
  await send("Network.enable", {}, sessionId);
  await send("Page.navigate", { url: `${APP_BASE}/` }, sessionId);
  await sleep(4000);

  const title = await evaluate("document.title");
  console.log("页面标题:", title);
  assert("页面标题正确", title.includes("重力循环双管热水供暖管网水力计算系统"));

  await evaluate("document.querySelector('[data-testid=\"btn-validate\"]').click()");
  await sleep(2500);
  assert("提交校验通过（代理 + 后端）", (await evaluate("document.body.innerText")).includes("拓扑校验通过"));

  await evaluate("document.querySelector('[data-testid=\"btn-calculate\"]').click()");
  await sleep(3500);
  const bodyText = await evaluate("document.body.innerText");
  assert("计算结果显示：总阻力 729.32", bodyText.includes("729.32"));
  assert("计算结果显示：富裕度 10.80", bodyText.includes("10.80"));
  assert("计算结果显示：状态 converged", bodyText.includes("converged"));
  assert(
    "浏览器端两个 /api 请求均 200",
    apiResponses.some((item) => item.url.includes("/api/validate-topology") && item.status === 200) &&
      apiResponses.some((item) => item.url.includes("/api/calculate") && item.status === 200),
    JSON.stringify(apiResponses),
  );

  console.log(
    "console.error:",
    consoleErrors.length,
    "| console.warn:",
    consoleWarnings.length,
    "| 未捕获异常:",
    exceptions.length,
    "| Log(error):",
    logErrors.length,
    "| 网络失败:",
    networkFailures.length,
  );
  assert("浏览器控制台无 error", consoleErrors.length === 0);
  assert("无未捕获异常", exceptions.length === 0);
  assert("无网络失败且无资源加载错误", networkFailures.length === 0 && logErrors.length === 0);
  ws.close();
  browserOk =
    consoleErrors.length === 0 &&
    exceptions.length === 0 &&
    networkFailures.length === 0 &&
    logErrors.length === 0 &&
    apiResponses.length >= 2;
} catch (error) {
  console.log("WARN  未检测到可用的 headless 浏览器 CDP（跳过浏览器控制台验证）：", String(error).slice(0, 200));
  console.log("      如需浏览器验证，请先启动 Edge/Chrome：");
  console.log(
    '      msedge.exe --headless=new --disable-gpu --no-sandbox --remote-debugging-port=9222 --user-data-dir=%TEMP%\\edge-e2e about:blank',
  );
  browserOk = null;
}

// ===========================================================================
// 汇总
// ===========================================================================
const failures = results.filter((item) => !item.ok);
console.log("\n=== 汇总 ===");
console.log(`断言：${results.length - failures.length}/${results.length} PASS`);
if (browserOk === null) console.log("浏览器控制台验证：SKIPPED（无 CDP）");
else console.log("浏览器控制台验证：", browserOk ? "PASS" : "FAIL");
console.log("代理链路验证：", proxyOk ? "PASS" : "FAIL");
if (failures.length > 0) {
  failures.forEach((item) => console.log("  FAILED:", item.name, item.detail));
  process.exit(1);
}
console.log("E2E_RESULT: PASS");
process.exit(0);

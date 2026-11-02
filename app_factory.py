# -*- coding: utf-8 -*-
"""app_factory.py — FastAPI 实例、生命周期、全局异常与静态资源兜底。"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import data_loader
import init_db
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api import auth as auth_api
from api import calculation, exports, projects

logger = logging.getLogger("hydraulic_api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时自动建表，并在 projects 表为空时写入内置默认模型。"""
    init_db.ensure_database()
    yield


IS_FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_DIR = (
    Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    if IS_FROZEN
    else Path(__file__).resolve().parent
)
DIST_DIR = RESOURCE_DIR / "dist"
ASSETS_DIR = DIST_DIR / "assets"


def _configure_bundled_data_paths() -> None:
    """打包环境：把 data_loader/init_db 的只读 Excel 路径指向 _MEIPASS。

    源码环境保持原路径不变（不改变开发/测试基线）。
    这属于打包资源路径适配，不涉及任何水力计算或业务逻辑。
    """
    if not IS_FROZEN:
        return
    sheet_dir = RESOURCE_DIR / "02 Calculation Sheet"
    data_loader.SHEET_DIR = sheet_dir
    data_loader.PIPE_XLSX = sheet_dir / "热水采暖管道水力计算表.xlsx"
    data_loader.LOCAL_LOSS_XLSX = sheet_dir / "局部阻力系数.xlsx"
    data_loader.GRAVITY_XLSX = sheet_dir / "重力循环上供下回热水供暖系统附加压力.xlsx"
    data_loader.SCHEMA_MANIFEST_PATH = RESOURCE_DIR / "schema_manifest.json"

    init_db.CALC_SHEET_DIR = sheet_dir
    init_db.SEGMENT_EXCEL = sheet_dir / "默认管路模型_管段明细参数.xlsx"
    init_db.LOOP_EXCEL = sheet_dir / "默认管路模型_环路参数.xlsx"


_configure_bundled_data_paths()


class StripApiPrefixMiddleware:
    """生产模式兼容层：前端请求统一带 /api 前缀（开发模式由 Vite 代理去掉）。

    单端口生产部署时没有 Vite 代理，故在 ASGI 层把 /api/xxx 重写为 /xxx 后再路由，
    这样同一份前端构建产物在开发与生产模式下都能复用；/api 本身不做静态兜底。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            path = scope.get("path", "")
            if path == "/api" or path.startswith("/api/"):
                new_scope = dict(scope)
                stripped = path[4:] or "/"
                new_scope["path"] = stripped
                new_scope["_original_path"] = path
                if "raw_path" in new_scope:
                    new_scope["raw_path"] = stripped.encode("utf-8")
                await self.app(new_scope, receive, send)
                return
        await self.app(scope, receive, send)


app = FastAPI(
    title="暖通水力计算后端",
    description="V8.3：OAuth2 + JWT 用户认证 + 多租户案例隔离 + SQLite 持久化 + NetworkX 动态拓扑寻优 + 报表导出",
    version="3.2.0",
    lifespan=lifespan,
)

# 前端构建产物静态资源
if ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

# /api 前缀兼容（必须早于路由匹配）
app.add_middleware(StripApiPrefixMiddleware)

# V9.0.2 起：登录/注册、项目、计算、导出路由按职责拆分
app.include_router(auth_api.router)
app.include_router(projects.router)
app.include_router(calculation.router)
app.include_router(exports.router)


# ---------------------------------------------------------------------------
# 异常处理器（JSONResponse；不使用 except Exception 兜底）
# ---------------------------------------------------------------------------
@app.exception_handler(ValueError)
async def handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
    """前置条件 / 工程参数非法 → 422。"""
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc), "error_type": "ValueError"},
    )


@app.exception_handler(RuntimeError)
async def handle_runtime_error(request: Request, exc: RuntimeError) -> JSONResponse:
    """后置条件违例 / 内部计算失败 → 500。"""
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "error_type": "RuntimeError"},
    )


_VALIDATION_MESSAGE_TEMPLATES = {
    "missing": "缺少必填字段",
    "greater_than": "数值必须大于 {gt}",
    "greater_than_equal": "数值必须大于等于 {ge}",
    "less_than": "数值必须小于 {lt}",
    "less_than_equal": "数值必须小于等于 {le}",
    "literal_error": "取值必须是：{expected}",
    "int_parsing": "必须是整数",
    "int_type": "必须是整数",
    "float_parsing": "必须是数字",
    "float_type": "必须是数字",
    "string_type": "必须是字符串",
    "list_type": "必须是数组",
    "dict_type": "必须是对象",
    "bool_parsing": "必须是布尔值",
    "extra_forbidden": "不支持的字段",
    "json_invalid": "JSON 格式不合法",
}


def _humanize_validation_error(error: dict) -> str:
    """把 Pydantic 校验错误翻译成中文自然语言（前端直接展示，不出现英文堆栈）。"""
    error_type = error.get("type", "")
    context = error.get("ctx") or {}
    template = _VALIDATION_MESSAGE_TEMPLATES.get(error_type)
    if template:
        try:
            return template.format(**context)
        except (KeyError, IndexError):
            pass
    raw_message = str(error.get("msg", "输入不合法"))
    # 自定义 model_validator 的 msg 形如 "Value error, rho_h 必须大于 rho_g：..."，去掉英文前缀
    for prefix in ("Value error, ", "Assertion failed, "):
        if raw_message.startswith(prefix):
            return raw_message[len(prefix):]
    return raw_message


def _collect_segment_validation_errors(exc: RequestValidationError) -> list[dict]:
    """从 Pydantic 错误中提取 segments[i] 的校验错误，供前端按 segment_id 精准标红。

    返回形如：
    [{"segment_id": 2, "field": "Q", "message": "数值必须大于 0.0"}, ...]
    只返回能定位到具体管段的错误；模型级错误仍由 detail 字符串兜底。
    """
    raw_body = exc.body if isinstance(exc.body, dict) else {}
    raw_segments = raw_body.get("segments") if isinstance(raw_body.get("segments"), list) else []
    collected: list[dict] = []
    seen: set[tuple[int, str]] = set()

    for error in exc.errors():
        loc = list(error.get("loc", []))
        segment_index = None
        if len(loc) >= 3 and loc[0] == "body" and loc[1] == "segments" and isinstance(loc[2], int):
            segment_index = loc[2]
            field = str(loc[-1])
        elif len(loc) >= 2 and loc[0] == "segments" and isinstance(loc[1], int):
            segment_index = loc[1]
            field = str(loc[-1])
        else:
            continue

        segment_id = None
        if 0 <= segment_index < len(raw_segments) and isinstance(raw_segments[segment_index], dict):
            segment_id = raw_segments[segment_index].get("segment_id")
        if segment_id is None:
            continue
        try:
            segment_id = int(segment_id)
        except (TypeError, ValueError):
            continue

        key = (segment_id, field)
        if key in seen:
            continue
        seen.add(key)
        collected.append(
            {
                "segment_id": segment_id,
                "field": field,
                "message": _humanize_validation_error(error),
            }
        )
    return collected


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Pydantic 请求体校验失败 → 422。

    - `detail` 保持自然语言字符串，兼容旧客户端/弹窗；
    - `segment_errors` 是结构化数组，前端据此直接标红对应管段行；
    - 其余非管段级错误仍在 `detail` 中给出。
    """
    messages = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", []) if item != "body")
        message = _humanize_validation_error(error)
        messages.append(f"{location}：{message}" if location else message)
    detail = "请求参数校验失败：" + "；".join(messages[:5])
    return JSONResponse(
        status_code=422,
        content={
            "detail": detail,
            "error_type": "RequestValidationError",
            "segment_errors": _collect_segment_validation_errors(exc),
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
    """统一兜底：未预期异常 → 500，返回人类可读中文提示，绝不向前端泄露 Python Traceback。"""
    logger.exception("未处理异常：%s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "服务器内部错误：请求处理过程中出现未预期异常，请检查输入后重试",
            "error_type": type(exc).__name__,
        },
    )


# ---------------------------------------------------------------------------
# SPA 静态资源兜底（V7.2）
# 必须注册在所有 /api 业务路由之后：优先命中 /projects、/calculate 等接口；
# 其余 GET 请求：若 dist 中存在同名文件则返回文件，否则回退 dist/index.html，
# 解决 React History 路由刷新 404。POST/PUT/DELETE 不受影响。
# ---------------------------------------------------------------------------
@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(request: Request, full_path: str):
    # /api/* 已被 StripApiPrefixMiddleware 重写；未知 API 不允许回退到 SPA 页面
    original_path = request.scope.get("_original_path", "")
    if original_path == "/api" or original_path.startswith("/api/") or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API 路由不存在")

    dist_root = DIST_DIR.resolve()
    if full_path:
        candidate = (DIST_DIR / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(dist_root):
            return FileResponse(candidate)

    index_file = DIST_DIR / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)

    raise HTTPException(
        status_code=404,
        detail="前端构建产物不存在：请先在 05_engine/04_ProgramFile 下执行 npm run build 生成 dist 目录",
    )

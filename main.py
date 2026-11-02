# -*- coding: utf-8 -*-
"""main.py — FastAPI 后端入口（V8.3：JWT 用户认证 + 多租户案例隔离 + NetworkX 水力计算）

路由：
- POST /token：OAuth2 表单登录，返回 JWT access_token（auth.py）；
- POST /users/register：注册测试用户（密码 PBKDF2 哈希存储）；
- GET/POST/PUT/DELETE /projects：须携带 Bearer token，案例按当前 user_id 隔离；
  列表仅返回 id/name/updated_at，详情返回含 nodes/edges/segments 的完整 data；
- POST /validate-topology：
  * 图模式（请求含 nodes+edges）：networkx 校验枝状 DAG / 单热源 / 末端存在，
    返回节点数、边数、是否 DAG、热源与末端节点；
  * 旧版兼容（nodes/edges 为空）：按 segments 顺序构造闭环图并校验强连通；
- POST /calculate：入参 ProjectInput，调用 controller.run_hydraulic_calculation，
  response_model=CalculationResponse（图模式返回 topology/parallel_loops）；
- POST /export/markdown：执行同一计算并生成 Markdown 报表（pandas.DataFrame.to_markdown）；
- POST /export/excel：执行同一计算并用 pandas + openpyxl 生成 Excel 文件流。

异常处理：
- ValueError  → 422（JSONResponse，ErrorResponse 结构）；
- RuntimeError → 500（JSONResponse）；
- 不使用 except Exception 兜底；FastAPI 自身的 Pydantic 请求校验错误由框架自动 422。
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import networkx as nx
import pandas as pd
from openpyxl.styles import Font
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

import auth
import controller
import data_loader
import init_db
from auth import get_current_user, hash_password
from database import get_db
from models import (
    CalculationResponse,
    ErrorResponse,
    ProjectInput,
    TopologyResponse,
)
from models_db import Project, utc_now_naive
from models_user import User

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

# 统一登记错误响应模型（OpenAPI 文档用）
_ERROR_RESPONSES = {
    422: {"model": ErrorResponse, "description": "前置条件 / 输入校验失败"},
    500: {"model": ErrorResponse, "description": "后置条件违例或内部计算失败"},
}

# V8.3 OAuth2 表单登录路由（POST /token）
app.include_router(auth.router)


# ---------------------------------------------------------------------------
# 用户注册（首次测试用；登录走 auth.py 的 POST /token）
# ---------------------------------------------------------------------------
class UserRegisterPayload(BaseModel):
    """POST /users/register 请求体。"""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="登录用户名（3-64 位字母/数字/下划线/点/横线）",
    )
    password: str = Field(min_length=6, max_length=128, description="登录密码（至少 6 位）")


@app.post("/users/register", status_code=201)
def register_user(payload: UserRegisterPayload, db: Session = Depends(get_db)) -> dict:
    """注册普通用户；密码仅保存 PBKDF2 哈希，不保存明文。"""
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="用户名已存在，请直接登录或更换用户名")
    user = User(
        id=str(uuid4()),
        username=payload.username,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user.to_public_dict()


# ---------------------------------------------------------------------------
# 案例持久化（SQLite + SQLAlchemy）
# ---------------------------------------------------------------------------
class ProjectCreatePayload(BaseModel):
    """POST /projects 请求体。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str = Field(min_length=1, max_length=255, description="案例名称")
    data: dict = Field(default_factory=dict, description="前端拓扑数据：nodes/edges/segments")


class ProjectUpdatePayload(BaseModel):
    """PUT /projects/{id} 请求体：覆盖 data。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str | None = Field(default=None, min_length=1, max_length=255, description="新名称（可选）")
    data: dict = Field(description="覆盖写入的完整 data（nodes/edges/segments）")


def _get_project_or_404(db: Session, project_id: str, current_user: User) -> Project:
    """按 id 获取“当前用户所属”案例；不存在或属于其他用户统一返回 404（避免越权探测）。"""
    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail=f"案例不存在：{project_id}")
    return project


@app.get("/projects")
def list_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """获取当前用户的案例列表（只返回 id / name / updated_at）。"""
    projects = (
        db.query(Project)
        .filter(Project.user_id == current_user.id)
        .order_by(Project.updated_at.desc())
        .all()
    )
    return [project.to_summary() for project in projects]


@app.get("/projects/{project_id}")
def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """获取当前用户指定案例的完整数据（含 nodes/edges/segments）。"""
    return _get_project_or_404(db, project_id, current_user).to_detail()


@app.post("/projects", status_code=201)
def create_project(
    payload: ProjectCreatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """新建案例（id 为 UUID），归属当前登录用户。"""
    project = Project(
        id=str(uuid4()),
        name=payload.name.strip(),
        data=payload.data,
        user_id=current_user.id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project.to_detail()


@app.put("/projects/{project_id}")
def update_project(
    project_id: str,
    payload: ProjectUpdatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """更新当前用户案例：覆盖 data；name 可选。"""
    project = _get_project_or_404(db, project_id, current_user)
    if payload.name is not None:
        project.name = payload.name.strip()
    project.data = payload.data
    project.updated_at = utc_now_naive()
    db.commit()
    db.refresh(project)
    return project.to_detail()


@app.delete("/projects/{project_id}")
def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """删除当前用户案例。"""
    project = _get_project_or_404(db, project_id, current_user)
    db.delete(project)
    db.commit()
    return {"deleted": True, "id": project_id}


# ---------------------------------------------------------------------------
# 拓扑校验
# ---------------------------------------------------------------------------
def build_closed_loop_graph(project: ProjectInput) -> nx.DiGraph:
    """旧版兼容：按 segments 输入顺序构造闭环有向图（相邻段连边 + 末段→首段闭合）。"""
    node_ids = [segment.segment_id for segment in project.segments]
    graph = nx.DiGraph()
    graph.add_nodes_from(node_ids)
    closing_tail = node_ids[1:] + node_ids[:1]
    graph.add_edges_from(zip(node_ids, closing_tail))
    return graph


def _validate_legacy_closed_loop(project: ProjectInput) -> TopologyResponse:
    """旧版兼容：segments 顺序闭环校验（保持既有客户端行为）。"""
    node_ids = [segment.segment_id for segment in project.segments]
    graph = build_closed_loop_graph(project)

    node_count = graph.number_of_nodes()
    edge_count = graph.number_of_edges()
    expected_count = len(project.segments)
    is_strongly_connected = bool(nx.is_strongly_connected(graph))
    closed_loop = bool(node_count >= 1 and edge_count == node_count)
    shared_subset_ok = set(project.shared_segments).issubset(set(node_ids))

    errors: list[str] = []
    if node_count != expected_count:
        errors.append(f"节点数与 segments 数量不一致：{node_count} != {expected_count}")
    if edge_count != expected_count:
        errors.append(f"边数与闭环要求不一致：{edge_count} != {expected_count}")
    if not is_strongly_connected:
        errors.append("闭环有向图不是强连通图")
    if not shared_subset_ok:
        errors.append("shared_segments 不是 segments 的子集")

    valid = not errors
    return TopologyResponse(
        valid=valid,
        node_count=node_count,
        edge_count=edge_count,
        is_strongly_connected=is_strongly_connected,
        closed_loop=closed_loop,
        shared_subset_ok=shared_subset_ok,
        message="闭环拓扑校验通过" if valid else "闭环拓扑校验失败",
        errors=errors,
        mode="legacy_closed_loop",
    )


@app.post("/validate-topology", response_model=TopologyResponse, responses=_ERROR_RESPONSES)
def validate_topology(project: ProjectInput) -> TopologyResponse:
    """校验拓扑：图模式走 NetworkX DAG/单热源/末端校验；旧版走闭环强连通校验。"""
    if project.nodes and project.edges:
        return TopologyResponse(**controller.validate_topology_graph(project))
    return _validate_legacy_closed_loop(project)


# ---------------------------------------------------------------------------
# 水力计算
# ---------------------------------------------------------------------------
@app.post("/calculate", response_model=CalculationResponse, responses=_ERROR_RESPONSES)
def calculate(
    project: ProjectInput,
    current_user: User = Depends(get_current_user),
) -> CalculationResponse:
    """执行 Part A 最不利环路 + Part B 并联环路计算（须登录）。"""
    result = controller.run_hydraulic_calculation(project)
    return CalculationResponse(**result)


# ---------------------------------------------------------------------------
# 后处理导出（V5）：Markdown / Excel
# ---------------------------------------------------------------------------
EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# V8.5 品牌标识：Markdown 与 Excel 报表顶部统一加注
REPORT_BRAND_LINE = "> 本报表由 [Atalantis 暖通工作台] 生成"

# 管段结果列的中文表头（Markdown / Excel 共用，保证“能直接用表格工具渲染”）
SEGMENT_COLUMNS = {
    "segment_id": "管段",
    "dn": "管径",
    "Q": "热负荷Q(W)",
    "G": "流量G(kg/h)",
    "l": "长度l(m)",
    "zeta_sum": "局部阻力Σξ",
    "shared": "共用段",
    "v": "流速v(m/s)",
    "Rm": "比摩阻Rm(Pa/m)",
    "dPy": "沿程阻力dPy(Pa)",
    "dPd": "动压头dPd(Pa)",
    "dPj": "局部阻力dPj(Pa)",
    "dP_total": "总阻力dP(Pa)",
}


def _segment_dataframe(segment_results: list[dict]) -> pd.DataFrame:
    """把后端 segment_results 转为 pandas.DataFrame（中文列名，数值保持全精度）。"""
    frame = pd.DataFrame(segment_results)
    if frame.empty:
        return pd.DataFrame(columns=list(SEGMENT_COLUMNS.values()))
    frame = frame[list(SEGMENT_COLUMNS.keys())].rename(columns=SEGMENT_COLUMNS)
    frame["共用段"] = frame["共用段"].map(lambda value: "是" if value else "否")
    return frame


def _parallel_loop_dataframe(result: dict) -> pd.DataFrame:
    """并联环路汇总 DataFrame（图模式取 parallel_loops；旧版兼容取 part_b）。"""
    parallel_loops = result.get("parallel_loops") or []
    if not parallel_loops and result.get("part_b"):
        parallel_loops = [result["part_b"]]
    rows = [
        {
            "环路": loop.get("label") or loop.get("sink_node") or "并联环路",
            "资用压力(Pa)": loop["available_pressure"],
            "共用段阻力(Pa)": loop["sum_shared"],
            "独用段总阻力(Pa)": loop["sum_resistance_specific"],
            "不平衡率(%)": loop["imbalance_percent"],
            "状态": loop["status"],
            "建议": loop["suggestion"],
        }
        for loop in parallel_loops
    ]
    return pd.DataFrame(rows)


def _warnings_dataframe(result: dict) -> pd.DataFrame:
    warnings = result.get("warnings") or []
    return pd.DataFrame(
        [{"序号": index + 1, "异常诊断": warning} for index, warning in enumerate(warnings)]
    ) if warnings else pd.DataFrame(columns=["序号", "异常诊断"])


def _topology_dataframe(result: dict) -> pd.DataFrame:
    topology = result.get("topology")
    if not topology:
        return pd.DataFrame(columns=["环路", "管段顺序", "总长度(m)", "共用管段", "独用管段"])
    loop1 = topology["loop1"]
    rows = [
        {
            "环路": f"Loop1（最不利环路，sink={loop1['sink_node']}）",
            "管段顺序": " → ".join(str(sid) for sid in loop1["segment_ids"]),
            "总长度(m)": loop1["total_length"],
            "共用管段": "",
            "独用管段": "",
        }
    ]
    for loop in topology.get("parallel_loops") or []:
        rows.append(
            {
                "环路": f"并联环路（sink={loop['sink_node']}）",
                "管段顺序": " → ".join(str(sid) for sid in loop["segment_ids"]),
                "总长度(m)": loop["total_length"],
                "共用管段": ", ".join(str(sid) for sid in loop["shared_segment_ids"]),
                "独用管段": ", ".join(str(sid) for sid in loop["dedicated_segment_ids"]),
            }
        )
    return pd.DataFrame(rows)


def build_markdown_report(project: ProjectInput, result: dict, project_name: str = "") -> str:
    """生成 Markdown 报表：核心结论 + 管段表格（pandas.to_markdown）+ 拓扑 + 异常诊断。"""
    title = project_name.strip() or "未命名案例"
    topology = result.get("topology")
    loop1 = topology["loop1"] if topology else None
    part_b = result.get("part_b")

    lines: list[str] = [
        REPORT_BRAND_LINE,
        "",
        f"# {title}_水力计算报表",
        "",
        "## 一、计算摘要",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 作用压力 dP′ | {result['dP_prime']:.4f} Pa |",
        f"| 最不利环路总阻力 ΣdP | {result['sum_resistance']:.4f} Pa |",
        f"| 系统富裕度 margin | {result['margin'] * 100:.2f}% |",
        f"| 最不利环路总长度 | {result['total_length']:.2f} m |",
        f"| 平均比摩阻 R_pj | {result['R_pj']:.4f} Pa/m |",
        f"| 迭代次数 | {result['iteration']} |",
        f"| 计算状态 | {result['status']} |",
    ]
    if topology:
        lines.extend(
            [
                f"| 自动识别热源节点 | {', '.join(topology['source_nodes'])} |",
                f"| 自动识别末端节点 | {', '.join(topology['sink_nodes'])} |",
                f"| 自动寻优最不利环路 Loop1 | {' → '.join(str(sid) for sid in loop1['segment_ids'])} |",
            ]
        )
    lines.extend(
        [
            "",
            "## 二、最不利环路管段计算表",
            "",
            _segment_dataframe(result["segment_results"]).to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 三、并联环路汇总",
            "",
        ]
    )

    parallel_frame = _parallel_loop_dataframe(result)
    if parallel_frame.empty:
        lines.append("无并联环路（未提供共用管段或多末端拓扑）。")
    else:
        lines.append(parallel_frame.to_markdown(index=False, floatfmt=".4f"))

    if topology:
        lines.extend(
            [
                "",
                "## 四、NetworkX 自动拓扑解析",
                "",
                _topology_dataframe(result).to_markdown(index=False, floatfmt=".4f"),
            ]
        )

    lines.extend(["", "## 五、异常诊断日志", ""])
    warnings = result.get("warnings") or []
    if warnings:
        lines.extend(f"{index + 1}. {warning}" for index, warning in enumerate(warnings))
    else:
        lines.append("无异常诊断记录。")

    lines.extend(["", "---", "", "本报表由后端水力计算接口自动生成，数值全部来自 engine.py 的计算结果。", ""])
    return "\n".join(lines)


def build_excel_workbook(project: ProjectInput, result: dict, project_name: str = "") -> bytes:
    """生成 Excel 工作簿字节流（pandas + openpyxl，多工作表）。"""
    title = project_name.strip() or "未命名案例"
    summary_rows: list[dict] = [
        {"指标": "案例名称", "数值": title},
        {"指标": "作用压力 dP′(Pa)", "数值": result["dP_prime"]},
        {"指标": "最不利环路总阻力 ΣdP(Pa)", "数值": result["sum_resistance"]},
        {"指标": "系统富裕度 margin", "数值": result["margin"]},
        {"指标": "最不利环路总长度(m)", "数值": result["total_length"]},
        {"指标": "平均比摩阻 R_pj(Pa/m)", "数值": result["R_pj"]},
        {"指标": "迭代次数", "数值": result["iteration"]},
        {"指标": "计算状态", "数值": result["status"]},
    ]
    topology = result.get("topology")
    if topology:
        summary_rows.extend(
            [
                {"指标": "自动识别热源节点", "数值": ", ".join(topology["source_nodes"])},
                {"指标": "自动识别末端节点", "数值": ", ".join(topology["sink_nodes"])},
                {
                    "指标": "最不利环路 Loop1 管段",
                    "数值": " → ".join(str(sid) for sid in topology["loop1"]["segment_ids"]),
                },
            ]
        )

    parallel_detail_rows: list[dict] = []
    for loop in result.get("parallel_loops") or []:
        for item in loop["segment_results"]:
            parallel_detail_rows.append(
                {"环路": loop.get("label") or loop.get("sink_node") or "并联环路", **item}
            )
    if not parallel_detail_rows and result.get("part_b"):
        for item in result["part_b"]["segment_results"]:
            parallel_detail_rows.append({"环路": result["part_b"].get("label") or "并联环路", **item})

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        summary_frame = pd.DataFrame(summary_rows)
        summary_frame.to_excel(writer, sheet_name="计算摘要", index=False, startrow=2)
        summary_sheet = writer.sheets["计算摘要"]
        summary_sheet["A1"] = REPORT_BRAND_LINE
        summary_sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=2)
        summary_sheet["A1"].font = Font(bold=True, color="1D39C4")
        _segment_dataframe(result["segment_results"]).to_excel(
            writer, sheet_name="最不利环路管段", index=False
        )
        parallel_frame = _parallel_loop_dataframe(result)
        if parallel_frame.empty:
            parallel_frame = pd.DataFrame([{"说明": "无并联环路"}])
        parallel_frame.to_excel(writer, sheet_name="并联环路汇总", index=False)
        if parallel_detail_rows:
            pd.DataFrame(parallel_detail_rows).rename(columns=SEGMENT_COLUMNS).to_excel(
                writer, sheet_name="并联环路明细", index=False
            )
        _topology_dataframe(result).to_excel(writer, sheet_name="拓扑解析", index=False)
        _warnings_dataframe(result).to_excel(writer, sheet_name="异常诊断", index=False)
    return buffer.getvalue()


@app.post("/export/markdown", response_class=PlainTextResponse, responses=_ERROR_RESPONSES)
def export_markdown(
    project: ProjectInput,
    project_name: str = "",
    current_user: User = Depends(get_current_user),
) -> PlainTextResponse:
    """导出 Markdown 报表（须登录）：执行同一计算并返回纯文本。"""
    result = controller.run_hydraulic_calculation(project)
    content = build_markdown_report(project, result, project_name=project_name)
    return PlainTextResponse(content=content, media_type="text/markdown; charset=utf-8")


@app.post("/export/excel", responses=_ERROR_RESPONSES)
def export_excel(
    project: ProjectInput,
    project_name: str = "",
    current_user: User = Depends(get_current_user),
) -> Response:
    """导出 Excel 报表（须登录）：pandas + openpyxl 生成文件流，前端 Blob 下载。"""
    result = controller.run_hydraulic_calculation(project)
    content = build_excel_workbook(project, result, project_name=project_name)
    filename = f"{project_name.strip() or 'case'}_hydraulic_calculation.xlsx"
    return Response(
        content=content,
        media_type=EXCEL_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename.encode("ascii", "ignore").decode() or "hydraulic_calculation.xlsx"}"'},
    )



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

# -*- coding: utf-8 -*-
"""api.exports — Markdown / Excel 报表导出路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse, Response

import controller
from auth import get_current_user
from models import ErrorResponse, ProjectInput
from models_user import User
from services.reporting import EXCEL_MEDIA_TYPE, build_excel_workbook, build_markdown_report

router = APIRouter()

ERROR_RESPONSES = {
    422: {"model": ErrorResponse, "description": "前置条件 / 输入校验失败"},
    500: {"model": ErrorResponse, "description": "后置条件违例或内部计算失败"},
}

@router.post("/export/markdown", response_class=PlainTextResponse, responses=ERROR_RESPONSES)
def export_markdown(
    project: ProjectInput,
    project_name: str = "",
    current_user: User = Depends(get_current_user),
) -> PlainTextResponse:
    """导出 Markdown 报表（须登录）：执行同一计算并返回纯文本。"""
    result = controller.run_hydraulic_calculation(project)
    content = build_markdown_report(project, result, project_name=project_name)
    return PlainTextResponse(content=content, media_type="text/markdown; charset=utf-8")


@router.post("/export/excel", responses=ERROR_RESPONSES)
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

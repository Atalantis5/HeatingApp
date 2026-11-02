# -*- coding: utf-8 -*-
"""api.projects — 当前登录用户的案例 CRUD。"""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models_db import Project, utc_now_naive
from models_user import User

router = APIRouter()

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


@router.get("/projects")
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


@router.get("/projects/{project_id}")
def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """获取当前用户指定案例的完整数据（含 nodes/edges/segments）。"""
    return _get_project_or_404(db, project_id, current_user).to_detail()


@router.post("/projects", status_code=201)
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


@router.put("/projects/{project_id}")
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


@router.delete("/projects/{project_id}")
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

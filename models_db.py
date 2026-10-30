# -*- coding: utf-8 -*-
"""models_db.py — SQLAlchemy ORM 模型（V8.3）

Project 表：
    id         : String(36) UUID 主键
    name       : 案例名称
    data       : JSON —— 前端的 nodes / edges / segments（以及可选的 calculationResult）
    user_id    : 归属用户 users.id（V8.3 多租户隔离；旧数据迁移时绑定 admin）
    created_at : 创建时间（UTC naive）
    updated_at : 更新时间（UTC naive，更新时自动刷新）
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String

from database import Base


def utc_now_naive() -> datetime:
    """返回不带 tzinfo 的 UTC 时间，避免 SQLite/Pydantic 序列化歧义。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Project(Base):
    """案例项目表：data 列保存前端拓扑/管段 JSON。"""

    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    data = Column(JSON, nullable=False, default=dict)
    user_id = Column(
        String(36),
        ForeignKey("users.id"),
        nullable=True,  # 允许旧库 ALTER 迁移时先为 NULL，随后由 init_db 绑定 admin
        index=True,
    )
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

    def to_summary(self) -> dict:
        """列表接口只返回 id/name/updated_at（契约要求）。"""
        return {
            "id": self.id,
            "name": self.name,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_detail(self) -> dict:
        """详情接口返回完整数据。"""
        return {
            "id": self.id,
            "name": self.name,
            "data": self.data or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

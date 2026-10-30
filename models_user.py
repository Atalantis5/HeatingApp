# -*- coding: utf-8 -*-
"""models_user.py — 用户 ORM 模型（V8.3 用户认证与多租户隔离）

User 表：
    id              : String(36) UUID 主键
    username        : 用户名（唯一）
    hashed_password : PBKDF2-HMAC-SHA256 哈希（含随机盐与迭代次数，绝不保存明文）
    created_at      : 创建时间（UTC naive）
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String

from database import Base


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    """系统用户表。"""

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

    def to_public_dict(self) -> dict:
        return {"id": self.id, "username": self.username}

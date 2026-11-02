# -*- coding: utf-8 -*-
"""api.auth — 登录与注册路由。"""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from auth import (
    authenticate_user,
    create_access_token,
    hash_password,
)
from database import get_db
from models_user import User

router = APIRouter()

# ---------------------------------------------------------------------------
# 登录（OAuth2 表单）
# ---------------------------------------------------------------------------
@router.post("/token")
def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> dict:
    """OAuth2 表单登录：username + password → access_token。"""
    user = authenticate_user(db, form_data.username, form_data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "username": user.username,
    }


# ---------------------------------------------------------------------------
# 注册（首次测试用；登录见上方 POST /token）
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


@router.post("/users/register", status_code=201)
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

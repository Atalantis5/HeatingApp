# -*- coding: utf-8 -*-
"""auth.py — V8.3 安全认证逻辑（OAuth2 Password Bearer + JWT）

职责：
- 密码哈希：PBKDF2-HMAC-SHA256 + 随机 16 字节盐 + 390000 次迭代（标准库实现，不存明文）；
- JWT 签发/校验：HS256，payload 包含用户 id(sub)、用户名与过期时间；
- OAuth2PasswordBearer 依赖：从 Authorization: Bearer <token> 获取当前用户；
- POST /token 表单登录路由（供 main.py include_router）。

安全说明：生产部署应通过环境变量 GRAVITY_AUTH_SECRET 覆盖默认密钥，
默认值仅用于本地教学/演示环境。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from database import get_db
from models_user import User

SECRET_KEY = os.environ.get("GRAVITY_AUTH_SECRET", "gravity-heating-local-dev-secret-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("GRAVITY_AUTH_EXPIRE_MINUTES", "1440"))
PBKDF2_ITERATIONS = 390_000
PBKDF2_PREFIX = "pbkdf2_sha256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")
router = APIRouter()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str) -> str:
    """生成密码哈希字符串：pbkdf2_sha256$迭代次数$盐$哈希。"""
    if not isinstance(password, str) or password == "":
        raise ValueError("密码不能为空")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{PBKDF2_PREFIX}${PBKDF2_ITERATIONS}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, hashed_password: str) -> bool:
    """恒定时间比较密码与存储哈希；格式非法返回 False（不抛内部细节）。"""
    if not isinstance(password, str) or not isinstance(hashed_password, str):
        return False
    try:
        prefix, iterations_text, salt_text, digest_text = hashed_password.split("$", 3)
        if prefix != PBKDF2_PREFIX:
            return False
        iterations = int(iterations_text)
        salt = _b64decode(salt_text)
        expected = _b64decode(digest_text)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def create_access_token(user: User, expires_minutes: Optional[int] = None) -> str:
    """签发 JWT（sub=用户 id；exp 使用 UTC 时间）。"""
    expire_delta = timedelta(minutes=expires_minutes or ACCESS_TOKEN_EXPIRE_MINUTES)
    expire_at = datetime.now(timezone.utc) + expire_delta
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "iat": datetime.now(timezone.utc),
        "exp": expire_at,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """校验 JWT 签名与过期时间；失败抛 jwt.PyJWTError。"""
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录已过期或凭证无效，请重新登录",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI 认证依赖：解析 Bearer token → 返回当前用户；无效/过期返回 401。"""
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise _credentials_exception()
    user_id = payload.get("sub")
    if not user_id:
        raise _credentials_exception()
    user = db.get(User, str(user_id))
    if user is None:
        raise _credentials_exception()
    return user


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

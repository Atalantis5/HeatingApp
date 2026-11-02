# -*- coding: utf-8 -*-
"""database.py — SQLAlchemy + SQLite 配置（V6.2）

数据库路径规则（V8.4 桌面打包自适应）：
- 源码/开发模式：``<05_engine/04_ProgramFile>/projects.db``（保持既有测试基线）；
- PyInstaller 打包后：``%APPDATA%/HeatingApp/projects.db``（可写、可持久，
  不放在 _MEIPASS 临时解压目录，避免重启丢数据；避免 Program Files 写入权限问题）；
- 可用环境变量 ``HEATING_APP_DATA_DIR`` 覆盖数据目录（自动化测试用）。

约定：
- Base 供 ORM 模型继承；
- SessionLocal 为 session 工厂；FastAPI 通过 ``get_db`` 依赖注入；
- SQLite 使用 check_same_thread=False，兼容 FastAPI 多线程请求；
- 本文件不负责数据初始化（默认案例由 init_db.py 负责）。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from config import BASE_DIR, IS_FROZEN, resolve_data_dir

DATA_DIR = resolve_data_dir(create=True)
DB_PATH = DATA_DIR / "projects.db"
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)
Base = declarative_base()


def get_db():
    """FastAPI 依赖：每个请求一个 Session，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

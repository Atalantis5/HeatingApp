# -*- coding: utf-8 -*-
"""main.py — FastAPI 应用入口。

路由与装配已拆分：
- app_factory.py：应用实例、生命周期、全局异常、静态资源兜底；
- api/auth.py：注册路由（登录仍由 auth.py 提供）；
- api/projects.py：案例 CRUD；
- api/calculation.py：/validate-topology 与 /calculate；
- api/exports.py：Markdown / Excel 导出；
- services/reporting.py：报表构造。
"""
from __future__ import annotations

from app_factory import app

__all__ = ["app"]

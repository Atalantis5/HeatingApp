# -*- coding: utf-8 -*-
"""pytest 启动环境。

生产/桌面启动要求显式配置 GRAVITY_AUTH_SECRET 与
HEATING_APP_ADMIN_PASSWORD；测试进程使用独立的固定测试值。
"""
from __future__ import annotations

import os

os.environ.setdefault("GRAVITY_AUTH_SECRET", "pytest-only-jwt-secret-not-for-production")
os.environ.setdefault("HEATING_APP_ADMIN_PASSWORD", "pytest-only-admin-password")
os.environ.setdefault("HEATING_APP_ADMIN_USERNAME", "admin")

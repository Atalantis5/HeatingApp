# -*- coding: utf-8 -*-
"""config.py — 统一的可写数据目录解析。

数据库、桌面启动日志与 WebView2 缓存必须落在同一目录，避免打包后路径分叉。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
IS_FROZEN = bool(getattr(sys, "frozen", False))


def resolve_data_dir(create: bool = False) -> Path:
    """返回可写数据目录：开发=源码目录；打包=%APPDATA%/HeatingApp。

    ``HEATING_APP_DATA_DIR`` 优先（自动化测试/便携部署用）。
    """
    override = os.environ.get("HEATING_APP_DATA_DIR", "").strip()
    if override:
        data_dir = Path(override).expanduser().resolve()
    elif IS_FROZEN:
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        data_dir = Path(appdata) / "HeatingApp"
    else:
        data_dir = BASE_DIR
    if create:
        data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir

# -*- mode: python ; coding: utf-8 -*-
"""build.spec — V8.5 PyInstaller 单文件 Windows 桌面应用配置（品牌定制）

产物：release/HeatingApp.exe（onefile，console=False）
打包内容：
- desktop_app.py（启动器）
- dist/（React 前端构建产物）
- 02 Calculation Sheet/（默认案例初始化所需的只读 Excel 数据源）
- schema_manifest.json（查表契约说明/元数据）
- PyWebView / pythonnet / Uvicorn / FastAPI / pandas / SQLAlchemy 等全部依赖

构建命令（在 05_engine/04_ProgramFile 下执行）：
    pyinstaller build.spec -y --distpath release --workpath build/pyinstaller
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT_DIR = Path.cwd().resolve()
CALC_SHEET_DIR = PROJECT_DIR.parent / "02 Calculation Sheet"
FRONTEND_DIST = PROJECT_DIR / "dist"
APP_ICON = PROJECT_DIR / "logo.ico"
VERSION_FILE = PROJECT_DIR / "version_info.txt"

datas = []
for source, destination in (
    (FRONTEND_DIST, "dist"),
    (CALC_SHEET_DIR, "02 Calculation Sheet"),
    (PROJECT_DIR / "schema_manifest.json", "."),
):
    if Path(source).exists():
        datas.append((str(source), destination))

# PyWebView 的 WebView2 互操作 DLL、pythonnet 的 .NET 运行时等属于包数据，
# PyInstaller 不会通过 import 自动收集，必须显式加入。
for package in ("webview", "pythonnet", "clr_loader"):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass

hiddenimports = [
    "clr",
    "clr_loader",
    "pythonnet",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "webview.platforms.win32",
    "openpyxl",
    "tabulate",
    "sqlalchemy.dialects.sqlite",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]
for package in ("uvicorn", "webview"):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:
        pass

a = Analysis(
    [str(PROJECT_DIR / "desktop_app.py")],
    pathex=[str(PROJECT_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "test_db_api",
        "test_api",
        "test_engine",
        "test_graph_api",
        "test_loop_solver",
        "test_lookup",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HeatingApp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(APP_ICON) if APP_ICON.exists() else None,
    version=str(VERSION_FILE) if VERSION_FILE.exists() else None,
)

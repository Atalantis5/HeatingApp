# -*- coding: utf-8 -*-
"""desktop_app.py — V8.4 Windows 桌面启动器

职责：
1. 在后台线程启动 Uvicorn（127.0.0.1:8000），复用原 FastAPI 应用；
2. 在 `--noconsole` 窗口模式下重定向 sys.stdout/sys.stderr，避免 Uvicorn 因 stdout=None 崩溃；
3. 使用 pywebview 创建 1400×900 原生窗口，加载本机 Web 服务；
4. 窗口关闭后优雅停止 Uvicorn 线程，确保进程完全退出。

打包后数据路径：
- FastAPI 静态资源 dist / 02 Calculation Sheet 从 PyInstaller 的 _MEIPASS 读取（只读）；
- projects.db 由 database.py 放到 %APPDATA%/HeatingApp（可写、持久）。
"""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

APP_TITLE = "重力循环供暖管网计算系统"
HOST = "127.0.0.1"
PORT = 8000
APP_URL = f"http://{HOST}:{PORT}"


# ---------------------------------------------------------------------------
# 1) 必须在导入 uvicorn/webview 前替换标准流（--noconsole 下 sys.stdout/stderr 为 None）
# ---------------------------------------------------------------------------
class DummyStdout:
    """虚拟输出流：让 Uvicorn/第三方库在无控制台环境下安全 write/flush。"""

    encoding = "utf-8"
    errors = "replace"

    def write(self, text):
        return len(text) if text else 0

    def flush(self):
        return None

    def isatty(self):
        return False

    def readable(self):
        return False

    def writable(self):
        return True

    def fileno(self):
        raise OSError("noconsole 模式下没有可用的 file descriptor")


def install_std_streams() -> None:
    if sys.stdout is None:
        sys.stdout = DummyStdout()
    if sys.stderr is None:
        sys.stderr = DummyStdout()
    if sys.stdin is None:
        sys.stdin = DummyStdout()


install_std_streams()


# ---------------------------------------------------------------------------
# 2) 日志与错误提示（无控制台时写文件 + 原生 MessageBox）
# ---------------------------------------------------------------------------
def frozen_data_dir() -> Path:
    """与 database.py 保持一致的 AppData 数据目录（仅用于日志/WebView2 缓存）。"""
    override = os.environ.get("HEATING_APP_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "HeatingApp"


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("heating_app")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger
    try:
        data_dir = frozen_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(data_dir / "app.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    except Exception:
        # 日志文件不可写时保持静默；主要流程仍然可以运行
        logger.addHandler(logging.NullHandler())
    return logger


LOGGER = setup_logging()


def show_native_error(message: str) -> None:
    LOGGER.error(message)
    try:
        ctypes.windll.user32.MessageBoxW(0, message, "启动失败", 0x00000010)
    except Exception:
        pass


def _write_crash_log(exc: BaseException) -> Path | None:
    try:
        path = frozen_data_dir() / "crash.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + "".join(traceback.format_exception(exc)),
            encoding="utf-8",
        )
        return path
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 3) PyWebView 的 .NET/pythonnet 运行时路径适配
#    .NET Framework 的 Assembly.LoadFile 对非 ASCII 路径/映射盘/远程位置非常敏感；
#    PyInstaller 解压目录与 AppData 可能包含中文用户名。这里优先使用纯 ASCII 缓存目录，
#    并显式从缓存加载 Python.Runtime.dll / WebView2 DLL，保证中文安装路径下也能弹窗。
# ---------------------------------------------------------------------------
def _is_ascii_path(path: Path) -> bool:
    return all(ord(char) < 128 for char in str(path))


def _resolve_ascii_runtime_root() -> Path | None:
    candidates = []
    override = os.environ.get("HEATING_APP_ASCII_RUNTIME_DIR", "").strip()
    if override:
        candidates.append(Path(override))
    for env_name in ("PROGRAMDATA", "PUBLIC", "LOCALAPPDATA"):
        base = os.environ.get(env_name, "").strip()
        if base:
            candidates.append(Path(base) / "HeatingApp" / "runtime")
    candidates.append(Path(tempfile.gettempdir()) / "HeatingAppRuntime")
    candidates.append(frozen_data_dir() / "runtime")
    for candidate in candidates:
        if _is_ascii_path(candidate):
            return candidate
    return None


def prepare_webview_clr_runtime() -> Path | None:
    """把 pythonnet / WebView2 的托管 DLL 放到纯 ASCII 缓存，再从该路径加载。

    打包环境首次启动会从 _MEIPASS 复制到 ProgramData/HeatingApp/runtime；
    若目录不可写（受限环境），会尝试使用预先存在的 ASCII 缓存。
    """
    ascii_root = _resolve_ascii_runtime_root()
    if ascii_root is None:
        LOGGER.warning("未找到纯 ASCII 运行时目录，将使用 _MEIPASS 默认路径")
        return None
    try:
        import pythonnet

        source_runtime = Path(pythonnet.__file__).resolve().parent / "runtime"
        target_runtime = ascii_root / "runtime"
        source_webview = Path(__import__("webview").__file__).resolve().parent / "lib"
        target_webview = ascii_root / "webview_lib"

        try:
            target_runtime.mkdir(parents=True, exist_ok=True)
            target_webview.mkdir(parents=True, exist_ok=True)
        except Exception:
            # 受限环境：缓存可能已经由安装器/上一次运行准备好
            pass

        if not (target_runtime / "Python.Runtime.dll").exists() and source_runtime.is_dir():
            shutil.copytree(source_runtime, target_runtime, dirs_exist_ok=True)
        if not (target_webview / "Microsoft.Web.WebView2.Core.dll").exists() and source_webview.is_dir():
            shutil.copytree(source_webview, target_webview, dirs_exist_ok=True)

        if not (target_runtime / "Python.Runtime.dll").exists():
            LOGGER.warning("ASCII 运行时缓存不可用，回退到默认路径")
            return None

        # pythonnet.load 内部按 Path(pythonnet.__file__).parent/'runtime' 定位 DLL，
        # 将 __file__ 指向 ASCII 缓存后即可绕过 .NET 的远程程序集限制。
        pythonnet.__file__ = str(ascii_root / "pythonnet_loader.py")
        pythonnet.load("netfx")
        import clr  # noqa: F401  预加载 .NET Framework，避免 pywebview 回退 coreclr

        import webview
        import webview.util as webview_util

        original_interop = webview_util.interop_dll_path

        def patched_interop(dll_name):
            if dll_name == "WebBrowserInterop.dll":
                dll_name = "WebBrowserInterop.x64.dll"
            for candidate in (
                target_webview / dll_name,
                target_webview / "runtimes" / dll_name / "native",
            ):
                if candidate.exists():
                    return str(candidate)
            return original_interop(dll_name)

        webview_util.interop_dll_path = patched_interop
        LOGGER.info("已加载 ASCII 运行时缓存：%s", ascii_root)
        return ascii_root
    except Exception:
        LOGGER.exception("准备 WebView/.NET 运行时缓存失败，将回退默认路径")
        return None


# ---------------------------------------------------------------------------
# 3) Uvicorn 后台线程
# ---------------------------------------------------------------------------
class BackendServer:
    def __init__(self) -> None:
        self.server = None
        self.thread: threading.Thread | None = None
        self.error: BaseException | None = None

    def start(self) -> None:
        import uvicorn

        from main import app  # 延迟导入：确保 stdout 替换、打包路径已就绪

        config = uvicorn.Config(
            app,
            host=HOST,
            port=PORT,
            log_level="warning",
            access_log=False,
            log_config=None,  # 避免 --noconsole 下 logging StreamHandler 访问空 stdout
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self._run, name="uvicorn-backend", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            self.server.run()
        except BaseException as exc:  # 记录线程内异常，主线程负责提示
            self.error = exc
            LOGGER.exception("Uvicorn 后台线程异常：%s", exc)

    def wait_ready(self, timeout: float = 40.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.error is not None:
                return False
            try:
                with socket.create_connection((HOST, PORT), timeout=0.5):
                    return True
            except OSError:
                time.sleep(0.15)
        return False

    def stop(self, timeout: float = 10.0) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=timeout)
        if self.thread is not None and self.thread.is_alive() and self.server is not None:
            self.server.force_exit = True
            self.thread.join(timeout=3.0)
        LOGGER.info("Uvicorn 后台线程已停止（alive=%s）", bool(self.thread and self.thread.is_alive()))


# ---------------------------------------------------------------------------
# 4) 可选：测试用自动关闭/远程调试端口（生产默认关闭）
# ---------------------------------------------------------------------------
def _maybe_schedule_auto_close(window) -> None:
    raw = os.environ.get("HEATING_APP_TEST_CLOSE_AFTER", "").strip()
    if not raw:
        return
    try:
        seconds = max(1.0, float(raw))
    except ValueError:
        return

    def _close_later():
        time.sleep(seconds)
        try:
            window.destroy()
        except Exception:
            LOGGER.exception("测试自动关闭窗口失败")

    threading.Thread(target=_close_later, name="e2e-auto-close", daemon=True).start()
    LOGGER.info("已启用 E2E 自动关闭：%.1f 秒", seconds)


def _configure_webview_remote_debug() -> None:
    raw = os.environ.get("HEATING_APP_REMOTE_DEBUG_PORT", "").strip()
    if not raw:
        return
    try:
        import webview

        webview.settings["REMOTE_DEBUGGING_PORT"] = int(raw)
        LOGGER.info("WebView2 远程调试端口已开启：%s", raw)
    except Exception:
        LOGGER.exception("配置 WebView2 远程调试端口失败")


# ---------------------------------------------------------------------------
# 5) 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    LOGGER.info("========== %s 启动 ==========", APP_TITLE)
    LOGGER.info("frozen=%s | executable=%s", getattr(sys, "frozen", False), sys.executable)
    LOGGER.info("_MEIPASS=%s", getattr(sys, "_MEIPASS", "<源码模式>"))

    backend = BackendServer()
    backend.start()
    LOGGER.info("Uvicorn 线程已启动，等待 %s 就绪…", APP_URL)
    if not backend.wait_ready(timeout=45.0):
        detail = f"{APP_URL} 后端启动失败"
        if backend.error:
            detail += f"：{type(backend.error).__name__}: {backend.error}"
        backend.stop()
        show_native_error(detail)
        return 2

    LOGGER.info("后端就绪：%s", APP_URL)
    try:
        prepare_webview_clr_runtime()
        import webview

        _configure_webview_remote_debug()
        data_dir = frozen_data_dir()
        try:
            storage_dir = data_dir / "webview"
            storage_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            storage_dir = Path.cwd() / ".heating-webview"
        window = webview.create_window(
            APP_TITLE,
            APP_URL,
            width=1400,
            height=900,
            min_size=(1200, 780),
            resizable=True,
            text_select=True,
            confirm_close=False,
            background_color="#f0f2f5",
        )
        _maybe_schedule_auto_close(window)
        # private_mode=False：保留 WebView localStorage（登录 token 可跨会话）
        webview.start(
            debug=bool(os.environ.get("HEATING_APP_WEBVIEW_DEBUG")),
            private_mode=False,
            storage_path=str(storage_dir),
        )
        LOGGER.info("原生窗口已关闭，开始停止后台服务")
    except BaseException as exc:
        crash_log = _write_crash_log(exc)
        LOGGER.exception("桌面窗口/后端启动失败")
        message = f"桌面程序启动失败：{type(exc).__name__}: {exc}"
        if crash_log:
            message += f"\n\n崩溃日志：{crash_log}"
        show_native_error(message)
        backend.stop()
        return 3
    finally:
        backend.stop()

    LOGGER.info("========== 程序退出 ==========")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

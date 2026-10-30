# ====================== 日志与全局异常兜底 ======================
"""统一日志出口。

- 运行日志写入 ``logs/app.log``（2MB × 4 归档），控制台仅输出 WARNING 以上，
  避免打包为 GUI 程序后控制台噪音。
- 接收数据日志（用户勾选"记录到文件"）走独立通道，见主窗口，不与本日志混写。
- 安装全局异常钩子：未捕获异常先落盘，再交给 UI 弹窗，避免"窗口静默消失"。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
import traceback
from collections.abc import Callable

__all__ = ["setup_logging", "install_excepthook", "get_logger"]

LOG_FILE_NAME = "app.log"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 4
_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def setup_logging(log_dir: str, *, level: int = logging.INFO) -> logging.Logger:
    """初始化根日志器（幂等，重复调用只生效一次）。

    Args:
        log_dir: 日志目录，不存在会自动创建；创建失败时降级为仅控制台输出。
        level: 文件日志级别。

    Returns:
        根日志器。
    """
    global _configured
    root = logging.getLogger()
    if _configured:
        return root

    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    console = logging.StreamHandler(stream=sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, LOG_FILE_NAME),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:  # 只读目录 / 权限不足：不阻断启动
        root.warning("无法创建日志文件，已降级为控制台输出: %s", exc)

    _configured = True
    root.info("=" * 60)
    root.info("日志系统就绪，Python %s", sys.version.split()[0])
    return root


def install_excepthook(on_fatal: Callable[[str], None] | None = None) -> None:
    """安装未捕获异常钩子：写日志 + 回调 UI 提示。

    Args:
        on_fatal: UI 回调（接收单行摘要），由调用方保证线程安全
            （通常用 ``QTimer.singleShot(0, ...)`` 投递回主线程）。
    """
    log = logging.getLogger("uncaught")
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc_value, exc_tb)
            return
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.critical("未捕获异常:\n%s", detail)
        if on_fatal is not None:
            summary = f"{exc_type.__name__}: {exc_value}"
            try:
                on_fatal(summary)
            except Exception:  # 兜底自身不得再抛
                log.exception("异常提示回调失败")

    sys.excepthook = _hook

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        detail = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
        log.critical("子线程未捕获异常 (%s):\n%s", args.thread.name if args.thread else "?", detail)

    threading.excepthook = _thread_hook

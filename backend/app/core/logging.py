"""运行日志的统一配置、上下文和脱敏 JSON 格式；与私有实验审计分离。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import traceback
from collections.abc import Iterator
from typing import Any

from .redaction import redact_secrets

LOG_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("operational_log_context", default={})
_FIELDS = frozenset({
    "request_id", "project_id", "run_id", "task_id", "worker_id", "operation_id", "step", "provider", "model",
    "method", "route", "status_code", "duration_ms", "error_type", "error_code", "stage", "source_file", "line",
    "finish_reason", "input_tokens", "output_tokens", "tool_calls", "request_bytes", "response_bytes",
    "timeout_seconds", "retryable", "attempt", "policy_version", "reason", "count", "file_count", "node_count",
    "wait_seconds", "errno", "winerror",
})
_URL = re.compile(r"(?i)https?://[^\s\"'<>]+")
_BEARER = re.compile(r"(?i)\b(?:Bearer|Basic)\s+[^\s\"',;]+")


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """绑定当前请求/任务的关联字段；支持异步和 to_thread 继承，退出后恢复原上下文。"""
    token = LOG_CONTEXT.set({**LOG_CONTEXT.get(), **fields})
    try:
        yield
    finally:
        LOG_CONTEXT.reset(token)


def _safe_text(value: str, maximum: int = 2048) -> str:
    """遮蔽常见凭据、实际模型密钥和 URL；限制输出长度，不宣称通用 DLP。"""
    key = os.getenv("CODE_EXPLORER_LLM_API_KEY", "").strip().strip("\"'")
    if key:
        value = value.replace(key, "[REDACTED]")
    value = _URL.sub("[URL_REDACTED]", _BEARER.sub("Bearer [REDACTED]", redact_secrets(value)))
    return value[:maximum] + ("…[truncated]" if len(value) > maximum else "")


class SafeJsonFormatter(logging.Formatter):
    """只输出允许字段和安全异常栈，不保存请求正文、源码行、局部变量或异常正文。"""

    def format(self, record: logging.LogRecord) -> str:
        """将一条运行记录转换为单行 UTF-8 JSON；非允许的 extra 字段丢弃。"""
        try:
            message = _safe_text(record.getMessage())
        except Exception:
            message = "invalid_log_message"
        result: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": _safe_text(record.levelname), "logger": _safe_text(record.name, 256), "pid": record.process, "event": message,
        }
        for key, value in {**LOG_CONTEXT.get(), **record.__dict__}.items():
            if key in _FIELDS and (value is None or isinstance(value, (str, int, float, bool))):
                result[key] = _safe_text(value, 256) if isinstance(value, str) else value
        if record.exc_info and record.exc_info[1] is not None:
            error = record.exc_info[1]
            result["exception"] = {
                "type": _safe_text(type(error).__name__),
                "frames": [{"file": _safe_text(Path(frame.filename).name), "function": _safe_text(frame.name),
                            "line": frame.lineno} for frame in traceback.extract_tb(record.exc_info[2])[-12:]],
            }
            if isinstance(error, json.JSONDecodeError):
                result["exception"].update(json_line=error.lineno, json_column=error.colno, json_position=error.pos)
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


class _SafeSinkErrors:
    """日志输出失败时不让 logging 的默认处理器回显原始参数和异常正文。"""

    def handleError(self, record: logging.LogRecord) -> None:
        """输出固定告警；不递归调用 logger，也不把日志失败变为业务失败。"""
        try:
            sys.__stderr__.write('{"level":"ERROR","event":"logging_sink_failed"}\n')
        except Exception:
            pass


class SafeStreamHandler(_SafeSinkErrors, logging.StreamHandler):
    """带安全失败回退的控制台处理器。"""


class SafeRotatingFileHandler(_SafeSinkErrors, RotatingFileHandler):
    """带安全失败回退的有界轮转文件处理器；每个进程使用独立文件。"""


def configure_logging(role: str = "api") -> Path | None:
    """为服务进程安装一次处理器，返回可选日志文件路径；不接管第三方 root logger。"""
    if role not in {"api", "agent-worker", "execution-worker"}:
        raise ValueError("Unsupported logging process role")
    level_name = os.getenv("CODE_EXPLORER_LOG_LEVEL", "INFO").strip().upper()
    if level_name not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("CODE_EXPLORER_LOG_LEVEL must be DEBUG/INFO/WARNING/ERROR/CRITICAL")
    logger = logging.getLogger("backend.app")
    managed = [handler for handler in logger.handlers if getattr(handler, "_code_explorer_managed", False)]
    if managed:
        return next((Path(handler.baseFilename) for handler in managed if isinstance(handler, RotatingFileHandler)), None)
    try:
        file_bytes = int(os.getenv("CODE_EXPLORER_LOG_MAX_BYTES", "10485760"))
        backups = int(os.getenv("CODE_EXPLORER_LOG_BACKUP_COUNT", "5"))
        if not 1024 <= file_bytes <= 100 * 1024 * 1024 or not 1 <= backups <= 20:
            raise ValueError
    except ValueError:
        raise ValueError("日志轮转大小须为 1024～104857600 字节，备份数量须为 1～20。") from None
    handlers: list[logging.Handler] = [SafeStreamHandler()]
    target = None
    file_enabled = os.getenv("CODE_EXPLORER_LOG_FILE_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
    if file_enabled:
        directory = Path(os.getenv("CODE_EXPLORER_LOG_DIR", "").strip() or Path(__file__).resolve().parents[2] / "storage" / "logs")
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{role}-{os.getpid()}.jsonl"
            handlers.append(SafeRotatingFileHandler(target, maxBytes=file_bytes, backupCount=backups, encoding="utf-8"))
        except OSError:
            target = None  # 文件不可写时保留控制台，日志设施失败不得阻断业务。
    formatter = SafeJsonFormatter()
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, "_code_explorer_managed", True)
        logger.addHandler(handler)
    logger.setLevel(level_name)
    logger.propagate = False
    # 请求概要由安全边界记录；禁止 Uvicorn 再输出含查询参数的访问日志。
    logging.getLogger("uvicorn.access").disabled = True
    server_logger = logging.getLogger("uvicorn.error")
    server_logger.handlers = handlers.copy()
    server_logger.setLevel(level_name)
    server_logger.propagate = False
    if target is None and file_enabled:
        logger.error("logging.file_unavailable_console_only")
    return target

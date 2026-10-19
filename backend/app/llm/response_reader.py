"""JSON/SSE 共用有界读取：空闲 socket 超时与独立总读取截止时间。"""

from __future__ import annotations

import socket
import threading
import time
from types import TracebackType
from typing import Any

MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_EVENT_BYTES = 1024 * 1024


class RequestCancelled(RuntimeError):
    """调用方取消当前尝试；后台线程不得继续写入已封存的实验审计。"""


class RequestControl:
    """跨异步调用与读取线程传递取消，不修改进程网络设置。"""

    def __init__(self) -> None:
        """创建本次尝试的取消信号；响应建立前不持有 socket。"""
        self.cancelled = threading.Event()
        self.reader: ResponseReader | None = None

    def cancel(self) -> None:
        """通知取消并尝试 shutdown；系统阻塞可能延后释放，但不得再发布响应。"""
        self.cancelled.set()
        if self.reader is not None:
            self.reader.interrupt()

    def attach(self, reader: ResponseReader) -> None:
        """处理取消和响应建立之间的竞争，避免错过连接中断。"""
        self.reader = reader
        if self.cancelled.is_set():
            reader.interrupt()


class ResponseReader:
    """控制已建立响应的读取；不接触认证或代理设置，不引入新 HTTP 客户端。"""

    def __init__(self, response: Any, *, started: float, total_timeout: float | None,
                 idle_timeout: float | None, control: RequestControl | None = None) -> None:
        """输入 urllib 响应与单调时钟预算；超时关闭已建立 socket，防止慢速滴流绕过。"""
        self.response = response
        self.deadline = started + total_timeout if total_timeout is not None else None
        self.idle_timeout = idle_timeout
        self.bytes_read = 0
        self.expired = threading.Event()
        candidate = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        self.sock = candidate if isinstance(candidate, socket.socket) else None
        self.timer: threading.Timer | None = None
        self.control = control
        if control is not None:
            control.attach(self)

    def remaining(self) -> float | None:
        """返回总剩余秒数；总预算耗尽即失败，不能接受截止后凑齐的正文。"""
        if self.control is not None and self.control.cancelled.is_set():
            raise RequestCancelled()
        if self.deadline is None:
            return None
        remaining = self.deadline - time.monotonic()
        if self.expired.is_set() or remaining <= 0:
            self.expired.set()
            raise TimeoutError("Model response deadline exceeded")
        return remaining

    def __enter__(self) -> ResponseReader:
        """启动总读取看门狗；仅关闭本次连接，不改全局 socket 或进程超时。"""
        remaining = self.remaining()
        if remaining is not None and self.sock is not None:
            self.timer = threading.Timer(remaining, self._expire)
            self.timer.daemon = True
            self.timer.start()
        return self

    def _expire(self) -> None:
        """中断截止后仍阻塞的 socket 读取；不从后台线程写审计或日志。"""
        self.expired.set()
        self.interrupt()

    def interrupt(self) -> None:
        """仅关闭本次已建立 socket，不在后台线程写日志或操作数据库。"""
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        """清理看门狗，连接由外层 urllib 上下文关闭。"""
        if self.timer is not None:
            self.timer.cancel()

    def _prepare(self) -> None:
        """每次读取使用空闲预算与剩余总预算的较小值。"""
        remaining = self.remaining()
        limits = [value for value in (remaining, self.idle_timeout) if value is not None]
        if limits and self.sock is not None:
            self.sock.settimeout(min(limits))

    def _count(self, raw: bytes) -> bytes:
        """限制完整 HTTP 正文，拒绝超限，不静默截断证据或模型回复。"""
        self.bytes_read += len(raw)
        if self.bytes_read > MAX_RESPONSE_BYTES:
            raise ValueError("Model response exceeds transport limit")
        self.remaining()
        return raw

    def json_body(self) -> bytes:
        """读取有界 JSON；无总预算的旧内部入口保持一次 read 调用兼容。"""
        if self.deadline is None:
            return self._count(self.response.read(MAX_RESPONSE_BYTES + 1))
        parts = []
        read = getattr(self.response, "read1", None) or self.response.read
        while True:
            self._prepare()
            try:
                raw = self._count(read(64 * 1024))
            except OSError:
                self.remaining()  # 将看门狗引起的 Windows 10053 等统一归因到总时限。
                raise
            if not raw:
                return b"".join(parts)
            parts.append(raw)

    def line(self) -> bytes:
        """读取单行 SSE，行及事件另受 1 MiB 上限约束。"""
        self._prepare()
        try:
            raw = self._count(self.response.readline(MAX_EVENT_BYTES + 1))
        except OSError:
            self.remaining()
            raise
        if len(raw) > MAX_EVENT_BYTES:
            raise ValueError("Model SSE line exceeds transport limit")
        return raw

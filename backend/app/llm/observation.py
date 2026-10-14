"""可选传输观察接口；只接收协议正文，不接触认证头、URL 或项目持久化实现。"""

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator
from typing import Any, Protocol


class ModelWireObserver(Protocol):
    """当前异步运行的实际 HTTP 正文观察器，线程通过 ContextVar 继承。"""

    def sent(self, payload: dict[str, Any], attempt: int, *, wire_sha256: str = "", wire_bytes: int = 0) -> int:
        """发送前记录真实协议正文，返回本次请求条目标识。"""
        ...

    def received(self, request_id: int, payload: dict[str, Any], *, diagnostics: dict[str, Any] | None = None) -> None:
        """记录响应的可见投影及可选阶段计时，不保存隐藏推理、地址或认证头。"""
        ...

    def failed(self, request_id: int, error_type: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        """记录失败尝试及安全阶段/状态字段，不接收原始错误文本。"""
        ...

    def retry_wait(self, attempt: int, requested_seconds: float, elapsed_seconds: float, *,
                   status_code: int | None, completed: bool) -> None:
        """可选扩展：记录失败尝试后的有限等待及取消状态，不算新增请求或用量。"""
        ...


WIRE_OBSERVER: ContextVar[ModelWireObserver | None] = ContextVar("model_wire_observer", default=None)


@contextmanager
def observe_requests(observer: ModelWireObserver | None) -> Iterator[None]:
    """在单次运行范围注入观察器，并在取消/失败时恢复上下文，防止并发串台。"""
    token = WIRE_OBSERVER.set(observer)
    try:
        yield
    finally:
        WIRE_OBSERVER.reset(token)

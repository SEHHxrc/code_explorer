"""可选传输观察接口；只接收协议正文，不接触认证头、URL 或项目持久化实现。"""

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator
from dataclasses import asdict
from typing import Any, Protocol
from .response_metadata import response_metadata


class MemoryWireObserver:
    """独立模型任务共用的传输计量；仅保存哈希、供应商用量和安全错误字段。"""

    def __init__(self) -> None:
        """为独立任务创建传输尝试及退避计量容器。"""
        self.attempts: list[dict[str, Any]] = []
        self.waits: list[dict[str, Any]] = []

    def sent(self, payload: dict[str, Any], attempt: int, *, wire_sha256: str = "", wire_bytes: int = 0) -> int:
        """记录实际发送，不保留请求正文、认证头或端点地址。"""
        self.attempts.append({"attempt": attempt, "wire_sha256": wire_sha256, "wire_bytes": wire_bytes,
                              "status": "sent", "responses_api": "messages" not in payload})
        return len(self.attempts) - 1

    def received(self, request_id: int, payload: dict[str, Any], *, diagnostics: dict[str, Any] | None = None) -> None:
        """按真实响应协议提取终止元数据及供应商用量，不保存响应正文。"""
        item = self.attempts[request_id]
        item.update(status="received", metadata=asdict(response_metadata(payload, responses_api=item["responses_api"])))

    def failed(self, request_id: int, error_type: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        """保留失败状态及实际收到的部分用量；缺失字段仍未知。"""
        self.attempts[request_id].update(status="failed", error_type=error_type,
                                       status_code=(diagnostics or {}).get("status_code"))
        usage = (diagnostics or {}).get("partial_usage")
        if isinstance(usage, dict):
            self.attempts[request_id]["metadata"] = asdict(response_metadata({"usage": usage},
                responses_api=self.attempts[request_id]["responses_api"]))

    def retry_wait(self, attempt: int, requested_seconds: float, elapsed_seconds: float, *,
                   status_code: int | None, completed: bool) -> None:
        """保留实际退避等待及是否完成，不把等待当作模型逻辑调用。"""
        self.waits.append({"attempt": attempt, "requested_seconds": requested_seconds, "elapsed_seconds": elapsed_seconds,
                           "status_code": status_code, "completed": completed})

    def usage(self, fallback: list[dict[str, Any] | None] | None = None) -> dict[str, int | None]:
        """只累计已报告字段；任一失败尝试用量未知，则总量仍未知。"""
        metadata = [item.get("metadata") for item in self.attempts] if self.attempts else (fallback or [])
        return {field: sum(item["usage"][field] for item in metadata)
                if metadata and all(item and item["usage"].get(field) is not None for item in metadata) else None
                for field in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens")}


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

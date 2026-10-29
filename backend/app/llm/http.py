"""模型 HTTP 请求、有限速率重试与安全错误解析。"""

from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import math
import random
import time
from datetime import datetime, timezone
from email.message import Message
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .observation import WIRE_OBSERVER
from .response_reader import MAX_EVENT_BYTES, RequestCancelled, RequestControl, ResponseReader
from .streaming import ChatStream, StreamProtocolError

logger = logging.getLogger(__name__)

MAX_ERROR_BODY_BYTES = 64 * 1024
MAX_TRANSIENT_RETRIES = 2
MAX_RETRY_WAIT_SECONDS = 300.0
RETRY_POLICY_VERSION = "exponential-jitter-v2"
_TRANSIENT_UPSTREAM_STATUSES = {502, 503, 504}
_NON_RETRYABLE_LIMIT_CODES = {
    "insufficient_quota",
    "credit_balance_exhausted",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded",
    "context_length_exceeded",
    "max_tokens_exceeded",
    "prompt_too_long",
}
_PUBLIC_ERROR_MESSAGES = {
    "context_length_exceeded": "模型平台报告上下文超限，请核对实际模型窗口、输入和输出预算。",
    "max_tokens_exceeded": "模型平台报告 Token 上限超限，请核对平台输出参数和预算。",
    "prompt_too_long": "模型平台报告输入过长，请核对输入预算和平台限制。",
    "credit_balance_exhausted": "模型 API 余额不足，请在供应商控制台补充余额后重试。",
    "organization_spend_limit_exceeded": "模型 API 组织消费上限已达到，请调整组织消费限制。",
    "project_spend_limit_exceeded": "模型 API 项目消费上限已达到，请调整项目消费限制。",
    "organization_usage_limit_exceeded": "模型 API 组织使用额度已达到，请提高额度后重试。",
    "slow_down": "模型 API 请求增长过快，请稍后重试。",
    "RateLimitReached": "模型平台报告速率限制，请等待后重试；本次可按有限退避策略避让。",
    "model_not_found": "配置的模型不存在，或当前 API Key 无权访问该模型。",
}


class ModelRequestError(RuntimeError):
    """模型请求失败的安全公共基类。"""

    public_message = "无法连接模型服务，请检查服务地址和网络配置。"
    retryable = False


class ModelConnectionError(ModelRequestError):
    """模型端点无法建立网络连接。"""

    retryable = True


class ModelStreamInterruptedError(ModelConnectionError):
    """已建立的流式请求失败；不重放或拼接部分响应，避免隐式重复生成。"""

    retryable = False
    public_message = "模型流式响应中断，未生成有效报告；本次用量可能未知，未自动重放部分响应。"


class ModelDeadlineError(ModelConnectionError):
    """本次尝试已耗尽总预算，不因网络可重试性重新发起长请求。"""

    retryable = False
    public_message = "模型请求超过本次总时限；未接受不完整报告，请调整传输预算后重试。"


class ModelResponseError(ModelRequestError):
    """响应协议错误，不伪装成网络故障，也不通过重传掩盖本地解析问题。"""

    public_message = "模型响应格式无效或流式响应未完整结束，不能作为有效报告；未自动切换协议或修补正文。"


class ModelEndpointError(ModelRequestError):
    """模型端点返回非成功 HTTP 状态及经过筛选的诊断字段。"""

    def __init__(
        self,
        *,
        status_code: int,
        error_type: str | None = None,
        error_code: str | None = None,
        retry_after: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """保存不含密钥、组织标识或完整上游正文的诊断数据。"""
        self.status_code = status_code
        self.error_type = error_type
        self.error_code = error_code
        self.retry_after = retry_after
        self.request_id = request_id
        self.retryable = self._is_retryable()
        self.public_message = self._public_message()
        super().__init__(self.public_message)

    def _is_retryable(self) -> bool:
        """将临时限流和网关级上游故障标记为可重试。"""
        if self.error_code in _NON_RETRYABLE_LIMIT_CODES or self.error_type == "insufficient_quota":
            return False
        if self.status_code in _TRANSIENT_UPSTREAM_STATUSES:
            return True
        if self.status_code != 429:
            return False
        return self.error_type == "rate_limit_error" or self.error_code is None or self.error_code.casefold() in {
            "slow_down", "ratelimitreached", "rate_limit_reached", "rate_limit_exceeded",
        }

    def _public_message(self) -> str:
        """根据安全错误码生成可直接展示给用户的中文说明。"""
        if self.error_code in _PUBLIC_ERROR_MESSAGES:
            return _PUBLIC_ERROR_MESSAGES[self.error_code]
        if self.error_type == "insufficient_quota":
            return "模型 API 余额或可用额度不足，请检查供应商控制台。"
        if self.status_code == 401:
            return "模型 API Key 无效、已过期或已撤销。"
        if self.status_code == 403:
            return "当前 API Key 无权访问配置的模型或端点。"
        if self.status_code == 404:
            return "配置的模型或模型端点不存在。"
        if self.status_code == 429:
            return "模型 API 当前受到速率、余额或消费额度限制，请检查供应商控制台。"
        if self.status_code >= 500:
            return "模型服务暂时不可用，请稍后重试。"
        return f"模型服务拒绝了请求（HTTP {self.status_code}）。"


async def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    *,
    transient_retries: int = MAX_TRANSIENT_RETRIES,
    total_timeout: float | None = None,
    stream_idle_timeout: float | None = None,
) -> dict[str, Any]:
    """异步发送 JSON POST，并对临时限流或上游故障执行有限退避重试。"""
    return await _request_with_retry(
        "POST",
        url,
        payload,
        headers,
        timeout,
        max_retries=min(MAX_TRANSIENT_RETRIES, max(0, transient_retries)),
        total_timeout=total_timeout, stream_idle_timeout=stream_idle_timeout,
    )


async def get_json(
    url: str,
    headers: dict[str, str],
    timeout: float,
    *,
    transient_retries: int = MAX_TRANSIENT_RETRIES,
) -> dict[str, Any]:
    """异步发送 JSON GET，并应用与模型生成相同的安全错误及重试策略。"""
    return await _request_with_retry(
        "GET",
        url,
        None,
        headers,
        timeout,
        max_retries=min(MAX_TRANSIENT_RETRIES, max(0, transient_retries)),
    )


async def _request_with_retry(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: float,
    *,
    max_retries: int,
    total_timeout: float | None = None,
    stream_idle_timeout: float | None = None,
) -> dict[str, Any]:
    """执行请求并遵循 Retry-After 或带抖动的指数退避。"""
    if max_retries < 0:
        raise ValueError("Retry count must be nonnegative")
    attempt = 0
    while True:
        control = RequestControl()
        try:
            return await asyncio.to_thread(
                _request_json_sync,
                method,
                url,
                payload,
                headers,
                timeout,
                attempt=attempt + 1,
                total_timeout=total_timeout, stream_idle_timeout=stream_idle_timeout,
                control=control,
            )
        except asyncio.CancelledError:
            control.cancel()
            raise
        except ModelRequestError as exc:
            if not exc.retryable or attempt >= max_retries:
                raise
            status = getattr(exc, "status_code", None)
            delay = _retry_delay(getattr(exc, "retry_after", None), attempt, rate_limited=status == 429)
            if delay is None:
                raise  # 平台要求更长等待时停止，不将其截短后提前重试。
            logger.warning("model.retry_scheduled", extra={"attempt": attempt + 2, "wait_seconds": delay,
                "status_code": status, "policy_version": RETRY_POLICY_VERSION})
            started = time.monotonic()
            completed = False
            try:
                await asyncio.sleep(delay)
                completed = True
            finally:
                elapsed_wait = time.monotonic() - started
                logger.info("model.retry_wait_finished", extra={"duration_ms": round(elapsed_wait * 1000, 2),
                    "attempt": attempt + 1, "stage": "completed" if completed else "interrupted"})
                observer = WIRE_OBSERVER.get()
                record_wait = getattr(observer, "retry_wait", None)
                if callable(record_wait):
                    record_wait(attempt + 1, delay, elapsed_wait,
                                status_code=status, completed=completed)
            attempt += 1


def _retry_delay(retry_after: str | None, attempt: int, *, rate_limited: bool = False) -> float | None:
    """计算从零编号的重试等待秒数，返回 None 表示服务端等待超出本地边界。

    指数窗口上限每次翻倍，在其后半段均匀采样以错开并发请求；429 的
    初始窗口为 60 秒、最大 300 秒，其余临时故障为 1 秒、最大 8 秒。
    Retry-After 支持秒数和 HTTP 日期，仅作为下限，绝不截短后提前重试。
    """
    if attempt < 0:
        raise ValueError("Retry attempt must be nonnegative")
    base, limit = (60.0, MAX_RETRY_WAIT_SECONDS) if rate_limited else (1.0, 8.0)
    minimum = 0.0
    try:
        if retry_after is not None:
            try:
                seconds = float(retry_after)
            except ValueError:
                date = parsedate_to_datetime(retry_after)
                seconds = date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp() - time.time()
            if math.isfinite(seconds):
                minimum = max(seconds, 0.0)
    except (ValueError, TypeError, OverflowError):
        pass
    if minimum > MAX_RETRY_WAIT_SECONDS:
        return None
    # 达到上限后仍保留随机区间；指数先封顶，避免异常重试编号导致浮点溢出。
    ceiling = min(base * 2.0 ** min(attempt, math.ceil(math.log2(limit / base))), limit)
    return max(minimum, random.uniform(ceiling / 2.0, ceiling))


def _request_json_sync(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: float,
    *, attempt: int = 1, total_timeout: float | None = None, stream_idle_timeout: float | None = None,
    control: RequestControl | None = None,
) -> dict[str, Any]:
    """执行同步 JSON 请求并把 HTTP/网络错误转换为安全异常。"""
    streaming = bool(payload and payload.get("stream"))
    if control is not None and control.cancelled.is_set():
        raise RequestCancelled()
    idle_limit = min(timeout, stream_idle_timeout) if streaming and stream_idle_timeout is not None else timeout
    request_headers = {"Accept": "text/event-stream" if streaming else "application/json", **headers}
    data = None
    observer = None
    receipt = 0
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
        # 与 BudgetPlanner/TokenCounter 使用同一紧凑 JSON 表示，避免估算和发送不一致。
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        observer = WIRE_OBSERVER.get()
        if observer is not None:
            receipt = observer.sent(payload, attempt, wire_sha256=hashlib.sha256(data).hexdigest(), wire_bytes=len(data))
    request = Request(url, data=data, headers=request_headers, method=method)
    # 时钟从审计发送记录之后开始，避免把本机审计写入时间混作网络耗时。
    started = time.monotonic()
    body_started: float | None = None
    phase = "awaiting_response_headers"
    reader: ResponseReader | None = None
    stream = ChatStream() if streaming else None
    diagnostics: dict[str, Any] = {
        "version": "http-attempt-timing-v1", "attempt": attempt,
        "started_at": datetime.now(timezone.utc).isoformat(), "timeout_seconds": timeout,
        "timeout_scope": "socket_operation_not_total_deadline",
        "request_bytes": len(data or b""), "headers_ms": None, "body_ms": None,
        "response_bytes": None, "status_code": None, "headers_received": False, "body_received": False,
    }
    if streaming or total_timeout is not None:
        diagnostics.update(version="http-attempt-timing-v2", total_timeout_seconds=total_timeout,
                           stream_idle_timeout_seconds=idle_limit if streaming else None,
                           mode="chat_sse" if streaming else "json")

    def finish(error: Exception | None = None, *, endpoint: ModelEndpointError | None = None) -> dict[str, Any]:
        """封存本次耗时和安全错误字段；不读取异常正文、URL、认证头或请求内容。"""
        if control is not None and control.cancelled.is_set():
            raise RequestCancelled()
        diagnostics.update(completed_at=datetime.now(timezone.utc).isoformat(),
                           elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                           failure_phase=phase if error is not None else None)
        if error is not None:
            cause = error.reason if isinstance(error, URLError) and isinstance(error.reason, BaseException) else error
            if (reader is not None and reader.expired.is_set()) or (total_timeout is not None and diagnostics["elapsed_ms"] >= total_timeout * 1000):
                diagnostics["timeout_kind"] = "attempt_total"
            elif isinstance(cause, TimeoutError):
                diagnostics["timeout_kind"] = "stream_idle" if streaming and diagnostics["headers_received"] else "socket_operation"
            if stream is not None:
                diagnostics.update(done=stream.done, events=stream.events)
                if "usage" in stream.payload:
                    # 只保存已经收到的供应商用量，不保存部分正文或隐藏推理。
                    diagnostics["partial_usage"] = stream.payload["usage"]
            if phase == "reading_response_body" and body_started is not None:
                diagnostics["body_ms"] = round((time.monotonic() - body_started) * 1000, 2)
            diagnostics.update(exception_type=type(error).__name__, cause_type=type(cause).__name__,
                errno=getattr(cause, "errno", None) if isinstance(getattr(cause, "errno", None), int) else None,
                winerror=getattr(cause, "winerror", None) if isinstance(getattr(cause, "winerror", None), int) else None)
            if endpoint is not None:
                diagnostics["provider_error"] = {"type": endpoint.error_type, "code": endpoint.error_code,
                    "request_id": endpoint.request_id, "retryable": endpoint.retryable, "retry_after": endpoint.retry_after}
            # 日志不打印供应商字符串、异常字符串或 traceback，避免地址/凭据随错误泄露。
            logger.warning("model.http_failed", extra={"attempt": attempt, "stage": phase,
                "duration_ms": diagnostics["elapsed_ms"], "error_type": type(error).__name__,
                "status_code": diagnostics["status_code"], "errno": diagnostics["errno"], "winerror": diagnostics["winerror"],
                "timeout_seconds": timeout, "request_bytes": diagnostics["request_bytes"]},
                exc_info=(type(error), error, error.__traceback__) if phase == "decoding_json" else None)
        return diagnostics

    try:
        if control is not None and control.cancelled.is_set():
            raise RequestCancelled()
        with urlopen(request, timeout=min(timeout, total_timeout) if total_timeout is not None else timeout) as response:
            status = getattr(response, "status", None)
            diagnostics.update(headers_ms=round((time.monotonic() - started) * 1000, 2),
                               status_code=status if type(status) is int else None, headers_received=True)
            phase = "reading_response_body"
            body_started = time.monotonic()
            reader = ResponseReader(response, started=started, total_timeout=total_timeout,
                                    idle_timeout=idle_limit, control=control)
            with reader:
                content_type = response.headers.get_content_type() if streaming else "application/json"
                if streaming and content_type == "text/event-stream":
                    assert stream is not None
                    decoded = _read_chat_stream(reader, stream, diagnostics, started)
                    raw = None
                else:
                    if streaming and content_type != "application/json":
                        raise StreamProtocolError("端点未返回 SSE 或 JSON 响应。")
                    if streaming:
                        diagnostics["server_ignored_stream"] = True  # 接受该次 JSON，不补发另一种请求。
                    raw = reader.json_body()
            diagnostics.update(body_ms=round((time.monotonic() - body_started) * 1000, 2),
                               response_bytes=reader.bytes_read, body_received=True)
            if raw is not None:
                phase = "decoding_json"
                decoded = json.loads(raw.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ModelRequestError("Model endpoint returned a non-object JSON response")
            phase = "closing_response"
    except RequestCancelled:
        raise  # 取消由编排器封存，不让遗留读取线程再追加 response/failed 记录。
    except (ValueError, UnicodeDecodeError, ModelRequestError) as exc:
        details = finish(exc)
        if observer is not None:
            observer.failed(receipt, type(exc).__name__, diagnostics=details)
        raise ModelResponseError("Model endpoint returned an invalid or incomplete response") from exc
    except HTTPError as exc:
        if control is not None and control.cancelled.is_set():
            exc.close()
            raise RequestCancelled() from None
        diagnostics.update(headers_ms=round((time.monotonic() - started) * 1000, 2),
                           status_code=exc.code, headers_received=True)
        endpoint = _model_endpoint_error(exc, diagnostics=diagnostics)
        details = finish(exc, endpoint=endpoint)
        if observer is not None:
            observer.failed(receipt, "HTTPError", diagnostics=details)
        raise endpoint from exc
    except URLError as exc:
        details = finish(exc)
        if observer is not None:
            observer.failed(receipt, "URLError", diagnostics=details)
        error_type = ModelDeadlineError if details.get("timeout_kind") == "attempt_total" else (
            ModelStreamInterruptedError if streaming and diagnostics["headers_received"] else ModelConnectionError)
        raise error_type("Unable to connect to model endpoint") from exc
    except (TimeoutError, ConnectionError, OSError) as exc:
        details = finish(exc)
        if observer is not None:
            observer.failed(receipt, type(exc).__name__, diagnostics=details)
        error_type = ModelDeadlineError if details.get("timeout_kind") == "attempt_total" else (
            ModelStreamInterruptedError if streaming and diagnostics["headers_received"] else ModelConnectionError)
        raise error_type("Model connection was interrupted") from exc
    except Exception as exc:
        # 例如 HTTP 协议提前 EOF：补齐审计，但原异常/可重试性保持不变。
        details = finish(exc)
        if observer is not None:
            observer.failed(receipt, type(exc).__name__, diagnostics=details)
        if streaming and diagnostics["headers_received"] and isinstance(exc, HTTPException):
            raise ModelStreamInterruptedError("Model stream was interrupted") from exc
        raise
    completed = finish()
    if observer is not None:
        observer.received(receipt, decoded, diagnostics=completed)
    logger.debug("model.http_completed", extra={"status_code": diagnostics["status_code"], "attempt": attempt,
        "request_bytes": diagnostics["request_bytes"], "response_bytes": diagnostics["response_bytes"],
        "timeout_seconds": timeout, "duration_ms": completed["elapsed_ms"]})
    return decoded


def _read_chat_stream(reader: ResponseReader, stream: ChatStream, diagnostics: dict[str, Any], started: float) -> dict[str, Any]:
    """从共用有界读取器消费 SSE；记录通道首帧计时，正文只在结束后交给 Provider。"""
    diagnostics.update(first_event_ms=None, first_reasoning_ms=None, first_content_ms=None,
                       reasoning_chars=0, content_chars=0, events=0, done=False)
    data: list[str] = []
    event_bytes = 0
    while not stream.done:
        raw = reader.line()
        if not raw:
            raise StreamProtocolError("流式响应在 DONE 前结束，不能作为有效报告。")
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            event_bytes += len(raw)
            if event_bytes > MAX_EVENT_BYTES:
                raise StreamProtocolError("模型 SSE 事件超过传输上限。")
            data.append(line[5:].removeprefix(" "))
        elif not line and data:
            channels = stream.consume("\n".join(data))
            data, event_bytes = [], 0
            elapsed = round((time.monotonic() - started) * 1000, 2)
            for channel, present in (("event", bool(stream.events)), ("content", "content" in channels),
                                     ("reasoning", bool(channels & {"reasoning_content", "thinking"}))):
                if present and diagnostics["first_" + channel + "_ms"] is None:
                    diagnostics["first_" + channel + "_ms"] = elapsed
                    logger.info("model.stream_progress", extra={"stage": "first_" + channel, "duration_ms": elapsed})
    diagnostics.update(done=True, events=stream.events,
                       content_chars=sum(map(len, stream.fragments.get("content", []))),
                       reasoning_chars=sum(len(text) for key in ("reasoning_content", "thinking") for text in stream.fragments.get(key, [])))
    return stream.finish()


def _model_endpoint_error(exc: HTTPError, *, diagnostics: dict[str, Any] | None = None) -> ModelEndpointError:
    """从有限 JSON 正文和允许的响应头提取安全诊断字段。"""
    payload: dict[str, Any] = {}
    started = time.monotonic()
    try:
        raw = exc.read(MAX_ERROR_BODY_BYTES + 1)
        if diagnostics is not None:
            diagnostics.update(error_body_bytes=len(raw), error_body_truncated=len(raw) > MAX_ERROR_BODY_BYTES)
        decoded = json.loads(raw[:MAX_ERROR_BODY_BYTES].decode("utf-8", errors="replace"))
        if isinstance(decoded, dict):
            payload = decoded
        if diagnostics is not None:
            diagnostics["error_body_format"] = "json_object" if isinstance(decoded, dict) else "non_object_json"
    except OSError as body_error:
        if diagnostics is not None:
            diagnostics["error_body_read_error"] = type(body_error).__name__
        payload = {}
    except (UnicodeError, json.JSONDecodeError):
        if diagnostics is not None:
            diagnostics["error_body_format"] = "empty" if not raw else "invalid_json"
        payload = {}
    finally:
        if diagnostics is not None:
            diagnostics["error_body_ms"] = round((time.monotonic() - started) * 1000, 2)
    raw_error = payload.get("error")
    error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
    headers = exc.headers if isinstance(exc.headers, Message) else Message()
    return ModelEndpointError(
        status_code=exc.code,
        error_type=_safe_identifier(error.get("type")),
        error_code=_safe_identifier(error.get("code")),
        retry_after=_safe_header(headers.get("Retry-After")),
        request_id=_safe_header(headers.get("x-request-id")),
    )


def _safe_identifier(value: Any) -> str | None:
    """仅允许短 ASCII 风格错误标识进入日志和前端。"""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 100 or not all(
        char.isascii() and (char.isalnum() or char in "_-") for char in value
    ):
        return None
    return value


def _safe_header(value: Any) -> str | None:
    """限制诊断响应头长度并移除控制字符。"""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 200 or any(ord(char) < 32 for char in value):
        return None
    return value

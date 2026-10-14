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
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .observation import WIRE_OBSERVER

logger = logging.getLogger(__name__)

MAX_ERROR_BODY_BYTES = 64 * 1024
MAX_TRANSIENT_RETRIES = 2
MAX_RATE_LIMIT_WAIT_SECONDS = 60.0
RETRY_POLICY_VERSION = "bounded-rate-limit-v1"
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
) -> dict[str, Any]:
    """异步发送 JSON POST，并对临时限流或上游故障执行有限退避重试。"""
    return await _request_with_retry(
        "POST",
        url,
        payload,
        headers,
        timeout,
        max_retries=min(MAX_TRANSIENT_RETRIES, max(0, transient_retries)),
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
) -> dict[str, Any]:
    """执行请求并遵循 Retry-After 或带抖动的指数退避。"""
    if max_retries < 0:
        raise ValueError("Retry count must be nonnegative")
    attempt = 0
    while True:
        try:
            return await asyncio.to_thread(
                _request_json_sync,
                method,
                url,
                payload,
                headers,
                timeout,
                attempt=attempt + 1,
            )
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
    """解析秒数/HTTP 日期；429 默认 30/60 秒，超出本地等待边界时返回 None、禁止提前重试。"""
    try:
        if retry_after is not None:
            try:
                seconds = float(retry_after)
            except ValueError:
                date = parsedate_to_datetime(retry_after)
                seconds = date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp() - time.time()
            if math.isfinite(seconds):
                seconds = max(seconds, 0.0)
                if rate_limited:
                    return seconds if seconds <= MAX_RATE_LIMIT_WAIT_SECONDS else None
                return min(seconds, 30.0)
    except (ValueError, TypeError, OverflowError):
        pass
    if rate_limited:
        return min(30.0 * 2.0 ** attempt + random.uniform(0.0, 0.25), MAX_RATE_LIMIT_WAIT_SECONDS)
    return min(2.0 ** attempt + random.uniform(0.0, 0.25), 8.0)


def _request_json_sync(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: float,
    *, attempt: int = 1,
) -> dict[str, Any]:
    """执行同步 JSON 请求并把 HTTP/网络错误转换为安全异常。"""
    request_headers = {"Accept": "application/json", **headers}
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
    diagnostics: dict[str, Any] = {
        "version": "http-attempt-timing-v1", "attempt": attempt,
        "started_at": datetime.now(timezone.utc).isoformat(), "timeout_seconds": timeout,
        "timeout_scope": "socket_operation_not_total_deadline",
        "request_bytes": len(data or b""), "headers_ms": None, "body_ms": None,
        "response_bytes": None, "status_code": None, "headers_received": False, "body_received": False,
    }

    def finish(error: Exception | None = None, *, endpoint: ModelEndpointError | None = None) -> dict[str, Any]:
        """封存本次耗时和安全错误字段；不读取异常正文、URL、认证头或请求内容。"""
        diagnostics.update(completed_at=datetime.now(timezone.utc).isoformat(),
                           elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                           failure_phase=phase if error is not None else None)
        if error is not None:
            if phase == "reading_response_body" and body_started is not None:
                diagnostics["body_ms"] = round((time.monotonic() - body_started) * 1000, 2)
            cause = error.reason if isinstance(error, URLError) and isinstance(error.reason, BaseException) else error
            diagnostics.update(exception_type=type(error).__name__, cause_type=type(cause).__name__,
                errno=getattr(cause, "errno", None) if isinstance(getattr(cause, "errno", None), int) else None,
                winerror=getattr(cause, "winerror", None) if isinstance(getattr(cause, "winerror", None), int) else None)
            if endpoint is not None:
                diagnostics["provider_error"] = {"type": endpoint.error_type, "code": endpoint.error_code,
                    "request_id": endpoint.request_id, "retryable": endpoint.retryable}
            # 日志不打印供应商字符串、异常字符串或 traceback，避免地址/凭据随错误泄露。
            logger.warning("model.http_failed", extra={"attempt": attempt, "stage": phase,
                "duration_ms": diagnostics["elapsed_ms"], "error_type": type(error).__name__,
                "status_code": diagnostics["status_code"], "errno": diagnostics["errno"], "winerror": diagnostics["winerror"],
                "timeout_seconds": timeout, "request_bytes": diagnostics["request_bytes"]},
                exc_info=(type(error), error, error.__traceback__) if phase == "decoding_json" else None)
        return diagnostics

    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            diagnostics.update(headers_ms=round((time.monotonic() - started) * 1000, 2),
                               status_code=status if type(status) is int else None, headers_received=True)
            phase = "reading_response_body"
            body_started = time.monotonic()
            raw = response.read()
            diagnostics.update(body_ms=round((time.monotonic() - body_started) * 1000, 2),
                               response_bytes=len(raw), body_received=True)
            phase = "decoding_json"
            decoded = json.loads(raw.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ModelRequestError("Model endpoint returned a non-object JSON response")
            phase = "closing_response"
    except (json.JSONDecodeError, UnicodeDecodeError, ModelRequestError) as exc:
        details = finish(exc)
        if observer is not None:
            observer.failed(receipt, type(exc).__name__, diagnostics=details)
        raise ModelRequestError("Model endpoint returned an invalid JSON response") from exc
    except HTTPError as exc:
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
        raise ModelConnectionError("Unable to connect to model endpoint") from exc
    except (TimeoutError, ConnectionError, OSError) as exc:
        details = finish(exc)
        if observer is not None:
            observer.failed(receipt, type(exc).__name__, diagnostics=details)
        raise ModelConnectionError("Model connection was interrupted") from exc
    except Exception as exc:
        # 例如 HTTP 协议提前 EOF：补齐审计，但原异常/可重试性保持不变。
        details = finish(exc)
        if observer is not None:
            observer.failed(receipt, type(exc).__name__, diagnostics=details)
        raise
    completed = finish()
    if observer is not None:
        observer.received(receipt, decoded, diagnostics=completed)
    logger.debug("model.http_completed", extra={"status_code": diagnostics["status_code"], "attempt": attempt,
        "request_bytes": diagnostics["request_bytes"], "response_bytes": diagnostics["response_bytes"],
        "timeout_seconds": timeout, "duration_ms": completed["elapsed_ms"]})
    return decoded


def _post_json_sync(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """保留既有内部入口，委托统一请求实现。"""
    return _request_json_sync("POST", url, payload, headers, timeout)


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

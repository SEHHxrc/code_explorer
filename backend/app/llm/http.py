# -*- coding: utf-8 -*-
"""模型 HTTP 请求、有限速率重试与安全错误解析。"""

from __future__ import annotations

import asyncio
from email.message import Message
import json
import random
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MAX_ERROR_BODY_BYTES = 64 * 1024
MAX_TRANSIENT_RETRIES = 2
_TRANSIENT_UPSTREAM_STATUSES = {502, 503, 504}
_NON_RETRYABLE_LIMIT_CODES = {
    "credit_balance_exhausted",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded",
}
_PUBLIC_ERROR_MESSAGES = {
    "credit_balance_exhausted": "模型 API 余额不足，请在供应商控制台补充余额后重试。",
    "organization_spend_limit_exceeded": "模型 API 组织消费上限已达到，请调整组织消费限制。",
    "project_spend_limit_exceeded": "模型 API 项目消费上限已达到，请调整项目消费限制。",
    "organization_usage_limit_exceeded": "模型 API 组织使用额度已达到，请提高额度后重试。",
    "slow_down": "模型 API 请求增长过快，请稍后重试。",
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
        if self.status_code in _TRANSIENT_UPSTREAM_STATUSES:
            return True
        if (
            self.status_code != 429
            or self.error_code in _NON_RETRYABLE_LIMIT_CODES
            or self.error_type == "insufficient_quota"
        ):
            return False
        return self.error_type == "rate_limit_error" or self.error_code in {None, "slow_down"}

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
        max_retries=max(0, transient_retries),
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
        max_retries=max(0, transient_retries),
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
    for attempt in range(max_retries + 1):
        try:
            return await asyncio.to_thread(
                _request_json_sync,
                method,
                url,
                payload,
                headers,
                timeout,
            )
        except ModelRequestError as exc:
            if not exc.retryable or attempt >= max_retries:
                raise
            await asyncio.sleep(_retry_delay(getattr(exc, "retry_after", None), attempt))
    raise RuntimeError("Unreachable model request retry state")


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    """把秒数型 Retry-After 转为有上限的等待时间。"""
    try:
        if retry_after is not None:
            return min(max(float(retry_after), 0.0), 30.0)
    except ValueError:
        pass
    return min(2.0 ** attempt + random.uniform(0.0, 0.25), 8.0)


def _request_json_sync(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """执行同步 JSON 请求并把 HTTP/网络错误转换为安全异常。"""
    request_headers = {"Accept": "application/json", **headers}
    data = None
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ModelRequestError("Model endpoint returned a non-object JSON response")
            return decoded
    except HTTPError as exc:
        raise _model_endpoint_error(exc) from exc
    except URLError as exc:
        raise ModelConnectionError("Unable to connect to model endpoint") from exc
    except (TimeoutError, ConnectionError, OSError) as exc:
        raise ModelConnectionError("Model connection was interrupted") from exc


def _post_json_sync(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """保留既有内部入口，委托统一请求实现。"""
    return _request_json_sync("POST", url, payload, headers, timeout)


def _model_endpoint_error(exc: HTTPError) -> ModelEndpointError:
    """从有限 JSON 正文和允许的响应头提取安全诊断字段。"""
    payload: dict[str, Any] = {}
    try:
        raw = exc.read(MAX_ERROR_BODY_BYTES + 1)[:MAX_ERROR_BODY_BYTES]
        decoded = json.loads(raw.decode("utf-8", errors="replace"))
        if isinstance(decoded, dict):
            payload = decoded
    except (OSError, UnicodeError, json.JSONDecodeError):
        payload = {}
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
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

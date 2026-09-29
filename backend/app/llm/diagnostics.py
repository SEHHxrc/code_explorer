"""模型配置的显式连通性探测与可见模型目录。"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from backend.app.llm.http import (
    ModelEndpointError,
    ModelRequestError,
    get_json,
    post_json,
)
from backend.app.llm.registry import (
    ModelConfiguration,
    get_model_api_key,
    get_model_configuration,
)

PROBE_TIMEOUT_SECONDS = 30.0
MAX_VISIBLE_MODELS = 2_000


def _headers(config: ModelConfiguration) -> dict[str, str]:
    """为已配置服务构造认证头；本地兼容服务允许空密钥。"""
    api_key = get_model_api_key()
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _endpoint(config: ModelConfiguration, path: str) -> str:
    """在规范化基础 URL 下拼接固定 API 路径。"""
    return urljoin(config.base_url.rstrip("/") + "/", path.lstrip("/"))


def _base_result(config: ModelConfiguration, model: str | None = None) -> dict[str, Any]:
    """生成不包含 API Key 和完整上游响应的公共诊断基线。"""
    return {
        "configured": config.configured,
        "provider": config.provider if config.configured else None,
        "model": (model or config.model) if config.configured else None,
    }


def _error_result(
    config: ModelConfiguration,
    exc: ModelRequestError,
    model: str | None = None,
) -> dict[str, Any]:
    """把模型请求异常转换成前端可安全展示的结构化结果。"""
    result = {
        **_base_result(config, model),
        "connected": isinstance(exc, ModelEndpointError),
        "model_accessible": None,
        "generation_available": False,
        "agent_compatible": False,
        "error_type": None,
        "error_code": None,
        "status_code": None,
        "retryable": exc.retryable,
        "retry_after": None,
        "request_id": None,
        "message": exc.public_message,
    }
    if isinstance(exc, ModelEndpointError):
        result.update({
            "model_accessible": False if exc.status_code in {403, 404} or exc.error_code == "model_not_found" else None,
            "error_type": exc.error_type,
            "error_code": exc.error_code or f"http_{exc.status_code}",
            "status_code": exc.status_code,
            "retry_after": exc.retry_after,
            "request_id": exc.request_id,
        })
    return result


async def probe_model_connection(model: str | None = None) -> dict[str, Any]:
    """用最小工具请求验证模型生成和 Agent 函数调用协议。"""
    config = get_model_configuration()
    if not config.configured:
        return {
            **_base_result(config, model),
            "connected": False,
            "model_accessible": None,
            "generation_available": False,
            "agent_compatible": False,
            "error_type": "configuration_error",
            "error_code": "model_not_configured",
            "status_code": None,
            "retryable": False,
            "retry_after": None,
            "request_id": None,
            "message": "模型配置不完整，请检查 Provider、模型、API Key 和 Base URL。",
        }

    selected_model = model or config.model
    probe_name = "agent_compatibility_probe"
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    if config.provider == "openai":
        payload = {
            "model": selected_model,
            "instructions": "This is an Agent protocol probe. Call agent_compatibility_probe now.",
            "input": "Call agent_compatibility_probe with an empty object.",
            "tools": [{
                "type": "function",
                "name": probe_name,
                "description": "Confirm that function-tool calling is supported.",
                "parameters": parameters,
                "strict": True,
            }],
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "store": False,
            "max_output_tokens": 64,
        }
        endpoint = _endpoint(config, "responses")
    else:
        payload = {
            "model": selected_model,
            "messages": [
                {
                    "role": "system",
                    "content": "This is an Agent protocol probe. Call agent_compatibility_probe now.",
                },
                {"role": "user", "content": "Call agent_compatibility_probe with an empty object."},
            ],
            "tools": [{
                "type": "function",
                "function": {
                    "name": probe_name,
                    "description": "Confirm that function-tool calling is supported.",
                    "parameters": parameters,
                },
            }],
            "tool_choice": "required",
            "stream": False,
            "temperature": 0,
            "max_tokens": 64,
        }
        endpoint = _endpoint(config, "chat/completions")

    try:
        response = await post_json(
            endpoint,
            payload,
            _headers(config),
            PROBE_TIMEOUT_SECONDS,
            transient_retries=0,
        )
    except ModelRequestError as exc:
        return _error_result(config, exc, selected_model)
    if config.provider == "openai":
        tool_called = any(
            item.get("type") == "function_call" and item.get("name") == probe_name
            for item in response.get("output", [])
            if isinstance(item, dict)
        )
    else:
        choices = response.get("choices") or []
        message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
        tool_called = any(
            isinstance(call, dict)
            and (call.get("function") or {}).get("name") == probe_name
            for call in message.get("tool_calls") or []
        )
    return {
        **_base_result(config, selected_model),
        "connected": True,
        "model_accessible": True,
        "generation_available": True,
        "agent_compatible": tool_called,
        "error_type": None,
        "error_code": None,
        "status_code": None,
        "retryable": False,
        "retry_after": None,
        "request_id": None,
        "message": (
            "模型端点、权限、生成能力和 Agent 工具调用协议均验证成功。"
            if tool_called
            else "模型能够生成内容，但没有按要求返回函数工具调用；该模型可能无法完成 Agent 对话。"
        ),
    }


async def list_available_models() -> dict[str, Any]:
    """调用兼容 ``GET /models`` 接口并返回当前凭据可见的模型 ID。"""
    config = get_model_configuration()
    if not config.configured:
        return {
            **_base_result(config),
            "connected": False,
            "models": [],
            "truncated": False,
            "error_type": "configuration_error",
            "error_code": "model_not_configured",
            "message": "模型配置不完整，无法查询可见模型。",
        }
    try:
        payload = await get_json(
            _endpoint(config, "models"),
            _headers(config),
            PROBE_TIMEOUT_SECONDS,
            transient_retries=0,
        )
    except ModelRequestError as exc:
        failure = _error_result(config, exc)
        return {
            **failure,
            "models": [],
            "truncated": False,
        }
    identifiers = sorted({
        str(item["id"]).strip()
        for item in payload.get("data", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
    })
    truncated = len(identifiers) > MAX_VISIBLE_MODELS
    return {
        **_base_result(config),
        "connected": True,
        "models": identifiers[:MAX_VISIBLE_MODELS],
        "truncated": truncated,
        "error_type": None,
        "error_code": None,
        "message": f"已读取 {min(len(identifiers), MAX_VISIBLE_MODELS)} 个当前凭据可见的模型。",
    }

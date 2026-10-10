"""模型配置的显式连通性探测与可见模型目录。"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin
import uuid

from backend.app.llm.budget import BudgetPlanner, ModelBudgetError, request_payload
from backend.app.llm.conversation import ModelMessage

from backend.app.llm.http import (
    ModelEndpointError,
    ModelRequestError,
    get_json,
)
from backend.app.llm.registry import (
    ModelConfiguration,
    get_model_api_key,
    get_model_configuration,
    create_model_provider,
    get_model_limits,
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
    """最多两次生成验证调用→结果→回答；无项目源码、无重试，不探测最大窗口。"""
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
    try:
        provider = create_model_provider(selected_model)
    except (ValueError, ImportError):
        return {**_base_result(config, selected_model), "connected": False, "model_accessible": None,
                "generation_available": False, "agent_compatible": False, "requests_sent": 0,
                "error_type": "configuration_error", "error_code": "invalid_model_options",
                "retryable": False, "message": "模型输出参数或分词配置无效；未发送探测请求。"}
    assert provider is not None
    limits = get_model_limits(selected_model)
    instructions = (
        "Protocol test. First call agent_compatibility_probe with {}. "
        "After its result arrives, answer with exactly its probe_token and nothing else."
    )
    tools = [{"type": "function", "name": "agent_compatibility_probe",
              "description": "Return a verification token.", "strict": True,
              "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}]
    messages = [ModelMessage(role="user", content="Call the verification tool now.")]
    result: dict[str, Any] = {
        **_base_result(config, selected_model), "connected": False, "model_accessible": None,
        "generation_available": False, "agent_compatible": False, "tool_calling_verified": False,
        "tool_roundtrip_verified": False, "probe_stage": "tool_call", "requests_sent": 0,
        "usage": [], "error_type": None, "error_code": None, "status_code": None,
        "retryable": False, "retry_after": None, "request_id": None, "context_capacity_verified": False,
    }
    try:
        planner = BudgetPlanner(limits, model=selected_model)
        output_limit = min(limits.max_output_tokens, 1024)
        first_payload = request_payload(provider, instructions=instructions, messages=messages, tools=tools,
                                        max_output_tokens=output_limit, tool_choice="required")
        planner.require(first_payload)
        result["requests_sent"] += 1
        # 通过适配器发送，与正式 Agent 使用完全相同的序列化与响应解析。
        first = await provider.generate_with_tools(
            instructions=instructions, messages=messages, tools=tools, tool_choice="required",
            max_output_tokens=output_limit, transient_retries=0, timeout=PROBE_TIMEOUT_SECONDS,
        )
        from dataclasses import asdict
        result.update(connected=True, model_accessible=True, generation_available=True,
                      actual_model=first.model)
        result["usage"].append(asdict(first.metadata))
        calls = first.tool_calls
        if len(calls) != 1 or calls[0].name != "agent_compatibility_probe" or calls[0].arguments:
            result["message"] = "端点能够生成响应，但未返回预期工具调用；尚未验证 Agent 完整流程。"
            return result
        result.update(tool_calling_verified=True, probe_stage="tool_result")
        if first.continuation is None:
            raise ValueError("Missing native continuation state")
        token = uuid.uuid4().hex[:12]
        messages.extend([first.continuation, ModelMessage(role="tool", tool_call_id=calls[0].id,
                                                         content='{"probe_token":"' + token + '"}')])
        second_payload = request_payload(provider, instructions=instructions, messages=messages, tools=[],
                                         max_output_tokens=output_limit)
        planner.require(second_payload)
        result["requests_sent"] += 1
        final = await provider.generate_with_tools(
            instructions=instructions, messages=messages, tools=[], max_output_tokens=output_limit,
            transient_retries=0, timeout=PROBE_TIMEOUT_SECONDS,
        )
        result["usage"].append(asdict(final.metadata))
        returned = final.text.strip() == token and not final.tool_calls
        complete = final.metadata.finish_reason == "stop" or final.metadata.response_status == "completed"
        compatible = returned and complete and not final.metadata.refused and not final.metadata.incomplete_reason
        result.update(tool_roundtrip_verified=compatible, agent_compatible=compatible, probe_stage="completed",
                      actual_model=final.model,
                      message="已验证工具调用、结果回传和完整回答；未验证平台最大上下文容量。" if compatible
                      else "工具调用成功，但结果回传后的回答未通过完整性或内容验证；请检查输出/思考预算及平台协议。")
        return result
    except ModelRequestError as exc:
        failure = _error_result(config, exc, selected_model)
        if result["generation_available"]:
            failure.update(connected=True, model_accessible=True, generation_available=True)
        return {**result, **failure}
    except (ModelBudgetError, ValueError):
        result.update(error_type="budget_or_protocol_error", error_code="probe_not_verified",
                      message="当前输入预算或工具消息格式无法完成探测；未验证 Agent 完整流程。")
        return result


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
        "model_metadata": {item["id"]: {
            key: item[key] for key in ("context_window", "context_length", "max_input_tokens", "max_output_tokens")
            if isinstance(item.get(key), int) and not isinstance(item.get(key), bool) and item[key] > 0
        } for item in payload.get("data", []) if isinstance(item, dict) and item.get("id") in identifiers[:MAX_VISIBLE_MODELS]},
        "truncated": truncated,
        "error_type": None,
        "error_code": None,
        "message": f"已读取 {min(len(identifiers), MAX_VISIBLE_MODELS)} 个当前凭据可见的模型。",
    }

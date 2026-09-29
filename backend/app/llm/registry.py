from __future__ import annotations

import os
from dataclasses import dataclass

from backend.app.llm.base import ModelProvider
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.providers.openai_provider import OpenAIResponsesProvider


@dataclass(frozen=True)
class ModelConfiguration:
    """环境变量解析后的模型配置；输出供应商、模型、基础 URL 和可用状态。"""
    provider: str
    model: str
    base_url: str
    configured: bool


@dataclass(frozen=True)
class ModelLimits:
    """应用侧模型预算；字符预算限制输入，Token 预算限制单次输出。"""

    max_context_chars: int
    max_output_tokens: int


def _environment_value(name: str, default: str = "") -> str:
    """读取并规范化环境变量，兼容手工设置时残留的成对引号。"""
    value = os.getenv(name, default).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def get_model_api_key() -> str:
    """返回规范化后的模型 API Key，仅供服务端请求构造使用。"""
    return _environment_value("CODE_EXPLORER_LLM_API_KEY")


def _bounded_environment_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """读取有界整数环境变量；非法值回退默认值，超界值收敛到安全范围。"""
    try:
        value = int(_environment_value(name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def get_model_limits() -> ModelLimits:
    """返回供应商无关的输入字符预算和单次输出 Token 上限。"""
    return ModelLimits(
        max_context_chars=_bounded_environment_int(
            "CODE_EXPLORER_LLM_MAX_CONTEXT_CHARS", 18_000, 2_000, 200_000,
        ),
        max_output_tokens=_bounded_environment_int(
            "CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS", 2_400, 128, 32_768,
        ),
    )


def get_model_configuration() -> ModelConfiguration:
    """读取 ``CODE_EXPLORER_LLM_*`` 环境变量并输出统一模型配置。"""
    provider = _environment_value("CODE_EXPLORER_LLM_PROVIDER", "disabled").lower()
    model = _environment_value("CODE_EXPLORER_LLM_MODEL")
    api_key = get_model_api_key()
    defaults = {
        "openai": "https://api.openai.com/v1",
        "ollama": "http://localhost:11434/v1",
        "vllm": "http://localhost:8001/v1",
        "compatible": "",
    }
    configured_base_url = _environment_value("CODE_EXPLORER_LLM_BASE_URL")
    base_url = configured_base_url or defaults.get(provider, "")
    configured = bool(
        model and base_url and provider in {"openai", "ollama", "vllm", "compatible"}
        and (provider != "openai" or api_key)
    )
    return ModelConfiguration(provider=provider, model=model, base_url=base_url, configured=configured)


def create_model_provider(model: str | None = None) -> ModelProvider | None:
    """根据当前配置创建适配器；可用请求级模型覆盖环境变量默认值。"""
    config = get_model_configuration()
    if not config.configured:
        return None
    selected_model = model or config.model
    limits = get_model_limits()
    api_key = get_model_api_key()
    if config.provider == "openai":
        return OpenAIResponsesProvider(
            api_key=api_key,
            model=selected_model,
            base_url=config.base_url,
            max_output_tokens=limits.max_output_tokens,
        )
    return OpenAICompatibleProvider(
        provider_name=config.provider,
        base_url=config.base_url,
        model=selected_model,
        api_key=api_key,
        max_output_tokens=limits.max_output_tokens,
    )

from __future__ import annotations

from backend.app.llm.base import ModelProvider
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.providers.openai_provider import OpenAIResponsesProvider


from backend.app.llm.settings import (
    ModelConfiguration, ModelLimits, environment_value, get_model_api_key,
    get_model_configuration, get_model_limits,
)

__all__ = ["ModelConfiguration", "ModelLimits", "get_model_api_key", "get_model_configuration",
           "get_model_limits", "create_model_provider"]


def create_model_provider(model: str | None = None) -> ModelProvider | None:
    """根据当前配置创建适配器；可用请求级模型覆盖环境变量默认值。"""
    config = get_model_configuration()
    if not config.configured:
        return None
    selected_model = model or config.model
    limits = get_model_limits(selected_model)
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
        output_token_parameter=environment_value("CODE_EXPLORER_LLM_OUTPUT_TOKEN_PARAMETER", "max_tokens"),
        reasoning_effort=environment_value("CODE_EXPLORER_LLM_REASONING_EFFORT") or None,
    )

# -*- coding: utf-8 -*-
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


def get_model_configuration() -> ModelConfiguration:
    """读取 ``CODE_EXPLORER_LLM_*`` 环境变量并输出统一模型配置。"""
    provider = os.getenv("CODE_EXPLORER_LLM_PROVIDER", "disabled").strip().lower()
    model = os.getenv("CODE_EXPLORER_LLM_MODEL", "").strip()
    api_key = os.getenv("CODE_EXPLORER_LLM_API_KEY", "").strip()
    defaults = {
        "openai": "https://api.openai.com/v1",
        "ollama": "http://localhost:11434/v1",
        "vllm": "http://localhost:8001/v1",
        "compatible": "",
    }
    configured_base_url = os.getenv("CODE_EXPLORER_LLM_BASE_URL", "").strip()
    base_url = configured_base_url or defaults.get(provider, "")
    configured = bool(
        model and base_url and provider in {"openai", "ollama", "vllm", "compatible"}
        and (provider != "openai" or api_key)
    )
    return ModelConfiguration(provider=provider, model=model, base_url=base_url, configured=configured)


def create_model_provider() -> ModelProvider | None:
    """根据当前配置创建模型适配器；未配置时输出 ``None``。"""
    config = get_model_configuration()
    if not config.configured:
        return None
    api_key = os.getenv("CODE_EXPLORER_LLM_API_KEY", "").strip()
    if config.provider == "openai":
        return OpenAIResponsesProvider(api_key=api_key, model=config.model, base_url=config.base_url)
    return OpenAICompatibleProvider(
        provider_name=config.provider,
        base_url=config.base_url,
        model=config.model,
        api_key=api_key,
    )

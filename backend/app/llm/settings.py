"""纯环境配置与预算契约；不依赖 Provider、不发网络请求，避免配置/适配器循环。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfiguration:
    """公开的连接状态；不包含密钥，输入来自服务环境变量。"""

    provider: str
    model: str
    base_url: str
    configured: bool


@dataclass(frozen=True)
class ModelLimits:
    """应用预算与可选运维容量声明；估算预算不是平台精确窗口。"""

    max_context_chars: int
    max_output_tokens: int
    max_input_tokens: int = 18_000
    context_window_tokens: int | None = None
    tokenizer: str = "auto"
    tokenizer_path: str = ""  # 仅为识别旧配置保留；非空值会被计数器明确拒绝。
    safety_margin_tokens: int = 512


def environment_value(name: str, default: str = "") -> str:
    """读取并移除手工设置值外侧成对引号；不记录内容。"""
    value = os.getenv(name, default).strip()
    return value[1:-1].strip() if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'} else value


def get_model_api_key() -> str:
    """读取仅供请求头使用的服务端密钥，不用于状态和报告。"""
    return environment_value("CODE_EXPLORER_LLM_API_KEY")


def bounded_environment_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """解析并限制整数；非法值使用默认值，不允许配置突破应用安全边界。"""
    try:
        value = int(environment_value(name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def get_model_limits(model: str | None = None) -> ModelLimits:
    """读取输入/输出保护；切换模型时清除默认模型专属窗口与旧词表配置。"""
    switched = bool(model and model != environment_value("CODE_EXPLORER_LLM_MODEL"))
    window = 0 if switched else bounded_environment_int("CODE_EXPLORER_LLM_CONTEXT_WINDOW_TOKENS", 0, 0, 10_000_000)
    return ModelLimits(
        max_context_chars=bounded_environment_int("CODE_EXPLORER_LLM_MAX_CONTEXT_CHARS", 0, 0, 200_000),
        max_output_tokens=bounded_environment_int("CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS", 2400, 128, 32_768),
        max_input_tokens=bounded_environment_int("CODE_EXPLORER_LLM_MAX_INPUT_TOKENS", 18_000, 1000, 1_000_000),
        context_window_tokens=window or None,
        tokenizer="auto" if switched else environment_value("CODE_EXPLORER_LLM_TOKENIZER", "auto"),
        tokenizer_path="" if switched else environment_value("CODE_EXPLORER_LLM_TOKENIZER_PATH"),
        safety_margin_tokens=bounded_environment_int("CODE_EXPLORER_LLM_TOKEN_MARGIN", 512, 128, 16_384),
    )


def get_model_configuration() -> ModelConfiguration:
    """解析连接参数并返回可公开状态；不检查权限或真实可用性。"""
    provider = environment_value("CODE_EXPLORER_LLM_PROVIDER", "disabled").lower()
    model = environment_value("CODE_EXPLORER_LLM_MODEL")
    defaults = {"openai": "https://api.openai.com/v1", "ollama": "http://localhost:11434/v1",
                "vllm": "http://localhost:8001/v1", "compatible": ""}
    base_url = environment_value("CODE_EXPLORER_LLM_BASE_URL") or defaults.get(provider, "")
    configured = bool(model and base_url and provider in defaults and (provider != "openai" or get_model_api_key()))
    return ModelConfiguration(provider=provider, model=model, base_url=base_url, configured=configured)

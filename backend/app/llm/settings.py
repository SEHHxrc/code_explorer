"""纯环境配置与预算契约；不依赖 Provider、不发网络请求，避免配置/适配器循环。"""

from __future__ import annotations

import os
import math
import hashlib
from dataclasses import dataclass
from typing import Any


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
    max_output_tokens: int | None  # None 表示不发送输出上限，而不是平台无限容量。
    max_input_tokens: int = 18_000
    context_window_tokens: int | None = None
    tokenizer: str = "auto"
    tokenizer_path: str = ""  # 仅为识别旧配置保留；非空值会被计数器明确拒绝。
    safety_margin_tokens: int = 512
    output_reserve_tokens: int = 32_768  # 仅在不指定输出上限时用于本地窗口预留。

    def output_budget(self) -> dict[str, Any]:
        """输出状态/审计共用快照；平台默认不是无限容量，预留值不进入模型请求。"""
        explicit = self.max_output_tokens is not None
        return {
            "max_output_tokens": self.max_output_tokens,
            "output_limit_mode": "explicit" if explicit else "provider_default",
            "output_reserve_tokens": self.max_output_tokens if explicit else self.output_reserve_tokens,
            "output_reserve_basis": "request_limit" if explicit else "planning_only_unknown_output",
        }


def environment_value(name: str, default: str = "") -> str:
    """读取并移除手工设置值外侧成对引号；不记录内容。"""
    value = os.getenv(name, default).strip()
    return value[1:-1].strip() if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'} else value


def get_model_api_key() -> str:
    """读取仅供请求头使用的服务端密钥，不用于状态和报告。"""
    return environment_value("CODE_EXPLORER_LLM_API_KEY")


def get_model_request_timeout() -> float:
    """读取连接/响应头及非流式 socket 超时；默认 600 秒，不是 Agent 总时限。"""
    return _timeout_setting("CODE_EXPLORER_LLM_REQUEST_TIMEOUT_SECONDS", 600, 3600)


def _timeout_setting(name: str, default: float, maximum: float) -> float:
    """严格读取有限秒数；错误只含变量名和范围，不回显配置内容。"""
    try:
        value = float(environment_value(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} 必须为 1～{maximum:g} 秒。") from None
    if not math.isfinite(value) or not 1 <= value <= maximum:
        raise ValueError(f"{name} 必须为 1～{maximum:g} 秒。")
    return value


@dataclass(frozen=True)
class ModelTransportSettings:
    """一次请求尝试的本地传输策略；不限制 Token，不改变提示词或安全报告契约。"""

    chat_stream: bool
    include_stream_usage: bool
    request_timeout_seconds: float
    stream_idle_timeout_seconds: float
    total_timeout_seconds: float

    def snapshot(self, provider: str) -> dict[str, Any]:
        """返回不含认证/地址的共同实验配置；Responses 暂不启用 SSE。"""
        streaming = self.chat_stream and provider in {"compatible", "ollama", "vllm"}
        return {"version": "bounded-http-sse-v2", "mode": "chat_sse" if streaming else "json",
                "request_timeout_seconds": self.request_timeout_seconds,
                "stream_idle_timeout_seconds": self.stream_idle_timeout_seconds if streaming else None,
                "attempt_total_timeout_seconds": self.total_timeout_seconds,
                "include_stream_usage": self.include_stream_usage if streaming else None,
                "partial_stream_retry": False}


def get_model_transport_settings() -> ModelTransportSettings:
    """读取并冻结传输开关和有限超时；开关不猜测供应商能力，失败不切换协议。"""
    def boolean(name: str, default: str) -> bool:
        """读取明确开关，拒绝含糊配置。"""
        value = environment_value(name, default).lower()
        if value not in {"0", "1", "false", "true"}:
            raise ValueError(f"{name} 必须为 0/1 或 false/true。")
        return value in {"1", "true"}
    return ModelTransportSettings(
        chat_stream=boolean("CODE_EXPLORER_LLM_CHAT_STREAM", "0"),
        include_stream_usage=boolean("CODE_EXPLORER_LLM_STREAM_INCLUDE_USAGE", "1"),
        request_timeout_seconds=get_model_request_timeout(),
        stream_idle_timeout_seconds=_timeout_setting("CODE_EXPLORER_LLM_STREAM_IDLE_TIMEOUT_SECONDS", 120, 3600),
        total_timeout_seconds=_timeout_setting("CODE_EXPLORER_LLM_REQUEST_TOTAL_TIMEOUT_SECONDS", 1800, 7200),
    )


def get_final_report_mode() -> str:
    """读取显式收尾协议；不按模型别名猜测，不在失败后自动切换。"""
    mode = environment_value("CODE_EXPLORER_LLM_FINAL_REPORT_MODE", "tools_none")
    if mode not in {"tools_none", "history_no_tools"}:
        raise ValueError("最终报告模式必须为 tools_none 或 history_no_tools。")
    return mode


def bounded_environment_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """解析并限制整数；非法值使用默认值，不允许配置突破应用安全边界。"""
    try:
        value = int(environment_value(name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _strict_output_integer(name: str, default: int, maximum: int) -> int:
    """读取输出预算正整数；非法配置明确失败，不打印值、不静默回退或截顶。"""
    try:
        value = int(environment_value(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} 必须为 128～{maximum} 范围内的整数。") from None
    if not 128 <= value <= maximum:
        raise ValueError(f"{name} 必须为 128～{maximum} 范围内的整数。")
    return value


def get_model_limits(model: str | None = None) -> ModelLimits:
    """读取输入/输出保护；切换模型时清除默认模型专属窗口与旧词表配置。"""
    switched = bool(model and model != environment_value("CODE_EXPLORER_LLM_MODEL"))
    window = 0 if switched else bounded_environment_int("CODE_EXPLORER_LLM_CONTEXT_WINDOW_TOKENS", 0, 0, 10_000_000)
    return ModelLimits(
        max_context_chars=bounded_environment_int("CODE_EXPLORER_LLM_MAX_CONTEXT_CHARS", 0, 0, 200_000),
        max_output_tokens=(_strict_output_integer("CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS", 2400, 32_768)
                           if environment_value("CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS", "2400") else None),
        max_input_tokens=bounded_environment_int("CODE_EXPLORER_LLM_MAX_INPUT_TOKENS", 18_000, 1000, 1_000_000),
        context_window_tokens=window or None,
        tokenizer="auto" if switched else environment_value("CODE_EXPLORER_LLM_TOKENIZER", "auto"),
        tokenizer_path="" if switched else environment_value("CODE_EXPLORER_LLM_TOKENIZER_PATH"),
        safety_margin_tokens=bounded_environment_int("CODE_EXPLORER_LLM_TOKEN_MARGIN", 512, 128, 16_384),
        output_reserve_tokens=_strict_output_integer("CODE_EXPLORER_LLM_OUTPUT_RESERVE_TOKENS", 32_768, 1_000_000),
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


def endpoint_accounting_fingerprint(model: str | None = None) -> str:
    """绑定供应商、端点及请求模型，不含 API Key；更换任一项须重新确认平台执行语义。"""
    config = get_model_configuration()
    identity = "\n".join((config.provider, config.base_url.rstrip("/"), model or config.model))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def pre_model_rejection_policy(model: str | None = None) -> dict[str, Any]:
    """仅接受管理员绑定当前端点的声明；不把 HTTP 状态码普遍解释为模型未执行。"""
    fingerprint = endpoint_accounting_fingerprint(model)
    declared = environment_value("CODE_EXPLORER_LLM_PRE_MODEL_REJECTION_ENDPOINT_SHA256")
    confirmed = bool(declared and declared == fingerprint)
    return {"version": "endpoint-declared-rejections-v1", "endpoint_sha256": fingerprint,
            "confirmed_pre_model_statuses": [429, 503] if confirmed else [],
            "basis": "operator_endpoint_declaration" if confirmed else "not_declared",
            "scope": "model_tokens_not_provider_invoice; only_observed_matching_http_rejections"}

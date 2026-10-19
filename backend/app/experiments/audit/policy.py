"""实验审计的存储上限、允许字段、脱敏和内容指纹；不生成模型安全结论。"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from backend.app.agents.policy import redact_secrets
from backend.app.llm.settings import bounded_environment_int, get_model_api_key


class AuditError(RuntimeError):
    """审计不能安全完整保存，需在模型调用前或运行中明确失败。"""


@dataclass(frozen=True)
class AuditLimits:
    """JSON 负载配额，不代表 SQLite 物理文件大小；已盲评记录不自动过期。"""
    record_bytes: int = 4 * 1024 * 1024
    run_bytes: int = 32 * 1024 * 1024
    total_bytes: int = 1024 * 1024 * 1024
    retention_days: int = 30

    @classmethod
    def from_environment(cls) -> "AuditLimits":
        """读取有界运维参数；两组预留同样的运行配额。"""
        return cls(
            bounded_environment_int("EXPERIMENT_AUDIT_RECORD_BYTES", 4 * 1024 * 1024, 1024, 16 * 1024 * 1024),
            bounded_environment_int("EXPERIMENT_AUDIT_RUN_BYTES", 32 * 1024 * 1024, 1024, 128 * 1024 * 1024),
            bounded_environment_int("EXPERIMENT_AUDIT_TOTAL_BYTES", 1024 * 1024 * 1024, 1024, 10 * 1024 * 1024 * 1024),
            bounded_environment_int("EXPERIMENT_AUDIT_RETENTION_DAYS", 30, 1, 3650),
        )


def digest(value: Any) -> str:
    """对规范 JSON 计算内容身份；哈希不保证语义正确，也不恢复原内容。"""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def contract_digest(instructions: str, schemas: list[dict[str, Any]], report_version: str,
                    execution_policy: dict[str, Any] | None = None) -> str:
    """对共同提示词、工具、报告版本及可选运行策略冻结契约，不回填旧哈希。"""
    contract: dict[str, Any] = {"instructions": instructions, "tools": schemas, "report_version": report_version}
    if execution_policy is not None:
        contract["execution_policy"] = execution_policy
    return digest(contract)


def sanitize(value: Any) -> Any:
    """递归移除隐藏续接字段、敏感标量和常见文本秘密；不静默截断正文。"""
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if isinstance(item, str) and key.lower() in {
            "authorization", "api_key", "apikey", "password", "secret", "access_token", "refresh_token",
        } else sanitize(item)) for key, item in value.items() if key not in {
            "reasoning_content", "encrypted_content", "provider_items",
        } and not (key == "type" and item == "reasoning")}
    if isinstance(value, list):
        return [sanitize(item) for item in value if not (isinstance(item, dict) and item.get("type") == "reasoning")]
    if isinstance(value, str):
        redacted = redact_secrets(value)
        key = get_model_api_key()
        return redacted.replace(key, "[REDACTED]") if key else redacted
    return value


def visible_request(payload: dict[str, Any]) -> dict[str, Any]:
    """允许列表投影实际请求；认证头从不传入此接口。"""
    projected = {key: payload[key] for key in (
        "model", "instructions", "messages", "input", "tools", "tool_choice", "max_tokens",
        "max_completion_tokens", "max_output_tokens", "temperature", "top_p", "stream", "store",
        "parallel_tool_calls", "reasoning_effort", "include", "stream_options",
    ) if key in payload}
    for name in ("messages", "input"):
        if isinstance(projected.get(name), list):
            projected[name] = [visible_message(item) for item in projected[name]
                               if isinstance(item, dict) and item.get("type") != "reasoning"]
    return sanitize(projected)


def visible_message(message: dict[str, Any]) -> dict[str, Any]:
    """只保留文本消息和函数调用协议字段，不信任供应商额外的 thinking 等字段。"""
    projected = {key: message[key] for key in (
        "id", "type", "role", "status", "content", "refusal", "tool_call_id", "name", "call_id", "arguments", "output",
    ) if key in message}
    if isinstance(projected.get("content"), list):
        projected["content"] = [{key: item[key] for key in ("type", "text", "refusal") if key in item}
                                for item in projected["content"] if isinstance(item, dict)
                                and item.get("type") in {"text", "input_text", "output_text", "refusal"}]
    if isinstance(message.get("tool_calls"), list):
        projected["tool_calls"] = [{"id": call.get("id"), "type": call.get("type"), "function": {
            key: value for key, value in (call.get("function") or {}).items() if key in {"name", "arguments"}
        }} for call in message["tool_calls"] if isinstance(call, dict)]
    return projected


def visible_response(payload: dict[str, Any]) -> dict[str, Any]:
    """保存供应商可见消息、工具调用、结束状态与 usage，不保存推理正文。"""
    projected = {key: payload[key] for key in ("id", "model", "status", "incomplete_details", "usage", "output_text") if key in payload}
    if isinstance(payload.get("choices"), list):
        projected["choices"] = [{"finish_reason": choice.get("finish_reason"),
                                 "message": visible_message(choice.get("message") or {})}
                                for choice in payload["choices"] if isinstance(choice, dict)]
    if isinstance(payload.get("output"), list):
        projected["output"] = [visible_message(item) for item in payload["output"]
                               if isinstance(item, dict) and item.get("type") in {"message", "function_call"}]
    return sanitize(projected)

"""仅用标准库估算请求体量；真实 Token 消耗只来自供应商 usage。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


class TextTokenCounter(Protocol):
    """可替换的本地分词计数接口；不接触密钥，不发送项目文本。"""

    def count(self, text: str) -> int:
        """输入文本，输出该策略的 Token 计数或保守估算。"""
        ...


@dataclass(frozen=True)
class TokenEstimate:
    """请求输入估算；本地未掌握平台 chat template，不能标为平台精确计数。"""

    tokens: int
    method: str
    exact: bool = False


class RequestTokenCounter(TextTokenCounter, Protocol):
    """请求计数扩展接口；后续可接平台计数端点，仍需声明来源和精确性。"""

    def request(self, payload: dict[str, Any]) -> TokenEstimate:
        """输入实际协议载荷，输出附带方法名和精确性声明的计数。"""
        ...


class TokenCounter:
    """按完整 JSON 的 UTF-8 字节数保守估算，不加载外部分词器。

    该估算既不是平台精确分词，也不是所有模型的数学上界；私有消息模板和
    工具注入仍需独立安全余量。保留请求计数协议，未来可注入平台计数实现。
    """

    def __init__(self, *, model: str = "", tokenizer: str = "auto", tokenizer_path: str = "") -> None:
        """输入模型及估算策略；旧词表参数仅用于明确拒绝已移除的配置。

        ``auto`` 与 ``utf8`` 都使用标准库估算。非空词表路径或旧 tiktoken 配置
        会抛出 ValueError，不能静默声称仍在使用原分词器。
        """
        if tokenizer not in {"auto", "utf8"} or tokenizer_path:
            raise ValueError("Only auto/utf8 estimation is supported; remove legacy tokenizer settings")
        self.model = model
        self.method = "utf8_bytes_estimate"

    def count(self, text: str) -> int:
        """输入文本，返回 UTF-8 字节估算；内容始终按普通文本处理。"""
        return len(text.encode("utf-8"))

    def request(self, payload: dict[str, Any]) -> TokenEstimate:
        """计数实际协议字段（包括工具定义、历史、推理状态）；不包含认证头。"""
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return TokenEstimate(self.count(text), self.method)


def get_token_counter(model: str, tokenizer: str, tokenizer_path: str) -> TokenCounter:
    """按配置构造无外部依赖、无项目文本缓存的计数器。"""
    return TokenCounter(model=model, tokenizer=tokenizer, tokenizer_path=tokenizer_path)

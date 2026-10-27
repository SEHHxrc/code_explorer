"""解释性安全知识的无副作用检索接口；不承担 API 绑定或静态传播模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SecurityKnowledgeRequest:
    """按规则和 CWE 标识请求外部安全知识。"""

    cwe_ids: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()
    query: str = ""
    limit: int = 5


@dataclass(frozen=True)
class SecurityKnowledgeDocument:
    """外部解释资料；不得当作源码事实或未经结构校验的 Source/Sink/净化模型。"""

    document_id: str
    title: str
    content: str
    source: str
    version: str = ""


class SecurityKnowledgeProvider(Protocol):
    """CWE 或其他权威安全知识库适配器的异步检索接口。"""

    async def retrieve(
        self,
        request: SecurityKnowledgeRequest,
    ) -> list[SecurityKnowledgeDocument]:
        """检索外部知识；实现不得修改项目证据。"""
        ...


class NullSecurityKnowledgeProvider:
    """当前默认空实现，确保尚未启用 RAG 时行为明确。"""

    async def retrieve(
        self,
        request: SecurityKnowledgeRequest,
    ) -> list[SecurityKnowledgeDocument]:
        """忽略请求并返回空知识列表。"""
        return []

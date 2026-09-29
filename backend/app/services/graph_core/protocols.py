"""图产物和图视图的结构化接口，不规定具体存储实现。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from .contracts import GraphCapabilities, GraphEdgeRef, GraphKind, GraphNodeRef


@runtime_checkable
class GraphArtifactView(Protocol):
    """供校验、文档和后续通用工具消费的最小图接口。"""

    @property
    def schema_version(self) -> str:
        """返回当前视图所包装产物的协议版本。"""
        ...

    @property
    def graph_kind(self) -> GraphKind:
        """返回宏观依赖图、微观程序图或安全流图类别。"""
        ...

    @property
    def capabilities(self) -> GraphCapabilities:
        """返回该图显式声明的能力集合。"""
        ...

    def iter_nodes(self) -> Iterable[GraphNodeRef]:
        """按稳定顺序迭代最小节点投影。"""
        ...

    def iter_edges(self) -> Iterable[GraphEdgeRef]:
        """按稳定顺序迭代最小边投影。"""
        ...


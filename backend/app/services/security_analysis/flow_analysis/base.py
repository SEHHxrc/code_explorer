"""跨语言数据流分析器的公共接口。"""

from __future__ import annotations

from typing import Any, Protocol

from backend.app.services.program_graph import FunctionProgramGraph, ProgramGraphArtifact, ProgramGraphEdge
from backend.app.services.semantic_index import SemanticIndex, SemanticIndexArtifact

from ..contracts import DataFlowEvidence, StaticSecurityFact
from ..ir import IRValueBoundary
from .diagnostics import FlowDiagnostics


class ValuePathQuery(Protocol):
    """初始化摘要与工厂返回共用的局部值路径接口；实现不拥有语言或安全规则。"""

    def __call__(self, function: FunctionProgramGraph, *, source_node_id: str,
                 source_names: tuple[str, ...], sink_node_id: str, sink_names: set[str],
                 reads_input: bool = True) -> list[ProgramGraphEdge] | None:
        """返回精确变量槽位路径；空列表表示同操作命中，None 表示当前模型未建立路径。"""
        ...


class DataFlowAnalyzer(Protocol):
    """把公共程序图和安全事实转换为可追溯数据流路径。"""

    def analyze(
        self,
        program_graph: ProgramGraphArtifact,
        *,
        sources: list[StaticSecurityFact],
        sinks: list[StaticSecurityFact],
        sanitizers: list[StaticSecurityFact],
        dependency_graph: dict[str, Any] | None = None,
        semantic_index: SemanticIndex | SemanticIndexArtifact | dict[str, Any] | None = None,
        value_boundaries: list[IRValueBoundary] | None = None,
        diagnostics: FlowDiagnostics | None = None,
    ) -> list[DataFlowEvidence]:
        """返回公共 CFG/DDG 能够实际建立的 Source-to-Sink 路径。"""
        ...

"""跨语言数据流分析器的公共接口。"""

from __future__ import annotations

from typing import Any, Protocol

from backend.app.services.program_graph import ProgramGraphArtifact

from ..contracts import DataFlowEvidence, StaticSecurityFact


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
    ) -> list[DataFlowEvidence]:
        """返回公共 CFG/DDG 能够实际建立的 Source-to-Sink 路径。"""
        ...

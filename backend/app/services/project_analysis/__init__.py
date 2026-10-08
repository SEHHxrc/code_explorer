"""无持久化副作用的项目源码分析流水线。

包入口保持轻量：图交换边界可独立用于快照查询，仅在访问流水线类型时才加载
tree-sitter 和各语言分析器。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .graph_exchange import GraphExchangeNormalizer

if TYPE_CHECKING:
    from .pipeline import (
        ProjectAnalysisBundle,
        ProjectAnalysisPipeline,
        ProjectAnalysisPipelineError,
    )

__all__ = [
    "GraphExchangeNormalizer",
    "ProjectAnalysisBundle",
    "ProjectAnalysisPipeline",
    "ProjectAnalysisPipelineError",
]


def __getattr__(name: str) -> Any:
    """按需公开分析流水线类型，避免查询路径加载语言前端。"""
    if name not in {
        "ProjectAnalysisBundle",
        "ProjectAnalysisPipeline",
        "ProjectAnalysisPipelineError",
    }:
        raise AttributeError(name)
    from .pipeline import (
        ProjectAnalysisBundle,
        ProjectAnalysisPipeline,
        ProjectAnalysisPipelineError,
    )

    exports = {
        "ProjectAnalysisBundle": ProjectAnalysisBundle,
        "ProjectAnalysisPipeline": ProjectAnalysisPipeline,
        "ProjectAnalysisPipelineError": ProjectAnalysisPipelineError,
    }
    return exports[name]

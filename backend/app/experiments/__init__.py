"""原始文件与静态安全证据增强的盲态配对实验模块。"""

from .contracts import BlindReviewRequest, ComparisonRequest, ExperimentError
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .service import ExperimentComparisonService

__all__ = ["BlindReviewRequest", "ComparisonRequest", "ExperimentComparisonService", "ExperimentError"]


def __getattr__(name: str) -> Any:
    """按需加载服务门面，避免纯契约导入反向初始化 Agent/Repository 形成循环。"""
    if name == "ExperimentComparisonService":
        from .service import ExperimentComparisonService
        return ExperimentComparisonService
    raise AttributeError(name)

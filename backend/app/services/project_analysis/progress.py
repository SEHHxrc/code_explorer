"""纯分析阶段的可选观测接口；进度失败不得影响分析结果或事务。"""

import logging
from collections.abc import Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)
ProgressReader = Callable[[], dict[str, Any]]


class AnalysisProgressReporter(Protocol):
    """接收阶段标识和可选的实时计数读取函数，不接收源码或绝对路径。"""

    def phase(self, stage: str, reader: ProgressReader | None = None) -> None:
        """进入 stage 阶段；reader 仅在该阶段有效，供查询时获取真实计数。"""
        ...


def report_progress(
    reporter: AnalysisProgressReporter | None,
    stage: str,
    reader: ProgressReader | None = None,
) -> None:
    """把阶段和计数读取器交给观察者；隔离观测异常，保持原分析语义。"""
    if reporter is not None:
        try:
            reporter.phase(stage, reader)
        except Exception:
            logger.warning("Analysis progress observer failed", exc_info=True)

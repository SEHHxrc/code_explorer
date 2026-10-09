"""临时无图对照组运行策略。

!!! TEMPORARY CONTROL GROUP / 可整体删除 !!!
正式智能体不得依赖本模块。静态安全证据实验确认更优后删除整个 baseline 目录。
"""

from typing import Any

from backend.app.agents.orchestrator import AgentRunManager
from backend.app.experiments.baseline.context_builder import BaselineContextBuilder
from backend.app.experiments.baseline.tool_registry import create_baseline_tool_registry
from backend.app.experiments.context import SECURITY_EXPERIMENT_INSTRUCTIONS, prepare_experiment_artifact

BASELINE_INSTRUCTIONS = SECURITY_EXPERIMENT_INSTRUCTIONS


def prepare_baseline_artifact(artifact: dict) -> dict:
    """【临时对照组】生成不含依赖图、图排序地图和图派生概览的隔离副本。"""
    return prepare_experiment_artifact(artifact, with_evidence=False)


class BaselineExperimentStrategy:
    """【临时对照组】隔离启动无依赖图运行；实验结束后应整体删除。"""

    def __init__(self, manager: AgentRunManager | None = None) -> None:
        """输入可选运行管理器，初始化隔离的无图对照组上下文与工具。"""
        self.manager = manager or AgentRunManager(
            context_builder=BaselineContextBuilder(),
            tools=create_baseline_tool_registry(),
            instructions=BASELINE_INSTRUCTIONS,
        )

    def start(self, *, artifact: dict[str, Any], **run_arguments: Any) -> None:
        """【临时对照组】剥离 dependency_graph、图排序 Repo Map 和图派生概览后启动。"""
        self.manager.start(artifact=prepare_baseline_artifact(artifact), **run_arguments)

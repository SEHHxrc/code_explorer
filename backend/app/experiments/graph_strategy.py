"""图增强实验策略；该能力代表实验胜出后应保留的正式方向。"""

from typing import Any

from backend.app.agents.orchestrator import AgentRunManager
from backend.app.experiments.graph_context import GraphAugmentedContextBuilder


class GraphAugmentedExperimentStrategy:
    """使用中性 Repo Map、紧凑依赖图和正式图工具启动运行。"""

    def __init__(self, manager: AgentRunManager | None = None) -> None:
        """输入可选运行管理器，初始化使用紧凑依赖图的实验策略。"""
        self.manager = manager or AgentRunManager(context_builder=GraphAugmentedContextBuilder())

    def start(self, *, artifact: dict[str, Any], **run_arguments: Any) -> None:
        """使用指定策略创建并启动智能体运行。"""
        self.manager.start(artifact=artifact, **run_arguments)
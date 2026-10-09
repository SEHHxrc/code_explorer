"""TEMPORARY CONTROL GROUP / 临时对照组：工具权限与实验组严格相同。"""

from backend.app.agents.tools.base import ToolRegistry
from backend.app.experiments.tools import create_experiment_tool_registry


def create_baseline_tool_registry() -> ToolRegistry:
    """【临时对照组】不再暴露 Manifest、入口索引、符号搜索或图查询。"""
    return create_experiment_tool_registry()

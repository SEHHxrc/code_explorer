"""临时无图对照组工具注册表。

!!! TEMPORARY CONTROL GROUP / 可整体删除 !!!
该注册表刻意不包含 DependencyNeighborsTool；不得用于正式 AgentRun API。
"""

from backend.app.agents.contracts import ToolResult
from backend.app.agents.tools.arguments import EmptyArguments
from backend.app.agents.tools.base import AgentTool, ToolContext, ToolRegistry
from backend.app.agents.tools.discovery import EntrypointsTool, SearchSymbolsTool
from backend.app.agents.tools.source import ReadFileTool, SearchProjectTextTool
from backend.app.experiments.context import neutral_manifest


class BaselineManifestTool(AgentTool):
    """【临时对照组】返回移除 graph_summary 的 Manifest；实验结束后删除。"""

    name = "get_project_manifest"
    description = "Return deterministic project frameworks, commands and entrypoints."
    arguments_model = EmptyArguments

    async def execute(self, context: ToolContext, arguments: EmptyArguments) -> ToolResult:
        """【临时对照组】阻止 Manifest 工具间接泄漏图统计。"""
        return ToolResult(content=neutral_manifest(context.artifact).model_dump())


def create_baseline_tool_registry() -> ToolRegistry:
    """【临时对照组】创建不含任何图查询能力的工具集合；可随实验整体删除。"""
    return ToolRegistry([
        BaselineManifestTool(),
        EntrypointsTool(),
        SearchSymbolsTool(),
        ReadFileTool(),
        SearchProjectTextTool(),
    ])
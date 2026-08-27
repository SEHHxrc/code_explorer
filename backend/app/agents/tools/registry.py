"""默认只读项目工具注册表。"""

from backend.app.agents.tools.base import ToolRegistry
from backend.app.agents.tools.discovery import EntrypointsTool, ManifestTool, SearchSymbolsTool
from backend.app.agents.tools.graph import DependencyNeighborsTool
from backend.app.agents.tools.source import ReadFileTool, SearchProjectTextTool


def create_project_tool_registry() -> ToolRegistry:
    """保持既有工具名称和模型 Schema 顺序。"""
    return ToolRegistry([
        ManifestTool(),
        EntrypointsTool(),
        SearchSymbolsTool(),
        ReadFileTool(),
        DependencyNeighborsTool(),
        SearchProjectTextTool(),
    ])
"""智能体工具协议、可信上下文和安全注册表。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Type

from pydantic import BaseModel, ValidationError

from backend.app.agents.contracts import ToolResult
from backend.app.agents.tools.evidence_index import ProjectEvidenceIndex


@dataclass(frozen=True)
class ToolContext:
    """一次工具执行的可信上下文，并在创建时构建共享证据索引。"""

    project_id: str
    user_id: str
    project_root: Path
    artifact: dict[str, Any]
    evidence_index: ProjectEvidenceIndex | None = None

    def __post_init__(self):
        if self.evidence_index is None:
            object.__setattr__(self, "evidence_index", ProjectEvidenceIndex(self.artifact))


class AgentTool(ABC):
    name: str
    description: str
    arguments_model: Type[BaseModel]

    def schema(self) -> dict[str, Any]:
        parameters = self.arguments_model.model_json_schema()
        properties = parameters.get("properties", {})
        parameters["required"] = list(properties)
        parameters["additionalProperties"] = False
        for definition in properties.values():
            definition.pop("default", None)
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": parameters,
            "strict": True,
        }

    def validate(self, arguments: dict[str, Any]) -> BaseModel:
        try:
            return self.arguments_model.model_validate(arguments)
        except ValidationError as exc:
            raise ValueError(f"Invalid arguments for {self.name}") from exc

    @abstractmethod
    async def execute(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        raise NotImplementedError


class ToolRegistry:
    """仅分派显式注册的工具，并拒绝重复名称造成的静默覆盖。"""

    def __init__(self, tools: list[AgentTool]):
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate agent tool names are not allowed")
        self._tools = {tool.name: tool for tool in tools}

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    async def execute(self, name: str, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError("Requested tool is not registered")
        return await tool.execute(context, tool.validate(arguments))
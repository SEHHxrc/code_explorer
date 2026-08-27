"""只读项目工具的严格参数模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyArguments(StrictArguments):
    pass


class SearchArguments(StrictArguments):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=20, ge=1, le=50)


class ReadFileArguments(StrictArguments):
    path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=120, ge=1)


class DependencyArguments(StrictArguments):
    node_id: str = Field(min_length=1, max_length=1000)
    direction: Literal["both", "incoming", "outgoing"] = "both"
    limit: int = Field(default=30, ge=1, le=100)
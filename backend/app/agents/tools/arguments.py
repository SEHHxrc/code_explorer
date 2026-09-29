"""只读项目工具的严格参数模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictArguments(BaseModel):
    """禁止未声明字段的智能体工具参数基类。"""
    model_config = ConfigDict(extra="forbid")


class EmptyArguments(StrictArguments):
    """表示不接收业务参数的工具请求。"""


class SearchArguments(StrictArguments):
    """定义符号搜索工具的输入参数。"""
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=20, ge=1, le=50)


class ReadFileArguments(StrictArguments):
    """定义受限源码读取工具的输入参数。"""
    path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=120, ge=1)


class DependencyArguments(StrictArguments):
    """定义依赖邻居查询工具的输入参数。"""
    node_id: str = Field(min_length=1, max_length=1000)
    direction: Literal["both", "incoming", "outgoing"] = "both"
    limit: int = Field(default=30, ge=1, le=100)


class SecurityEvidenceArguments(StrictArguments):
    """分页查询面向 LLM 的静态安全候选。"""

    query: str = Field(default="", max_length=200)
    offset: int = Field(default=0, ge=0, le=10_000)
    limit: int = Field(default=10, ge=1, le=25)

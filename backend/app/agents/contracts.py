# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    """创建智能体运行的输入；包含问题、模型开关和允许的最大步骤数。"""
    question: str = Field(min_length=1, max_length=8000)
    use_model: bool = True
    max_steps: int = Field(default=4, ge=1, le=6)


class AgentEvidence(BaseModel):
    """智能体结论引用的项目证据；输入路径及可选行号、符号和说明。"""
    path: str
    line: int | None = None
    symbol: str | None = None
    detail: str = ""


class ContextPacket(BaseModel):
    """发送给编排器的有界项目上下文。

    输入项目 manifest、筛选后的 repo map 和证据；输出系统提示所需文本与保留的
    结构化事实。
    """
    project_id: str
    project_name: str
    prompt_context: str
    manifest: dict[str, Any]
    repo_map: str
    evidence: list[AgentEvidence] = Field(default_factory=list)


class ToolResult(BaseModel):
    """工具的统一输出；包含 JSON 兼容内容、证据和截断标记。"""
    content: Any
    evidence: list[AgentEvidence] = Field(default_factory=list)
    truncated: bool = False


class AgentEvent(BaseModel):
    """前端可消费的增量事件；输出序号、事件类型和 JSON 载荷。"""
    sequence: int
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentRunView(BaseModel):
    """智能体运行的公开视图；输出状态、模型、答案或错误，不暴露数据库对象。"""
    id: str
    project_id: str
    question: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    provider: str | None = None
    model: str | None = None
    answer: str | None = None
    error: str | None = None

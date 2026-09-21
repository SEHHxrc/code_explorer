# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    """创建智能体运行的输入；包含问题、可选模型和允许的最大步骤数。"""
    question: str = Field(min_length=1, max_length=8000)
    use_model: bool = True
    max_steps: int = Field(default=4, ge=1, le=6)
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )


class AgentClaim(BaseModel):
    """Worker 原子认领后获得的内部运行快照。"""

    run_id: str
    project_id: str
    user_id: str
    question: str
    use_model: bool
    max_steps: int
    model: str | None = None
    strategy: Literal["default", "graph", "baseline"] = "default"


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
    created_at: datetime | None = None


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
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AgentRunHistoryItem(BaseModel):
    """项目历史列表中的轻量运行摘要，不包含答案正文和事件载荷。"""

    id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    question_preview: str
    provider: str | None = None
    model: str | None = None
    strategy: Literal["default", "graph", "baseline"] = "default"
    tool_calls: int = 0
    evidence_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AgentRunSnapshot(BaseModel):
    """可供前端恢复一次历史会话的运行、展示事件和去重证据。"""

    run: AgentRunView
    strategy: Literal["default", "graph", "baseline"] = "default"
    events: list[AgentEvent] = Field(default_factory=list)
    evidence: list[AgentEvidence] = Field(default_factory=list)

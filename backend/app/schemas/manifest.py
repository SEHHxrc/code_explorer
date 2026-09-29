from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """描述项目推断所依据的源码位置；输入路径、可选行号和说明。"""
    path: str
    line: int | None = None
    detail: str = ""


CommandOrigin = Literal["observed", "inferred", "generated", "documented"]
CommandPurpose = Literal["serve", "run", "build", "test", "worker", "unknown"]
ExecutionProfile = Literal["development", "test", "production", "unknown"]
CommandAuthority = Literal[
    "trusted_operator", "service_manager", "ci_pipeline",
    "administrative_user", "remote_user_influenced", "unknown",
]
Confidence = Literal["high", "medium", "low"]


class CommandFact(BaseModel):
    """一条带来源和执行边界的命令事实；命令存在本身不代表安全问题。"""

    command: str = Field(min_length=1, max_length=8000)
    purpose: CommandPurpose = "unknown"
    launcher: str | None = Field(default=None, max_length=200)
    argv: list[str] = Field(default_factory=list)
    origin: CommandOrigin
    source_kind: str = Field(max_length=100)
    path: str | None = Field(default=None, max_length=1000)
    line: int | None = Field(default=None, ge=1)
    execution_profile: ExecutionProfile = "unknown"
    authority: CommandAuthority = "unknown"
    shell_interpreted: bool = False
    confidence: Confidence = "high"


class Entrypoint(BaseModel):
    """描述一个可运行或框架入口；输入入口类型、名称、位置及可选启动命令。"""
    kind: str
    name: str
    path: str
    line: int | None = None
    command: str | None = None
    command_origin: CommandOrigin | None = None
    suggested_command: str | None = None
    framework: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class ProjectManifest(BaseModel):
    """项目的确定性事实清单。

    输入来自静态分析的语言、框架、入口、模块、命令和证据；输出为可持久化 JSON，
    同时作为项目概览及智能体上下文的事实底座。
    """
    schema_version: str = "2.0"
    project_name: str
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    entrypoints: list[Entrypoint] = Field(default_factory=list)
    build_commands: list[str] = Field(default_factory=list)
    run_commands: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    commands: list[CommandFact] = Field(default_factory=list)
    suggested_commands: list[str] = Field(default_factory=list)
    modules: list[dict[str, Any]] = Field(default_factory=list)
    graph_summary: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProjectOverviewRequest(BaseModel):
    """项目概览生成参数；输入模型使用开关和输出语言。"""
    use_model: bool = True
    language: str = "zh-CN"

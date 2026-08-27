# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """描述项目推断所依据的源码位置；输入路径、可选行号和说明。"""
    path: str
    line: int | None = None
    detail: str = ""


class Entrypoint(BaseModel):
    """描述一个可运行或框架入口；输入入口类型、名称、位置及可选启动命令。"""
    kind: str
    name: str
    path: str
    line: int | None = None
    command: str | None = None
    framework: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class ProjectManifest(BaseModel):
    """项目的确定性事实清单。

    输入来自静态分析的语言、框架、入口、模块、命令和证据；输出为可持久化 JSON，
    同时作为项目概览及智能体上下文的事实底座。
    """
    schema_version: str = "1.0"
    project_name: str
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    entrypoints: list[Entrypoint] = Field(default_factory=list)
    build_commands: list[str] = Field(default_factory=list)
    run_commands: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    modules: list[dict[str, Any]] = Field(default_factory=list)
    graph_summary: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProjectOverviewRequest(BaseModel):
    """项目概览生成参数；输入模型使用开关和输出语言。"""
    use_model: bool = True
    language: str = "zh-CN"

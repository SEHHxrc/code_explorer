# -*- coding: utf-8 -*-
from __future__ import annotations

from backend.app.schemas.manifest import ProjectManifest


def render_deterministic_overview(manifest: ProjectManifest) -> str:
    """输入项目 manifest，输出不调用模型的中文 Markdown 架构概览。"""
    languages = "、".join(manifest.languages) or "尚未识别"
    frameworks = "、".join(manifest.frameworks) or "尚未识别"
    managers = "、".join(manifest.package_managers) or "尚未识别"
    graph = manifest.graph_summary
    lines = [
        f"## {manifest.project_name} 项目概览",
        "",
        f"该项目主要使用 **{languages}**，检测到的框架/工具包括 **{frameworks}**，包管理方式为 **{managers}**。",
        "",
        "### 程序入口",
        "",
    ]
    if manifest.entrypoints:
        for entry in manifest.entrypoints:
            location = f"{entry.path}:{entry.line}" if entry.line else entry.path
            if entry.command:
                command = f"；观察到的命令：`{entry.command}`"
            elif entry.suggested_command:
                command = f"；系统运行建议（非项目事实）：`{entry.suggested_command}`"
            else:
                command = ""
            lines.append(f"- `{entry.name}`（{entry.kind}），位置 `{location}`{command}")
    else:
        lines.append("- 暂未发现高置信度程序入口，需要进一步检查项目文档或构建配置。")
    lines.extend([
        "",
        "### 项目结构",
        "",
    ])
    for module in manifest.modules[:12]:
        lines.append(f"- `{module['path']}`：{module['file_count']} 个文件")
    lines.extend([
        "",
        "### 依赖图摘要",
        "",
        f"当前静态分析得到 {graph.get('node_count', 0)} 个节点、{graph.get('edge_count', 0)} 条关系。",
    ])
    relations = graph.get("relations", {})
    if relations:
        lines.append("主要关系：" + "、".join(f"{name} {count}" for name, count in relations.items()) + "。")
    if manifest.build_commands:
        lines.extend(["", "### 构建命令", ""] + [f"- `{cmd}`" for cmd in manifest.build_commands])
    if manifest.run_commands:
        lines.extend(["", "### 运行命令", ""] + [f"- `{cmd}`" for cmd in manifest.run_commands])
    if manifest.test_commands:
        lines.extend(["", "### 测试命令", ""] + [f"- `{cmd}`" for cmd in manifest.test_commands])
    if manifest.suggested_commands:
        lines.extend([
            "",
            "### 系统生成的命令建议",
            "",
            "> 以下命令由分析器生成，仅用于辅助运行，不代表项目实际部署配置，也不应直接作为安全结论。",
            "",
            *[f"- `{cmd}`" for cmd in manifest.suggested_commands],
        ])
    lines.extend([
        "",
        "> 本概览由确定性静态分析生成；模型增强版本会在此基础上解释模块职责和调用关系。",
    ])
    return "\n".join(lines)

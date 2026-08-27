"""临时无图对照组上下文。

!!! TEMPORARY CONTROL GROUP / 可整体删除 !!!
本文件只用于 A/B 实验的“无依赖图”对照组。图增强效果确认后应删除整个 baseline 目录。
"""

from __future__ import annotations

import json

from backend.app.agents.context_builder import MAX_CONTEXT_CHARS, ProjectContextBuilder
from backend.app.agents.contracts import AgentEvidence, ContextPacket
from backend.app.experiments.context import neutral_manifest, neutral_repo_map


class BaselineContextBuilder:
    """【临时对照组】构造完全不含图摘要、图节点和图关系的模型上下文。"""

    def build(self, *, project_id: str, question: str, artifact: dict) -> ContextPacket:
        """【临时对照组】使用中性 Manifest 与 Repo Map；实验结束后随目录删除。"""
        manifest = neutral_manifest(artifact)
        repo_map = ProjectContextBuilder._select_repo_map(neutral_repo_map(artifact), question)
        evidence = [AgentEvidence(
            path=item.path,
            line=item.line,
            symbol=item.name,
            detail=f"{item.kind} entrypoint",
        ) for item in manifest.entrypoints[:20]]
        prompt_context = (
            "PROJECT_MANIFEST\n"
            + json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2)
            + "\n\nNEUTRAL_REPO_MAP\n"
            + repo_map
        )[:MAX_CONTEXT_CHARS]
        return ContextPacket(
            project_id=project_id,
            project_name=manifest.project_name,
            prompt_context=prompt_context,
            manifest=manifest.model_dump(),
            repo_map=repo_map,
            evidence=evidence,
        )
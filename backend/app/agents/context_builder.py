# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re

from backend.app.agents.contracts import AgentEvidence, ContextPacket
from backend.app.schemas.manifest import ProjectManifest


MAX_CONTEXT_CHARS = 18000


class ProjectContextBuilder:
    """从项目分析产物构建受字符预算约束的模型上下文。"""

    def build(self, *, project_id: str, question: str, artifact: dict) -> ContextPacket:
        """构建项目上下文。

        Args:
            project_id: 已分析项目的标识。
            question: 用户问题，用于从仓库地图选择相关行。
            artifact: 包含 ``manifest`` 与 ``repo_map`` 的分析产物。

        Returns:
            可直接交给编排器的 :class:`ContextPacket`。
        """
        manifest = ProjectManifest.model_validate(artifact.get("manifest") or {})
        repo_map = str(artifact.get("repo_map") or "")
        selected_map = self._select_repo_map(repo_map, question)
        evidence = [
            AgentEvidence(
                path=item.path,
                line=item.line,
                symbol=item.name,
                detail=f"{item.kind} entrypoint",
            )
            for item in manifest.entrypoints[:20]
        ]
        prompt_context = (
            "PROJECT_MANIFEST\n"
            + json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2)
            + "\n\nRELEVANT_REPO_MAP\n"
            + selected_map
        )[:MAX_CONTEXT_CHARS]
        return ContextPacket(
            project_id=project_id,
            project_name=manifest.project_name,
            prompt_context=prompt_context,
            manifest=manifest.model_dump(),
            repo_map=selected_map,
            evidence=evidence,
        )

    @staticmethod
    def _select_repo_map(repo_map: str, question: str, max_lines: int = 140) -> str:
        """按问题关键词筛选仓库地图；输入全文与行数上限，输出相关文本片段。"""
        lines = repo_map.splitlines()
        terms = {
            item.casefold() for item in re.findall(r"[A-Za-z_][A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", question)
        }
        header = lines[:25]
        matches = [line for line in lines[25:] if any(term in line.casefold() for term in terms)]
        fallback = lines[25: max_lines]
        chosen = header + (matches[: max_lines - len(header)] if matches else fallback)
        return "\n".join(chosen[:max_lines])

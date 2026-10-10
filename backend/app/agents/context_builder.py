from __future__ import annotations

import json
import re
from typing import Any, Protocol

from backend.app.agents.contracts import AgentEvidence, ContextPacket
from backend.app.llm.registry import create_model_provider, get_model_configuration, get_model_limits
from backend.app.llm.budget import BudgetPlanner, initial_message, request_payload
from backend.app.schemas.manifest import ProjectManifest
from backend.app.services.security_analysis import SecurityEvidencePromptBuilder


class ContextBuilder(Protocol):
    """把项目分析产物转换为单轮模型上下文的最小接口。"""

    def build(
        self,
        *,
        project_id: str,
        question: str,
        artifact: dict[str, Any],
        model: str | None = None,
        budgeted: bool = True,
    ) -> ContextPacket:
        """返回给智能体编排器使用的上下文包。"""
        ...


class ProjectContextBuilder:
    """从项目分析产物构建受字符预算约束的模型上下文。"""

    def __init__(self, security_builder: SecurityEvidencePromptBuilder | None = None,) -> None:
        """注入静态安全证据投影器。"""
        self.security_builder = security_builder or SecurityEvidencePromptBuilder()

    def build(self, *, project_id: str, question: str, artifact: dict[str, Any], model: str | None = None, budgeted: bool = True) -> ContextPacket:
        """构建项目上下文。

        Args:
            project_id: 已分析项目的标识。
            question: 用户问题，用于从仓库地图选择相关行。
            artifact: 包含 ``manifest`` 与 ``repo_map`` 的分析产物。

        Returns:
            可直接交给编排器的 :class:`ContextPacket`。
        """
        from backend.app.agents.instructions import AGENT_INSTRUCTIONS
        from backend.app.agents.tools import create_project_tool_registry
        limits = get_model_limits(model)
        if not budgeted:
            return self._render(project_id=project_id, question=question, artifact=artifact,
                                max_chars=limits.max_context_chars or 18000)
        config = get_model_configuration()
        provider = create_model_provider(model)
        schemas = create_project_tool_registry().schemas()
        selected_model = model or config.model
        return BudgetPlanner(limits, model=selected_model).build_initial_context(
            lambda ceiling: self._render(project_id=project_id, question=question, artifact=artifact, max_chars=ceiling),
            lambda packet: request_payload(provider, instructions=AGENT_INSTRUCTIONS,
                                           messages=[initial_message(question, packet.prompt_context)], tools=schemas,
                                           model=selected_model, max_output_tokens=limits.max_output_tokens),
        )

    def _render(self, *, project_id: str, question: str, artifact: dict[str, Any], max_chars: int) -> ContextPacket:
        """保留完整 Manifest/安全 JSON 和 Repo Map 行，不做任意字符硬截断。"""
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
        manifest_text = json.dumps(
            manifest.model_dump(), ensure_ascii=False, separators=(",", ":"),
        )
        security_text, security_evidence = self._security_context(
            artifact,
            question,
            max_chars=max(256, int(max_chars * 0.55)),
        )
        evidence.extend(security_evidence)
        prefix = "PROJECT_MANIFEST\n" + manifest_text
        if security_text:
            prefix += "\n\nSTATIC_SECURITY_EVIDENCE\n" + security_text
        repo_header = "\n\nRELEVANT_REPO_MAP\n"
        remaining = max(0, max_chars - len(prefix) - len(repo_header))
        map_lines: list[str] = []
        for line in selected_map.splitlines():
            if len("\n".join([*map_lines, line])) > remaining:
                break
            map_lines.append(line)
        prompt_context = prefix + repo_header + "\n".join(map_lines)
        return ContextPacket(
            project_id=project_id,
            project_name=manifest.project_name,
            prompt_context=prompt_context,
            manifest=manifest.model_dump(),
            repo_map=selected_map,
            evidence=evidence,
        )

    def _security_context(
        self,
        artifact: dict[str, Any],
        question: str,
        *,
        max_chars: int,
    ) -> tuple[str, list[AgentEvidence]]:
        """生成有效 JSON，并把已选候选端点加入 Agent 证据列表。"""
        raw_pack = artifact.get("security_evidence")
        if not isinstance(raw_pack, dict):
            return "", []
        try:
            envelope = self.security_builder.build(
                raw_pack,
                question=question,
                max_chars=max_chars,
            )
        except (TypeError, ValueError):
            return "", []
        items = [
            AgentEvidence(
                    path=endpoint.location.path,
                    line=endpoint.location.line,
                    symbol=endpoint.symbol,
                    detail=f"static security {endpoint.role}: {finding.rule_id}",
            )
            for finding in envelope.findings
            for endpoint in (finding.source, finding.sink)
        ]
        items.extend(AgentEvidence(
            path=item.location.path, line=item.location.line, symbol=item.symbol,
            detail=f"static {item.kind} rule match; not a vulnerability",
        ) for item in envelope.observations)
        text = json.dumps(
            envelope.model_dump(exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return text, items

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

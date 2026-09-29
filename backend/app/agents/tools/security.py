"""面向智能体的只读静态安全证据工具。"""

from backend.app.agents.contracts import AgentEvidence, ToolResult
from backend.app.agents.tools.arguments import SecurityEvidenceArguments
from backend.app.agents.tools.base import AgentTool, ToolContext
from backend.app.services.security_analysis import SecurityEvidencePromptBuilder


class SecurityEvidenceTool(AgentTool):
    """分页返回与 Prompt 相同协议的紧凑安全候选。"""

    name = "get_static_security_evidence"
    description = (
        "Return paginated static-security findings with source, sink, flow steps, "
        "snippets, uncertainty and coverage. Structural reachability is not taint flow."
    )
    arguments_model = SecurityEvidenceArguments

    async def execute(self, context: ToolContext, arguments: SecurityEvidenceArguments,) -> ToolResult:
        """读取持久化证据并按查询、偏移和上限返回自包含候选。"""
        raw_pack = context.artifact.get("security_evidence")
        if not isinstance(raw_pack, dict):
            return ToolResult(content={"available": False, "findings": []})
        envelope = SecurityEvidencePromptBuilder().build(
            raw_pack,
            question=arguments.query,
            offset=arguments.offset,
            max_findings=arguments.limit,
            max_chars=24_000,
        )
        evidence = [
            AgentEvidence(
                    path=endpoint.location.path,
                    line=endpoint.location.line,
                    symbol=endpoint.symbol,
                    detail=f"static security {endpoint.role}: {finding.rule_id}",
            )
            for finding in envelope.findings
            for endpoint in (finding.source, finding.sink)
        ]
        return ToolResult(
            content=envelope.model_dump(exclude_none=True),
            evidence=evidence,
            truncated=envelope.truncated,
        )

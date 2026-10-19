"""面向智能体的只读静态安全证据工具。"""

from backend.app.agents.contracts import AgentEvidence, ToolResult
from backend.app.agents.tools.arguments import SecurityEvidenceArguments, SecurityEvidenceSection
from backend.app.agents.tools.arguments import StrictArguments
from pydantic import Field
from typing import Any
from backend.app.agents.tools.base import AgentTool, ToolContext


def _detail_result(result: dict[str, Any]) -> ToolResult:
    """统一详情包装；只有实际交付片段记录源码证据，路径目录不算源码已读。"""
    evidence = [AgentEvidence(path=snippet["path"], line=snippet["start_line"],
                              detail="已交付静态片段；不代表完整实现已核验。") for snippet in result["snippets"].values()]
    return ToolResult(content=result, evidence=evidence, truncated=result["has_more"])


class SecurityEvidenceTool(AgentTool):
    """普通 Agent 的目录/详情门面，与分批实验复用同一查询实现。"""

    name = "get_static_security_evidence"
    description = (
        "For a path-summary ID or _coverage, set evidence_id and section with offset/limit to fetch details. "
        "Empty evidence_id searches ranked path metadata with query/cursor/limit, no source bodies. "
        "Structural reachability is not taint flow."
    )
    arguments_model = SecurityEvidenceArguments

    async def execute(self, context: ToolContext, arguments: SecurityEvidenceArguments,) -> ToolResult:
        """只调用公共目录；目录定位不计为已交付源码，详情仅对实际片段记证据。"""
        if arguments.evidence_id:
            return _detail_result(context.evidence_index.security.detail(arguments.evidence_id, section=arguments.section,
                                  offset=arguments.offset, limit=min(10, arguments.limit)))
        if arguments.offset:
            raise ValueError("目录分页请使用 cursor；offset 仅用于指定 ID 的详情分页。")
        result = context.evidence_index.security.search(query=arguments.query, cursor=arguments.cursor, limit=arguments.limit)
        return ToolResult(content=result, truncated=result["has_more"])


class EvidenceSearchArguments(StrictArguments):
    """静态目录元数据字面筛选和不透明游标。"""

    query: str = Field(default="", max_length=200)
    category: str = Field(default="", max_length=100)
    path: str = Field(default="", max_length=1000)
    trust_class: str = Field(default="", max_length=100)
    cursor: str = Field(default="", max_length=1000)
    limit: int = Field(default=8, ge=1, le=25)


class EvidenceDetailArguments(StrictArguments):
    """按稳定 ID 与组件读取，保留长路径和共享片段的续页。"""

    evidence_id: str = Field(min_length=1, max_length=1000)
    section: SecurityEvidenceSection = "overview"
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=5, ge=1, le=10)


class SearchStaticEvidenceTool(AgentTool):
    """仅检索未报告目录；不把目录定位算作已读取源码。"""

    name = "search_static_security_evidence"
    description = "Search ranked propagation paths, structural leads and operation leads by literal metadata (empty query lists all). priority is investigation order, not vulnerability probability. No source bodies. Completed IDs are excluded; finish active batch before searching again."
    arguments_model = EvidenceSearchArguments

    async def execute(self, context: ToolContext, arguments: EvidenceSearchArguments) -> ToolResult:
        """使用本运行的共享目录与后端排除集合返回确定性分页。"""
        if context.batches.active_ids:
            raise ValueError("请先分析并提交当前已选择证据的批次报告，再选择下一批。")
        result = context.evidence_index.security.search(**arguments.model_dump(), excluded=context.batches.completed_ids)
        return ToolResult(content=result, truncated=result["has_more"])


class SelectStaticEvidenceTool(AgentTool):
    """分批实验的 ID 详情入口；与普通 Agent 共用目录，仅额外约束批次状态。"""

    name = "get_static_security_evidence"
    description = "Select an evidence ID; fetch overview/flow/snippets/protections/unresolved with next_offset. No sampled path. Three active IDs max; reported IDs excluded. _coverage reads global overview, unresolved (limitations/failures), protections (rule coverage), and is not a selected candidate."
    arguments_model = EvidenceDetailArguments

    async def execute(self, context: ToolContext, arguments: EvidenceDetailArguments) -> ToolResult:
        """先成功读取完整页再选择 ID，失败不消耗证据；共享片段沿用已有持久化正文。"""
        if arguments.evidence_id in context.batches.completed_ids:
            raise ValueError("该 ID 已输出报告，请选择尚未报告的证据。")
        result = context.evidence_index.security.detail(arguments.evidence_id, section=arguments.section,
                                                      offset=arguments.offset, limit=arguments.limit)
        if result["items"] and result["view"] == "detail":
            context.batches.select(arguments.evidence_id)
        return _detail_result(result)

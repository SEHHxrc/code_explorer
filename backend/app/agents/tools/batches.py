"""两组共同的批次报告工具；只写运行台账，不修改项目文件。"""

from pydantic import Field

from .arguments import StrictArguments
from .base import AgentTool, ToolContext
from ..contracts import ToolResult


class BatchReportArguments(StrictArguments):
    """一批的台账 JSON、已读证据 ID 和实际展示源码范围。"""

    ledger_json: str = Field(min_length=1, description="Complete compact findings/coverage ledger JSON string matching the common report contract; no Markdown. No application-level total character limit.")
    evidence_ids: list[str] = Field(default_factory=list, max_length=3)
    scopes: list[str] = Field(default_factory=list, max_length=30)


class SubmitAnalysisBatchTool(AgentTool):
    """提交报告后才开始下一次选证据；范围与 ID 由后端验证。"""

    name = "submit_analysis_batch"
    description = "Publish this batch's structured findings/coverage ledger as ledger_json. The server generates report counts and Markdown. evidence_ids must equal selected IDs; scopes use shown_scopes path:start-end. Next batch excludes reported IDs. Only call in its turn."
    arguments_model = BatchReportArguments

    async def execute(self, context: ToolContext, arguments: BatchReportArguments) -> ToolResult:
        """保存本运行报告并返回短确认；编排器随后持久化与广播完整报告。"""
        batch = context.batches.publish(arguments.ledger_json, arguments.evidence_ids, arguments.scopes)
        return ToolResult(content={"batch_id": batch["batch_id"], "status": "reported", "next": "select_next_unreported_subject"})

"""两组共享的简洁报告契约与可审计完整性检查，不判断漏洞结论正确与否。"""

import re
from typing import Any

from backend.app.llm.response_metadata import ModelResponseMetadata

REPORT_VERSION = "concise-security-v1"
REPORT_SECTIONS = ("结论", "已核实问题", "未确认风险", "范围与局限")
REPORT_END = "<!-- SECURITY_REPORT_END -->"
REPORT_INSTRUCTIONS = """
两组使用完全相同的简洁报告格式：依次输出四个二级标题“## 结论”、“## 已核实问题”、
“## 未确认风险”、“## 范围与局限”。每节必须有内容，没有条目时明确写无或未确认。
优先直接回答问题；总篇幅尽量控制在 900 中文字以内，最多列 5 个最相关问题，避免重复解释和大表格。
每个问题注明证据 [相对路径:行号]、验证状态、攻击者控制条件；不得把健壮性问题直接升级为远程漏洞。
零候选或零 Sink 仅代表本次已启用规则和已扫描文件范围内未匹配，不证明无 RCE 或项目整体安全。
必须说明实际读过的文件/范围、静态覆盖与未核实部分；注册规则包不表示项目使用了相应框架。
完整报告最后单独输出 <!-- SECURITY_REPORT_END -->，不要提前输出此标记；它只用于检测报告是否写完。
"""


def assess_answer(
    answer: str, metadata: ModelResponseMetadata, *, require_report: bool,
) -> dict[str, Any]:
    """检查供应商终止状态、非空文本、章节顺序/正文和结束标记，输出独立维度。"""
    if metadata.refused or metadata.incomplete_reason or metadata.response_status in {"incomplete", "failed", "cancelled"}:
        transport_complete: bool | None = False
    elif metadata.finish_reason in {"length", "content_filter", "tool_calls", "function_call"}:
        transport_complete = False
    elif metadata.finish_reason == "stop" or metadata.response_status == "completed":
        transport_complete = True
    else:
        transport_complete = None
    headings = list(re.finditer(r"^##\s+(.+?)\s*$", answer, re.MULTILINE))
    section_names = [match.group(1) for match in headings]
    report_complete = section_names == list(REPORT_SECTIONS) and answer.rstrip().endswith(REPORT_END)
    if report_complete:
        report_complete = all(
            answer[match.end():headings[index + 1].start() if index + 1 < len(headings) else answer.rfind(REPORT_END)].strip()
            for index, match in enumerate(headings)
        )
    known_incomplete = transport_complete is False or not answer.strip() or (require_report and not report_complete)
    status = "incomplete" if known_incomplete else (
        "complete" if transport_complete is True else "unknown"
    )
    return {
        "status": status, "transport_complete": transport_complete,
        "nonempty": bool(answer.strip()), "report_required": require_report,
        "report_complete": report_complete if require_report else None,
        "report_version": REPORT_VERSION if require_report else None,
        "semantic_coverage": "not_proven",
    }

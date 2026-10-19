"""两组共享的简洁报告契约与可审计完整性检查，不判断漏洞结论正确与否。"""

import re
import json
from typing import Any

from backend.app.llm.response_metadata import ModelResponseMetadata
from .findings import FindingLedger

REPORT_VERSION = "ledger-security-v5"
BATCH_PROTOCOL = "static-security-v14-review-ledger"
CUMULATIVE_PROTOCOL = "static-security-v15-cumulative-ledger"
FULL_SOURCE_PROTOCOL = "static-security-v16-full-source-ledger"
REPORT_SECTIONS = ("结论", "已核实问题", "未确认风险", "范围与局限")
REPORT_END = "<!-- SECURITY_REPORT_END -->"
REPORT_INSTRUCTIONS = """
两组共用此契约，最终只输出完整台账 JSON，不手写 Markdown、编号、计数或结束标记。
后台按状态生成结论、已核实问题、未确认风险、范围与局限；摘要最多突出 5 项，其余独立问题仍保留，不按摘要编号评分。
同根因多影响合并，不同根因即使位于同一接口也分列；不能合并认证、上传、跨域、资源问题。
每项记录代码行为、输入到真实危险实现的链路、防护、安全影响、利用前提和引用。
behavior_status 区分观察/推断/未知；chain.description 说明输入、参数、实现与缺口。未读关键实现不能闭合链路或确认影响；not_applicable 仅用于无需传播链的本地缺陷并说明原因。
impact 仅含 status/description；confirmed_conditions（已核实条件）与 unresolved_conditions（未知/未满足条件）属于 finding 同级，不嵌入 impact；conditional 必须列未知条件。
source_supported_risk 须为 observed、链路闭合或明确不适用、影响已建立或有条件；不等于已确认远程漏洞。
unclosed_candidate 用于关键链路、实现或影响未建立；模板拼接无攻击者来源不能计入源码支持风险。
excluded 必须有 exclusion_reason；未找到调用方/输入不证明全项目不存在通路。
防护须核实：处理的是进入危险操作的值、适用于该危险上下文、发生在危险操作之前；附近的清洗不证明防护。
protections 非空时每项必须有 description、value_match/context_match/before_operation（verified/unverified）、evidence 引用数组；不能以 location/note 代替引用。
六类 coverage 必填；checked/not_applicable 须有实际检查依据，未检查填 not_checked。核对不是答案清单，不强迫报问题，已读不等于充分审查。
引用须用工具返回的完整项目相对路径及实际展示行范围，不用简称、不混实现；合法坐标不证明断言。
抛出异常不等于服务崩溃；区分格式/长度/数值越界条件，不笼统推断异常。算法未测试只记局限，除非有异常逻辑。
reviewed_ranges 仅实际已读范围，unreviewed 记未读，limitations 记静态/规则/部署等局限；未发现仅针对实际范围，零候选/零 Sink 不证明安全。
后台仅校验结构和一致性，不证明模型判断语义正确。
以下完整 JSON Schema 由后台模型生成；输出实例而非 Schema。遵守 required/枚举/长度/数量上限；禁止额外字段（含 findings_summary_note）。
无发现时 findings=[]，无防护依据时 protections=[]。检查字符串转义、括号、逗号和字段层级，只交完整对象。
"""


def _compact_schema(value: Any) -> Any:
    """去掉字符串元数据，保留同名字段定义及全部约束/引用，避免手写第二份契约。"""
    if isinstance(value, dict):
        return {key: _compact_schema(item) for key, item in value.items()
                if key not in {"title", "description"} or not isinstance(item, str)}
    if isinstance(value, list):
        return [_compact_schema(item) for item in value]
    return value


REPORT_INSTRUCTIONS += json.dumps(_compact_schema(FindingLedger.model_json_schema()), ensure_ascii=False, separators=(",", ":"))


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

"""两组共享的简洁报告契约与可审计完整性检查，不判断漏洞结论正确与否。"""

import re
import json
from typing import Any

from backend.app.llm.response_metadata import ModelResponseMetadata
from .findings import FindingLedger, LedgerValidationError

REPORT_VERSION = "ledger-security-v8"
FORMAT_RETRY_POLICY: dict[str, Any] = {"version": "ledger-consistency-retry-v2", "max_extra_requests": 1,
                      "scope": "per_run", "eligible": ["syntax", "schema", "state"],
                      "new_evidence": False, "local_json_repair": False}
BATCH_PROTOCOL = "static-security-v21-chain-boundaries"
CUMULATIVE_PROTOCOL = "static-security-v22-cumulative-boundaries"
FULL_SOURCE_PROTOCOL = "static-security-v23-full-source-boundaries"
REPORT_SECTIONS = ("结论", "已核实问题", "未确认风险", "范围与局限")
REPORT_END = "<!-- SECURITY_REPORT_END -->"
REPORT_INSTRUCTIONS = """
两组共用此契约，最终只输出完整台账 JSON，不手写 Markdown、编号、计数或结束标记。
后台按状态生成结论、已核实问题、未确认风险、范围与局限；摘要最多突出 5 项，其余独立问题仍保留，不按摘要编号评分。
同根因多影响合并，不同根因即使位于同一接口也分列；不能合并认证、上传、跨域、资源问题。
每项记录代码行为、输入到真实危险实现的链路、防护、安全影响、利用前提和引用。
behavior_status 区分观察/推断/未知；chain 必填 scope、start、end、description。端点包含 kind、description、evidence。closed 只证明所填起终点间的区间。
scope=local_segment 表示局部路径，内部函数形参不能自动当作 HTTP 等外部输入；external_to_sink 闭合必须从已核实 external_input 到 sensitive_operation，缺失关键连接保留 open。
closed 指列明路径条件下，所填端点之间的源码连接已核实，不要求每次调用都执行到终点。已核实的触发条件记 confirmed_conditions；仅有 if 不构成 chain_gap，也不意味着它过滤了输入值。条件是否满足、调用/值绑定是否存在须分别说明，不能把未验证连接当作已知条件。
未读关键实现不能闭合链路或确认最终操作。无需传播链的本地缺陷可填 not_applicable，scope 同值、start/end=null 并说明原因；不能以此规避未知的外部输入连接。
impact 含 status/scope/description，scope=local/external/unknown。局部闭合只能确认局部影响；external 的已建立/有条件影响须有外部到敏感操作的闭合链，否则外部影响填 not_established。confirmed_conditions 与 unresolved_conditions 属于 finding 同级。unresolved_conditions 每项必填 kind/description：chain_gap 是输入来源、调用、参数、实现等关键连接未知，deployment 是已建立源码机制的部署/暴露前提，security_requirement 是是否应限制该行为未知，other 是尚未分类条件。
chain_gap 必须 open；不能把未追到的 HTTP 输入标成 deployment。conditional 必须列未知条件。关键连接或安全要求未知时保留候选，不能仅因缺少认证就把可能公开的资源算作漏洞。
source_supported_risk 须为 observed、链路闭合或明确不适用、影响已建立或有条件，且 unresolved_conditions 只能包含 deployment；存在 security_requirement、chain_gap 或 other 时保留 unclosed_candidate。不等于已确认远程漏洞。
unclosed_candidate 用于关键链路、实现或影响未建立；模板拼接无攻击者来源不能计入源码支持风险。
excluded 必须有 exclusion_reason；未找到调用方/输入不证明全项目不存在通路。
防护须核实：处理的是进入危险操作的值、适用于该危险上下文、发生在危险操作之前；附近的清洗不证明防护。
protections 非空时每项必须有 description、value_match/context_match/before_operation（verified/unverified）、evidence 引用数组；不能以 location/note 代替引用。
六类 coverage 必填；checked/not_applicable 须有实际检查依据，未检查填 not_checked。核对不是答案清单，不强迫报问题，已读不等于充分审查。
引用须用工具返回的完整项目相对路径及实际展示行范围，不用简称、不混实现；合法坐标不证明断言。较早批次已展示的证据可以保留来源引用，但本轮未重放正文时不能声称本轮重新核实或升级状态；只有历史模型摘要不等于直接源码证据。
不得把按已知或可猜测名称访问推广为枚举全部资源，除非已经读取相应枚举实现。发现广度、链路核验、结论可信度分别评价，更多条目不能抵消过度断言。
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


REPORT_SHAPE = (
    "reviewed_ranges、unreviewed、limitations 必须与 coverage 同级；coverage 只含六类核对，必须先闭合，再写上述根字段。\n"
)
REPORT_INSTRUCTIONS += REPORT_SHAPE + json.dumps(_compact_schema(FindingLedger.model_json_schema()), ensure_ascii=False, separators=(",", ":"))


def format_retry_message(error: LedgerValidationError) -> str:
    """返回格式或状态一致性重提指令；不增加证据、不替模型分类或静默改写。"""
    consistency = ("核对诊断指出的相互矛盾字段；保持源码观察、独立条目、引用与未知条件。"
                   "未知前提仍成立时使用候选状态，不得删除条目/未知条件、把未知一律改成 deployment，或补造闭合链来通过校验。"
                   "只有已有证据支持时才由你明确修正原状态或分类。" if error.category == "state" else
                   "保留独立条目、判断和未知条件，不因格式错误改安全状态或编造证据。")
    return ("APPLICATION_REPORT_FORMAT_RETRY\n上次可见台账未通过格式校验，原始回复及用量已保留。"
            "现在仅重新提交完整台账 JSON，不输出解释、差异、补丁或续写片段。\n"
            "只使用原请求已经展示的证据；" + consistency +
            "不读取文件、不调用调查工具、不扩展调查。报告阶段可单独调用已提供的 submit_analysis_batch，或直接返回完整台账 JSON。\n"
            + REPORT_SHAPE + "校验诊断（不是项目证据）："
            + json.dumps({"category": error.category, "issues": error.issues}, ensure_ascii=False, separators=(",", ":")))


def report_transport_complete(metadata: ModelResponseMetadata, *, allow_tool_calls: bool = False) -> bool | None:
    """只判断传输结束状态；工具结束仅在已识别的报告提交阶段可接受。"""
    if metadata.refused or metadata.incomplete_reason or metadata.response_status in {"incomplete", "failed", "cancelled"}:
        return False
    if metadata.finish_reason in {"length", "content_filter"}:
        return False
    if metadata.finish_reason in {"tool_calls", "function_call"}:
        return allow_tool_calls
    if metadata.finish_reason == "stop" or metadata.response_status == "completed":
        return True
    return None


def assess_answer(
    answer: str, metadata: ModelResponseMetadata, *, require_report: bool,
) -> dict[str, Any]:
    """检查供应商终止状态、非空文本、章节顺序/正文和结束标记，输出独立维度。"""
    transport_complete = report_transport_complete(metadata)
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

"""两组共享的简洁报告契约与可审计完整性检查，不判断漏洞结论正确与否。"""

import re
from typing import Any

from backend.app.llm.response_metadata import ModelResponseMetadata

REPORT_VERSION = "concise-security-v3"
REPORT_SECTIONS = ("结论", "已核实问题", "未确认风险", "范围与局限")
REPORT_END = "<!-- SECURITY_REPORT_END -->"
REPORT_INSTRUCTIONS = """
两组使用完全相同的简洁报告格式：依次输出四个二级标题“## 结论”、“## 已核实问题”、
“## 未确认风险”、“## 范围与局限”。每节必须有内容，没有条目时明确写无或未确认。
优先直接回答问题；结论摘要最多突出 5 个问题，其他有证据的独立问题仍须在后续清单简要列出。
问题清单按独立根因编号，摘要引用清单编号；不合并不同根因，不重复计算同一根因。评分依据去重清单，不是摘要编号。
相同根因的多个影响合为一项并列出影响；不同根因即使位于同一接口也分别编号，不把认证、上传、跨域或资源问题笼统合并。
每个问题分别说明三个维度：代码行为（源码直接确认、推测或未知）、安全影响（已建立与未建立部分）、
利用前提（仓库调用方、输入来源、攻击者控制条件，以及未知条件）。已核实代码行为不等于已确认远程漏洞。
没有找到调用方或攻击者控制证据，不等于证明对应前提不存在。每项注明证据 [相对路径:行号或起始行-结束行]。
每项简要记录：根因、已确认的代码行为、已有证据支持的安全影响、尚未建立的利用前提、引用。
关键执行实现未读取时不能确认最终执行行为，不能同时声称完整影响链已建立；已确认行为与未知利用条件分别记录。
判断校验或净化有效时，核实处理的值是否就是进入危险操作的值、是否适用于该危险上下文、是否发生在危险操作之前。
仅有附近的清洗代码不足以确认防护成立；DOM ID 清洗、路径校验、HTML 转义与 shell 参数处理不能互相替代。
使用工具返回的完整项目相对路径；一个引用括号只包含一个文件和一个行号范围，不使用“同文件”或仅文件名简称。
提交前核对引用位置是否直接支持断言，不得混用不同文件或实现的行为。
抛出异常不等于宿主服务崩溃，除非已有调用方和异常处理证据；区分格式错误、长度错误、数值越界等触发条件，
不得笼统推断异常类型。“未发现”只针对实际检查范围；搜索未完成、文件跳过或结果省略时必须说明。
未测试算法正确性等内容放入范围与局限；只有指出具体异常逻辑或违反的条件，才能列为候选问题。
表达简洁，但不以固定篇幅删掉有证据的独立问题；不得把健壮性问题直接升级为远程漏洞。
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

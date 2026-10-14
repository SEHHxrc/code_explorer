"""共同的调查/报告阶段协议；不生成漏洞结论，不增加额外调查轮次。"""

from typing import Any

from backend.app.llm.conversation import ModelMessage
from backend.app.services.security_analysis.llm_contracts import LLM_SECURITY_SCHEMA_VERSION

PHASE_VERSION = "explicit-report-phases-v1"


def execution_policy(max_tool_rounds: int, final_report_mode: str) -> dict[str, Any]:
    """冻结两组共同的阶段版本、调查轮次与供应商收尾模式，用于审计及比较。"""
    return {"phase_version": PHASE_VERSION, "max_tool_rounds": max_tool_rounds,
            "final_report_mode": final_report_mode, "security_evidence_schema": LLM_SECURITY_SCHEMA_VERSION}


def phase_message(*, remaining_rounds: int, final: bool = False) -> ModelMessage:
    """返回本轮应用阶段说明；剩余轮次含当前请求，不等于可调用函数的次数。"""
    if final:
        text = """当前为最终报告阶段，剩余工具轮次：0。
不再读取文件，不调用任何新工具。依据当前请求中实际展示的证据完成最终回答。
没有核实的链路列入未确认风险，未读取或已省略的范围列入局限；不得声称核实未展示正文。
不得用继续调查的说明或下一步读取计划代替最终报告。若要求安全报告，必须按四段契约和结束标记提交。
此阶段说明不是新增源码或安全证据。"""
    else:
        text = f"""当前为调查阶段，剩余工具轮次：{remaining_rounds}（含当前轮）。
优先补齐影响关键结论的证据，不扩展新的非必要调查；一轮可以调用多个只读工具。
证据充分时可以直接提交最终回答；若要求安全报告，须遵守四段报告契约。
此阶段说明不是新增源码或安全证据。"""
    return ModelMessage(role="user", content="APPLICATION_STAGE\n" + text)

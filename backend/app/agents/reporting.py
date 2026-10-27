"""共同的调查/报告阶段协议；不生成漏洞结论，不增加额外调查轮次。"""

from typing import Any

from backend.app.llm.conversation import ModelMessage
from backend.app.services.security_analysis.evidence_selection import SELECTION_VERSION
from backend.app.llm.settings import pre_model_rejection_policy, get_model_transport_settings, get_model_configuration
from backend.app.experiments.findings import LEDGER_VERSION
from backend.app.agents.batching import BATCH_VERSION
from backend.app.agents.path_delivery import PATH_DELIVERY_VERSION
from backend.app.experiments.report import FORMAT_RETRY_POLICY
from backend.app.core.redaction import REDACTION_VERSION

PHASE_VERSION = "explicit-report-phases-v4-evidence-history"
BATCH_REPORT_TOOL = "submit_analysis_batch"


def phase_tools(schemas: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    """按阶段返回唯一工具允许列表；不修改注册表，未知阶段明确拒绝。"""
    if phase == "investigation":
        return [item for item in schemas if item["name"] != BATCH_REPORT_TOOL]
    if phase == "batch_report":
        return [item for item in schemas if item["name"] == BATCH_REPORT_TOOL]
    if phase == "final_report":
        return []
    raise ValueError("Unknown agent phase")


def execution_policy(max_tool_rounds: int, final_report_mode: str, *, batch_mode: bool = False, full_source: bool = False, model: str | None = None) -> dict[str, Any]:
    """冻结两组共同的阶段版本、调查轮次与供应商收尾模式，用于审计及比较。"""
    policy = {"phase_version": PHASE_VERSION, "max_tool_rounds": max_tool_rounds,
              "path_observation_version": PATH_DELIVERY_VERSION, "source_redaction": REDACTION_VERSION,
              "final_report_mode": final_report_mode, "security_evidence_schema": SELECTION_VERSION,
              "finalization_requests_max": 2 if batch_mode else 1, "findings_ledger": LEDGER_VERSION,
              "report_format": dict(FORMAT_RETRY_POLICY),
              "network_accounting": pre_model_rejection_policy(model),
              "transport": get_model_transport_settings().snapshot(get_model_configuration().provider)}
    if full_source:
        policy.update(max_tool_rounds=0, final_report_mode="single_shot_no_tools", finalization_requests_max=1,
                      evidence_delivery="identical-full-source-plus-path-summary-v2")
    if batch_mode:
        policy.update(batch_protocol=BATCH_VERSION, evidence_delivery="security-evidence-on-demand-v3-families",
                      configured_final_report_mode=final_report_mode, final_report_mode="isolated_text_no_tools",
                      batch_report_mode="batch_submit_only", report_history="observation_projection_no_native_calls",
                      investigation_budget_excludes_reports=True, batch_report_requests_per_batch=1,
                      batch_report_requests_max=max_tool_rounds, finalization_requests_max=max_tool_rounds + 1,
                      final_summary_requests_max=1,
                      initial_summary_schema=SELECTION_VERSION,
                      initial_summary_max_chars=8000, detail_page_max_chars=24000,
                      batch_report_max_chars=None, final_evidence_archive_max_chars=12000,
                      max_evidence_per_batch=3, family_browsing_selects_routes=False,
                      external_api_knowledge="separate_explicit_agent_not_in_trial")
    return policy


def phase_message(*, remaining_rounds: int, final: bool = False, batch_mode: bool = False) -> ModelMessage:
    """返回本轮应用阶段说明；剩余轮次含当前请求，不等于可调用函数的次数。"""
    if final:
        text = """当前为最终报告阶段，剩余工具轮次：0。
不再读取文件，不调用任何新工具。依据已获得证据完成最终回答。
较早批次已展示的源码可保留来源引用；本轮省略原始正文须说明不能在本轮重新核验，不等于此前从未展示。
历史模型台账本身不等于独立源码证明，不能仅凭摘要扩大路径边界或升级判断。未核实连接保留未知，从未读取的正文不能声称已核实。
不得用继续调查的说明或下一步读取计划代替最终报告。若本次要求发现台账，必须只输出完整台账 JSON；四段正文和结束标记由后台生成。
此阶段说明不是新增源码或安全证据。"""
    else:
        completion = ("本批证据足够时停止工具调用并简短说明可收尾，不在当前调查阶段生成或提交台账；应用随后单独请求报告。"
                      if batch_mode else "证据充分时可以直接提交最终回答；若要求发现台账，须遵守共同 JSON 契约，不另写摘要或计数。")
        text = f"""当前为调查阶段，剩余工具轮次：{remaining_rounds}（含当前轮）。
优先补齐影响关键结论的证据，不扩展新的非必要调查；一轮可以调用多个只读工具。
{completion}
此阶段说明不是新增源码或安全证据。"""
        if batch_mode:
            text += "\n报告请求不占调查轮次。"
            if remaining_rounds <= 2:
                text += "\n调查即将结束：优先补齐本批危险实现、来源与防护，不扩展非必要路径；未闭合链路留为未知。即使调查轮次耗尽，当前批次仍有独立报告请求。"
    return ModelMessage(role="user", content="APPLICATION_STAGE\n" + text)

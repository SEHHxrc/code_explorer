"""累计证据预算实验；不改变供应商窗口，不让搜索预览绕过源码配额。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.app.agents.contracts import AgentEvidence, ContextPacket, ToolResult
from backend.app.agents.policy import MAX_READ_LINES
from backend.app.agents.reporting import execution_policy, phase_message
from backend.app.agents.tools.arguments import ReadFileArguments, SearchManyArguments
from backend.app.agents.tools.base import AgentTool, ToolContext, ToolRegistry
from backend.app.experiments.audit.policy import digest
from backend.app.experiments.context import SECURITY_EXPERIMENT_INSTRUCTIONS
from backend.app.experiments.full_source import model_settings_hash, freeze_source, selector_hash
from backend.app.experiments.report import REPORT_INSTRUCTIONS, CUMULATIVE_PROTOCOL
from backend.app.llm.budget import BudgetPlanner, initial_message, request_payload
from backend.app.llm.registry import create_model_provider
from backend.app.services.security_analysis.evidence_selection import SecurityEvidenceSelector, SELECTION_VERSION, encode_navigation

NAVIGATION_PROTOCOL = CUMULATIVE_PROTOCOL
INSTRUCTIONS = SECURITY_EXPERIMENT_INSTRUCTIONS.split("分批调查：", 1)[0] + """
本次是累计证据预算实验，不分批，不提交中间报告。两组原始文件工具和调查轮次相同。
累计预算以 UTF-8 字节计，不是模型 Token 或平台窗口。静态事实正文一次计费，源码按首次返回的完整行计费；
重复展示同一行不重复计费。搜索命中正文也计费，路径、行号、共同指令和协议包装不计费。
固定 scope 是全部可审查范围，不代表正文已展示。只能依据实际返回的源码确认行为，静态事实不能替代源码核实。
优先闭合关键链路；读取上限及 next_start_line 以工具结果为准。预算不足的整行不返回，不得猜测。
搜索是原始文件字面检索，未命中不证明项目安全。证据足够或累计预算不足时完成最终报告，明确未读范围。
""" + REPORT_INSTRUCTIONS


def policy(max_steps: int, final_mode: str, budget: int, selection: str | None = None, *, model: str | None = None) -> dict[str, Any]:
    """返回两组相同的实验条件；静态消耗量是实际输入，不是不同的资源上限。"""
    result = {**execution_policy(max_steps, final_mode, model=model), "evidence_delivery": "cumulative-utf8-v1",
            "cumulative_evidence_bytes": budget, "source_charge": "unique-full-lines-including-newlines",
            "static_charge": "compact-json-utf8-once", "repeat_source_charge": 0}
    if selection == SELECTION_VERSION:
        result["static_selection"] = selection
    return result


def prepare_inputs(root: Path, paths: list[str], pack: dict[str, Any], condition: str, *, static_max_bytes: int = 8000) -> dict[str, dict[str, Any]]:
    """冻结同一范围与静态选择；预算只由源码体量决定，不根据实验回答调整。"""
    if condition not in {"baseline_fits", "neither_fits"}:
        raise ValueError("未知累计预算条件。")
    source, manifest = freeze_source(root, paths)
    facts = encode_navigation(SecurityEvidenceSelector(pack, set(paths)).build(max_bytes=static_max_bytes, details_available=False))
    total = sum(item["bytes"] for item in manifest.values())
    budget = total if condition == "baseline_fits" else total // 2
    if not 0 < len(facts.encode("utf-8")) < budget < total + len(facts.encode("utf-8")):
        raise ValueError("该样本不满足事前预算条件，未创建配对。")
    # TEMPORARY CONTROL GROUP / 临时对照组：唯一处理差异为附加静态事实及其自然占用的共同预算。
    return {strategy: {"_experiment_protocol": NAVIGATION_PROTOCOL, "_cumulative_input": {
        "condition": condition, "budget_bytes": budget, "source_bytes": total,
        "source_manifest": manifest, "files": source["files"],
        "static_text": facts if strategy == "security_evidence" else "",
        "selection_version": SELECTION_VERSION, "selection_ceiling_bytes": static_max_bytes,
        "static_pack_sha256": digest(pack),
        "selector_sha256": selector_hash(),
        "model_settings_hash": model_settings_hash(),
        "implementation_sha256": digest(Path(__file__).read_text(encoding="utf-8")),
    }} for strategy in ("baseline", "security_evidence")}


class EvidenceAllowance:
    """每次运行独占配额；所有源码输出共用同一账本，完整行只扣一次。"""

    def __init__(self, frozen: dict[str, Any]) -> None:
        """接收私有冻结输入；源码仅留服务器内存，不能整体放入初始上下文。"""
        self.frozen = frozen
        self.lines = {item["path"]: item["text"].splitlines(keepends=True) for item in frozen["files"]}
        self.seen: set[tuple[str, int]] = set()
        self.static_bytes = len(frozen["static_text"].encode("utf-8"))
        self.source_bytes = 0

    def snapshot(self) -> dict[str, Any]:
        """返回已分配到工具结果的用量；不冒称供应商已经看到最后一次未续接的结果。"""
        return {"budget_bytes": self.frozen["budget_bytes"], "static_bytes": self.static_bytes,
                "source_bytes_returned": self.source_bytes,
                "remaining_bytes": self.frozen["budget_bytes"] - self.static_bytes - self.source_bytes,
                "unique_lines_returned": len(self.seen), "scope_source_bytes": self.frozen["source_bytes"],
                "all_source_returned": len(self.seen) == sum(map(len, self.lines.values())),
                "accounting": "tool-result-allocation; actual-visible-coverage-is-audit-request-only"}

    def take(self, path: str, line: int) -> str | None:
        """返回完整原行或 None；不得跳过超预算行后拼出虚假的连续片段。"""
        text = self.lines[path][line - 1]
        key = (path, line)
        charge = 0 if key in self.seen else len(text.encode("utf-8"))
        if charge > self.snapshot()["remaining_bytes"]:
            return None
        self.source_bytes += charge
        self.seen.add(key)
        return text.rstrip("\r\n")


class CumulativeReadTool(AgentTool):
    """按固定范围读取，不访问未冻结文件或绕过累计配额。"""
    name = "read_file_range"
    description = f"Read complete source lines in the fixed scope, at most {MAX_READ_LINES} per call, sharing the cumulative evidence allowance. Follow next_start_line."
    arguments_model = ReadFileArguments

    def __init__(self, allowance: EvidenceAllowance) -> None:
        """绑定本次运行的共享配额；不创建独立读取额度。"""
        self.allowance = allowance

    async def execute(self, context: ToolContext, arguments: ReadFileArguments) -> ToolResult:
        """返回连续整行、真实续读位置、累计用量；范围外和反向区间明确拒绝。"""
        if arguments.path not in self.allowance.lines or arguments.end_line < arguments.start_line:
            raise ValueError("文件不在固定范围内或行范围无效。")
        lines = self.allowance.lines[arguments.path]
        start = arguments.start_line
        end = min(arguments.end_line, start + MAX_READ_LINES - 1, len(lines))
        chosen = []
        for line in range(start, end + 1):
            text = self.allowance.take(arguments.path, line)
            if text is None:
                break
            chosen.append(f"{line}: {text}")
        last = start + len(chosen) - 1
        return ToolResult(content={"path": arguments.path, "start_line": start, "end_line": last,
            "text": "\n".join(chosen), "file_total_lines": len(lines), "requested_end_line": arguments.end_line,
            "has_more_lines": last < len(lines), "next_start_line": last + 1 if last < len(lines) else None,
            "budget_limited": last < end, "allowance": self.allowance.snapshot()},
            evidence=[AgentEvidence(path=arguments.path, line=start, detail="source excerpt")] if chosen else [],
            truncated=last < min(arguments.end_line, len(lines)))


class CumulativeSearchTool(AgentTool):
    """固定小范围的多关键词字面检索；命中源码与读取使用同一配额。"""
    name = "search_project_text_many"
    description = "Search 1-8 literal queries across the frozen raw source scope; matching full lines consume the same unique-line allowance as reading."
    arguments_model = SearchManyArguments

    def __init__(self, allowance: EvidenceAllowance) -> None:
        """绑定与读取工具相同的配额，防止搜索旁路。"""
        self.allowance = allowance

    async def execute(self, context: ToolContext, arguments: SearchManyArguments) -> ToolResult:
        """全范围字面搜索，但预算或命中上限终止时不能声称结果完整。"""
        if any(not term or len(term) > 200 for term in arguments.queries):
            raise ValueError("关键词须为 1～200 字符。")
        queries = [term.casefold() for term in arguments.queries]
        hits, reason, scanned_files = [], None, 0
        for path, lines in sorted(self.allowance.lines.items()):
            for line, text in enumerate(lines, 1):
                indices = [i for i, query in enumerate(queries) if query in text.casefold()]
                if not indices:
                    continue
                selected = self.allowance.take(path, line)
                if selected is None:
                    reason = "cumulative_evidence_budget"
                    break
                hits.append({"path": path, "line": line, "text": selected, "text_truncated": False, "query_indices": indices})
                if len(hits) >= arguments.limit:
                    reason = "hit_budget"
                    break
            if reason:
                break
            scanned_files += 1
        return ToolResult(content={"queries": arguments.queries, "matches": hits,
            "scope": {"scope_files": len(self.allowance.lines), "searched_text_files": scanned_files,
                      "results_complete": reason is None, "termination_reason": reason,
                      "coverage": "explicit frozen scope only; not all repository files"},
            "allowance": self.allowance.snapshot()},
            evidence=[AgentEvidence(path=hit["path"], line=hit["line"], detail="text match") for hit in hits],
            truncated=reason is not None)


def tool_registry(frozen: dict[str, Any]) -> ToolRegistry:
    """两组模式完全相同；配额状态按运行隔离，不增加静态专用工具。"""
    allowance = EvidenceAllowance(frozen)
    return ToolRegistry([CumulativeReadTool(allowance), CumulativeSearchTool(allowance)])


class CumulativeContextBuilder:
    """初始只给共同文件清单和可选静态事实，所有源码均经相同工具选择。"""

    def build(self, *, project_id: str, question: str, artifact: dict[str, Any], model: str | None = None, budgeted: bool = True) -> ContextPacket:
        """校验冻结配置和初始传输预算，不把私有源码快照误当作模型输入。"""
        frozen = artifact["_cumulative_input"]
        if frozen["model_settings_hash"] != model_settings_hash() or frozen["implementation_sha256"] != digest(Path(__file__).read_text(encoding="utf-8")):
            raise ValueError("累计预算实验配置或实现已改变，请新建配对。")
        if frozen.get("selector_sha256") != selector_hash():
            raise ValueError("静态选择器实现已改变，请创建新配对；未发送请求。")
        summary = {"scope": frozen["source_manifest"], "allowance": EvidenceAllowance(frozen).snapshot()}
        prompt = "CUMULATIVE_EVIDENCE_SCOPE\n" + json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
        if frozen["static_text"]:
            prompt += "\nSTATIC_SECURITY_EVIDENCE\n" + frozen["static_text"]
        provider = create_model_provider(model)
        if provider is None:
            raise ValueError("实验需要配置模型，不能退回静态报告。")
        BudgetPlanner(provider.model_limits, model=provider.model).require(request_payload(provider,
            instructions=INSTRUCTIONS, messages=[initial_message(question, prompt), phase_message(remaining_rounds=8)],
            tools=tool_registry(frozen).schemas(), max_output_tokens=provider.model_limits.max_output_tokens))
        return ContextPacket(project_id=project_id, project_name=project_id, prompt_context=prompt, manifest={}, repo_map="")

"""运行内分批状态与模型报告台账；状态不代表安全结论已经验证。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from backend.app.llm.conversation import ModelMessage
from backend.app.llm.response_metadata import ModelResponseMetadata

BATCH_VERSION = "investigation-batches-v2-ledger"


@dataclass
class AnalysisBatches:
    """后端控制已选择/已报告 ID；完整报告另外通过运行事件持久化。"""

    active_ids: set[str] = field(default_factory=set)
    completed_ids: set[str] = field(default_factory=set)
    visible_ids: set[str] = field(default_factory=set)
    scopes: set[str] = field(default_factory=set)
    completed_scopes: set[str] = field(default_factory=set)
    reports: list[dict[str, Any]] = field(default_factory=list)
    frames: dict[str, dict[str, Any]] = field(default_factory=dict)
    archives: list[dict[str, Any]] = field(default_factory=list)
    metadata: ModelResponseMetadata = field(default_factory=ModelResponseMetadata)

    def select(self, evidence_id: str) -> None:
        """允许续读当前批次，不允许将已报告证据重新作为下一批处理。"""
        if evidence_id in self.completed_ids:
            raise ValueError("该证据已形成批次报告；请查看已有台账，不要重复选择。")
        if evidence_id not in self.active_ids and len(self.active_ids) >= 3:
            raise ValueError("当前批次最多选择三个证据；请先提交本批报告。")
        self.active_ids.add(evidence_id)

    def observe(self, messages: list[ModelMessage]) -> None:
        """只记录本次模型请求实际携带的工具正文；位置目录不当作源码展示。"""
        for message in messages:
            if message.get("role") != "tool":
                continue
            try:
                result = json.loads(message.get("content") or "")
            except ValueError:
                continue
            content = result.get("content") if isinstance(result, dict) else None
            if not isinstance(content, dict):
                continue
            if content.get("text") or content.get("snippets") or content.get("matches") or content.get("view") in {"detail", "coverage"}:
                self.frames[message["content"]] = result
            if content.get("view") == "detail" and content.get("items"):
                evidence_id = content.get("id")
                if evidence_id in self.active_ids:
                    self.visible_ids.add(evidence_id)
            if content.get("path") and content.get("text"):
                scope = f'{content["path"]}:{content.get("start_line", 1)}-{content.get("end_line", 1)}'
                if scope not in self.completed_scopes:
                    self.scopes.add(scope)
            for match in content.get("matches", []):
                if isinstance(match, dict) and match.get("path") and match.get("text"):
                    scope = f'{match["path"]}:{match.get("line", 1)}-{match.get("line", 1)}'
                    if scope not in self.completed_scopes:
                        self.scopes.add(scope)

    def publish(self, ledger_json: str, evidence_ids: list[str], scopes: list[str]) -> dict[str, Any]:
        """验证输出格式/供应商元数据与展示状态，再原子提交一批；不做语义验证。"""
        from backend.app.experiments.findings import parse_ledger
        metadata = self.metadata
        if len(ledger_json) > 16000:
            raise ValueError("本批报告超过 16000 字符的台账上限；未截断、未标记已报告。")
        if metadata.refused or metadata.incomplete_reason or metadata.finish_reason in {"length", "content_filter"} or metadata.response_status in {"incomplete", "failed", "cancelled"}:
            raise ValueError("本批模型输出未完整返回，不能标记为已分析。")
        if metadata.finish_reason not in {"stop", "tool_calls", "function_call"} and metadata.response_status != "completed":
            raise ValueError("本批终止状态未知，不能把有效 JSON 当作已完整返回。")
        ledger = parse_ledger(ledger_json)
        report = ledger.render()
        if len(report) > 16000:
            raise ValueError("台账生成的本批报告超过原有 16000 字符上限；未截断。")
        ids = set(evidence_ids)
        if ids != self.active_ids or not ids.issubset(self.visible_ids):
            raise ValueError("批次 ID 必须覆盖本批所选且已展示的证据；不能提交尚未读取的 ID。")
        if not scopes and not ids:
            raise ValueError("批次至少需要一个已展示的源码范围或静态证据 ID。")
        if not set(scopes).issubset(self.scopes):
            raise ValueError("批次范围包含本轮尚未展示的源码路径。")
        batch = {"batch_id": len(self.reports) + 1, "report": report, "evidence_ids": sorted(ids),
                 "ledger": ledger.model_dump(), "scopes": sorted(set(scopes)), "status": "reported", "semantic_coverage": "not_proven"}
        self.reports.append(batch)
        self.archives.append({"batch_id": batch["batch_id"], "observations": list(self.frames.values())})
        self.frames.clear()
        self.completed_ids.update(ids)
        self.completed_scopes.update(scopes)
        self.active_ids.clear()
        self.visible_ids.clear()
        self.scopes.clear()
        return batch

    def message(self) -> ModelMessage:
        """每轮携带已报告台账；原文属于模型输出，不升级成静态事实。"""
        state = {"version": BATCH_VERSION, "active_ids": sorted(self.active_ids),
                 "completed_ids": sorted(self.completed_ids), "reported_scopes": sorted(self.completed_scopes),
                 "shown_scopes": sorted(self.scopes),
                 "reports": [{key: value for key, value in report.items() if key != "report"} for report in self.reports],
                 "semantics": "reported 表示已输出批次报告，非漏洞已确认。历史报告是模型观点，不能替代源码或提高确认等级。"}
        return ModelMessage(role="user", content="APPLICATION_BATCH_STATE\n" + json.dumps(state, ensure_ascii=False, separators=(",", ":")))

    def evidence_message(self, max_chars: int = 12000) -> ModelMessage:
        """汇总仅携带此前实际展示的完整观察，不拼接跨批调用 ID 或供应商推理状态。"""
        rows = [{"batch_id": archive["batch_id"], "result": observation}
                for archive in self.archives for observation in archive["observations"]]
        envelope: dict[str, Any] = {"kind": "batch_evidence_archive", "observations": [], "omitted_observations": len(rows),
                                   "limitation": "仅保留实际展示过的完整观察；省略正文不能直接核验；批次报告本身不是源码事实。"}
        for row in rows:
            trial = {**envelope, "observations": [*envelope["observations"], row],
                     "omitted_observations": envelope["omitted_observations"] - 1}
            if len(json.dumps(trial, ensure_ascii=False, separators=(",", ":"))) <= max_chars:
                envelope = trial
        return ModelMessage(role="user", content="APPLICATION_BATCH_EVIDENCE\n" + json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))

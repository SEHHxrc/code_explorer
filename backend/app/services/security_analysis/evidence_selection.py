"""路径摘要的唯一整合与排序入口；不读取源码、不补造链路、不生成漏洞结论。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from .contracts import SecurityCandidate, SecurityEvidencePack, StaticSecurityFact

SELECTION_VERSION = "security-path-summary-v2"
PRIORITIES = {"propagation_path": 1, "structural_lead": 2, "operation_lead": 3}


def encode_navigation(value: Any) -> str:
    """返回完整紧凑 JSON；模型交付与 UTF-8 预算使用同一表示。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class SecurityEvidenceSelector:
    """把既有候选与未配对操作转换为有优先级的路径目录，供所有初始摘要复用。"""

    def __init__(self, raw_pack: dict[str, Any] | SecurityEvidencePack, paths: set[str] | None = None) -> None:
        """验证实体身份；可限定固定审查范围，默认覆盖原产物位置，不访问目标项目。"""
        self.pack = raw_pack if isinstance(raw_pack, SecurityEvidencePack) else SecurityEvidencePack.model_validate(raw_pack)
        self.available = bool(raw_pack) if isinstance(raw_pack, dict) else True
        if (any(key != item.fact_id for key, item in self.pack.facts.items())
                or any(key != item.flow_id for key, item in self.pack.dataflows.items())
                or any(key != item.edge_id for key, item in self.pack.call_edges.items())
                or len({item.candidate_id for item in self.pack.candidates}) != len(self.pack.candidates)):
            raise ValueError("静态实体 ID 不一致，不能建立路径引用。")
        self.scope = paths
        self.facts = {key: item for key, item in self.pack.facts.items() if paths is None or item.location.path in paths}
        self.candidates = {item.candidate_id: item for item in self.pack.candidates
                           if item.source_fact_id in self.facts and item.sink_fact_id in self.facts}
        self.unavailable = sum(item.source_fact_id not in self.pack.facts or item.sink_fact_id not in self.pack.facts
                               for item in self.pack.candidates)
        self.snapshot = hashlib.sha256(json.dumps(self.pack.model_dump(mode="json"), ensure_ascii=False,
                                                 sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.items = [self._candidate(item) for item in self.candidates.values()]
        paired = {item.sink_fact_id for item in self.candidates.values()}
        self.items.extend(self._operation(item) for key, item in self.facts.items()
                          if item.fact_kind == "sink" and key not in paired)
        if len({item["id"] for item in self.items}) != len(self.items) or any(item["id"] == "_coverage" for item in self.items):
            raise ValueError("路径目录 ID 冲突或占用全局详情保留 ID。")
        self.items = self._ranked(self.items)

    @staticmethod
    def locator(fact: StaticSecurityFact) -> dict[str, Any]:
        """保留真实行列及规则角色；信任类别不证明攻击者控制，API 名不证明类型绑定。"""
        role = {key: value for key, value in (fact.metadata.get("value_flow") or {}).items()
                if value not in (None, "none", [], {})}
        return {"id": fact.fact_id, "name": fact.name, "symbol": fact.symbol,
                **fact.location.model_dump(exclude_none=True, exclude={"location_id"}),
                "trust_class": fact.trust_class, "provenance": fact.provenance,
                **({"parameter_roles": role} if role else {}),
                **({"shell": fact.metadata["shell"]} if "shell" in fact.metadata else {})}

    def _operation(self, fact: StaticSecurityFact) -> dict[str, Any]:
        """单点操作不是传播路径；保留原事实 ID，便于按需获取已有详情。"""
        return {"id": fact.fact_id, "kind": "operation_lead", "priority": 3,
                "priority_reason": "operation_without_saved_source_path", "category": fact.category,
                "rule_id": fact.rule_id, "rule_severity": fact.severity,
                "relation": "single_operation", "sink": self.locator(fact),
                "path_status": "not_established", "step_count": 0, "confidence": "not_reported",
                "value_flow": "not_analyzed", "sequence_delivery": "not_available"}

    def _candidate(self, candidate: SecurityCandidate) -> dict[str, Any]:
        """区分传播、结构及未建立关系；不会从共现端点补造调用或参数绑定。"""
        source, sink = self.facts[candidate.source_fact_id], self.facts[candidate.sink_fact_id]
        flow = self.pack.dataflows.get(candidate.dataflow_id or "")
        if flow and (flow.source_fact_id, flow.sink_fact_id) != (source.fact_id, sink.fact_id):
            raise ValueError("候选与值流端点不一致，不能绑定为同一路径。")
        if source.fact_kind != "source" or sink.fact_kind != "sink":
            raise ValueError("候选端点角色与事实不一致。")
        edges = [self.pack.call_edges[key] for key in candidate.call_edge_ids if key in self.pack.call_edges]
        missing = len(edges) != len(candidate.call_edge_ids) or bool(candidate.dataflow_id and (not flow or not flow.steps))
        scope_gap = self.scope is not None and (
            any(step.location.path not in self.scope for step in flow.steps) if flow else
            any(edge.callsite and edge.callsite.path not in self.scope for edge in edges))
        if edges and not flow:
            missing = missing or edges[0].source != source.symbol or edges[-1].target != sink.symbol or any(
                left.target != right.source for left, right in zip(edges, edges[1:]))
        relation = (("interprocedural_dataflow" if flow.scope == "interprocedural" else "intra_procedural_dataflow") if flow and flow.steps else "structural_call_path" if edges else
                    "same_function_only" if source.symbol == sink.symbol else "unresolved")
        kind = "propagation_path" if flow and flow.steps else "structural_lead" if edges else "operation_lead"
        return {"id": candidate.candidate_id, "kind": kind, "priority": PRIORITIES[kind],
                "priority_reason": "modelled_value_path" if kind == "propagation_path" else relation,
                "category": candidate.category, "rule_id": candidate.rule_id, "rule_severity": candidate.severity,
                "source": self.locator(source), "sink": self.locator(sink), "relation": relation,
                "path_status": "outside_scope" if scope_gap else "incomplete" if missing or (flow and flow.truncated) or any(edge.truncated for edge in edges) else (
                    "bounded_model_path" if flow or edges else "not_established"),
                "step_count": len(flow.steps) if flow else len(edges),
                "confidence": candidate.confidence, "value_flow": flow.status if flow and flow.steps else "not_analyzed",
                "unresolved_count": len(candidate.unresolved_boundaries) + (len(flow.unresolved) if flow else 0),
                "analysis_truncated": candidate.truncated, "sequence_delivery": "detail_only" if (flow and flow.steps) or edges else "not_available"}

    @staticmethod
    def _ranked(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """传播优先，其次结构、操作；同级危险上下文/规则等级优先，并分散目标调用点。"""
        ordered = sorted(items, key=lambda item: (item["priority"], item["sink"].get("shell") is not True,
            {"high": 0, "medium": 1, "low": 2, "informational": 3}[item["rule_severity"]],
            item["sink"]["path"], item["sink"]["line"], item["sink"].get("column", 0), item["id"]))
        result = []
        for priority in (1, 2, 3):
            first, rest, seen = [], [], set()
            for item in ordered:
                if item["priority"] != priority:
                    continue
                key = (item["sink"]["path"], item["sink"]["line"], item["sink"].get("column"), item["rule_id"])
                (rest if key in seen else first).append(item)
                seen.add(key)
            result.extend([*first, *rest])
        return result

    def rows(self, *, excluded: set[str] | None = None) -> list[dict[str, Any]]:
        """返回同一优先级目录；排除已报告 ID 后不把其 Sink 重新生成为操作线索。"""
        return [dict(item) for item in self.items if item["id"] not in (excluded or set())]

    def sequence(self, evidence_id: str) -> list[dict[str, Any]]:
        """仅展开原有步骤/调用边，不含表达式正文；范围外路径整条不交付，不抽样。"""
        candidate = self.candidates.get(evidence_id)
        if candidate is None:
            return []
        row = next(item for item in self.items if item["id"] == evidence_id)
        if row["path_status"] == "outside_scope":
            return []
        flow = self.pack.dataflows.get(candidate.dataflow_id or "")
        if flow:
            return [{"order": step.order, "kind": step.kind, "symbol": step.symbol,
                     **step.location.model_dump(exclude_none=True, exclude={"location_id"}),
                     "uses": step.input_names, "defines": step.output_names,
                     "certainty": step.certainty, "provenance": step.provenance} for step in flow.steps]
        return [self.pack.call_edges[key].model_dump(mode="json", exclude_none=True)
                for key in candidate.call_edge_ids if key in self.pack.call_edges]

    def build(self, *, max_bytes: int = 8000, excluded: set[str] | None = None, max_items: int = 8,
              details_available: bool = True) -> dict[str, Any]:
        """优先交付整条已有路径；超预算降为明确定位，按实际调用能力标记能否继续取详情。"""
        rows = self.rows(excluded=excluded)
        result: dict[str, Any] = {"schema_version": SELECTION_VERSION, "snapshot": self.snapshot, "available": self.available,
            "items": [], "total_matching": len(rows), "omitted_paths": len(rows), "has_more": bool(rows),
            "path_kind_counts": dict(sorted(Counter(row["kind"] for row in rows).items())),
            "details_available": details_available, **({"coverage_details_id": "_coverage"} if details_available else {}),
            "unavailable_candidates": self.unavailable,
            "outside_scope_candidates": len(self.pack.candidates) - len(self.candidates) - self.unavailable,
            "semantics": "priority是核验顺序非漏洞概率：1=已有模型传播，2=结构线索，3=操作线索；同级先覆盖不同目标，再按shell/规则等级排序；"
                         "bounded_model_path仅指保留的静态关系，非全路径/运行可行/可利用证明；"
                         "sequence_delivery=detail_only表示步骤未展开、可按ID读flow；not_delivered表示本条件无静态详情工具且步骤未交付；"
                         "API/参数角色待核实；目录无源码、无防护结论；完整覆盖/局限未包含，零条目不证明安全。"}

        def fits(proposal: dict[str, Any]) -> bool:
            """所有头部、计数和路径都按完整 UTF-8 JSON 计费，不截断字段。"""
            return len(encode_navigation(proposal).encode("utf-8")) <= max_bytes

        if max_bytes < 1000 or not fits(result):
            raise ValueError("应用预算无法容纳完整路径摘要头；未截断。")
        for row in rows:
            if len(result["items"]) >= max_items:
                break
            if row["sequence_delivery"] == "detail_only" and not details_available:
                row = {**row, "sequence_delivery": "not_delivered"}
            proposal = {**result, "items": [*result["items"], row],
                        "omitted_paths": len(rows) - len(result["items"]) - 1,
                        "has_more": len(rows) > len(result["items"]) + 1}
            sequence = self.sequence(row["id"]) if row["step_count"] else []
            if sequence:
                expanded = {**proposal, "items": [*result["items"], {**row, "sequence": sequence,
                            "sequence_delivery": "inline_all_retained_steps"}]}
                if fits(expanded):
                    result = expanded
                    continue
            if fits(proposal):
                result = proposal
            else:
                if not details_available:
                    break
                # 极长定位也可按 ID 续查，不让名称或长路径挤掉高优先级 ID。
                compact = {key: value for key, value in row.items() if key not in {"source", "sink"}}
                compact.update(locators_delivery="detail_only", sink_ref=row["sink"]["id"])
                if "source" in row:
                    compact["source_ref"] = row["source"]["id"]
                proposal["items"] = [*result["items"], compact]
                if not fits(proposal):
                    break
                result = proposal
        return result

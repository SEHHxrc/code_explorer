"""只读证据目录与按 ID 分页；不重新扫描，不生成漏洞结论。"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from .evidence_selection import SecurityEvidenceSelector, SELECTION_VERSION, encode_navigation

DELIVERY_VERSION = SELECTION_VERSION


class SecurityEvidenceCatalog:
    """一次验证持久化证据，复用已有 ID 和共享片段注册表。"""

    def __init__(self, raw: dict[str, Any] | None) -> None:
        """冻结本目录快照；索引只包含实际保留的候选和未配对规则观察。"""
        self.available = isinstance(raw, dict) and bool(raw)
        self.selector = SecurityEvidenceSelector(raw or {})
        self.pack = self.selector.pack
        self.snapshot = self.selector.snapshot
        self.candidates = self.selector.candidates
        paired = {key for item in self.candidates.values() for key in (item.source_fact_id, item.sink_fact_id)}
        self.observations = {key: item for key, item in self.pack.facts.items() if key not in paired}
        if "_coverage" in self.candidates or "_coverage" in self.pack.facts or self.candidates.keys() & self.observations.keys():
            raise ValueError("证据 ID 存在歧义或占用覆盖信息保留 ID。")
        self.rows_by_id = {item["id"]: item for item in self.selector.items}

    def search(
        self, *, query: str = "", category: str = "", path: str = "", trust_class: str = "",
        excluded: set[str] | None = None, cursor: str = "", limit: int = 10,
        path_group_id: str = "",
    ) -> dict[str, Any]:
        """按元数据字面筛选；游标绑定快照、条件与排除集合，不用返回条数猜测进度。"""
        excluded = excluded or set()
        filters = (query.casefold(), category, path, trust_class, sorted(excluded), path_group_id)
        identity = hashlib.sha256(json.dumps((DELIVERY_VERSION, self.snapshot, filters), ensure_ascii=False).encode()).hexdigest()
        offset = 0
        if cursor:
            try:
                frozen, offset = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if frozen != identity or type(offset) is not int or offset < 0:
                    raise ValueError("stale cursor")
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValueError("证据目录或查询状态已变化，请用空 cursor 重新检索。") from exc
        if path_group_id and path_group_id not in self.selector.group_members:
            raise ValueError("当前快照中不存在该路径族。")
        rows = ([row for row in self.selector.rows(excluded=excluded) if row.get("path_group_id") == path_group_id]
                if path_group_id else self.selector.groups(excluded=excluded))
        matches = []
        for row in rows:
            locations = [row[key] for key in ("source", "sink", "location") if key in row]
            if category and row["category"] != category:
                continue
            if path and not any(item["path"].startswith(path) for item in locations):
                continue
            if trust_class and not any(item["trust_class"] == trust_class for item in locations):
                continue
            if query and query.casefold() not in json.dumps(row, ensure_ascii=False).casefold():
                continue
            matches.append(row)
        end = min(len(matches), offset + max(1, limit))
        return {"kind": "static_security_evidence", "view": "catalog", "delivery_version": DELIVERY_VERSION,
                "available": self.available, "snapshot": self.snapshot,
                "items": [self.selector.route(row) for row in matches[offset:end]] if path_group_id else matches[offset:end],
                "path_group_id": path_group_id or None,
                "total_matching": len(matches), "excluded_count": len(excluded),
                "next_cursor": base64.urlsafe_b64encode(json.dumps((identity, end)).encode()).decode() if end < len(matches) else "",
                "has_more": end < len(matches), "query_semantics": "literal_metadata_filter_not_semantic_search"}

    def summary(self, *, excluded: set[str] | None = None, max_chars: int = 8000) -> str:
        """替换旧覆盖/观察摘要；只用公共路径选择器，按 UTF-8 字节保守满足字符上限。"""
        return encode_navigation(self.selector.build(max_bytes=max_chars, excluded=excluded))

    def detail(self, evidence_id: str, *, section: str = "overview", offset: int = 0, limit: int = 5,
               excluded: set[str] | None = None) -> dict[str, Any]:
        """解引用当前快照内 ID；路径保留原顺序，源码和相关事实独立完整条目分页。"""
        if evidence_id in self.selector.group_members:
            if section != "overview":
                raise ValueError("路径族仅提供 overview 导航；请使用族内路线 ID 读取具体 section。")
            members = [row for row in self.selector.group_members[evidence_id] if row["id"] not in (excluded or set())]
            selected = members[offset:offset + max(1, min(limit, 10))]
            result = {"kind": "static_security_evidence", "view": "group", "delivery_version": DELIVERY_VERSION,
                      "snapshot": self.snapshot, "id": evidence_id, "group": self.selector.group(evidence_id, excluded=excluded),
                      "items": [self.selector.route(row) for row in selected], "snippets": {},
                      "offset": offset, "total_items": len(members), "next_offset": offset + len(selected),
                      "has_more": offset + len(selected) < len(members),
                      "semantics": "族目录是导航，非已读源码；步骤通过路线ID分页，via仅为有省略标记的定位概览。"}
            if len(self._encode(result)) > 24000:
                raise ValueError("路径族导航页过大，请缩小 limit；未截断条目。")
            return result
        if evidence_id == "_coverage":
            values = [{"coverage": self.pack.coverage.model_dump(), "completed": self.available and self.pack.completed,
                       "languages": self.pack.languages_analyzed, "unsupported_languages": self.pack.unsupported_languages,
                       "rule_packs": self.pack.rule_packs,
                       "flow_diagnostics": {key: value for key, value in self.pack.flow_diagnostics.items() if key not in {"items", "value_backends"}}}] if section == "overview" else (
                [{"kind": "limitation", "description": item} for item in self.pack.limitations] +
                [{"kind": "propagation_frontier", **item} for item in self.pack.flow_diagnostics.get("items", [])] +
                [{"kind": "scan_failure", **{key: item[key] for key in (
                    "path", "line", "language", "reason", "file_count", "callsite_id"
                ) if item.get(key) not in (None, "")}} for item in self.pack.scan_failures]
                if section == "unresolved" else self.pack.rule_coverage if section == "protections" else
                [{"method_id": key, **item} for key, item in self.pack.flow_diagnostics.get("value_backends", {}).items()]
                if section == "backends" else [])
            selected = values[offset:offset + max(1, min(limit, 10))]
            result = {"kind": "static_security_evidence", "view": "coverage", "snapshot": self.snapshot,
                      "id": evidence_id, "section": section, "items": selected, "snippets": {},
                      "offset": offset, "next_offset": offset + len(selected), "total_items": len(values),
                      "has_more": offset + len(selected) < len(values),
                      "semantics": "全局元数据；unresolved=局限、已保留的传播诊断和扫描失败，非全部断点；诊断完整事件计数与省略量见 overview。protections=规则覆盖，非防护已成立。"}
            if len(self._encode(result)) > 24000:
                raise ValueError("覆盖详情页过大，请缩小 limit；未截断。")
            return result
        candidate = self.candidates.get(evidence_id)
        fact = self.observations.get(evidence_id)
        if candidate is None and fact is None:
            raise ValueError("当前项目证据快照中不存在该 ID。")
        snippets: dict[str, Any] = {}
        if section == "overview":
            values = [{**self.rows_by_id[evidence_id], "candidate": candidate.model_dump(exclude_none=True),
                       "endpoint_context": {key: {name: self.pack.facts[key].metadata[name] for name in (
                           "framework", "route_method", "route_path", "annotation", "shell", "query_shape",
                           "parameter_argument_present", "mode", "api_resolution", "visible_headers", "binding_certainty",
                           "resolution_method", "api_binding_callsite_ids"
                       ) if name in self.pack.facts[key].metadata} for key in (candidate.source_fact_id, candidate.sink_fact_id)},
                       "preconditions": candidate.preconditions,
                       "checks_association": "functions_on_route_not_proven_value_binding_or_effectiveness"}
                      if candidate else self.rows_by_id.get(evidence_id, fact.model_dump(exclude={"metadata"}))]
        elif section == "conditions":
            flow = self.pack.dataflows.get(candidate.dataflow_id or "") if candidate else None
            values = [item for index, proof in enumerate(flow.path_conditions if flow else [])
                      for item in [{"proof_index": index, "kind": "summary", **proof.model_dump(exclude={"conditions", "segments", "cfg_edge_ids", "waypoint_node_ids"})},
                                   *({"proof_index": index, "kind": "waypoint", "order": order, "node_id": key} for order, key in enumerate(proof.waypoint_node_ids)),
                                   *({"proof_index": index, "kind": "cfg_edge", "order": order, "edge_id": key} for order, key in enumerate(proof.cfg_edge_ids)),
                                   *(row for segment_index, segment in enumerate(proof.segments) for row in [
                                       {"proof_index": index, "kind": "segment", "segment_index": segment_index, **segment.model_dump(exclude={"bindings"})},
                                       *({"proof_index": index, "kind": "binding", "segment_index": segment_index, **binding.model_dump()} for binding in segment.bindings)]),
                                   *({"proof_index": index, "kind": "branch", **constraint.model_dump()} for constraint in proof.conditions)]]
        elif section == "flow":
            flow = self.pack.dataflows.get(candidate.dataflow_id or "") if candidate else None
            values = [item.model_dump() for item in flow.steps] if flow else [
                self.pack.call_edges[key].model_dump() for key in candidate.call_edge_ids if key in self.pack.call_edges
            ] if candidate else []
        elif section == "snippets":
            keys = candidate.snippet_ids if candidate else [str(fact.metadata.get("snippet_id") or "")]
            values = [{"snippet_id": key} for key in keys if key in self.pack.snippets]
        elif section == "protections":
            keys = [*candidate.guard_fact_ids, *candidate.sanitizer_fact_ids] if candidate else []
            values = [self.pack.facts[key].model_dump() for key in keys if key in self.pack.facts]
        elif section == "unresolved":
            flow = self.pack.dataflows.get(candidate.dataflow_id or "") if candidate else None
            values = [*candidate.unresolved_boundaries,
                      *({"kind": "limitation", "description": text} for text in dict.fromkeys([
                          *candidate.limitations, *(flow.unresolved if flow else [])]))] if candidate else list(fact.metadata.get("limitations") or [])
        else:
            raise ValueError("Unknown evidence section")
        selected = values[offset:offset + max(1, min(limit, 10))]
        if section == "snippets":
            snippets = {item["snippet_id"]: self.pack.snippets[item["snippet_id"]].model_dump() for item in selected}
        counts = {"flow": len(self.pack.dataflows[candidate.dataflow_id].steps) if candidate and candidate.dataflow_id in self.pack.dataflows else sum(key in self.pack.call_edges for key in candidate.call_edge_ids) if candidate else 0,
                  "snippets": len(candidate.snippet_ids) if candidate else int(bool(fact.metadata.get("snippet_id"))),
                  "protections": len(candidate.guard_fact_ids) + len(candidate.sanitizer_fact_ids) if candidate else 0,
                  "unresolved": (len(candidate.unresolved_boundaries) + len(set(candidate.limitations) | set(self.pack.dataflows[candidate.dataflow_id].unresolved if candidate.dataflow_id in self.pack.dataflows else [])))
                  if candidate else len(fact.metadata.get("limitations") or [])}
        flow = self.pack.dataflows.get(candidate.dataflow_id or "") if candidate else None
        counts["conditions"] = sum(1 + len(proof.conditions) + len(proof.waypoint_node_ids) + len(proof.cfg_edge_ids) +
            sum(1 + len(segment.bindings) for segment in proof.segments) for proof in flow.path_conditions) if flow else 0
        result = {"kind": "static_security_evidence", "view": "detail", "delivery_version": DELIVERY_VERSION,
                  "snapshot": self.snapshot, "id": evidence_id, "section": section, "items": selected,
                  "snippets": snippets, "section_counts": counts, "total_items": len(values),
                  "offset": offset, "next_offset": offset + len(selected), "has_more": offset + len(selected) < len(values),
                  "claim": self.rows_by_id.get(evidence_id, {}).get("relation", "rule_match_only"),
                  "limitation": "本次只展示所选 section；结构路径不等于污点传播，条件核验仅相对于模型，匹配防护不证明防护成立；flow.expression 是分析表示，不保证为源码原文。"}
        if len(self._encode(result)) > 24000:
            raise ValueError("该页超过静态详情交付预算，请将 limit 减为 1；超大单条请按位置读取原始源码，未返回的条目未展示。")
        return result

    @staticmethod
    def _encode(value: Any) -> str:
        """统一使用完整紧凑 JSON，不切割字符串或源码行。"""
        return encode_navigation(value)

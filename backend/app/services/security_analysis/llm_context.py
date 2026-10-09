"""把完整安全分析产物投影为高信息密度 LLM 证据。"""

from __future__ import annotations

import json
import re
from typing import Any

from .contracts import (
    CodeSnippet,
    DataFlowEvidence,
    SecurityCandidate,
    SecurityEvidencePack,
    StaticSecurityFact,
)
from .llm_contracts import (
    LLMFlowStep,
    LLMRelatedFact,
    LLMSecurityEndpoint,
    LLMSecurityEvidenceEnvelope,
    LLMSecurityFinding,
)


class SecurityEvidencePromptBuilder:
    """按问题相关性和字符预算生成始终有效的紧凑 JSON。"""

    def build(
        self,
        raw_pack: SecurityEvidencePack | dict[str, Any],
        *,
        question: str = "",
        max_chars: int = 30_000,
        offset: int = 0,
        max_findings: int = 20,
    ) -> LLMSecurityEvidenceEnvelope:
        """选择并展开候选；预算不足时整条省略而不截断 JSON。"""
        pack = (
            raw_pack if isinstance(raw_pack, SecurityEvidencePack)
            else SecurityEvidencePack.model_validate(raw_pack)
        )
        candidates = self._ranked_candidates(pack, question)
        selected = candidates[max(0, offset):]
        envelope = self._empty_envelope(pack)
        for candidate in selected[:max(1, max_findings)]:
            finding = self._finding(pack, candidate)
            trial = envelope.model_copy(update={
                "findings": envelope.findings + [finding],
            })
            if self._size(trial) > max(2_000, max_chars):
                envelope.truncated = True
                break
            envelope.findings.append(finding)
        consumed = max(0, offset) + len(envelope.findings)
        envelope.omitted_findings = max(0, len(candidates) - consumed)
        envelope.truncated = envelope.truncated or envelope.omitted_findings > 0
        return envelope

    def render(
        self,
        raw_pack: SecurityEvidencePack | dict[str, Any],
        *,
        question: str = "",
        max_chars: int = 30_000,
        offset: int = 0,
        max_findings: int = 20,
    ) -> str:
        """返回可直接放入 Prompt 且不会产生半截 JSON 的紧凑文本。"""
        envelope = self.build(
            raw_pack,
            question=question,
            max_chars=max_chars,
            offset=offset,
            max_findings=max_findings,
        )
        return json.dumps(
            envelope.model_dump(exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _finding(
        self,
        pack: SecurityEvidencePack,
        candidate: SecurityCandidate,
    ) -> LLMSecurityFinding:
        """解引用内部注册表并构造自包含候选。"""
        source = pack.facts[candidate.source_fact_id]
        sink = pack.facts[candidate.sink_fact_id]
        dataflow = pack.dataflows.get(candidate.dataflow_id or "")
        flow = (
            self._dataflow_steps(dataflow)
            if dataflow is not None
            else self._structural_steps(pack, candidate)
        )
        return LLMSecurityFinding(
            finding_id=candidate.candidate_id,
            fingerprint=candidate.candidate_id,
            rule_id=candidate.rule_id,
            cwe=candidate.cwe,
            category=candidate.category,
            severity=candidate.severity,
            confidence=candidate.confidence,
            confidence_dimensions={
                **({"shared_state_binding": "inferred_may"} if dataflow is not None and dataflow.value_boundary_ids else {}),
                "source_rule_match": source.metadata.get("rule_match_confidence", "high"),
                "sink_rule_match": sink.metadata.get("rule_match_confidence", "high"),
                "data_dependency": (
                    dataflow.confidence if dataflow is not None else "not_analyzed"
                ),
                "control_flow": (
                    "cfg_used_without_path_feasibility_proof"
                    if dataflow is not None else "not_analyzed"
                ),
                "structural_path": candidate.confidence,
            },
            claim=(
                (
                    "interprocedural_dataflow"
                    if dataflow.scope == "interprocedural"
                    else "intra_procedural_dataflow"
                ) if dataflow is not None
                else "structural_reachability_only"
            ),
            taint_status=candidate.taint_status,
            source=self._endpoint(source, "source"),
            sink=self._endpoint(sink, "sink"),
            flow=flow,
            guards=self._related(pack, candidate.guard_fact_ids, "guard"),
            sanitizers=self._related(pack, candidate.sanitizer_fact_ids, "sanitizer"),
            snippets=self._selected_snippets(pack, candidate),
            preconditions=candidate.preconditions,
            unresolved=[
                self._compact_unresolved(item)
                for item in candidate.unresolved_boundaries[:12]
            ],
            limitations=candidate.limitations,
            truncated=candidate.truncated or len(candidate.unresolved_boundaries) > 12,
        )

    @staticmethod
    def _empty_envelope(pack: SecurityEvidencePack) -> LLMSecurityEvidenceEnvelope:
        """构造只包含扫描范围和真实性语义的证据头。"""
        coverage_gaps = [
            SecurityEvidencePromptBuilder._compact_scan_failure(item)
            for item in pack.scan_failures[:50]
        ]
        return LLMSecurityEvidenceEnvelope(
            source_schema_version=pack.schema_version,
            analysis={
                "analysis_kind": pack.analysis_kind,
                "completed": pack.completed,
                "languages": pack.languages_analyzed,
                "unsupported_languages": pack.unsupported_languages,
                "rule_packs": pack.rule_packs,
                "dataflow_scope": {
                    "kind": "cfg_value_flow_with_bounded_calls",
                    "languages": pack.languages_analyzed,
                    "control_flow": "cfg_used_without_path_feasibility_proof",
                    "interprocedural": "resolved_call_arguments_to_parameters",
                    **({"shared_state": "bounded_inferred_may_value_boundaries"} if any(item.value_boundary_ids for item in pack.dataflows.values()) else {}),
                },
                "coverage": pack.coverage.model_dump(),
                "coverage_gaps": coverage_gaps,
                "omitted_coverage_gaps": max(
                    0, len(pack.scan_failures) - len(coverage_gaps),
                ),
                "global_limitations": pack.limitations,
            },
            claim_semantics={
                "observed": "源码中直接观察到的位置、表达式或声明",
                "inferred": "确定性静态分析推导出的关系，受解析精度限制",
                "structural_reachability_only": "仅函数调用结构可达，未证明值传播",
                "intra_procedural_dataflow": "公共函数图中的变量感知值流已连接 Source 与 Sink 实参",
                "interprocedural_dataflow": "公共函数图值流结合已解析调用或有静态依据的有界状态绑定，连接 Source 与 Sink；不证明运行时路径可行",
            },
            findings=[],
        )

    @staticmethod
    def _endpoint(fact: StaticSecurityFact, role: str) -> LLMSecurityEndpoint:
        """保留端点角色、位置、信任类别和参数角色。"""
        return LLMSecurityEndpoint(
            fact_id=fact.fact_id,
            rule_id=fact.rule_id,
            role="source" if role == "source" else "sink",
            category=fact.category,
            name=fact.name,
            symbol=fact.symbol,
            location=fact.location,
            trust_class=fact.trust_class,
            provenance=fact.provenance,
            value_role=fact.metadata.get("value_flow") or {},
            context={
                key: fact.metadata[key]
                for key in (
                    "framework", "route_method", "route_path", "annotation",
                    "shell", "query_shape", "parameter_argument_present", "mode",
                    "api_resolution", "visible_headers",
                    "binding_certainty",
                )
                if key in fact.metadata
            },
        )

    @staticmethod
    def _selected_snippets(
        pack: SecurityEvidencePack,
        candidate: SecurityCandidate,
    ) -> list[CodeSnippet]:
        """保留 Source、Sink 和最多两个 Guard 片段；中间步骤使用表达式。"""
        selected = []
        guard_count = 0
        for snippet_id in candidate.snippet_ids:
            snippet = pack.snippets.get(snippet_id)
            if snippet is None or snippet.role == "intermediate":
                continue
            if snippet.role == "guard":
                if guard_count >= 2:
                    continue
                guard_count += 1
            selected.append(snippet)
            if len(selected) >= 4:
                break
        return selected

    @staticmethod
    def _dataflow_steps(dataflow: DataFlowEvidence) -> list[LLMFlowStep]:
        """保留端点之间的赋值、调用和返回边界；Source/Sink 已单独呈现。"""
        steps = [
            item for item in dataflow.steps
            if item.kind in {"assignment", "call", "return"}
        ]
        selected = SecurityEvidencePromptBuilder._sample_steps(steps, 10)
        return [LLMFlowStep(
            order=index,
            kind=item.kind,
            location=item.location,
            symbol=item.symbol,
            expression=item.expression,
            input_names=item.input_names,
            output_names=item.output_names,
            certainty=item.certainty,
            importance=item.importance,
            provenance=item.provenance,
        ) for index, item in enumerate(selected)]

    @staticmethod
    def _structural_steps(
        pack: SecurityEvidencePack,
        candidate: SecurityCandidate,
    ) -> list[LLMFlowStep]:
        """将已有调用边投影为结构步骤，不伪装成值传播。"""
        edges = [
            pack.call_edges[item]
            for item in candidate.call_edge_ids
            if item in pack.call_edges
        ]
        selected = SecurityEvidencePromptBuilder._sample_steps(edges, 10)
        return [LLMFlowStep(
            order=index,
            kind="call",
            location=edge.callsite,
            symbol=edge.source,
            expression=f"{edge.source} -> {edge.target}",
            certainty=edge.target_certainty,
            provenance=edge.provenance,
        ) for index, edge in enumerate(selected)]

    @staticmethod
    def _sample_steps(steps: list[Any], limit: int) -> list[Any]:
        """均匀保留长路径，并始终保留首尾步骤。"""
        if len(steps) <= limit:
            return list(steps)
        indexes = {
            round(index * (len(steps) - 1) / (limit - 1))
            for index in range(limit)
        }
        return [steps[index] for index in sorted(indexes)]

    @staticmethod
    def _related(
        pack: SecurityEvidencePack,
        fact_ids: list[str],
        role: str,
    ) -> list[LLMRelatedFact]:
        """展开候选路径上的 Guard 或 Sanitizer。"""
        result: list[LLMRelatedFact] = []
        for fact_id in fact_ids[:12]:
            fact = pack.facts.get(fact_id)
            if fact is None:
                continue
            result.append(LLMRelatedFact(
                fact_id=fact.fact_id,
                role="guard" if role == "guard" else "sanitizer",
                category=fact.category,
                name=fact.name,
                symbol=fact.symbol,
                location=fact.location,
                provenance=fact.provenance,
            ))
        return result

    @staticmethod
    def _compact_unresolved(item: dict[str, Any]) -> dict[str, Any]:
        """保留定位和失败原因，移除对模型判断无帮助的内部字段。"""
        keys = (
            "callsite_id", "file", "line", "column", "from_fqn", "kind",
            "name", "receiver", "resolution_method", "unresolved_reason", "confidence",
        )
        return {key: item[key] for key in keys if item.get(key) not in (None, "")}

    @staticmethod
    def _compact_scan_failure(item: dict[str, Any]) -> dict[str, Any]:
        """保留覆盖盲区的文件、语言和原因，丢弃内部异常正文。"""
        keys = ("path", "line", "language", "reason", "file_count", "callsite_id")
        return {key: item[key] for key in keys if item.get(key) not in (None, "")}

    @staticmethod
    def _ranked_candidates(
        pack: SecurityEvidencePack,
        question: str,
    ) -> list[SecurityCandidate]:
        """优先真实数据流、高风险和与问题关键词相关的候选。"""
        terms = {
            item.casefold()
            for item in re.findall(
                r"[A-Za-z_][A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}",
                question,
            )
        }
        severity = {"high": 3, "medium": 2, "low": 1, "informational": 0}

        def score(candidate: SecurityCandidate) -> tuple[int, int, int, str]:
            """返回稳定的候选排序键。"""
            source = pack.facts.get(candidate.source_fact_id)
            sink = pack.facts.get(candidate.sink_fact_id)
            haystack = " ".join((
                candidate.rule_id,
                candidate.cwe or "",
                candidate.category,
                source.symbol if source else "",
                sink.symbol if sink else "",
                source.location.path if source else "",
                sink.location.path if sink else "",
            )).casefold()
            relevance = sum(term in haystack for term in terms)
            return (
                -int(candidate.dataflow_verified),
                -severity.get(candidate.severity, 0),
                -relevance,
                candidate.candidate_id,
            )

        return sorted(pack.candidates, key=score)

    @staticmethod
    def _size(envelope: LLMSecurityEvidenceEnvelope) -> int:
        """计算紧凑 JSON 的字符数。"""
        return len(json.dumps(
            envelope.model_dump(exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
        ))

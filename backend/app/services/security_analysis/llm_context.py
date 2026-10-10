"""把完整安全分析产物投影为高信息密度 LLM 证据。"""

from __future__ import annotations

import json
import re
from collections import Counter
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
    LLMSecurityObservation,
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
        if self._size(envelope) > max(2_000, max_chars) * 0.65:
            envelope = self._compact_header(envelope, pack)
        for candidate in selected[:max(1, max_findings)]:
            finding = self._finding(pack, candidate)
            trial = envelope.model_copy(update={
                "findings": envelope.findings + [finding],
            })
            if self._size(trial) > max(2_000, max_chars):
                # 不能让详细头部挤掉第一条完整候选，甚至出现预算增加却丢失候选的退化。
                if not envelope.findings and envelope.analysis.get("representation") != "compact_header_full_counts_and_locations":
                    compact = self._compact_header(envelope, pack)
                    compact_trial = compact.model_copy(update={"findings": [finding]})
                    if self._size(compact_trial) <= max(2_000, max_chars):
                        envelope, trial = compact, compact_trial
                if self._size(trial) > max(2_000, max_chars):
                    envelope.truncated = True
                    continue  # 跳过过大的整条候选，继续尝试排序靠后的完整候选。
            envelope.findings.append(finding)
        consumed = max(0, offset) + len(envelope.findings)
        envelope.omitted_findings = max(0, len(candidates) - consumed)
        paired = {endpoint.fact_id for finding in envelope.findings for endpoint in (finding.source, finding.sink)}
        unpaired = [fact for fact in pack.facts.values() if fact.fact_kind in {"source", "sink"} and fact.fact_id not in paired]
        if not pack.candidates and not unpaired:
            unpaired = [fact for fact in pack.facts.values() if fact.fact_kind in {"guard", "sanitizer"}]
        candidate_sources = {item.source_fact_id for item in candidates}
        # 完整候选放不下时，危险操作和相关输入的定位应优先于冗长的入口元数据。
        def observation_priority(fact: StaticSecurityFact) -> tuple[int, str, int]:
            """优先高风险 Sink，其次候选 Source，保留排序可重复且不虚构关联路径。"""
            rank = (0 if fact.fact_kind == "sink" and fact.severity == "high" else
                    1 if fact.fact_id in candidate_sources else
                    2 if fact.fact_kind == "source" else 3)
            return rank, fact.location.path, fact.location.line

        unpaired.sort(key=observation_priority)
        for fact in unpaired[max(0, offset):max(0, offset) + max(1, max_findings)]:
            location = fact.location
            if envelope.analysis.get("representation") == "compact_header_full_counts_and_locations":
                location = location.model_copy(update={
                    "location_id": None, "column": None, "end_column": None,
                    "end_line": location.end_line if location.end_line != location.line else None,
                })
            observation = LLMSecurityObservation(
                fact_id=fact.fact_id, kind=fact.fact_kind, rule_id=fact.rule_id,
                category=fact.category, name=fact.name, symbol=fact.symbol,
                location=location, provenance=fact.provenance, trust_class=fact.trust_class,
                snippet=None,
            )
            trial = envelope.model_copy(update={"observations": [*envelope.observations, observation]})
            if self._size(trial) > max(2_000, max_chars) - 128:
                # 保留完整定位，源码可由两组相同的 read_file_range 读取，绝不切碎片段。
                observation = observation.model_copy(update={"snippet": None})
                trial = envelope.model_copy(update={"observations": [*envelope.observations, observation]})
                if self._size(trial) > max(2_000, max_chars) - 128:
                    break
            envelope.observations.append(observation)
        entries = sorted(pack.entrypoints.values(), key=lambda item: (item.location.path, item.location.line))
        for entry in entries[:20]:
            trial = envelope.model_copy(update={"entrypoints": [*envelope.entrypoints, entry]})
            if self._size(trial) > max(2_000, max_chars) - 128:
                break
            envelope.entrypoints.append(entry)
        envelope.omitted_entrypoints = len(entries) - len(envelope.entrypoints)
        # 先保留尽可能多的完整定位，再用剩余预算补充整块源码；不能用一个长片段挤掉其他输入点。
        for index, observation in enumerate(envelope.observations):
            fact = pack.facts[observation.fact_id]
            snippet = pack.snippets.get(str(fact.metadata.get("snippet_id") or ""))
            if snippet is None:
                continue
            trial_items = list(envelope.observations)
            trial_items[index] = observation.model_copy(update={"snippet": snippet})
            trial = envelope.model_copy(update={"observations": trial_items})
            if self._size(trial) <= max(2_000, max_chars) - 128:
                envelope.observations = trial_items
        envelope.omitted_observations = max(0, len(unpaired) - max(0, offset) - len(envelope.observations))
        envelope.truncated = envelope.truncated or any((
            envelope.omitted_findings, envelope.omitted_entrypoints, envelope.omitted_observations,
        ))
        return envelope

    @staticmethod
    def _compact_header(
        envelope: LLMSecurityEvidenceEnvelope, pack: SecurityEvidencePack,
    ) -> LLMSecurityEvidenceEnvelope:
        """小上下文只压缩重复解释和规则清单表示，保留完整统计、启用类别和不确定性。"""
        coverage = {
            "source_categories": sorted({category for item in pack.rule_coverage for category in item.get("source_categories", [])}),
            "sink_categories": sorted({category for item in pack.rule_coverage for category in item.get("sink_categories", [])}),
            "framework_matchers": sorted({framework for item in pack.rule_coverage for framework in item.get("framework_matchers", [])}),
        }
        gap_counts = Counter(str(item.get("reason") or "unspecified") for item in pack.scan_failures)
        gap_examples = envelope.analysis["coverage_gaps"][:2]
        analysis = {
            "completed": pack.completed, "languages": pack.languages_analyzed,
            "unsupported_languages": pack.unsupported_languages, "rule_packs": pack.rule_packs,
            "rule_coverage": coverage, "rule_coverage_available": bool(pack.rule_coverage),
            "coverage": pack.coverage.model_dump(),
            "retained_fact_count": len(pack.facts),
            "retained_entrypoint_count": len(pack.entrypoints),
            "evidence_pack_limited": pack.coverage.candidate_limit_reached or len(pack.facts) < (
                pack.coverage.source_count + pack.coverage.sink_count +
                pack.coverage.guard_count + pack.coverage.sanitizer_count
            ),
            "count_semantics": "入口=框架入口匹配，非全部CLI/启动入口；Source=输入边界；Sink=已启用规则匹配；候选≠漏洞；零匹配≠无RCE或整体安全。",
            "limitations": "CFG不证明运行时分支可行；动态/反射/别名可漏报；启用框架匹配器≠项目使用该框架。",
            "coverage_gaps": gap_examples,
            "omitted_coverage_gaps": max(0, len(pack.scan_failures) - len(gap_examples)),
            "coverage_gap_counts": dict(gap_counts.most_common(8)),
            "omitted_gap_categories": max(0, len(gap_counts) - 8),
            "representation": "compact_header_full_counts_and_locations",
        }
        semantics = {
            "observed": "源码事实", "inferred": "静态推断非运行时证明",
            "rule_match_only": "单点匹配非污点路径，Guard不证明充分校验",
        }
        if pack.candidates:
            semantics["structural_reachability_only"] = "调用可达不证明值传播"
        return envelope.model_copy(update={"analysis": analysis, "claim_semantics": semantics})

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
                "rule_coverage": pack.rule_coverage,
                "rule_coverage_available": bool(pack.rule_coverage),
                "count_semantics": {
                    "entrypoint_count": "registered_framework_entry_matches_not_all_process_entrypoints_or_external_inputs",
                    "source_count": "matched_input_boundaries_including_stdin_files_environment_and_framework_parameters",
                    "sink_count": "matched_enabled_sink_rules_not_all_dangerous_behaviors",
                    "candidate_count": "constructed_source_sink_paths_not_confirmed_vulnerability_count",
                    "zero_detection": "only_no_match_in_scanned_files_under_enabled_rules_not_project_safety_or_no_RCE",
                    "retained_fact_count": len(pack.facts),
                    "retained_entrypoint_count": len(pack.entrypoints),
                },
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
                "rule_match_only": "单点规则观察，没有形成传播候选；Guard 匹配不证明校验充分，Source 匹配不等于进程/HTTP 入口",
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

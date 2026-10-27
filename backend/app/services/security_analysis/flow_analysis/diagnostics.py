"""有界传播前沿诊断；没有建立路径不等于代码安全，也不一定是漏边。"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

FlowCheckpoint = Literal["source", "value_identity", "call_return", "field_memory", "sink_argument", "evidence_delivery", "unclassified"]
DiagnosticKind = Literal["mapping_gap", "model_gap", "budget_limit", "untrackable_value", "unclassified"]

_CATEGORIES: dict[str, tuple[FlowCheckpoint, DiagnosticKind]] = {
    "source_seed_not_mapped": ("source", "mapping_gap"),
    "sink_without_trackable_value": ("sink_argument", "untrackable_value"),
    "callsite_not_mapped": ("call_return", "mapping_gap"),
    "call_target_not_modelled": ("call_return", "model_gap"),
    "call_depth_limit": ("call_return", "budget_limit"),
    "condition_alternative_route_limit": ("call_return", "budget_limit"),  # 历史产物兼容分类。
    "path_variant_limit": ("call_return", "budget_limit"),
    "route_search_state_limit": ("call_return", "budget_limit"),
    "value_path_search_limit": ("value_identity", "budget_limit"),
    "return_binding_not_modelled": ("call_return", "model_gap"),
    "variable_identity_not_mapped": ("value_identity", "mapping_gap"),
    "unresolved_call_with_tracked_argument": ("call_return", "model_gap"),
    "external_api_semantics_unknown": ("call_return", "model_gap"),
    "instance_factory_return_not_modelled": ("call_return", "model_gap"),
    "instance_factory_depth_limit": ("call_return", "budget_limit"),
    "established_flows_outside_candidate_budget": ("evidence_delivery", "budget_limit"),
    "constructor_heap_effect_not_modelled": ("field_memory", "model_gap"),
    "instance_shared_storage_not_modelled": ("field_memory", "model_gap"),
    "instance_receiver_rebound": ("field_memory", "model_gap"),
    "instance_summary_budget_exceeded": ("field_memory", "budget_limit"),
    "instance_alias_budget_exceeded": ("field_memory", "budget_limit"),
    "instance_nested_path_budget_exceeded": ("field_memory", "budget_limit"),
    "instance_nested_reference_not_resolved": ("field_memory", "model_gap"),
    "instance_alias_not_resolved": ("field_memory", "model_gap"),
    "instance_allocation_not_modelled": ("field_memory", "model_gap"),
    "instance_alias_escape_or_mutation_not_modelled": ("field_memory", "model_gap"),
    "instance_receiver_identity_not_modelled": ("field_memory", "model_gap"),
    "instance_factory_receiver_not_modelled": ("field_memory", "model_gap"),
    "object_call_effect_not_modelled": ("field_memory", "model_gap"),
    "object_call_effect_budget_exceeded": ("field_memory", "budget_limit"),
    "storage_layout_budget_exceeded": ("field_memory", "budget_limit"),
    "storage_member_not_resolved": ("field_memory", "model_gap"),
    "storage_unknown_effect": ("field_memory", "model_gap"),
    "object_snapshot_not_resolved": ("field_memory", "model_gap"),
    "object_reference_effect_not_resolved": ("field_memory", "model_gap"),
    "collection_layout_incomplete": ("field_memory", "model_gap"),
    "collection_effect_not_modelled": ("field_memory", "model_gap"),
    "selector_unknown": ("value_identity", "model_gap"),
    "selector_finite_alternatives": ("value_identity", "unclassified"),
    "selector_budget_exceeded": ("value_identity", "budget_limit"),
}


@dataclass
class FlowDiagnostics:
    """单次分析私有的诊断收集器，不在共享分析服务中保存可变运行状态。"""

    limit: int = 200
    counts: dict[str, int] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    per_reason_limit: int = 20
    value_backends: dict[str, dict[str, Any]] = field(default_factory=dict)
    _retained: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        """允许零条定位的纯计数模式；负总额或非正分层额度无效。"""
        if self.limit < 0 or self.per_reason_limit < 1:
            raise ValueError("Invalid flow diagnostic sample limits")
        self._retained = dict(Counter(item["reason"] for item in self.items))

    def record(self, reason: str, **context: Any) -> None:
        """完整累计事件，按原因有界留样；后出现的少数类可替换高频类，不保存源码正文。"""
        self.counts[reason] = self.counts.get(reason, 0) + 1
        retained = self._retained.get(reason, 0)
        if self.limit == 0 or retained >= self.per_reason_limit:
            self.truncated = True
            return
        if len(self.items) >= self.limit:
            self.truncated = True
            crowded = max(self._retained, key=lambda name: (self._retained[name], name))
            if self._retained[crowded] <= max(1, retained):
                return
            index = next(index for index in range(len(self.items) - 1, -1, -1)
                         if self.items[index]["reason"] == crowded)
            self.items.pop(index)
            self._retained[crowded] -= 1
        checkpoint, kind = _CATEGORIES.get(reason, ("unclassified", "unclassified"))
        if reason == "instance_factory_return_not_modelled" and context.get("reason_detail") == "return_budget_exceeded":
            kind = "budget_limit"
        self.items.append({**context, "reason": reason, "checkpoint": checkpoint, "diagnostic_kind": kind})
        self._retained[reason] = retained + 1

    def as_dict(self) -> dict[str, Any]:
        """输出可持久化的能力前沿；计数为事件次数，不能作为漏洞或独立漏边数量。"""
        checkpoint_counts: Counter[str] = Counter()
        for reason, count in self.counts.items():
            checkpoint_counts[_CATEGORIES.get(reason, ("unclassified", "unclassified"))[0]] += count
        return {
            "version": "propagation-frontier-v2", "counts": dict(self.counts),
            "value_backends": dict(self.value_backends),
            "value_backend_counts": dict(Counter(item["backend"] for item in self.value_backends.values())),
            "items": list(self.items), "truncated": self.truncated, "limit": self.limit,
            "per_reason_limit": self.per_reason_limit, "sample_counts": dict(self._retained),
            "omitted_counts": {reason: count - self._retained.get(reason, 0) for reason, count in self.counts.items()
                               if count > self._retained.get(reason, 0)},
            "checkpoint_counts": dict(checkpoint_counts), "sampling": "bounded_by_reason_with_minority_replacement",
            "coverage": "diagnostic_events_only",
            "semantics": "诊断是映射失败、未建模调用或深度前沿，非完整反向切片；未建立路径不证明安全，也不能据此断言存在漏洞。",
        }

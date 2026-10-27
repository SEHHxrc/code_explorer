"""路径保留策略；只合并完全相同的证据 ID，不把端点相同当作安全语义等价。"""

from collections import defaultdict

from ..contracts import DataFlowEvidence
from .diagnostics import FlowDiagnostics


def retain_variants(flows: list[DataFlowEvidence], *, limit: int, source_limits: set[str],
                    variant_limits: set[tuple[str, str]], diagnostics: FlowDiagnostics | None) -> list[DataFlowEvidence]:
    """每端点族有界保留不同路线；可满足优先，超限明确标记，绝不把其余路线判安全。"""
    groups: dict[tuple[str, str], dict[str, DataFlowEvidence]] = defaultdict(dict)
    for flow in flows:
        groups[(flow.source_fact_id, flow.sink_fact_id)].setdefault(flow.flow_id, flow)
    result = []
    for pair, values in sorted(groups.items()):
        ordered = sorted(values.values(), key=lambda flow: (
            {"satisfiable_under_model": 0, "unknown": 1, "not_checked": 2, "infeasible_under_model": 3}.get(
                flow.path_conditions[0].status if flow.path_conditions else "not_checked", 2), len(flow.steps), flow.flow_id))
        truncated = len(ordered) > limit or pair in variant_limits
        if truncated and diagnostics is not None:
            diagnostics.record("path_variant_limit", source_fact_id=pair[0], sink_fact_id=pair[1],
                               limit=limit, observed_variants=len(ordered))
        for flow in ordered[:limit]:
            reasons = list(flow.unresolved)
            if truncated:
                reasons.append("路径变体保留达到上限；未交付其余路线，不能据此证明全部路径安全或不可达。")
            if pair[0] in source_limits:
                reasons.append("该输入的跨过程搜索达到状态上限；未枚举其余调用上下文。")
            result.append(flow.model_copy(update={"truncated": flow.truncated or truncated or pair[0] in source_limits,
                                                  "unresolved": list(dict.fromkeys(reasons))}))
    return sorted(result, key=lambda flow: (flow.source_fact_id, flow.sink_fact_id,
        {"satisfiable_under_model": 0, "unknown": 1, "not_checked": 2, "infeasible_under_model": 3}.get(
            flow.path_conditions[0].status if flow.path_conditions else "not_checked", 2), len(flow.steps), flow.flow_id))

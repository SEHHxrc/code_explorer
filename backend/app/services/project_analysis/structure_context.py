"""对独立敏感操作反查有界已有调用；不执行污点求解，不按相邻节点补边。"""

from collections import defaultdict
from typing import Any

from backend.app.services.semantic_index.projection import ProgramFacts


def incoming_context(facts: ProgramFacts, symbols: set[str], *, max_depth: int = 2,
                     max_edges: int = 2_048, per_target: int = 8) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    """最多两轮扫描调用事实，返回入边邻接及显式截断；内存不随全项目边数复制增长。"""
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    frontier, seen = set(symbols), set()
    total, truncated = 0, False
    for _ in range(max_depth):
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        following = set()
        for edge in facts.call_edges():
            target, source = str(edge.get("target", "")), str(edge.get("source", ""))
            if target not in frontier or edge.get("relation") != "calls" or source == target:
                continue
            if len(buckets[target]) >= per_target:
                truncated = True
                continue
            buckets[target].append(edge)
        # 每个目标先分到第一条直接入边，再轮询其余调用点；不能由遍历顺序耗尽预算。
        for position in range(per_target):
            for target in sorted(buckets):
                if len(buckets[target]) <= position:
                    continue
                if total >= max_edges:
                    truncated = True
                    continue
                edge = buckets[target][position]
                incoming[target].append(edge)
                total += 1
                following.add(str(edge["source"]))
        seen.update(frontier)
        frontier = following - seen
        if not frontier:
            break
    return dict(incoming), truncated

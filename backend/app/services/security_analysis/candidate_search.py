"""有界分批组织结构线索；保留完整端点，不把窗口大小解释为事实总额。"""

from collections import defaultdict
from collections.abc import Iterator
import time

from .contracts import StaticSecurityFact
from .path_finder import StructuralCallPathFinder, StructuralPath


class StructuralCandidateSearch:
    """按端点窗口查询可达函数，限制总查询数和耗时，不执行 Source×Sink 笛卡尔积。"""

    def __init__(self, finder: StructuralCallPathFinder, sources: list[StaticSecurityFact],
                 sinks: list[StaticSecurityFact], *, source_window: int, sink_window: int,
                 max_queries: int, max_seconds: float) -> None:
        """接收完整事实与显式资源预算；窗口只影响查询调度，不删除事实。"""
        if min(source_window, sink_window, max_queries, max_seconds) <= 0:
            raise ValueError("结构候选查询预算必须为正数")
        self.finder, self.sources, self.sinks = finder, sources, sinks
        self.source_window, self.sink_window = source_window, sink_window
        self.max_queries, self.max_seconds = max_queries, max_seconds
        self.queries = 0
        self.reason: str | None = None
        self.exhausted = False

    def pairs(self, known_pairs: set[tuple[str, str]]) -> Iterator[tuple[StaticSecurityFact, StaticSecurityFact, list[StructuralPath]]]:
        """依次返回可达端点及其有界路线；未遍历窗口明确记录，不能解释为无风险。"""
        started = time.monotonic()
        for sink_start in range(0, len(self.sinks), self.sink_window):
            targets: dict[str, list[StaticSecurityFact]] = defaultdict(list)
            for sink in self.sinks[sink_start:sink_start + self.sink_window]:
                targets[sink.symbol].append(sink)
            for source_start in range(0, len(self.sources), self.source_window):
                cache: dict[str, dict[str, list[StructuralPath]]] = {}
                for source in self.sources[source_start:source_start + self.source_window]:
                    if time.monotonic() - started >= self.max_seconds:
                        self.reason = "time_budget"
                        return
                    if source.symbol not in cache:
                        if self.queries >= self.max_queries:
                            self.reason = "query_budget"
                            return
                        self.queries += 1
                        cache[source.symbol] = self.finder.find_variants(source.symbol, set(targets))
                    for symbol, paths in sorted(cache[source.symbol].items()):
                        for sink in targets[symbol]:
                            if (source.fact_id, sink.fact_id) not in known_pairs:
                                yield source, sink, paths
        self.exhausted = True

    def coverage(self) -> dict[str, int | float | str | bool | None]:
        """输出查询预算与终止原因，exhausted 仅指窗口遍历，不证明路径或源码全覆盖。"""
        return {"source_window": self.source_window, "sink_window": self.sink_window,
                "max_queries": self.max_queries, "max_seconds": self.max_seconds, "queries": self.queries,
                "windows_exhausted": self.exhausted, "stop_reason": self.reason}

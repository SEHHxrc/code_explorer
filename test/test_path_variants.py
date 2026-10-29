"""同端点不同路线与完整 ID 交付回归；不执行被分析代码，不调用模型。"""

import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from backend.app.services.security_analysis.evidence_query import SecurityEvidenceCatalog
from backend.app.services.security_analysis import SecurityAnalysisService
from backend.app.services.security_analysis.flow_analysis.program_graph import ProgramGraphSecurityFlowAnalyzer
from backend.app.services.security_analysis.path_finder import StructuralCallPathFinder
from backend.app.services.program_graph.value_query import ValueFlowQuery
from test import test_condition_routes as route_fixtures


class PathVariantTests(unittest.TestCase):
    """使用真实分析器核对较长路线、汇合点、返回边界和候选身份，不只比较路径数量。"""

    python = route_fixtures.ConditionRouteTests.python
    analyze = route_fixtures.ConditionRouteTests.analyze

    def test_local_merge_keeps_short_and_long_value_paths(self) -> None:
        """两分支汇合到同一个 Sink 时，不允许最先到达的值状态遮住另一条路线。"""
        pack = self.python("", "if value=='a':\n result=value\nelse:\n first=value\n result=first+'suffix'\nos.system(result)")
        flows = list(pack.dataflows.values())
        self.assertEqual(2, len(flows), pack.flow_diagnostics)
        self.assertEqual(2, len({flow.flow_id for flow in flows}))
        self.assertEqual({"satisfiable_under_model"}, {flow.path_conditions[0].status for flow in flows})
        self.assertGreater(max(len(flow.steps) for flow in flows), min(len(flow.steps) for flow in flows))
        self.assertEqual(2, len(pack.candidates))
        self.assertEqual(1, len({candidate.path_group_id for candidate in pack.candidates}))

    def test_satisfiable_direct_call_does_not_hide_longer_call(self) -> None:
        """原来首条 SAT 后停止的逻辑也必须移除，非最短调用经过不同实现仍需交付。"""
        pack = self.python("def worker(command):\n os.system(command)\ndef relay(message):\n worker(message)\n",
                           "worker(value)\nrelay(value)")
        flows = [flow for flow in pack.dataflows.values() if pack.facts[flow.sink_fact_id].symbol == "app.py::worker"]
        self.assertEqual(2, len(flows), pack.flow_diagnostics)
        self.assertEqual({1, 2}, {len(flow.call_edge_ids) for flow in flows})
        self.assertEqual(2, len({candidate.dataflow_id for candidate in pack.candidates}))

    def test_return_merge_keeps_internal_value_variants(self) -> None:
        """不能只在最终 Sink 前展开，返回值进入接收者前的两条路线也必须保留。"""
        pack = self.python("def wrap(command):\n if command=='a':\n  result=command\n else:\n  first=command\n  result=first+'suffix'\n return result\n",
                           "result=wrap(value)\nos.system(result)")
        self.assertEqual(2, len(pack.dataflows), pack.flow_diagnostics)
        self.assertTrue(all(any(step.kind == "return" for step in flow.steps) for flow in pack.dataflows.values()))

    def test_candidate_budget_preserves_value_variants_before_structural_hints(self) -> None:
        """候选总预算紧张时，结构可达但值无关的调用不能挤掉已建成的较长值流。"""
        with patch("test.test_instance_fields.SecurityAnalysisService",
                   side_effect=lambda: SecurityAnalysisService(max_candidates=2)):
            pack = self.python("def worker(command):\n os.system(command)\ndef relay(message):\n worker(message)\n"
                               "def constant(ignored):\n os.system('fixed')\n",
                               "worker(value)\nrelay(value)\nconstant(value)")
        self.assertEqual(2, len(pack.dataflows), pack.flow_diagnostics)
        self.assertEqual(2, len(pack.candidates))
        self.assertTrue(all(item.dataflow_verified for item in pack.candidates))
        self.assertTrue(pack.coverage.candidate_limit_reached)

    def test_same_endpoints_keep_conflicting_and_satisfiable_call_conditions(self) -> None:
        """同端点的矛盾与可满足路线分别保存，不能用一个总体状态覆盖另一条。"""
        pack = self.python("def worker(command):\n if command!='a':\n  os.system(command)\n",
                           "if value=='a':\n worker(value)\nworker(value)")
        self.assertEqual({"satisfiable_under_model", "infeasible_under_model"},
                         {flow.path_conditions[0].status for flow in pack.dataflows.values()})
        self.assertEqual(len(pack.dataflows), len(pack.candidates))

    def test_every_variant_can_be_delivered_by_its_own_id(self) -> None:
        """摘要族计数不代替逐条路径，flow 分页必须可精确恢复每条变体的全部原步骤。"""
        pack = self.python("def worker(command):\n os.system(command)\ndef relay(message):\n worker(message)\n",
                           "worker(value)\nrelay(value)")
        catalog = SecurityEvidenceCatalog(pack.model_dump(mode="json"))
        rows = catalog.search()["items"]
        self.assertEqual(1, len(rows))
        self.assertEqual(2, rows[0]['retained_routes'])
        routes=catalog.detail(rows[0]['id'])['items']
        self.assertEqual({item.candidate_id for item in pack.candidates},{row['id'] for row in routes})
        for candidate in pack.candidates:
            items, offset = [], 0
            while True:
                page = catalog.detail(candidate.candidate_id, section="flow", offset=offset, limit=1)
                items.extend(page["items"])
                if not page["has_more"]:
                    break
                offset = page["next_offset"]
            self.assertEqual([step.model_dump() for step in pack.dataflows[candidate.dataflow_id].steps], items)
        self.assertEqual("security-path-summary-v6-possible-connections", json.loads(catalog.summary())["schema_version"])

    def test_variant_and_search_limits_never_claim_exhaustion(self) -> None:
        """数量和上下文预算各自保留诊断，旧路线仍可查询，截断不等于安全。"""
        for budget, reason in (({"max_path_variants": 1}, "path_variant_limit"),
                               ({"max_search_states": 1}, "route_search_state_limit")):
            with self.subTest(budget=budget), patch("backend.app.services.security_analysis.service.ProgramGraphSecurityFlowAnalyzer",
                side_effect=lambda **kwargs: ProgramGraphSecurityFlowAnalyzer(**budget, **kwargs)):
                pack = self.python("def worker(command):\n os.system(command)\ndef relay(message):\n worker(message)\n",
                                   "worker(value)\nrelay(value)")
            self.assertGreater(pack.flow_diagnostics["counts"].get(reason, 0), 0)
            if pack.dataflows:
                self.assertTrue(all(flow.truncated for flow in pack.dataflows.values()))

    def test_nonshort_structural_and_parallel_callsite_paths_are_retained(self) -> None:
        """没有值流时仍保留非最短结构线索；同节点对的不同调用点不能互相覆盖。"""
        graph = {"links": [{"id": key, "callsite_id": key, "relation": "calls", "source": source, "target": target}
            for key, source, target in (("direct-a", "a", "c"), ("direct-b", "a", "c"),
                                        ("first", "a", "b"), ("second", "b", "c"))]}
        finder = StructuralCallPathFinder(graph)
        variants = finder.find_variants("a", {"c"})["c"]
        self.assertEqual({("direct-a",), ("direct-b",), ("first", "second")},
                         {tuple(edge.edge_id for edge in path.edges) for path in variants})
        self.assertEqual(1, len(finder.find("a", "c").edges))
        self.assertTrue(finder.find_variants("a", {"c"}, max_variants=1)["c"][0].truncated)

    def test_unknown_api_keeps_tracked_argument_and_known_call_prefix(self) -> None:
        """无第三方语义时保留到达调用点的前缀，不把未知返回认作已净化或确定透传。"""
        pack = self.python("import vendor\ndef helper(command):\n vendor.filter(command)\n", "helper(value)")
        frontiers = [item for item in pack.flow_diagnostics["items"] if item["reason"] in {
            "unresolved_call_with_tracked_argument", "external_api_semantics_unknown"} and item.get("name", "").endswith("filter")]
        self.assertTrue(frontiers, pack.flow_diagnostics)
        item = frontiers[0]
        self.assertEqual("unknown", item["return_relation"])
        self.assertEqual(["command"], item["value_names"])
        self.assertTrue(any(step["kind"] == "call" for step in item["known_prefix"]))
        self.assertFalse(any("expression" in step for step in item["known_prefix"]))

    def test_go_regexp_guard_tracks_text_not_pattern(self) -> None:
        """Go 正则规则使用第二参数；仅校正身份，不因匹配观察自动截断传播。"""
        pack = self.analyze({"app.go": 'package app\nimport("os";"regexp")\nfunc run(){value:=os.Getenv("VALUE");regexp.MatchString("^[a-z]+$",value)}'})
        guard = next(fact for fact in pack.facts.values() if fact.rule_id == "GO-GUARD-REGEXP")
        self.assertEqual([1], guard.metadata["value_flow"]["arguments"])
        self.assertEqual("not_verified", guard.metadata["protection_effectiveness"])

    def test_shared_analyzer_isolates_concurrent_runs_and_releases_caches(self) -> None:
        """共享服务并发分析不能串用路径缓存，结束后当前上下文必须解除整张图引用。"""
        shared, barrier = ProgramGraphSecurityFlowAnalyzer(), Barrier(2)
        original = ValueFlowQuery.project

        def project(query, graph):
            barrier.wait(timeout=10)
            return original(query, graph)

        def analyze(suffix):
            pack = self.python("", f"os.system(value+'{suffix}')")
            return pack, shared._active_run.get()

        with patch("backend.app.services.security_analysis.service.ProgramGraphSecurityFlowAnalyzer", return_value=shared), \
                patch.object(ValueFlowQuery, "project", new=project), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(analyze, ("first", "second")))
        for pack, active in results:
            self.assertTrue(pack.dataflows, pack.scan_failures)
            self.assertIsNone(active)
        self.assertIsNone(shared._active_run.get())

    def test_failed_analysis_also_releases_task_context(self) -> None:
        """不合法输入导致异常也必须恢复 ContextVar，不能将失败项目留给下一次查询。"""
        shared = ProgramGraphSecurityFlowAnalyzer()
        with self.assertRaises(AttributeError):
            shared.analyze(None, sources=[], sinks=[], sanitizers=[])
        self.assertIsNone(shared._active_run.get())


if __name__ == "__main__":
    unittest.main()

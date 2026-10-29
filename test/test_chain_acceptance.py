"""冻结的端到端断点矩阵：逐阶段核对源码真值，而非只统计候选或规则命中。"""

import json
from pathlib import Path
import tempfile
import unittest

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.semantic_index import ProjectSemanticResult
from backend.app.services.security_analysis import SecurityAnalysisService, SecurityEvidenceCatalog
from backend.app.services.security_analysis.contracts import SecurityEvidencePack

MATRIX = Path(__file__).with_name("fixtures") / "chain_acceptance.json"


class NonNativeResolver:
    """显式验证保留的非原生路径；不是把工具缺失当作原生成功。"""

    def resolve(self, project_root: str, *, file_paths: list[str], max_file_bytes: int) -> ProjectSemanticResult:
        """返回明确未启用状态，不读取、执行或安装测试项目。"""
        return ProjectSemanticResult(backend="fixture", status="unavailable", files=file_paths)


class ChainAcceptanceTests(unittest.TestCase):
    """每个样例同时含真实值链和无路径反例；阶段名直接标明失败断点。"""

    def test_frozen_framework_chains_and_exact_delivery(self) -> None:
        """输入→调用/返回/对象→敏感槽位→证据交付逐项验收；所有目标只静态读取。"""
        matrix = json.loads(MATRIX.read_text(encoding="utf8"))
        self.assertEqual("chain-acceptance-server-v2", matrix["version"])
        for case in matrix["cases"]:
            with self.subTest(case=case["id"]):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    for path, content in case["files"].items():
                        (root / path).write_text(content, encoding="utf8")
                    before = {path: (root / path).read_bytes() for path in case["files"]}
                    analysis = UnifiedCodeAnalyzer(directory, max_workers=1, semantic_resolver=NonNativeResolver()).run_full_analysis()
                    pack = SecurityAnalysisService().analyze(project_root=root, dependency_graph=analysis["dependency_graph"],
                        semantic_index=analysis["semantic_index"])
                    self.assertEqual(before, {path: (root / path).read_bytes() for path in case["files"]}, "源码只读")
                self.assertFalse(any(item.get("reason") == "program_graph_dataflow_error" for item in pack.scan_failures), "CFG/DFG 构建")
                self.assertTrue(any(fact.fact_kind == "source" for fact in pack.facts.values()), "输入识别")
                if case["entry"]:
                    self.assertTrue(pack.entrypoints, "入口映射")
                expected = {(path, line) for path, content in case["files"].items()
                            for line, text in enumerate(content.splitlines(), 1) if "CHAIN_BAD" in text}
                candidates = [item for item in pack.candidates if item.rule_id == case["rule"] and item.dataflow_verified]
                actual = {(pack.facts[item.sink_fact_id].location.path, pack.facts[item.sink_fact_id].location.line) for item in candidates}
                self.assertEqual(expected, actual, "传播/敏感参数；固定值、状态或无关实参不能变成同一条值链")
                restored = SecurityEvidencePack.model_validate_json(pack.model_dump_json())
                catalog = SecurityEvidenceCatalog(restored.model_dump())
                for candidate in candidates:
                    flow = restored.dataflows[candidate.dataflow_id]
                    self.assertEqual((candidate.source_fact_id, candidate.sink_fact_id), (flow.source_fact_id, flow.sink_fact_id), "端点身份")
                    if case.get("calls"):
                        self.assertTrue(flow.call_edge_ids, "实际调用边/实参返回绑定")
                    if case.get("objects"):
                        self.assertTrue(any(step.object_allocation_id for step in flow.steps), "对象字段身份")
                    if case.get("boundaries"):
                        self.assertTrue(flow.value_boundary_ids, "框架公共值边界")
                    sink = flow.steps[-1]
                    self.assertEqual("sink", sink.kind, "最终敏感操作")
                    self.assertEqual(set(sink.input_names), {name for binding in sink.argument_bindings for name in binding.value_names}, "实际敏感参数槽")
                    delivered, offset = [], 0
                    while True:
                        page = catalog.detail(candidate.candidate_id, section="flow", offset=offset, limit=2)
                        delivered.extend(page["items"])
                        if not page["has_more"]:
                            break
                        self.assertGreater(page["next_offset"], offset, "分页前进")
                        offset = page["next_offset"]
                    self.assertEqual([step.model_dump() for step in flow.steps], delivered, "正确链路完整交付")
                    for step in flow.steps:
                        self.assertIn(step.location.path, case["files"], "真实源码路径")
                        self.assertLessEqual(step.location.line, len(case["files"][step.location.path].splitlines()), "真实行号")
                self.assertTrue(pack.flow_diagnostics["value_backends"], "逐函数后端来源")


if __name__ == "__main__":
    unittest.main()

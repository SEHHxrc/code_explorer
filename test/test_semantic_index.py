"""公共语义索引的契约、提供器和 Java 调用目标回归测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.program_graph import ProgramGraphService
from backend.app.services.security_analysis.flow_analysis.call_graph import (
    CallTransitionIndex,
)
from backend.app.services.semantic_index import (
    SemanticIndex,
    SemanticIndexArtifact,
    SemanticIndexView,
    ProgramGraphSemanticProvider,
    call_result_slot_id,
)


class SemanticIndexTests(unittest.TestCase):
    """验证共享事实来自同一次依赖分析且能被下游直接查询。"""

    def test_java_facts_are_persistable_and_queryable(self):
        """Java 可调用对象、调用目标和变量类型应进入公共事实产物。"""
        source = (
            "class Demo {\n"
            "  void run(String command) { launch(command); }\n"
            "  void launch(String value) { }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Demo.java").write_text(source, encoding="utf-8")
            result = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()

        artifact = SemanticIndexArtifact.model_validate(result["semantic_index"])
        self.assertEqual("1.0", artifact.schema_version)
        index = SemanticIndex(artifact)
        self.assertIsInstance(index, SemanticIndexView)
        self.assertEqual("1.0", index.schema_version)
        self.assertIn("Demo.java::Demo::run", artifact.callables)
        variable_type = index.variable_type("Demo.java::Demo::run", "command")
        self.assertIsNotNone(variable_type)
        self.assertEqual("String", variable_type.type_literal)
        callsite = next(
            item for item in index.calls_from("Demo.java::Demo::run")
            if item.name == "launch"
        )
        self.assertEqual("Demo.java", callsite.location.path)
        self.assertGreater(callsite.location.line, 0)
        self.assertEqual(
            ["Demo.java::Demo::launch"],
            [item.target_symbol for item in callsite.targets],
        )
        self.assertEqual(
            artifact.coverage.resolved_target_count,
            sum(len(item.targets) for item in artifact.callsites.values()),
        )

    def test_unresolved_callsite_keeps_reason_and_location(self):
        """未解析调用不能从索引中消失，也不能伪造目标。"""
        source = "class Demo { void run(String value) { missing(value); } }\n"
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Demo.java").write_text(source, encoding="utf-8")
            result = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()

        artifact = SemanticIndexArtifact.model_validate(result["semantic_index"])
        callsite = next(item for item in artifact.callsites.values() if item.name == "missing")
        self.assertEqual([], callsite.targets)
        self.assertTrue(callsite.unresolved_reason)
        self.assertGreater(callsite.location.column, 0)
        self.assertGreaterEqual(artifact.coverage.unresolved_callsite_count, 1)

    def test_call_transition_consumes_semantic_index(self):
        """跨过程分析应直接消费索引目标，而不是重新解释图边格式。"""
        source = (
            "class Demo {\n"
            "  void run(String command) { launch(command); }\n"
            "  void launch(String value) { }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Demo.java").write_text(source, encoding="utf-8")
            result = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()

        transitions = CallTransitionIndex.from_semantic_index(result["semantic_index"])
        items = transitions.for_source("Demo.java::Demo::run")
        self.assertEqual(1, len(items))
        self.assertEqual("Demo.java::Demo::launch", items[0].target)
        self.assertTrue(items[0].callsite_id.startswith("callsite:"))

    def test_program_graph_enriches_parameters_returns_and_call_results(self):
        """ProgramGraph 应通过提供器追加值接口，而不是改写依赖图。"""
        source = (
            "class Demo {\n"
            "  String run(String command) { String result = forward(command); return result; }\n"
            "  String forward(String value) { return value; }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Demo.java").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            program_graph = ProgramGraphService().analyze(
                directory,
                dependency_graph=analysis["dependency_graph"],
                languages={"java"},
            )

        base = SemanticIndexArtifact.model_validate(analysis["semantic_index"])
        enriched = ProgramGraphSemanticProvider().enrich(
            base,
            program_graph=program_graph,
        )
        self.assertEqual("1.0", base.schema_version)
        self.assertEqual("1.1", enriched.schema_version)
        self.assertGreaterEqual(enriched.coverage.parameter_count, 2)
        self.assertGreaterEqual(enriched.coverage.return_count, 2)
        callsite = next(item for item in base.callsites.values() if item.name == "forward")
        result_slot = enriched.value_slots[call_result_slot_id(callsite.callsite_id)]
        self.assertEqual("call_result", result_slot.kind)
        self.assertEqual(["result"], result_slot.value_names)
        self.assertEqual({}, base.parameters)
        self.assertEqual({}, base.returns)


if __name__ == "__main__":
    unittest.main()

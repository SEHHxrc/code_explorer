"""公共可能写入契约，防止框架适配器破坏其他语言的到达定义语义。"""

from __future__ import annotations

import unittest

from backend.app.services.program_graph.contracts import (
    FunctionProgramGraph,
    ProgramGraphLocation,
)
from backend.app.services.program_graph.control_ir import (
    ControlFunction,
    ControlStatement,
)
from backend.app.services.program_graph.passes.cfg import ControlFlowGraphBuilder
from backend.app.services.program_graph.passes.reaching_definitions import (
    ReachingDefinitionsPass,
)


class FrameworkBindingTests(unittest.TestCase):
    """不依赖语言 AST，直接验证公共事件写入的 kill 与不确定性规则。"""

    def test_may_write_preserves_previous_definition(self) -> None:
        """可能事件写入不能杀死原有确定定义，两个到达定义都应保留。"""
        location = ProgramGraphLocation(
            path="view.js",
            line=1,
            column=1,
            end_line=1,
            end_column=8,
            location_id="loc",
        )
        function = ControlFunction(
            "method",
            "symbol",
            "javascript",
            "view",
            location,
            (),
            (
                ControlStatement(
                    "initial", "assignment", location, "value=1", definitions=("value",)
                ),
                ControlStatement(
                    "event",
                    "assignment",
                    location,
                    "v-model",
                    definitions=("value",),
                    certainty="may",
                    provenance="inferred",
                ),
                ControlStatement(
                    "read", "operation", location, "value", uses=("value",)
                ),
            ),
        )
        graph = ReachingDefinitionsPass().apply(
            ControlFlowGraphBuilder().build(function)
        )
        edges = [
            item
            for item in graph.edges
            if item.kind == "reaching_def" and item.target == "read"
        ]
        self.assertEqual({"initial", "event"}, {item.source for item in edges})
        self.assertTrue(all(item.certainty == "may" for item in edges))
        restored = FunctionProgramGraph.model_validate_json(graph.model_dump_json())
        self.assertEqual("may", restored.nodes["event"].certainty)
        self.assertEqual("inferred", restored.nodes["event"].provenance)

    def test_old_nodes_default_to_must(self) -> None:
        """历史图未提供新字段时仍保持原先的确定写入语义。"""
        from backend.app.services.program_graph.contracts import ProgramGraphNode

        node = ProgramGraphNode.model_validate(
            {"node_id": "n", "kind": "assignment", "language": "java", "method_id": "m"}
        )
        self.assertEqual("must", node.certainty)
        self.assertEqual("observed", node.provenance)

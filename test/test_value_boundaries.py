"""值边界共享协议与图隔离验证，不依赖 JavaScript 或某个框架。"""

from __future__ import annotations

import unittest

from backend.app.services.program_graph.contracts import (
    ProgramGraphArtifact,
    ProgramGraphLocation,
)
from backend.app.services.program_graph.control_ir import (
    ControlFunction,
    ControlStatement,
)
from backend.app.services.program_graph.passes import (
    ControlFlowGraphBuilder,
    ReachingDefinitionsPass,
    ValueFlowPass,
)
from backend.app.services.security_analysis.contracts import (
    EvidenceLocation,
    StaticSecurityFact,
)
from backend.app.services.security_analysis.ir import IRLocation, IRValueBoundary
from backend.app.services.security_analysis.flow_analysis import (
    ProgramGraphSecurityFlowAnalyzer,
)
from backend.app.services.security_analysis.flow_analysis.value_boundaries import (
    ValueBoundaryIndex,
)


class ValueBoundaryTests(unittest.TestCase):
    """用无 AST 的公共函数图验证捕获、退出值、原图不变及默认无边界行为。"""

    def _fixture(self) -> tuple[ProgramGraphArtifact, IRValueBoundary]:
        """构造两个 Java 标记的通用函数，证明传播器不依赖 JS/TS 类型分支。"""
        writer_scope = ProgramGraphLocation(
            path="A.java",
            line=1,
            column=1,
            end_line=4,
            end_column=1,
            location_id="writer",
        )
        reader_scope = ProgramGraphLocation(
            path="A.java",
            line=10,
            column=1,
            end_line=14,
            end_column=1,
            location_id="reader",
        )
        write = writer_scope.model_copy(
            update={"line": 2, "end_line": 2, "end_column": 12, "location_id": "write"}
        )
        read = reader_scope.model_copy(
            update={"line": 12, "end_line": 12, "end_column": 8, "location_id": "read"}
        )
        functions = (
            ControlFunction(
                "writer",
                "writer",
                "java",
                "writer",
                writer_scope,
                ("input",),
                (
                    ControlStatement(
                        "write",
                        "assignment",
                        write,
                        "body=input",
                        definitions=("body",),
                        uses=("input",),
                    ),
                ),
            ),
            ControlFunction(
                "reader",
                "reader",
                "java",
                "reader",
                reader_scope,
                (),
                (
                    ControlStatement(
                        "read", "operation", read, "sink(body)", uses=("body",)
                    ),
                ),
            ),
        )
        graphs = {}
        for function in functions:
            graphs[function.method_id] = ValueFlowPass().apply(
                ReachingDefinitionsPass().apply(
                    ControlFlowGraphBuilder().build(function)
                )
            )
        boundary = IRValueBoundary(
            "value-boundary:test",
            "shared_value",
            IRLocation(**writer_scope.model_dump()),
            IRLocation(**writer_scope.model_dump()),
            ("body",),
            IRLocation(**reader_scope.model_dump()),
            IRLocation(**reader_scope.model_dump()),
            ("body",),
            capture=True,
            source_at_exit=True,
        )
        return ProgramGraphArtifact(functions=graphs), boundary

    def test_shared_overlay_does_not_modify_original_graph(self) -> None:
        """导出/捕获局部 Overlay 不污染原始参数、函数图及持久化值。"""
        graph, boundary = self._fixture()
        before = graph.model_dump_json()
        index = ValueBoundaryIndex(graph, [boundary])
        self.assertEqual(before, graph.model_dump_json())
        self.assertEqual(1, len(index.for_source("writer")))
        target = index.graph.functions["reader"]
        self.assertIn("body", target.nodes[target.entry_node_id].definitions)
        source = index.graph.functions["writer"]
        self.assertIn("body", source.nodes[source.exit_node_id].uses)
        self.assertIs(graph, ValueBoundaryIndex(graph, []).graph)

    def test_shared_algorithm_is_language_independent(self) -> None:
        """无调用图也可通过显式边界连接，空边界时不伪造跨函数传播。"""
        graph, boundary = self._fixture()
        source = StaticSecurityFact(
            fact_id="source",
            fact_kind="source",
            rule_id="source-rule",
            category="input",
            name="input",
            symbol="writer",
            location=EvidenceLocation(path="A.java", line=1, column=1),
            metadata={"value_flow": {"result": "parameter"}},
        )
        sink = StaticSecurityFact(
            fact_id="sink",
            fact_kind="sink",
            rule_id="sink-rule",
            category="sink",
            name="sink",
            symbol="reader",
            location=EvidenceLocation(path="A.java", line=12, column=1),
            metadata={
                "value_flow": {"argument_role": "sink"},
                "value_identifiers": ["body"],
            },
        )
        analyzer = ProgramGraphSecurityFlowAnalyzer()
        self.assertEqual(
            [], analyzer.analyze(graph, sources=[source], sinks=[sink], sanitizers=[])
        )
        flows = analyzer.analyze(
            graph,
            sources=[source],
            sinks=[sink],
            sanitizers=[],
            value_boundaries=[boundary],
        )
        self.assertEqual(1, len(flows))
        self.assertEqual(["value-boundary:test"], flows[0].value_boundary_ids)
        self.assertEqual([], flows[0].call_edge_ids)
        self.assertEqual("low", flows[0].confidence)

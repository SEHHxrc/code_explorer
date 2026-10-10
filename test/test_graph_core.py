# -*- coding: utf-8 -*-
"""公共图接口、适配器和结构校验回归测试。"""

import unittest

from backend.app.services.graph_core import (
    DependencyGraphView,
    GraphArtifactView,
    GraphViewValidator,
    ProgramGraphView,
)
from backend.app.services.program_graph import (
    FunctionProgramGraph,
    ProgramGraphArtifact,
    ProgramGraphEdge,
    ProgramGraphLocation,
    ProgramGraphNode,
)


class GraphCoreTests(unittest.TestCase):
    """验证图共性通过接口表达，且不依赖两个图的内部存储一致。"""

    def test_dependency_view_implements_protocol_and_validates_edges(self):
        """NetworkX node-link 字典应通过适配器满足公共图接口。"""
        view = DependencyGraphView({
            "nodes": [
                {"id": "a.py", "kind": "module"},
                {"id": "a.py::run", "kind": "function"},
            ],
            "links": [{
                "source": "a.py",
                "target": "a.py::run",
                "relation": "contains",
                "key": 0,
            }],
        })

        self.assertIsInstance(view, GraphArtifactView)
        self.assertEqual("dependency_graph", view.graph_kind)
        self.assertTrue(view.capabilities.cross_file)
        self.assertTrue(view.capabilities.multigraph)
        self.assertEqual([], GraphViewValidator().validate(view))

    def test_common_validator_reports_dangling_dependency_edge(self):
        """公共校验器应在不知道 NetworkX 的情况下发现悬空端点。"""
        view = DependencyGraphView({
            "nodes": [{"id": "a.py", "kind": "module"}],
            "links": [{
                "id": "edge:missing",
                "source": "a.py",
                "target": "missing.py",
                "relation": "imports",
            }],
        })

        issues = GraphViewValidator().validate(view)

        self.assertEqual(["dangling_edge"], [item.code for item in issues])
        self.assertEqual("edge:missing", issues[0].edge_id)

    def test_program_view_flattens_function_partitions_without_mutation(self):
        """ProgramGraph 适配器应仅投影分区，不改变原有类型化产物。"""
        location = ProgramGraphLocation(
            path="a.py",
            line=1,
            column=1,
            end_line=2,
            end_column=1,
            location_id="location:a",
        )
        entry = ProgramGraphNode(
            node_id="node:entry",
            kind="method_entry",
            language="python",
            method_id="a.py::run",
            location=location,
        )
        exit_node = ProgramGraphNode(
            node_id="node:exit",
            kind="method_exit",
            language="python",
            method_id="a.py::run",
            location=location,
        )
        function = FunctionProgramGraph(
            method_id="a.py::run",
            symbol_id="a.py::run",
            language="python",
            name="run",
            location=location,
            entry_node_id=entry.node_id,
            exit_node_id=exit_node.node_id,
            nodes={entry.node_id: entry, exit_node.node_id: exit_node},
            edges=[ProgramGraphEdge(
                edge_id="edge:cfg",
                source=entry.node_id,
                target=exit_node.node_id,
                kind="cfg",
                branch="normal",
            )],
        )
        artifact = ProgramGraphArtifact(functions={function.method_id: function})
        view = ProgramGraphView(artifact)

        self.assertIsInstance(view, GraphArtifactView)
        self.assertEqual("program_graph", view.graph_kind)
        self.assertTrue(view.capabilities.control_flow)
        self.assertTrue(view.capabilities.data_flow)
        self.assertEqual(2, len(list(view.iter_nodes())))
        self.assertEqual([], GraphViewValidator().validate(view))
        self.assertEqual(2, len(artifact.functions[function.method_id].nodes))


if __name__ == "__main__":
    unittest.main()

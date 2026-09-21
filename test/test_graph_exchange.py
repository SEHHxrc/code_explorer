# -*- coding: utf-8 -*-
import unittest

from backend.app.services.project_analysis.graph_exchange import GraphExchangeNormalizer


class GraphExchangeNormalizerTests(unittest.TestCase):
    def test_normalizes_fields_paths_edges_and_summary(self):
        raw = {
            "nodes": [
                {"id": "app.py", "name": "app", "level": "module", "file": "app.py", "secret": "drop"},
                {"id": "app.py::run", "name": "run", "kind": "function", "level": "function", "file": "C:\\private\\app.py", "line": 3},
                {"id": "<stdlib>::json", "name": "json", "level": "stdlib_module"},
                {"id": "app.py", "name": "duplicate"},
                {"name": "missing id"},
            ],
            "links": [
                {"source": "app.py", "target": "app.py::run", "relation": "declares"},
                {"source": {"id": "app.py::run"}, "target": {"id": "<stdlib>::json"}, "relation": "imports", "dispatch": "dynamic"},
                {"source": "app.py", "target": "missing", "relation": "calls"},
                {"source": "app.py", "target": "app.py", "relation": "calls"},
            ],
        }

        result = GraphExchangeNormalizer().normalize(raw)
        payload = result.model_dump()

        self.assertEqual("1.0", payload["schema_version"])
        self.assertNotIn("links", payload)
        self.assertEqual(3, payload["summary"]["node_count"])
        self.assertEqual(2, payload["summary"]["edge_count"])
        self.assertEqual("stdlib", payload["nodes"][2]["scope"])
        self.assertIsNone(payload["nodes"][1]["file"])
        self.assertNotIn("secret", payload["nodes"][0])
        self.assertEqual(1, payload["nodes"][0]["out_degree"])
        self.assertEqual(2, payload["nodes"][1]["degree"])
        self.assertTrue(payload["edges"][1]["dynamic"])
        self.assertIn("duplicate_nodes_removed:1", payload["warnings"])
        self.assertIn("dangling_edges_removed:1", payload["warnings"])
        self.assertIn("self_loops_removed:1", payload["warnings"])

    def test_applies_limits_without_emitting_dangling_edges(self):
        result = GraphExchangeNormalizer(max_nodes=1, max_edges=1).normalize({
            "nodes": [{"id": "a"}, {"id": "b"}],
            "edges": [{"source": "a", "target": "b"}],
        })
        self.assertEqual(1, result.summary.node_count)
        self.assertEqual(0, result.summary.edge_count)
        self.assertTrue(result.summary.truncated)

    def test_aggregates_parallel_call_sites_only_for_display(self):
        result = GraphExchangeNormalizer().normalize({
            "nodes": [{"id": "caller"}, {"id": "target"}],
            "links": [
                {"id": "call-1", "source": "caller", "target": "target", "relation": "calls", "dispatch": "direct", "callsite": {"line": 4}},
                {"id": "call-2", "source": "caller", "target": "target", "relation": "calls", "dispatch": "direct", "callsite": {"line": 9}},
            ],
        })

        self.assertEqual(1, result.summary.edge_count)
        self.assertEqual(2, result.edges[0].occurrence_count)
        self.assertIn("parallel_edges_aggregated:1", result.warnings)


if __name__ == "__main__":
    unittest.main()

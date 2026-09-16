# -*- coding: utf-8 -*-
"""保护依赖图配对实验的变量隔离与临时对照组删除边界。"""

import unittest
from pathlib import Path

from backend.app.experiments.baseline.context_builder import BaselineContextBuilder
from backend.app.experiments.baseline.strategy import BaselineExperimentStrategy
from backend.app.experiments.baseline.tool_registry import create_baseline_tool_registry
from backend.app.experiments.graph_context import GraphAugmentedContextBuilder


def sample_artifact():
    return {
        "manifest": {
            "project_name": "demo",
            "languages": ["python"],
            "frameworks": ["FastAPI"],
            "entrypoints": [{
                "kind": "application", "name": "app", "path": "main.py", "line": 1,
                "command": "uvicorn main:app", "confidence": 1.0,
            }],
            "graph_summary": {"node_count": 2, "edge_count": 1},
        },
        "repo_map": "GRAPH_RANKED_CONTENT_SHOULD_NOT_LEAK",
        "overview": "GRAPH_DERIVED_OVERVIEW_SHOULD_NOT_LEAK",
        "file_symbols": {
            "main.py": [{
                "name": "run", "kind": "function", "fully_qualified_name": "run",
                "extent_utf16": {"start": {"line_number": 4}},
            }],
        },
        "dependency_graph": {
            "nodes": [
                {"id": "main.py", "name": "main.py", "kind": "module", "file": "main.py"},
                {"id": "run", "name": "run", "kind": "function", "file": "main.py"},
            ],
            "links": [{"source": "main.py", "target": "run", "relation": "contains"}],
        },
    }


class CapturingManager:
    def __init__(self):
        self.arguments = None

    def start(self, **arguments):
        self.arguments = arguments


class ExperimentIsolationTests(unittest.TestCase):
    def test_baseline_context_contains_no_graph_material(self):
        packet = BaselineContextBuilder().build(
            project_id="p1", question="入口在哪", artifact=sample_artifact(),
        )
        self.assertNotIn("DEPENDENCY_GRAPH", packet.prompt_context)
        self.assertNotIn("GRAPH_RANKED_CONTENT_SHOULD_NOT_LEAK", packet.prompt_context)
        self.assertEqual({}, packet.manifest["graph_summary"])

    def test_baseline_registry_excludes_dependency_tool(self):
        names = {schema["name"] for schema in create_baseline_tool_registry().schemas()}
        self.assertNotIn("get_dependency_neighbors", names)
        self.assertIn("search_symbols", names)

    def test_baseline_strategy_strips_graph_derived_artifacts(self):
        manager = CapturingManager()
        BaselineExperimentStrategy(manager=manager).start(
            run_id="r1", project_id="p1", user_id="u1", project_root=".", artifact=sample_artifact(),
            request=None,
        )
        stripped = manager.arguments["artifact"]
        self.assertNotIn("dependency_graph", stripped)
        self.assertNotIn("overview", stripped)
        self.assertNotIn("GRAPH_RANKED_CONTENT_SHOULD_NOT_LEAK", stripped["repo_map"])

    def test_graph_context_contains_bounded_dependency_context(self):
        packet = GraphAugmentedContextBuilder().build(
            project_id="p1", question="入口在哪", artifact=sample_artifact(),
        )
        self.assertIn("DEPENDENCY_GRAPH_CONTEXT", packet.prompt_context)
        self.assertIn('"source": "main.py"', packet.prompt_context)

    def test_normal_agent_api_does_not_import_control_group(self):
        source = Path("backend/app/api/agent.py").read_text(encoding="utf-8")
        self.assertNotIn("experiments.baseline", source)
        self.assertNotIn("BaselineExperimentStrategy", source)


if __name__ == "__main__":
    unittest.main()

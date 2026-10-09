# -*- coding: utf-8 -*-
"""保护依赖图配对实验的变量隔离与临时对照组删除边界。"""

import unittest
from unittest.mock import patch
from backend.app.llm.registry import ModelLimits
from pathlib import Path

from backend.app.experiments.baseline.context_builder import BaselineContextBuilder
from backend.app.experiments.baseline.strategy import BaselineExperimentStrategy
from backend.app.experiments.baseline.tool_registry import create_baseline_tool_registry
from backend.app.experiments.context import SecurityExperimentContextBuilder
from backend.app.experiments.tools import create_experiment_tool_registry
from backend.app.services.security_analysis.contracts import SecurityEvidencePack


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
        "security_evidence": SecurityEvidencePack().model_dump(),
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
        self.assertEqual({}, packet.manifest)
        self.assertEqual("", packet.repo_map)
        self.assertNotIn("uvicorn", packet.prompt_context)

    def test_baseline_registry_excludes_dependency_tool(self):
        names = {schema["name"] for schema in create_baseline_tool_registry().schemas()}
        self.assertNotIn("get_dependency_neighbors", names)
        self.assertEqual({"list_project_files", "read_file_range", "search_project_text"}, names)
        self.assertEqual(create_experiment_tool_registry().schemas(), create_baseline_tool_registry().schemas())

    def test_baseline_strategy_strips_graph_derived_artifacts(self):
        manager = CapturingManager()
        BaselineExperimentStrategy(manager=manager).start(
            run_id="r1", project_id="p1", user_id="u1", project_root=".", artifact=sample_artifact(),
            request=None,
        )
        stripped = manager.arguments["artifact"]
        self.assertNotIn("dependency_graph", stripped)
        self.assertNotIn("overview", stripped)
        self.assertNotIn("repo_map", stripped)
        self.assertNotIn("security_evidence", stripped)
        self.assertNotIn("file_symbols", stripped)

    def test_security_context_contains_only_static_security_evidence(self):
        packet = SecurityExperimentContextBuilder().build(
            project_id="p1", question="入口在哪", artifact=sample_artifact(),
        )
        self.assertIn("STATIC_SECURITY_EVIDENCE", packet.prompt_context)
        self.assertNotIn("DEPENDENCY_GRAPH_CONTEXT", packet.prompt_context)
        self.assertNotIn("uvicorn", packet.prompt_context)
        self.assertNotIn("FastAPI", packet.prompt_context)

    def test_normal_agent_api_does_not_import_control_group(self):
        source = Path("backend/app/api/agent.py").read_text(encoding="utf-8")
        self.assertNotIn("experiments.baseline", source)
        self.assertNotIn("BaselineExperimentStrategy", source)

    def test_evidence_json_is_not_silently_cut_by_small_prompt_budget(self):
        """预算不能容纳完整证据头时提前拒绝，而不是传入破损 JSON。"""
        with patch("backend.app.experiments.context.get_model_limits", return_value=ModelLimits(2000, 128)):
            with self.assertRaises(ValueError):
                SecurityExperimentContextBuilder().build(project_id="p1", question="q" * 8000, artifact=sample_artifact())


if __name__ == "__main__":
    unittest.main()

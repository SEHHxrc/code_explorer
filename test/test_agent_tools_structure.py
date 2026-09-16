# -*- coding: utf-8 -*-
import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.app.agents.tools.base import ToolContext, ToolRegistry
from backend.app.agents.tools.discovery import ManifestTool
from backend.app.agents.tools.graph import DependencyNeighborsTool


class AgentToolStructureTests(unittest.TestCase):
    def test_registry_rejects_duplicate_names(self):
        with self.assertRaises(ValueError):
            ToolRegistry([ManifestTool(), ManifestTool()])

    def test_dependency_tool_uses_context_index_after_artifact_changes(self):
        artifact = {
            "dependency_graph": {
                "nodes": [{"id": "a"}, {"id": "b", "name": "B", "file": "b.py"}],
                "links": [{"source": "a", "target": "b", "relation": "calls"}],
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            context = ToolContext("p", "u", Path(directory), artifact)
            artifact["dependency_graph"]["links"].clear()
            result = asyncio.run(DependencyNeighborsTool().execute(
                context,
                DependencyNeighborsTool().validate({"node_id": "a", "direction": "outgoing", "limit": 10}),
            ))
        self.assertEqual("b", result.content[0]["node_id"])
        self.assertEqual("calls", result.content[0]["relation"])


if __name__ == "__main__":
    unittest.main()
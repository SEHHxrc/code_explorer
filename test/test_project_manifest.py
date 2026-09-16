# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path

from backend.app.schemas.manifest import ProjectManifest
from backend.app.services.code_intelligence.manifest_builder import ProjectManifestBuilder
from backend.app.services.code_intelligence.repo_map_builder import build_repo_map


class ProjectManifestBuilderTests(unittest.TestCase):
    def test_detects_framework_entrypoint_scripts_and_graph_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "backend").mkdir()
            (root / "frontend").mkdir()
            (root / "backend" / "main.py").write_text(
                "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8"
            )
            (root / "frontend" / "package.json").write_text(json.dumps({
                "scripts": {"dev": "vite", "build": "vite build", "test": "vitest"},
                "dependencies": {"vue": "^3", "vite": "^8"},
            }), encoding="utf-8")
            # Detector source-like text in a Python file must not become a Java framework hit.
            (root / "rules.py").write_text("marker = '@SpringBootApplication'", encoding="utf-8")
            graph = {
                "nodes": [{"id": "backend/main.py"}, {"id": "app"}],
                "links": [{"source": "backend/main.py", "target": "app", "relation": "declares"}],
            }

            manifest = ProjectManifestBuilder(str(root)).build(graph)

            self.assertEqual(manifest.languages, ["Python"])
            self.assertIn("FastAPI", manifest.frameworks)
            self.assertIn("Vue", manifest.frameworks)
            self.assertNotIn("Spring Boot", manifest.frameworks)
            self.assertIn("uvicorn backend.main:app --reload", manifest.run_commands)
            self.assertIn("npm run dev", manifest.run_commands)
            self.assertIn("npm run build", manifest.build_commands)
            self.assertIn("npm run test", manifest.test_commands)
            self.assertEqual(manifest.graph_summary["edge_count"], 1)
            fastapi_entry = next(item for item in manifest.entrypoints if item.framework == "FastAPI")
            self.assertEqual(fastapi_entry.path, "backend/main.py")
            self.assertEqual(fastapi_entry.line, 2)

    def test_repo_map_is_bounded_and_uses_analyzer_symbols(self):
        manifest = ProjectManifest(project_name="demo", languages=["Python"])
        symbols = {
            "main.py": [
                {
                    "name": "app",
                    "kind": "variable",
                    "fully_qualified_name": "app",
                    "extent_utf16": {"start": {"line_number": 4}},
                },
                {
                    "name": "run",
                    "kind": "function",
                    "fully_qualified_name": "run",
                    "extent_utf16": {"start": {"line_number": 8}},
                },
            ]
        }

        repo_map = build_repo_map(manifest, symbols, max_symbols=1)

        self.assertIn("PROJECT demo", repo_map)
        self.assertIn("function run @ main.py:8", repo_map)
        self.assertNotIn("variable app", repo_map)


if __name__ == "__main__":
    unittest.main()


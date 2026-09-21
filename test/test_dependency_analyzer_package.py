# -*- coding: utf-8 -*-
"""Regression tests for the packaged multi-language dependency analyzer."""
import tempfile
import unittest
from pathlib import Path

from backend.app.services.dependency_analyzer import (
    CHandler,
    CppHandler,
    GoHandler,
    JavaHandler,
    JavaScriptHandler,
    PythonHandler,
    RustHandler,
    TypeScriptHandler,
    UnifiedCodeAnalyzer,
)


class DependencyAnalyzerPackageTests(unittest.TestCase):
    """Verify legacy exports and the complete language collection pipeline."""

    def test_public_imports_remain_compatible(self):
        """The old module import path must continue to expose the analyzer and handlers."""
        handlers = (
            PythonHandler, JavaScriptHandler, TypeScriptHandler, GoHandler,
            JavaHandler, CHandler, CppHandler, RustHandler,
        )
        self.assertTrue(all(handler().handlers for handler in handlers))

    def test_all_supported_languages_pass_through_packaged_pipeline(self):
        """Each supported parser should contribute a file context and graph nodes."""
        sources = {
            "main.py": "import os\nclass App:\n    def run(self):\n        return os.getcwd()\n",
            "main.js": "import fs from 'node:fs';\nexport function run() { return fs.readFileSync('x'); }\n",
            "main.ts": "interface Job { run(): void }\nclass Worker implements Job { run(): void {} }\n",
            "main.go": "package main\nimport \"fmt\"\nfunc main() { fmt.Println(\"ok\") }\n",
            "App.java": "package demo; class App { static void run() { System.out.println(\"ok\"); } }\n",
            "main.c": "#include <stdio.h>\nint main(void) { puts(\"ok\"); return 0; }\n",
            "main.cpp": "#include <vector>\nclass App { public: void run() {} };\n",
            "main.rs": "use std::collections::HashMap;\nfn main() { let _m = HashMap::new(); }\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in sources.items():
                (root / name).write_text(content, encoding="utf-8")

            result = UnifiedCodeAnalyzer(directory, max_workers=2).run_full_analysis()

        self.assertEqual(result["stats"].get("files_parsed"), len(sources))
        self.assertEqual(set(result["file_symbols"]), set(sources))
        self.assertTrue(all(result["file_symbols"][name] for name in sources))
        self.assertGreater(len(result["dependency_graph"].get("nodes", [])), len(sources))

    def test_call_edges_preserve_occurrences_locations_and_resolution_metadata(self):
        """Repeated calls between the same symbols must remain separate, traceable evidence."""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.py").write_text(
                "def target():\n"
                "    return 1\n\n"
                "def caller():\n"
                "    target()\n"
                "    target()\n"
                "    missing_target()\n",
                encoding="utf-8",
            )
            result = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()

        graph = result["dependency_graph"]
        self.assertTrue(graph["multigraph"])
        call_edges = [
            edge for edge in graph.get("links", graph.get("edges", []))
            if edge.get("relation") == "calls"
            and str(edge.get("source", "")).endswith("::caller")
            and str(edge.get("target", "")).endswith("::target")
        ]
        self.assertEqual([5, 6], sorted(edge["callsite"]["line"] for edge in call_edges))
        self.assertEqual(2, len({edge["id"] for edge in call_edges}))
        self.assertTrue(all(edge["id"].startswith("ref:") for edge in call_edges))
        self.assertTrue(all(edge["resolution_method"] == "local_scope" for edge in call_edges))
        self.assertTrue(all(edge["target_certainty"] == "must" for edge in call_edges))
        unresolved = result["diagnostics"]["unresolved_references"]
        self.assertTrue(any(item["name"] == "missing_target" and item["line"] == 7 for item in unresolved))
        self.assertEqual(1, result["diagnostics"]["coverage"]["unresolved_total"])


if __name__ == "__main__":
    unittest.main()

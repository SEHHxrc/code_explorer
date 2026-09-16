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


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from backend.app.services.dependency_analyzer import (
    GoHandler,
    JavaHandler,
    JavaScriptHandler,
    PythonHandler,
    RustHandler,
    UnifiedCodeAnalyzer,
)


class DependencyScopeTests(unittest.TestCase):
    def test_language_handlers_identify_runtime_libraries(self):
        self.assertTrue(PythonHandler().is_stdlib_module("pathlib"))
        self.assertTrue(GoHandler().is_stdlib_module("net/http"))
        self.assertTrue(RustHandler().is_stdlib_module("std::collections"))
        self.assertTrue(JavaScriptHandler().is_stdlib_module("node:fs"))
        self.assertTrue(JavaScriptHandler().is_stdlib_module("node:test"))
        self.assertTrue(JavaHandler().is_stdlib_module("java.util"))
        self.assertFalse(JavaHandler().is_stdlib_module("jakarta.persistence"))

    def test_python_standard_library_is_separate_from_third_party(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.py").write_text(
                "import os\nimport requests\n\n"
                "def run():\n"
                "    os.path.join('a', 'b')\n"
                "    requests.get('https://example.com')\n",
                encoding="utf-8",
            )
            result = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            nodes = result["dependency_graph"]["nodes"]

        stdlib = [node for node in nodes if node.get("dependency_scope") == "stdlib"]
        third_party = [node for node in nodes if node.get("dependency_scope") == "third_party"]
        self.assertTrue(any(
            node["id"].startswith("<stdlib>::os") and node.get("level") == "stdlib_module"
            for node in stdlib
        ))
        self.assertTrue(any(
            node["id"].startswith("<stdlib>::os.") and node.get("level") == "stdlib"
            for node in stdlib
        ))
        self.assertTrue(any(node["id"].startswith("<external>::requests") for node in third_party))


if __name__ == "__main__":
    unittest.main()

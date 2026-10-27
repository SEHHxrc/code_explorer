# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services.dependency_analyzer import (
    GoHandler,
    JavaHandler,
    JavaScriptHandler,
    PythonHandler,
    RustHandler,
    UnifiedCodeAnalyzer,
)


class DependencyScopeTests(unittest.TestCase):
    def test_graph_payload_keeps_links_across_networkx_defaults(self):
        """旧版 links 与新版 edges 都输出既定 links，空图也不能改变交换格式。"""
        for name in ("links", "edges"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                analyzer = UnifiedCodeAnalyzer(directory)
                with patch("backend.app.services.dependency_analyzer.analyzer.nx.node_link_data",
                           return_value={"nodes": [], name: [{"source": "a", "target": "b", "key": 0}]}):
                    graph = analyzer.run_full_analysis()["dependency_graph"]
                self.assertNotIn("edges", graph)
                self.assertEqual(graph["links"], [{"source": "a", "target": "b", "key": 0}])

    def test_typescript_runtime_extensions_resolve_source_implementations(self):
        """显式运行时扩展名映射真实源码；无对应文件时不能按同名跨目录猜测。"""
        for extension, implementation in ((".js", ".ts"), (".js", ".tsx"),
                                           (".jsx", ".tsx"), (".mjs", ".mts"),
                                           (".cjs", ".cts")):
            with self.subTest(extension=extension, implementation=implementation):
                with tempfile.TemporaryDirectory() as directory:
                    Path(directory, "main.ts").write_text(
                        f"import {{execute}} from './worker{extension}'; execute();", encoding="utf-8")
                    Path(directory, "worker" + implementation).write_text(
                        "export function execute() {}", encoding="utf-8")
                    graph = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()["dependency_graph"]
                    self.assertTrue(any(edge.get("relation") == "calls" and
                                        edge["target"] == f"worker{implementation}::execute"
                                        for edge in graph["links"]))

    def test_javascript_keeps_runtime_file_and_typescript_prefers_source(self):
        """JS 不被同名 TS 劫持；TS 按扩展替换优先选择仓库实现而非产出 JS。"""
        with tempfile.TemporaryDirectory() as directory:
            for name in ("main.js", "main.ts"):
                Path(directory, name).write_text("import {execute} from './worker.js'; execute();", encoding="utf-8")
            for name in ("worker.js", "worker.ts"):
                Path(directory, name).write_text("export function execute() {}", encoding="utf-8")
            analyzer = UnifiedCodeAnalyzer(directory, max_workers=1)
            analyzer.run_full_analysis()
            self.assertEqual("worker.js", analyzer._resolve_js_module("main.js", "./worker.js"))
            self.assertEqual("worker.ts", analyzer._resolve_js_module("main.ts", "./worker.js"))
            self.assertEqual("", analyzer._resolve_js_module("main.ts", "./missing.js"))
            self.assertEqual("", analyzer._resolve_js_module("main.ts", "package/worker.js"))
            self.assertEqual("", analyzer._resolve_js_module("main.ts", "./worker.json"))

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

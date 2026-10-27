"""任务内语法复用的等价、安全隔离、容量与线程回归；不调用模型。"""

from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from backend.app.services.syntax_analysis import DEFAULT_TREE_SITTER_PARSER_POOL
from backend.app.services.syntax_analysis.session import parse_tree, source_view, syntax_session, syntax_statistics
from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.program_graph import ProgramGraphService
from backend.app.services.security_analysis import SecurityAnalysisService
from backend.app.services.project_analysis import ProjectAnalysisPipeline
from test.test_go_network_security import NoNativeResolver


class CountingPool:
    """通过真实本地 Parser 计数，避免用假树掩盖 C 层句柄和坐标行为。"""

    def __init__(self) -> None:
        self.parses = 0

    def get(self, grammar: str) -> object:
        parser = DEFAULT_TREE_SITTER_PARSER_POOL.get(grammar)
        owner = self

        class CountingParser:
            def parse(self, source: bytes) -> object:
                owner.parses += 1
                return parser.parse(source)
        return CountingParser()


class SyntaxSessionTests(unittest.TestCase):
    """相同源码复用树，不复用权限、结论、其他项目或修改后的树。"""

    def test_tree_is_copied_and_content_path_grammar_pool_are_isolated(self) -> None:
        pool = CountingPool()
        source = b"const value = input;"
        with syntax_session() as session:
            tree = parse_tree("app.js", source, "javascript", pool)
            tree.edit(start_byte=0, old_end_byte=0, new_end_byte=1,
                      start_point=(0, 0), old_end_point=(0, 0), new_end_point=(0, 1))
            another = parse_tree("app.js", source, "javascript", pool)
            self.assertEqual((0, 0), another.root_node.start_point)
            self.assertEqual(source, another.root_node.text)
            parse_tree("app.js", b"const value = other;", "javascript", pool)
            parse_tree("other.js", source, "javascript", pool)
            parse_tree("app.js", source, "typescript", pool)
            parse_tree("app.js", source, "javascript", CountingPool())
            self.assertEqual(4, pool.parses)
            self.assertEqual(1, session.snapshot()["tree_hits"])
        self.assertEqual({}, syntax_statistics())
        self.assertEqual(0, session.snapshot()["entries"])

    def test_nested_context_failure_and_parallel_readers(self) -> None:
        source = b"package main\nfunc run(){}"
        with syntax_session() as outer:
            parse_tree("main.go", source, "go")
            with self.assertRaises(RuntimeError):
                with syntax_session() as inner:
                    parse_tree("main.go", source, "go")
                    raise RuntimeError("fixture")
            self.assertEqual(0, inner.snapshot()["entries"])
            self.assertEqual(1, syntax_statistics()["tree_misses"])
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(copy_context().run, parse_tree, "main.go", source, "go") for _ in range(8)]
                trees = [future.result() for future in futures]
            self.assertEqual(8, len({id(tree) for tree in trees}))
            self.assertEqual(8, outer.snapshot()["tree_hits"])
        self.assertEqual(0, outer.snapshot()["entries"])

    def test_capacity_oversized_and_failed_build_do_not_change_results(self) -> None:
        pool = CountingPool()
        with syntax_session(max_entries=1):
            parse_tree("a.go", b"package main", "go", pool)
            parse_tree("b.go", b"package main", "go", pool)
            parse_tree("a.go", b"package main", "go", pool)
            self.assertEqual(2, pool.parses)
            self.assertEqual(1, syntax_statistics()["capacity_not_retained"])
            self.assertEqual(1, syntax_statistics()["tree_hits"])
        with syntax_session(max_estimated_bytes=1):
            parse_tree("a.go", b"package main", "go", pool)
            self.assertEqual(0, syntax_statistics()["entries"])
            self.assertEqual(1, syntax_statistics()["oversized_not_retained"])
        with syntax_session() as session:
            with self.assertRaises(RuntimeError):
                session.memo(("failure",), lambda: self.fail_build(), lambda _value: 1)
            self.assertEqual(0, session.snapshot()["entries"])
        with self.assertRaises(ValueError):
            with syntax_session(max_entries=0):
                pass

    @staticmethod
    def fail_build() -> None:
        raise RuntimeError("fixture")

    def test_vue_view_and_script_tree_keep_original_offsets(self) -> None:
        source = b'<script setup lang="ts">const value = "";</script><template><div v-html="value"></div></template>'
        with syntax_session():
            first = source_view("view.vue", source, "typescript")
            second = source_view("view.vue", source, "typescript")
            self.assertEqual(first, second)
            self.assertIs(first, second)
            self.assertEqual(len(source), len(first.source))
            self.assertEqual(1, syntax_statistics()["unit_hits"])
            self.assertEqual(1, syntax_statistics()["tree_misses"])
        self.assertEqual(first, source_view("view.vue", source, "typescript"))

    def test_all_language_artifacts_equal_with_bounded_or_disabled_retention(self) -> None:
        samples = {
            "main.py": "def run(value):\n    return value\n",
            "Main.java": "class Main { String run(String value) { return value; } }",
            "main.go": 'package main\nimport("net/http";"os/exec")\nfunc run(r *http.Request){exec.Command(r.FormValue("cmd"))}',
            "main.js": "const cp=require('child_process'); function run(){cp.exec(process.argv[2]);}",
            "main.ts": "import {exec} from 'child_process'; function run(value:string){exec(value);}",
            "view.vue": '<script setup>import {ref} from "vue";const value=ref("");</script><template><input v-model="value"><div v-html="value"></div></template>',
            "main.c": '#include <stdlib.h>\nvoid run(){system(getenv("CMD"));}',
            "main.cpp": '#include <cstdlib>\nvoid run(){system(getenv("CMD"));}',
        }
        with tempfile.TemporaryDirectory() as directory:
            for path, source in samples.items():
                Path(directory, path).write_text(source, encoding="utf-8")

            def analyze() -> tuple:
                analysis = UnifiedCodeAnalyzer(directory, max_workers=1, semantic_resolver=NoNativeResolver()).run_full_analysis()
                graph = ProgramGraphService().analyze(directory, dependency_graph=analysis["dependency_graph"])
                pack = SecurityAnalysisService().analyze(project_root=directory, dependency_graph=analysis["dependency_graph"], semantic_index=analysis["semantic_index"])
                return analysis, graph.model_dump(), pack.model_dump()

            with syntax_session(max_estimated_bytes=1):
                uncached = analyze()
            with syntax_session() as session:
                cached = analyze()
                self.assertGreater(session.snapshot().get("tree_hits", 0), 8)
            self.assertEqual(uncached, cached)

    def test_production_pipeline_uses_one_parse_and_rereads_changed_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "main.go")
            path.write_text('package main\nfunc run(){}', encoding="utf-8")

            def factory(root: str, max_workers: int) -> UnifiedCodeAnalyzer:
                return UnifiedCodeAnalyzer(root, max_workers=max_workers, semantic_resolver=NoNativeResolver())

            result = ProjectAnalysisPipeline(analyzer_factory=factory).analyze(directory)
            self.assertEqual(1, result.diagnostics["syntax_reuse"]["tree_misses"])
            self.assertEqual(2, result.diagnostics["syntax_reuse"]["tree_hits"])
            with syntax_session():
                first = ProgramGraphService().analyze(directory)
                path.write_text('package main\nfunc changed(){}', encoding="utf-8")
                second = ProgramGraphService().analyze(directory)
            self.assertNotEqual(set(first.functions), set(second.functions))

    def test_cached_tree_cannot_bypass_size_or_missing_file_checks(self) -> None:
        from backend.app.services.security_analysis.language_adapters.go import GoSecurityFrontend

        with tempfile.TemporaryDirectory() as directory, syntax_session():
            path = Path(directory, "main.go")
            path.write_text("package main\nfunc run(){}", encoding="utf-8")
            ProgramGraphService().analyze(directory)
            scan = GoSecurityFrontend(max_file_bytes=1).build(directory)
            self.assertEqual("file_too_large", scan.failures[0]["reason"])
            path.unlink()
            scan = GoSecurityFrontend().build(directory, file_paths=["main.go"])
            self.assertEqual(0, scan.files_scanned)
            self.assertEqual("FileNotFoundError", scan.failures[0]["detail"])

    def test_parallel_collection_inherits_task_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "a.go").write_text("package main\nfunc first(){}", encoding="utf-8")
            Path(directory, "b.go").write_text("package main\nfunc second(){}", encoding="utf-8")
            with syntax_session() as session:
                analysis = UnifiedCodeAnalyzer(directory, max_workers=2, semantic_resolver=NoNativeResolver()).run_full_analysis()
                ProgramGraphService().analyze(directory, dependency_graph=analysis["dependency_graph"])
                self.assertEqual(2, session.snapshot()["tree_misses"])
                self.assertEqual(2, session.snapshot()["tree_hits"])

    def test_sequential_scan_cannot_flush_previously_admitted_batch(self) -> None:
        pool = CountingPool()
        with syntax_session(max_entries=2):
            for _repeat in range(3):
                for index in range(8):
                    parse_tree(str(index) + ".go", b"package main", "go", pool)
            self.assertEqual(4, syntax_statistics()["tree_hits"])
            self.assertEqual(20, pool.parses)
            self.assertEqual(2, syntax_statistics()["entries"])


if __name__ == "__main__":
    unittest.main()

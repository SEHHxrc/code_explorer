"""C/C++ 安全规则、跨文件传播和防误报边界回归。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.program_graph import ProgramGraphService
from backend.app.services.security_analysis import SecurityAnalysisService, SecurityEvidencePromptBuilder
from backend.app.services.security_analysis.contracts import SecurityEvidencePack
from backend.app.services.security_analysis.frontends import CSecurityFrontend, CppSecurityFrontend
from backend.app.services.security_analysis.scanner import SecurityScanner
from backend.app.services.security_analysis.semantics import CLanguageSemantics, CppLanguageSemantics


class CFamilySecurityTests(unittest.TestCase):
    """验证新增前端复用公共 CFG/DFG，并区分事实、结构候选和已验证数据流。"""

    def _analyze(self, files: dict[str, str]) -> SecurityEvidencePack:
        """构建临时源码项目，通过真实依赖解析和公共数据流入口返回证据包。"""
        with tempfile.TemporaryDirectory() as directory:
            for name, source in files.items():
                target = Path(directory, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            return SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
                semantic_index=analysis["semantic_index"],
            )

    def test_c_environment_to_shell_keeps_locations_and_trust_precondition(self) -> None:
        """环境配置经普通赋值进入 Shell，保留位置、调用点及攻击者控制前提。"""
        pack = self._analyze({"main.c": (
            "#include <stdlib.h>\n"
            "int launch(void) {\n"
            '  char *command = getenv("APP_COMMAND");\n'
            "  char *value = command;\n"
            "  return system(value);\n"
            "}\n"
        )})
        candidate = next(item for item in pack.candidates if item.rule_id == "C-SINK-SHELL")
        source, sink = pack.facts[candidate.source_fact_id], pack.facts[candidate.sink_fact_id]
        self.assertEqual(["c"], pack.languages_analyzed)
        self.assertIn("c-family-core/1.0", pack.rule_packs)
        self.assertEqual("external_configuration", source.trust_class)
        self.assertEqual(3, source.location.line)
        self.assertEqual(5, sink.location.line)
        self.assertTrue(sink.metadata["shell"])
        self.assertTrue(candidate.dataflow_verified)
        self.assertEqual("intra_procedural_dataflow", candidate.path_kind)
        self.assertTrue(any("环境变量" in value for value in candidate.preconditions))
        self.assertTrue(all(item in pack.snippets for item in candidate.snippet_ids))
        finding = SecurityEvidencePromptBuilder().build(pack).findings[0]
        self.assertEqual("medium", finding.confidence_dimensions["sink_rule_match"])
        self.assertEqual("header_and_lexical_scope", finding.sink.context["api_resolution"])

    def test_c_safe_sibling_declarator_does_not_verify_shell_flow(self) -> None:
        """同一 declaration 的安全兄弟变量不得获得 Source 的返回值污点。"""
        pack = self._analyze({"main.c": (
            "#include <stdlib.h>\n"
            "int launch(void) {\n"
            '  char *tainted = getenv("CMD"), *safe = "fixed";\n'
            "  return system(safe);\n"
            "}\n"
        )})
        candidates = [item for item in pack.candidates if item.rule_id == "C-SINK-SHELL"]
        self.assertTrue(candidates)
        self.assertTrue(all(not item.dataflow_verified for item in candidates))
        source = next(item for item in pack.facts.values() if item.rule_id == "C-SOURCE-ENV")
        self.assertEqual(["tainted"], source.metadata["assigned_targets"])

    def test_c_source_crosses_resolved_file_boundary(self) -> None:
        """跨文件数据流必须引用依赖分析已解析的调用边。"""
        pack = self._analyze({
            "launch.h": "int execute(const char *value);\n",
            "main.c": (
                "#include <stdlib.h>\n#include \"launch.h\"\n"
                'int launch(void) { char *cmd = getenv("CMD"); return execute(cmd); }\n'
            ),
            "worker.c": (
                "#include <stdlib.h>\n#include \"launch.h\"\n"
                "int execute(const char *value) { return system(value); }\n"
            ),
        })
        candidate = next(item for item in pack.candidates if item.rule_id == "C-SINK-SHELL")
        self.assertTrue(candidate.dataflow_verified)
        self.assertEqual("interprocedural_dataflow", candidate.path_kind)
        self.assertEqual(1, len(candidate.call_edge_ids))
        edge = pack.call_edges[candidate.call_edge_ids[0]]
        self.assertEqual("main.c", edge.callsite.path)
        self.assertEqual("worker.c::execute", edge.target)

    def test_cpp_class_identity_and_callsite_match_program_graph(self) -> None:
        """类方法、限定标准库调用与公共图使用同一函数和调用点身份。"""
        source = (
            "#include <cstdlib>\n"
            "namespace app { class Runner { public:\n"
            '  int run() { char *value = std::getenv("CMD"); return std::system(value); }\n'
            "}; }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.cpp").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            ir = CppSecurityFrontend().build(directory)
            graph = ProgramGraphService().analyze(directory, dependency_graph=analysis["dependency_graph"])
            pack = SecurityAnalysisService().analyze(
                project_root=directory, dependency_graph=analysis["dependency_graph"],
                semantic_index=analysis["semantic_index"],
            )
        function = next(item for item in ir.functions if item.name == "run")
        self.assertEqual("main.cpp::app::Runner::run", function.symbol)
        self.assertIn(function.symbol, {item.symbol_id for item in graph.functions.values()})
        graph_calls = {
            call.callsite_id for item in graph.functions.values()
            for node in item.nodes.values() for call in node.calls
        }
        self.assertTrue(all(call.callsite_id in graph_calls for call in ir.calls))
        candidate = next(item for item in pack.candidates if item.rule_id == "C-SINK-SHELL")
        self.assertTrue(candidate.dataflow_verified)

    def test_c_project_function_and_macro_are_not_shell_api(self) -> None:
        """项目自定义函数或预处理宏遮蔽标准库名称时，不生成 Shell Sink。"""
        for definition in (
            "int system(const char *s) { return 0; }\n",
            "#define system(value) custom(value)\n",
        ):
            with self.subTest(definition=definition):
                pack = self._analyze({"main.c": (
                    "#include <stdlib.h>\n" + definition
                    + 'int launch(void) { char *cmd = getenv("CMD"); return system(cmd); }\n'
                )})
                self.assertFalse(any(item.rule_id == "C-SINK-SHELL" for item in pack.facts.values()))

    def test_c_same_named_function_in_other_file_is_not_libc(self) -> None:
        """扫描范围内其他文件存在同名实现时，不能仅据包含头文件猜成 libc。"""
        pack = self._analyze({
            "main.c": '#include <stdlib.h>\nint launch(void) { return system("fixed"); }\n',
            "custom.c": "int system(const char *value) { return 0; }\n",
        })
        self.assertFalse(any(item.rule_id == "C-SINK-SHELL" for item in pack.facts.values()))

    def test_sqlite_uses_query_argument_not_database_handle(self) -> None:
        """SQLite SQL 角色是第二个参数；数据库句柄污点不能验证 SQL 注入链。"""
        for query, expected in (("value", True), ('"SELECT 1"', False)):
            with self.subTest(query=query):
                pack = self._analyze({"main.c": (
                    "#include <stdlib.h>\n#include <sqlite3.h>\n"
                    "int query(sqlite3 *db) {\n"
                    '  char *value = getenv("QUERY");\n'
                    f"  return sqlite3_exec(db, {query}, 0, 0, 0);\n"
                    "}\n"
                )})
                candidate = next(item for item in pack.candidates if item.rule_id == "C-SINK-SQLITE")
                self.assertEqual(expected, candidate.dataflow_verified)
                sink = pack.facts[candidate.sink_fact_id]
                self.assertEqual([1], sink.metadata["value_flow"]["arguments"])
                self.assertEqual("dynamic" if expected else "literal", sink.metadata["query_shape"])
                self.assertNotIn("parameter_argument_present", sink.metadata)

    def test_printf_data_argument_does_not_taint_fixed_format(self) -> None:
        """固定格式字符串的数据参数不等同于攻击者控制格式本身。"""
        for invocation, expected in (('printf("%s", value)', False), ("printf(value)", True)):
            with self.subTest(invocation=invocation):
                pack = self._analyze({"main.c": (
                    "#include <stdlib.h>\n#include <stdio.h>\n"
                    'int show(void) { char *value = getenv("TEXT"); return ' + invocation + "; }\n"
                )})
                candidate = next(item for item in pack.candidates if item.rule_id == "C-SINK-FORMAT")
                self.assertEqual(expected, candidate.dataflow_verified)

    def test_output_buffer_source_is_verified_without_tainting_return_status(self) -> None:
        """读取缓冲区的状态返回值不得被当作数据内容，局限仍进入证据包。"""
        pack = self._analyze({"main.c": (
            "#include <unistd.h>\n#include <stdlib.h>\n"
            "int launch(void) {\n"
            "  char buf[64];\n"
            "  int count = read(0, buf, 64);\n"
            "  return system(buf);\n"
            "}\n"
        )})
        source = next(item for item in pack.facts.values() if item.rule_id == "C-SOURCE-FD-BUFFER")
        self.assertEqual([1], source.metadata["value_flow"]["output_arguments"])
        self.assertEqual("unknown", source.trust_class)
        self.assertFalse(any(item.get("reason") == "output_argument_binding_unresolved"
                             for item in pack.scan_failures))
        self.assertTrue(any(item.dataflow_verified for item in pack.candidates))
        finding = SecurityEvidencePromptBuilder().build(pack).findings[0]
        self.assertEqual([1], finding.source.value_role["output_arguments"])
        self.assertTrue(any("输出参数" in item for item in finding.limitations))

    def test_exec_fixed_program_argument_is_not_shell_injection(self) -> None:
        """exec 的固定程序路径与可控参数不同；只验证声明为路径角色的实参。"""
        pack = self._analyze({"main.c": (
            "#include <stdlib.h>\n#include <unistd.h>\n"
            'int launch(void) { char *value = getenv("ARG"); return execl("/bin/echo", "echo", value, 0); }\n'
        )})
        candidate = next(item for item in pack.candidates if item.rule_id == "C-SINK-EXEC-PATH")
        self.assertFalse(candidate.dataflow_verified)
        self.assertTrue(any("不自动等同于 Shell 注入" in item for item in candidate.preconditions))
        self.assertFalse(pack.facts[candidate.sink_fact_id].metadata["shell"])

    def test_missing_header_is_a_coverage_gap_not_a_confirmed_api(self) -> None:
        """无法观察头文件时报告覆盖缺口，不从名字断言库 API 身份。"""
        pack = self._analyze({"main.c": 'int launch(void) { return system("fixed"); }\n'})
        self.assertFalse(any(item.rule_id == "C-SINK-SHELL" for item in pack.facts.values()))
        self.assertTrue(any(item.get("reason") == "api_header_not_visible"
                            for item in pack.scan_failures))

    def test_nested_source_is_not_assigned_to_wrapper_result(self) -> None:
        """嵌套 Source 返回值不会被误当成外层函数可能独立生成的结果。"""
        pack = self._analyze({"main.c": (
            "#include <stdlib.h>\n"
            'char *clean(const char *v) { return "fixed"; }\n'
            'int launch(void) { char *cmd = clean(getenv("CMD")); return system(cmd); }\n'
        )})
        source = next(item for item in pack.facts.values() if item.rule_id == "C-SOURCE-ENV")
        self.assertEqual([], source.metadata["assigned_targets"])
        self.assertFalse(any(item.dataflow_verified for item in pack.candidates))

    def test_c_and_cpp_reuse_positional_binding(self) -> None:
        """两语言分别注册，但共享固定前缀的位置参数绑定；额外实参不补造形参。"""
        for frontend, semantics, filename in (
            (CSecurityFrontend(), CLanguageSemantics(), "main.c"),
            (CppSecurityFrontend(), CppLanguageSemantics(), "main.cpp"),
        ):
            with self.subTest(language=frontend.language), tempfile.TemporaryDirectory() as directory:
                Path(directory, filename).write_text(
                    "int target(char *first, int second) { return second; }\n"
                    'int caller(void) { return target("x", 2); }\n', encoding="utf-8",
                )
                program = frontend.build(directory)
                call = next(item for item in program.calls if item.qualified_name == "target")
                target = next(item for item in program.functions if item.name == "target")
                self.assertEqual(["first", "second"], [item.parameter_name
                                                       for item in semantics.bind_arguments(call, target)])

    def test_registered_coverage_does_not_claim_remaining_languages(self) -> None:
        """新增语言覆盖可见，Rust 等未接入安全规则的语言继续显式报告不支持。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.c").write_text("int main(void) { return 0; }", encoding="utf-8")
            result = SecurityScanner().scan(directory, dependency_graph={"nodes": [
                {"id": "main.c", "kind": "module", "lang": "c", "file": "main.c"},
                {"id": "main.rs", "kind": "module", "lang": "rust", "file": "main.rs"},
            ]})
        self.assertEqual(["c"], result.languages_analyzed)
        self.assertEqual(["rust"], result.unsupported_languages)


if __name__ == "__main__":
    unittest.main()

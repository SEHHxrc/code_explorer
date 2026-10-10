# -*- coding: utf-8 -*-
"""跨语言最小程序图与 Java 验证测试。"""

import tempfile
import unittest
from pathlib import Path

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.dependency_analyzer.constants import EXT_MAP
from backend.app.services.program_graph import ProgramGraphService
from backend.app.services.program_graph.frontends import AccessPath
from backend.app.services.program_graph.frontends import ProgramGraphFrontend
from backend.app.services.program_graph.frontends.treesitter import (
    TreeSitterFunctionCollector,
    TreeSitterProgramGraphFrontend,
)
from backend.app.services.syntax_analysis import extensions_for_language


class ProgramGraphTests(unittest.TestCase):
    """验证公共 CFG/DDG Pass 不依赖 Python 专用语法。"""

    def test_access_path_has_stable_cross_language_serialization(self):
        """公共访问路径应稳定编码属性、字符串下标和整数下标。"""
        path = AccessPath("payload").index("command").index(0).attribute("value")
        self.assertEqual('payload["command"][0].value', path.render())

    def test_frontend_registry_covers_every_dependency_language(self):
        """依赖分析语言集合扩展后，ProgramGraph 注册表不得静默落后。"""
        service = ProgramGraphService()
        self.assertEqual(
            set(EXT_MAP.values()),
            set(service.frontends.languages()),
        )
        self.assertTrue(all(
            isinstance(service.frontends.get(language), ProgramGraphFrontend)
            for language in service.frontends.languages()
        ))
        self.assertTrue(all(
            isinstance(service.frontends.get(language), TreeSitterProgramGraphFrontend)
            for language in service.frontends.languages()
        ))
        self.assertTrue(all(
            issubclass(
                service.frontends.get(language).collector_type,
                TreeSitterFunctionCollector,
            )
            for language in service.frontends.languages()
        ))
        self.assertTrue(all(
            service.frontends.get(language).extensions == extensions_for_language(language)
            for language in service.frontends.languages()
        ))

    def test_java_validates_shared_cfg_and_reaching_definitions(self):
        """Java 分支、循环、break、异常和重载应进入同一公共图协议。"""
        source = (
            "class Flow {\n"
            "  int choose(int input, boolean enabled) {\n"
            "    int value = input;\n"
            "    if (enabled) { value = value + 1; } else { value = 0; }\n"
            "    while (value < 10) {\n"
            "      value++;\n"
            "      if (value == 5) break;\n"
            "    }\n"
            "    try {\n"
            "      if (value < 0) throw new IllegalArgumentException();\n"
            "    } catch (IllegalArgumentException error) { value = 1; }\n"
            "    return value;\n"
            "  }\n"
            "  int choose(String input) { return input.length(); }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Flow.java").write_text(source, encoding="utf-8")
            dependency = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            artifact = ProgramGraphService().analyze(
                directory,
                dependency_graph=dependency["dependency_graph"],
            )
            dependency_node_ids = {
                str(node.get("id") or "")
                for node in dependency["dependency_graph"]["nodes"]
            }

        self.assertEqual(["java"], artifact.languages)
        self.assertEqual(2, artifact.coverage.function_count)
        methods = list(artifact.functions.values())
        self.assertEqual(1, len({item.symbol_id for item in methods}))
        self.assertEqual(2, len({item.method_id for item in methods}))
        self.assertTrue(all(item.symbol_id in dependency_node_ids for item in methods))
        method = next(item for item in methods if item.method_id.endswith("(int,boolean)"))
        cfg = [edge for edge in method.edges if edge.kind == "cfg"]
        branches = {edge.branch for edge in cfg}
        self.assertTrue({"true", "false", "back", "break", "return", "exception"} <= branches)
        self.assertTrue(all(
            edge.source in method.nodes and edge.target in method.nodes
            for edge in method.edges
        ))
        return_node = next(node for node in method.nodes.values() if node.kind == "return")
        value_defs = [
            edge for edge in method.edges
            if edge.kind == "reaching_def"
            and edge.target == return_node.node_id
            and edge.variable == "value"
        ]
        self.assertGreaterEqual(len(value_defs), 2)
        self.assertTrue(all(edge.certainty == "may" for edge in value_defs))

    def test_python_uses_the_same_cfg_and_dataflow_passes(self):
        """Python 仅负责降级语法，CFG、到达定义和值流由公共 Pass 生成。"""
        source = (
            "def choose(value, enabled):\n"
            "    result = value\n"
            "    if enabled:\n"
            "        result = result + 1\n"
            "    else:\n"
            "        result = 0\n"
            "    return result\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "flow.py").write_text(source, encoding="utf-8")
            artifact = ProgramGraphService().analyze(directory)

        self.assertEqual(["python"], artifact.languages)
        method = next(iter(artifact.functions.values()))
        self.assertTrue(any(edge.branch == "true" for edge in method.edges))
        self.assertTrue(any(edge.branch == "false" for edge in method.edges))
        self.assertTrue(any(edge.kind == "reaching_def" for edge in method.edges))
        value_edges = [edge for edge in method.edges if edge.kind == "value_flow"]
        self.assertTrue(value_edges)
        self.assertIn("value_flow", artifact.overlays)
        self.assertEqual(len(value_edges), artifact.coverage.value_flow_edge_count)
        self.assertTrue(any(
            edge.source_variable == "value"
            and edge.target_variable == "result"
            and edge.transfer_kind == "assignment"
            for edge in value_edges
        ))

    def test_python_unpacking_and_compound_assignment_keep_variable_mapping(self):
        """Python 等长解包应按位置配对，复合赋值应同时读取旧值和右值。"""
        source = (
            "def run(user, safe):\n"
            "    tainted, clean = user, safe\n"
            "    tainted += safe\n"
            "    return clean\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "flow.py").write_text(source, encoding="utf-8")
            artifact = ProgramGraphService().analyze(directory)

        method = next(iter(artifact.functions.values()))
        edges = [edge for edge in method.edges if edge.kind == "value_flow"]
        mappings = {
            (edge.source_variable, edge.target_variable)
            for edge in edges
        }
        self.assertIn(("user", "tainted"), mappings)
        self.assertIn(("safe", "clean"), mappings)
        self.assertNotIn(("user", "clean"), mappings)
        self.assertNotIn(("safe", "tainted"), {
            (edge.source_variable, edge.target_variable)
            for edge in edges
            if method.nodes[edge.target].code.startswith("tainted, clean")
        })
        compound = [
            edge for edge in edges
            if method.nodes[edge.target].code.startswith("tainted +=")
        ]
        self.assertEqual(
            {("tainted", "tainted"), ("safe", "tainted")},
            {(edge.source_variable, edge.target_variable) for edge in compound},
        )

    def test_java_multi_declaration_keeps_declarator_mapping(self):
        """Java 同一声明中的多个 declarator 不得互相污染输入输出映射。"""
        source = (
            "class Flow {\n"
            "  int run(int input, int safe) {\n"
            "    int left = input, right = safe;\n"
            "    return right;\n"
            "  }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Flow.java").write_text(source, encoding="utf-8")
            artifact = ProgramGraphService().analyze(directory)

        method = next(item for item in artifact.functions.values() if item.name == "run")
        declaration_edges = [
            edge for edge in method.edges
            if edge.kind == "value_flow"
            and method.nodes[edge.target].code.startswith("int left")
        ]
        mappings = {
            (edge.source_variable, edge.target_variable)
            for edge in declaration_edges
        }
        self.assertEqual({("input", "left"), ("safe", "right")}, mappings)

    def test_python_attribute_paths_remain_field_sensitive(self):
        """Python 同一对象的不同属性必须保持独立的 Def/Use 身份。"""
        source = (
            "def run(user, safe):\n"
            "    holder.tainted = user\n"
            "    holder.clean = safe\n"
            "    return holder.clean\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "flow.py").write_text(source, encoding="utf-8")
            artifact = ProgramGraphService().analyze(directory)

        method = next(iter(artifact.functions.values()))
        edges = [edge for edge in method.edges if edge.kind == "value_flow"]
        mappings = {
            (edge.source_variable, edge.target_variable)
            for edge in edges
        }
        self.assertIn(("user", "holder.tainted"), mappings)
        self.assertIn(("safe", "holder.clean"), mappings)
        self.assertNotIn(("user", "holder.clean"), mappings)
        return_node = next(node for node in method.nodes.values() if node.kind == "return")
        self.assertEqual(["holder.clean"], return_node.uses)

    def test_java_field_paths_remain_field_sensitive(self):
        """Java 同一实例的不同字段必须保持独立的 Def/Use 身份。"""
        source = (
            "class Flow {\n"
            "  String left; String right;\n"
            "  String run(String input, String safe) {\n"
            "    this.left = input;\n"
            "    this.right = safe;\n"
            "    return this.right;\n"
            "  }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Flow.java").write_text(source, encoding="utf-8")
            artifact = ProgramGraphService().analyze(directory)

        method = next(item for item in artifact.functions.values() if item.name == "run")
        mappings = {
            (edge.source_variable, edge.target_variable)
            for edge in method.edges
            if edge.kind == "value_flow"
        }
        self.assertIn(("input", "this.left"), mappings)
        self.assertIn(("safe", "this.right"), mappings)
        self.assertNotIn(("input", "this.right"), mappings)
        return_node = next(node for node in method.nodes.values() if node.kind == "return")
        self.assertEqual(["this.right"], return_node.uses)

    def test_python_static_subscripts_remain_element_sensitive(self):
        """Python 字符串和整数静态下标应形成规范化、可嵌套的变量身份。"""
        source = (
            "def run(user, safe):\n"
            "    payload['commands'][0] = user\n"
            "    payload[\"commands\"][1] = safe\n"
            "    return payload['commands'][1]\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "flow.py").write_text(source, encoding="utf-8")
            artifact = ProgramGraphService().analyze(directory)

        method = next(iter(artifact.functions.values()))
        mappings = {
            (edge.source_variable, edge.target_variable)
            for edge in method.edges
            if edge.kind == "value_flow"
        }
        self.assertIn(('user', 'payload["commands"][0]'), mappings)
        self.assertIn(('safe', 'payload["commands"][1]'), mappings)
        self.assertNotIn(('user', 'payload["commands"][1]'), mappings)
        return_node = next(node for node in method.nodes.values() if node.kind == "return")
        self.assertEqual(['payload["commands"][1]'], return_node.uses)

    def test_java_static_array_indices_remain_element_sensitive(self):
        """Java 常量数组下标应区分同一数组的不同元素。"""
        source = (
            "class Flow {\n"
            "  String[] values = new String[2];\n"
            "  String run(String input, String safe) {\n"
            "    values[0] = input;\n"
            "    values[1] = safe;\n"
            "    return values[1];\n"
            "  }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Flow.java").write_text(source, encoding="utf-8")
            artifact = ProgramGraphService().analyze(directory)

        method = next(item for item in artifact.functions.values() if item.name == "run")
        mappings = {
            (edge.source_variable, edge.target_variable)
            for edge in method.edges
            if edge.kind == "value_flow"
        }
        self.assertIn(("input", "values[0]"), mappings)
        self.assertIn(("safe", "values[1]"), mappings)
        self.assertNotIn(("input", "values[1]"), mappings)
        return_node = next(node for node in method.nodes.values() if node.kind == "return")
        self.assertEqual(["values[1]"], return_node.uses)

    def test_dependency_graph_limits_program_graph_file_scope(self):
        """公共程序图复用依赖分析文件范围，不重新发现额外文件。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Included.java").write_text(
                "class Included { int run(int value) { return value; } }",
                encoding="utf-8",
            )
            Path(directory, "Excluded.java").write_text(
                "class Excluded { int skip() { return 0; } }",
                encoding="utf-8",
            )
            graph = {
                "nodes": [{
                    "id": "Included.java",
                    "file": "Included.java",
                    "lang": "java",
                    "kind": "module",
                }],
                "links": [],
            }
            artifact = ProgramGraphService().analyze(
                directory,
                dependency_graph=graph,
            )

        self.assertEqual(1, artifact.coverage.files_considered)
        self.assertEqual(1, artifact.coverage.function_count)
        self.assertTrue(all(
            function.location.path == "Included.java"
            for function in artifact.functions.values()
        ))

    def test_requested_languages_avoid_unneeded_frontend_work(self):
        """调用方可只为已有规则的语言构图，其他已支持语言不应被解析。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(
                "def run(value):\n    return value\n",
                encoding="utf-8",
            )
            Path(directory, "Flow.java").write_text(
                "class Flow { int run(int value) { return value; } }",
                encoding="utf-8",
            )
            artifact = ProgramGraphService().analyze(
                directory,
                languages={"python"},
            )

        self.assertEqual(["python"], artifact.languages)
        self.assertEqual(1, artifact.coverage.files_considered)
        self.assertTrue(all(
            function.language == "python"
            for function in artifact.functions.values()
        ))

    def test_all_dependency_languages_share_cfg_value_flow_and_calls(self):
        """其余六种依赖语言应达到 Python/Java 相同的公共图契约能力。"""
        sources = {
            "flow.js": (
                "function run(input) { let value = input; "
                "if (value) { value++; } while (value < 3) { value++; } "
                "return sink(value); }\n"
            ),
            "flow.ts": (
                "function run(input: number): number { let value = input; "
                "if (value > 0) { value++; } while (value < 3) { value++; } "
                "return sink(value); }\n"
            ),
            "flow.go": (
                "package sample\n"
                "func run(input int) int { value := input; "
                "if value > 0 { value++ }; for value < 3 { value++ }; "
                "return sink(value) }\n"
            ),
            "flow.c": (
                "int sink(int value); int run(int input) { int value = input; "
                "if (value) { value++; } while (value < 3) { value++; } "
                "return sink(value); }\n"
            ),
            "flow.cpp": (
                "int sink(int value); class Flow { public: int run(int input) { "
                "int value = input; if (value) { value++; } "
                "while (value < 3) { value++; } return sink(value); } };\n"
            ),
            "flow.rs": (
                "fn sink(value: i32) -> i32 { value }\n"
                "fn run(input: i32) -> i32 { let mut value = input; "
                "if value > 0 { value += 1; }; while value < 3 { value += 1; } "
                "sink(value) }\n"
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, source in sources.items():
                Path(directory, name).write_text(source, encoding="utf-8")
            dependency = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            artifact = ProgramGraphService().analyze(
                directory,
                dependency_graph=dependency["dependency_graph"],
            )
            dependency_node_ids = {
                str(node.get("id") or "")
                for node in dependency["dependency_graph"]["nodes"]
            }

        expected = {"javascript", "typescript", "go", "c", "cpp", "rust"}
        self.assertEqual(expected, set(artifact.languages))
        self.assertEqual(len(sources), artifact.coverage.files_considered)
        self.assertEqual(len(sources), artifact.coverage.files_parsed)
        self.assertEqual([], artifact.failures)
        run_functions = {
            function.language: function
            for function in artifact.functions.values()
            if function.name == "run"
        }
        self.assertEqual(expected, set(run_functions))
        for language, function in run_functions.items():
            with self.subTest(language=language):
                self.assertIn("input", function.parameters)
                self.assertIn(function.symbol_id, dependency_node_ids)
                self.assertTrue(any(
                    edge.kind == "cfg" and edge.branch == "true"
                    for edge in function.edges
                ))
                self.assertTrue(any(
                    edge.kind == "cfg" and edge.branch == "back"
                    for edge in function.edges
                ))
                self.assertTrue(any(
                    edge.kind == "reaching_def" and edge.variable == "value"
                    for edge in function.edges
                ))
                self.assertTrue(any(
                    edge.kind == "value_flow"
                    and edge.source_variable == "input"
                    and edge.target_variable == "value"
                    for edge in function.edges
                ))
                sink_call = next(
                    call
                    for node in function.nodes.values()
                    for call in node.calls
                    if call.name == "sink"
                )
                self.assertIn("value", sink_call.positional_arguments[0])
                self.assertTrue(sink_call.callsite_id.startswith("callsite:"))

    def test_remaining_languages_match_precise_value_flow_progress(self):
        """六种薄前端应同时具备绑定配对、访问路径、复合赋值和接收者调用。"""
        sources = {
            "flow.js": (
                "function run(input, safe) { let left = input, right = safe; "
                "box.value = input; box[\"tainted\"] = input; "
                "box[\"safe\"] = safe; box.value += safe; "
                "return client.sink(box[\"safe\"]); }\n"
            ),
            "flow.ts": (
                "function run(input: string, safe: string) { "
                "let left = input, right = safe; box.value = input; "
                "box[\"tainted\"] = input; "
                "box[\"safe\"] = safe; box.value += safe; "
                "return client.sink(box[\"safe\"]); }\n"
            ),
            "flow.go": (
                "package sample\nfunc run(input, safe string) string { "
                "left, right := input, safe; box.value = input; items[0] = input; "
                "items[1] = safe; box.value += safe; return client.sink(items[1]) }\n"
            ),
            "flow.c": (
                "int run(int input, int safe) { int left = input, right = safe; "
                "box.value = input; items[0] = input; items[1] = safe; "
                "box.value += safe; return client.sink(items[1]); }\n"
            ),
            "flow.cpp": (
                "int run(int input, int safe) { int left = input, right = safe; "
                "box.value = input; items[0] = input; items[1] = safe; "
                "box.value += safe; return client.sink(items[1]); }\n"
            ),
            "flow.rs": (
                "fn run(input: i32, safe: i32) -> i32 { "
                "let (left, right) = (input, safe); box.value = input; "
                "items[0] = input; items[1] = safe; "
                "box.value += safe; client.sink(items[1]) }\n"
            ),
        }
        expected_element = {
            "javascript": "box.safe",
            "typescript": "box.safe",
            "go": "items[1]",
            "c": "items[1]",
            "cpp": "items[1]",
            "rust": "items[1]",
        }
        tainted_element = {
            "javascript": "box.tainted",
            "typescript": "box.tainted",
            "go": "items[0]",
            "c": "items[0]",
            "cpp": "items[0]",
            "rust": "items[0]",
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, source in sources.items():
                Path(directory, name).write_text(source, encoding="utf-8")
            artifact = ProgramGraphService().analyze(directory)

        self.assertEqual([], artifact.failures)
        functions = {
            function.language: function
            for function in artifact.functions.values()
            if function.name == "run"
        }
        self.assertEqual(set(expected_element), set(functions))
        for language, function in functions.items():
            with self.subTest(language=language):
                transfers = [
                    transfer
                    for node in function.nodes.values()
                    for transfer in node.value_transfers
                ]
                mappings = {
                    (input_name, transfer.output_variable)
                    for transfer in transfers
                    for input_name in transfer.input_variables
                }
                self.assertIn(("input", "left"), mappings)
                self.assertIn(("safe", "right"), mappings)
                self.assertNotIn(("input", "right"), mappings)
                self.assertNotIn(("safe", "left"), mappings)
                self.assertIn(("input", "box.value"), mappings)
                self.assertIn(("input", tainted_element[language]), mappings)
                self.assertIn(("safe", expected_element[language]), mappings)
                self.assertNotIn(("input", expected_element[language]), mappings)
                compound = next(
                    transfer
                    for node in function.nodes.values()
                    if "+=" in node.code
                    for transfer in node.value_transfers
                    if transfer.output_variable == "box.value"
                )
                self.assertEqual({"box.value", "safe"}, set(compound.input_variables))
                sink_call = next(
                    call
                    for node in function.nodes.values()
                    for call in node.calls
                    if call.name == "sink"
                )
                self.assertEqual(["client"], sink_call.receiver_identifiers)
                self.assertEqual(
                    [[expected_element[language]]],
                    sink_call.positional_arguments,
                )

    def test_language_specific_callable_identities_match_dependency_graph(self):
        """箭头函数、类方法、接收者方法和 impl 方法应复用依赖图 FQN。"""
        sources = {
            "arrow.js": "const run = (input) => { let value = input; return value; };\n",
            "Flow.ts": (
                "class Flow { run(input: number): number { "
                "let value = input; return value; } }\n"
            ),
            "flow.go": (
                "package sample\n"
                "type Flow struct{}\n"
                "func (flow *Flow) run(input int) int { value := input; return value }\n"
            ),
            "flow.rs": (
                "struct Flow;\n"
                "impl Flow { fn run(&self, input: i32) -> i32 { "
                "let value = input; value } }\n"
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, source in sources.items():
                Path(directory, name).write_text(source, encoding="utf-8")
            dependency = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            artifact = ProgramGraphService().analyze(
                directory,
                dependency_graph=dependency["dependency_graph"],
            )
            dependency_node_ids = {
                str(node.get("id") or "")
                for node in dependency["dependency_graph"]["nodes"]
            }

        functions = [
            function for function in artifact.functions.values()
            if function.name == "run"
        ]
        self.assertEqual(4, len(functions))
        self.assertTrue(all(
            function.symbol_id in dependency_node_ids
            for function in functions
        ))
        self.assertEqual(
            {"javascript", "typescript", "go", "rust"},
            {function.language for function in functions},
        )


if __name__ == "__main__":
    unittest.main()

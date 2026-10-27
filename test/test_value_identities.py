"""声明身份进入公共到达定义/值流；名字只展示，赋值版本由节点区分。"""

import ast
import unittest

from backend.app.services.program_graph import FunctionProgramGraph, ProgramGraphNode, ProgramGraphEdge, ProgramGraphLocation, ProgramValueTransfer
from backend.app.services.program_graph.passes import ReachingDefinitionsPass, ValueFlowPass
from backend.app.services.security_analysis.flow_analysis.program_graph import ProgramGraphSecurityFlowAnalyzer
from backend.app.services.security_analysis.language_adapters.python import _PythonIRVisitor
from backend.app.services.security_analysis.ir import SecurityProgramIR
from backend.app.services.security_analysis.rule_engine import SecurityRuleEngine
from backend.app.services.security_analysis.rules.python import PYTHON_RULE_PACKS


class ValueIdentityTests(unittest.TestCase):
    """公共算法正反例，不依赖某种语言的编译器或原生 SSA。"""

    def graph(self, *, inner_reads_outer: bool = False, outer_identity: str = "decl:outer", inner_identity: str = "decl:inner") -> FunctionProgramGraph:
        """构造两个同名声明的顺序 CFG，内层操作可读外层进行初始化。"""
        location = ProgramGraphLocation(path="sample", line=1, column=1, end_line=1, end_column=20, location_id="loc")
        nodes = {
            "outer": ProgramGraphNode(node_id="outer", method_id="f", kind="method_entry", language="test",
                definitions=["value"], definition_declarations={"value": outer_identity}),
            "inner": ProgramGraphNode(node_id="inner", method_id="f", kind="assignment", language="test",
                definitions=["value"], uses=["value"] if inner_reads_outer else [],
                definition_declarations={"value": inner_identity},
                use_declarations={"value": outer_identity}, value_transfers=[ProgramValueTransfer(
                    output_variable="value", input_variables=["value"] if inner_reads_outer else [], transfer_kind="assignment")]),
            "inner_sink": ProgramGraphNode(node_id="inner_sink", method_id="f", kind="call", language="test",
                uses=["value"], use_declarations={"value": inner_identity}),
            "outer_sink": ProgramGraphNode(node_id="outer_sink", method_id="f", kind="call", language="test",
                uses=["value"], use_declarations={"value": outer_identity}),
        }
        edges = [ProgramGraphEdge(edge_id=str(index), source=source, target=target, kind="cfg")
                 for index, (source, target) in enumerate(zip(nodes, list(nodes)[1:]))]
        graph = FunctionProgramGraph(method_id="f", symbol_id="f", name="f", language="test", location=location,
            parameters=["value"], entry_node_id="outer", exit_node_id="outer_sink", nodes=nodes, edges=edges)
        return ValueFlowPass().apply(ReachingDefinitionsPass().apply(graph))

    def path(self, graph: FunctionProgramGraph, start: str, end: str) -> list | None:
        """查询公共分析器真实使用的值身份搜索，不通过测试自建遍历替代。"""
        return ProgramGraphSecurityFlowAnalyzer()._path(graph, source_node_id=start,
            source_names=("value",), sink_node_id=end, sink_names={"value"})

    def test_inner_clean_definition_does_not_kill_outer_source(self) -> None:
        """同名不同声明各自 KILL；内层安全定义不能杀死外层污点。"""
        graph = self.graph()
        self.assertIsNotNone(self.path(graph, "outer", "outer_sink"))
        self.assertIsNone(self.path(graph, "outer", "inner_sink"))
        self.assertIsNone(self.path(graph, "inner", "outer_sink"))

    def test_same_name_initializer_binds_read_and_write_separately(self) -> None:
        """inner value = outer value 同名但输入输出身份不同，两条真实链均保留。"""
        graph = self.graph(inner_reads_outer=True)
        self.assertIsNotNone(self.path(graph, "outer", "inner_sink"))
        self.assertIsNotNone(self.path(graph, "outer", "outer_sink"))
        edge = next(item for item in graph.edges if item.kind == "value_flow" and item.target == "inner")
        self.assertEqual(("decl:outer", "decl:inner", "decl:outer"),
                         (edge.source_value_id, edge.target_value_id, edge.read_value_id))

    def test_reassignment_to_same_declaration_still_kills_previous_value(self) -> None:
        """声明身份不是赋值版本；同一变量写入安全值仍应切断旧污点。"""
        graph = self.graph(inner_identity="decl:outer")
        self.assertIsNone(self.path(graph, "outer", "outer_sink"))

    def test_missing_identity_preserves_legacy_conservative_path(self) -> None:
        """读取绑定缺失不能静默证明安全，也不能用未知写入杀死已绑定声明。"""
        graph = self.graph()
        nodes = {key: node.model_copy(update={"use_declarations": {}}) if key == "outer_sink" else node
                 for key, node in graph.nodes.items()}
        graph = ValueFlowPass().apply(ReachingDefinitionsPass().apply(graph.model_copy(update={"nodes": nodes})))
        self.assertIsNotNone(self.path(graph, "outer", "outer_sink"))

    def test_same_operation_read_source_uses_read_declaration(self) -> None:
        """属性读取型 Source 没有写入定义，仍能在同一调用位置进入已建模值边界。"""
        graph = self.graph()
        node = graph.nodes["inner_sink"]
        self.assertIsNotNone(self.path(graph, "inner_sink", "inner_sink"))
        self.assertTrue(node.use_declarations)

    def test_unknown_child_write_does_not_hide_bound_parent_value(self) -> None:
        """未知成员写入不能冒充确定覆盖而排除已绑定父值，缺口须保守保留。"""
        graph = self.graph()
        nodes = dict(graph.nodes)
        nodes["inner"] = nodes["inner"].model_copy(update={
            "definitions": ["value.field"], "definition_declarations": {},
            "value_transfers": [ProgramValueTransfer(output_variable="value.field", transfer_kind="assignment")],
        })
        nodes["outer_sink"] = nodes["outer_sink"].model_copy(update={"uses": ["value.field"]})
        graph = ValueFlowPass().apply(ReachingDefinitionsPass().apply(graph.model_copy(update={"nodes": nodes})))
        path = ProgramGraphSecurityFlowAnalyzer()._path(graph, source_node_id="outer", source_names=("value",),
            sink_node_id="outer_sink", sink_names={"value.field"})
        self.assertIsNotNone(path)
        self.assertTrue(any(edge.certainty == "may" for edge in path))


class PythonAPIBindingTests(unittest.TestCase):
    """在既有 AST/规则链验证遮蔽与导入，不读取或运行目标源码。"""

    def sinks(self, source: str) -> tuple[set[int], list]:
        """返回实际 Shell Sink 行号和调用 IR，源码只作静态解析。"""
        tree = ast.parse(source)
        visitor = _PythonIRVisitor("sample.py", tree)
        visitor.visit(tree)
        program = SecurityProgramIR(language="python", calls=visitor.calls, functions=visitor.functions)
        result = SecurityRuleEngine().evaluate(program, PYTHON_RULE_PACKS)
        return {item.location.line for item in result.sinks if item.rule_id == "PY-SINK-SHELL"}, visitor.calls

    def test_parameter_and_local_rebinding_do_not_impersonate_import(self) -> None:
        """同名参数和函数内赋值遮蔽导入，但真实模块调用保留。"""
        lines, calls = self.sinks('''import os as api
def real(value):
 api.system(value)
def parameter(api, value):
 api.system(value)
def local(value):
 api.system(value)
 api = value
''')
        self.assertEqual({3}, lines)
        self.assertEqual([False, True, True], [item.callee_shadowed for item in calls])

    def test_nested_imports_do_not_leak_and_local_import_is_preserved(self) -> None:
        """一个函数中的导入不覆盖模块别名，也不泄漏到另一个函数。"""
        lines, _ = self.sinks('''import os as api
def local(value):
 import other as api
 api.system(value)
def real(value):
 api.system(value)
def nested(value):
 import os as own
 own.system(value)
''')
        self.assertEqual({6, 9}, lines)

    def test_builtin_shadow_lambda_and_comprehension_names(self) -> None:
        """lambda/推导式目标不冒充导入，且不污染后续真实调用。"""
        lines, _ = self.sinks('''import os as api
def run(values):
 helper = lambda api: api.system("x")
 result = [api.system("x") for api in values]
 api.system("real")
''')
        self.assertEqual({5}, lines)


if __name__ == "__main__":
    unittest.main()

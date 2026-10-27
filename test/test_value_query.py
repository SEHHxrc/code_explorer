"""原生事实到公共值查询的离线验证；目标程序不执行，不调用模型。"""

from pathlib import Path
import tempfile
import unittest

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.program_graph import ProgramGraphService
from backend.app.services.program_graph.value_query import ValueFlowQuery
from backend.app.services.program_graph.passes import ReachingDefinitionsPass
from backend.app.services.semantic_index import SemanticIndex
from backend.app.services.security_analysis import SecurityAnalysisService, SecurityEvidenceCatalog


class ValueQueryTests(unittest.TestCase):
    """原生 Def-Use 必须实际影响查询，且不能与回退边无条件并集。"""

    def setUp(self) -> None:
        """每个测试独立的临时模块，不安装或执行其代码。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def go(self, source: str) -> tuple:
        """返回真实 Go 工具产物、公共图和投影，缺工具不能默认为原生测试成功。"""
        (self.root / "go.mod").write_text("module example.test/query\n\ngo 1.23.0\n", encoding="utf8")
        (self.root / "main.go").write_text(source, encoding="utf8")
        result = UnifiedCodeAnalyzer(str(self.root), max_workers=1).run_full_analysis()
        index = SemanticIndex.load(result["semantic_index"])
        self.assertTrue(index.artifact.native_ssa, index.artifact.backend_runs)
        graph = ProgramGraphService().analyze(self.root, semantic_index=index)
        return result, index, graph, ValueFlowQuery(index).project(graph)

    def test_go_phi_and_kill_replace_public_def_use(self) -> None:
        """φ 保留两分支候选；常量覆盖不沿旧输入值传播，旧边不留在原生函数里。"""
        _, index, original, projected = self.go('''package sample
func Flow(input string, flag bool) string {
 value := input
 if flag { value = "safe" }
 return value
}
func Clean(input string) string { input = "safe"; return input }
''')
        before = original.model_dump_json()
        for function in projected.functions.values():
            self.assertEqual("native_ssa_def_use", function.value_backend.backend, function.value_backend)
            self.assertTrue(all(edge.edge_id.startswith("ssa-def-use:") for edge in function.edges if edge.kind == "reaching_def"))
            returned = next(node for node in function.nodes.values() if node.kind == "return")
            reads = [edge for edge in function.edges if edge.kind == "reaching_def" and edge.target == returned.node_id]
            if function.name == "Flow":
                self.assertEqual(2, len(reads))
            else:
                self.assertEqual(1, len(reads))
                self.assertNotEqual(function.entry_node_id, reads[0].source)
        self.assertEqual(before, original.model_dump_json())
        self.assertEqual(projected, type(projected).model_validate_json(projected.model_dump_json()))
        self.assertEqual(projected, ValueFlowQuery(index).project(original))
        self.assertTrue(all(ReachingDefinitionsPass().apply(function).value_backend is None for function in projected.functions.values()))
        fallback = ValueFlowQuery(None).project(projected)
        self.assertTrue(all(function.value_backend.backend != "native_ssa_def_use" for function in fallback.functions.values()))
        self.assertFalse(any(edge.edge_id.startswith("ssa-def-use:") for function in fallback.functions.values() for edge in function.edges))

    def test_go_production_flow_calls_returns_and_exact_sensitive_argument(self) -> None:
        """原生函数真正进入安全求解；公共实参/返回继续隔离，常量命令不被污点环境参数污染。"""
        result, _, _, _ = self.go('''package sample
import ("os"; "os/exec")
func forward(value string) string { return value }
func run() {
 value := os.Getenv("CMD")
 command := forward(value)
 exec.Command(command)
 exec.Command("fixed", value)
}
''')
        pack = SecurityAnalysisService().analyze(project_root=self.root,
            dependency_graph=result["dependency_graph"], semantic_index=result["semantic_index"])
        self.assertFalse(pack.scan_failures)
        self.assertEqual({7}, {pack.facts[item.sink_fact_id].location.line for item in pack.candidates if item.dataflow_verified})
        self.assertGreater(pack.flow_diagnostics["value_backend_counts"].get("native_ssa_def_use", 0), 0)
        catalog = SecurityEvidenceCatalog(pack.model_dump())
        delivered, offset = [], 0
        while True:
            page = catalog.detail("_coverage", section="backends", offset=offset, limit=1)
            delivered.extend(page["items"])
            if not page["has_more"]:
                break
            offset = page["next_offset"]
        self.assertEqual(len(pack.flow_diagnostics["value_backends"]), len(delivered))
        self.assertNotIn("value_backends", catalog.detail("_coverage")["items"][0]["flow_diagnostics"])

    def test_memory_and_unknown_native_operations_use_whole_function_fallback(self) -> None:
        """Store/Load 不能伪装为地址污点，函数回退时完整保留原公共结果，不留下半套原生边。"""
        _, _, original, projected = self.go('''package sample
type Box struct { Value string }
func use(box *Box, value string) string { box.Value=value; return box.Value }
''')
        for key, function in projected.functions.items():
            self.assertNotEqual("native_ssa_def_use", function.value_backend.backend)
            self.assertTrue(function.value_backend.fallback_reason.startswith("native_operation_not_modelled:"))
            self.assertEqual(original.functions[key].edges, function.edges)
        for argument, expected in (("value", {10}), ('"fixed"', set())):
            result, _, _, _ = self.go('''package sample
import("os";"os/exec")
type Box struct { Command string }
func set(box *Box,value string){box.Command=value}
func run(){
 value:=os.Getenv("CMD")
 _=value
 box:=&Box{Command:"fixed"}
 set(box,ARG)
 exec.Command(box.Command)
}
'''.replace("ARG", argument))
            pack = SecurityAnalysisService().analyze(project_root=self.root,
                dependency_graph=result["dependency_graph"], semantic_index=result["semantic_index"])
            self.assertEqual(expected, {pack.facts[item.sink_fact_id].location.line for item in pack.candidates if item.dataflow_verified})
            selected = next(item for item in pack.flow_diagnostics["value_backends"].values() if item["symbol"] == "main.go::run")
            self.assertNotEqual("native_ssa_def_use", selected["backend"])
            self.assertTrue(selected["fallback_reason"].startswith("native_operation_not_modelled:"), selected)

    def test_native_phi_flow_reaches_only_sink_before_clean_overwrite(self) -> None:
        """同一真实函数全程选原生 Def-Use；φ 不丢不安全分支，第二次常量覆盖正确 KILL。"""
        result, _, _, _ = self.go('''package sample
import("os";"os/exec")
func run(flag bool){
 command:=os.Getenv("CMD")
 if flag {command="fixed"}
 exec.Command(command)
 command="fixed"
 exec.Command(command)
}
''')
        pack = SecurityAnalysisService().analyze(project_root=self.root,
            dependency_graph=result["dependency_graph"], semantic_index=result["semantic_index"])
        self.assertEqual({6}, {pack.facts[item.sink_fact_id].location.line for item in pack.candidates if item.dataflow_verified})
        selected = next(item for item in pack.flow_diagnostics["value_backends"].values() if item["symbol"] == "main.go::run")
        self.assertEqual("native_ssa_def_use", selected["backend"], selected)

    def test_corrupt_and_missing_identity_fall_back_without_partial_edges(self) -> None:
        """历史/损坏事实不能中断整仓分析；缺少源码声明绑定时不按变量名称冒充精确 SSA。"""
        _, index, graph, _ = self.go('package sample\nfunc run(value string) string {return value}\n')
        broken = index.artifact.model_copy(deep=True)
        native = broken.native_ssa["main.go::run"]
        first = next(iter(native.instructions.values()))
        first.operands.append("missing-native-value")
        view = ValueFlowQuery(SemanticIndex(broken)).project(graph)
        self.assertEqual("invalid_native_value_reference", next(iter(view.functions.values())).value_backend.fallback_reason)
        functions = {key: function.model_copy(update={"nodes": {node_id: node.model_copy(update={
            "definition_declarations": {}, "use_declarations": {}}) for node_id, node in function.nodes.items()}})
            for key, function in graph.functions.items()}
        view = ValueFlowQuery(index).project(graph.model_copy(update={"functions": functions}))
        self.assertTrue(next(iter(view.functions.values())).value_backend.fallback_reason.startswith("native_variable_identity_not_mapped:"))

    def test_typescript_uses_compiler_binding_without_claiming_native_ssa(self) -> None:
        """TS 使用原生声明身份+公共求解；遮蔽和同名变量不能被标签掩盖。"""
        (self.root / "main.ts").write_text('function run(value:string){ {let value="safe";eval(value);} eval(value); }', encoding="utf8")
        result = UnifiedCodeAnalyzer(str(self.root), max_workers=1).run_full_analysis()
        index = SemanticIndex.load(result["semantic_index"])
        self.assertTrue(index.artifact.variable_references)
        graph = ValueFlowQuery(index).project(ProgramGraphService().analyze(self.root, semantic_index=index))
        function = next(iter(graph.functions.values()))
        self.assertEqual("compiler_bound_cfg", function.value_backend.backend)
        self.assertEqual("native_ssa_not_provided", function.value_backend.fallback_reason)
        self.assertEqual("compiler", function.value_backend.identity_backend)

    def test_receiver_and_multiple_return_slots_keep_common_call_contract(self) -> None:
        """接收者不占普通参数位置，返回数据与安全兄弟分量经原生查询后仍隔离。"""
        result, _, _, _ = self.go('''package sample
import("os";"os/exec")
type Runner struct{}
func(r *Runner) execute(value string){exec.Command(value)}
func split(value string)(string,string){return value,"fixed"}
func run(){
 value:=os.Getenv("CMD")
 data,safe:=split(value)
 receiver:=&Runner{}
 receiver.execute(data)
 exec.Command(safe)
}
''')
        pack = SecurityAnalysisService().analyze(project_root=self.root,
            dependency_graph=result["dependency_graph"], semantic_index=result["semantic_index"])
        self.assertFalse(any(item.get("reason") == "program_graph_dataflow_error" for item in pack.scan_failures))
        self.assertEqual({4}, {pack.facts[item.sink_fact_id].location.line for item in pack.candidates if item.dataflow_verified})
        selected = {item["symbol"]: item for item in pack.flow_diagnostics["value_backends"].values()}
        self.assertEqual("native_ssa_def_use", selected["main.go::split"]["backend"])
        method = next(item for symbol, item in selected.items() if symbol.endswith("::execute"))
        self.assertEqual("native_ssa_def_use", method["backend"], method)

    def test_go_anonymous_handler_has_distinct_ssa_and_variable_owner(self) -> None:
        """匿名网络入口不借用外层函数身份；编译器与公共语法的源码范围对应同一符号。"""
        _, index, graph, projected = self.go('''package sample
import("net/http";"os/exec")
func register() {
 http.HandleFunc("/run", func(w http.ResponseWriter, r *http.Request) {
  value:=r.FormValue("cmd")
  exec.Command(value)
 })
}
''')
        handler = next(function for function in graph.functions.values() if "anonymous@" in function.symbol_id)
        self.assertIsNotNone(index.native_ssa_for(handler.symbol_id))
        refs = [fact for fact in index.artifact.variable_references.values() if fact.owner_symbol == handler.symbol_id]
        self.assertTrue(refs)
        self.assertFalse(any(fact.owner_unresolved_reason for fact in refs))
        self.assertEqual("native_ssa_def_use", projected.functions[handler.method_id].value_backend.backend,
                         projected.functions[handler.method_id].value_backend)


if __name__ == "__main__":
    unittest.main()

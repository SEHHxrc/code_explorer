"""返回契约及真实 Source→调用→返回→敏感参数端到端验证；不请求模型。"""

from pathlib import Path
import tempfile
import unittest

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.program_graph import ProgramGraphArtifact, ProgramGraphService
from backend.app.services.security_analysis.service import SecurityAnalysisService
from backend.app.services.value_binding import BindingPattern, BindingValue, PatternMember, ValueMember
from backend.app.services.value_binding.returns import ReturnBinding, resolve_return
from test.test_go_network_security import NoNativeResolver


class ReturnBindingContractTests(unittest.TestCase):
    """相同绑定器必须区分一个聚合对象和原生多个返回分量。"""

    def test_single_aggregate_and_native_multiple_are_not_interchangeable(self) -> None:
        """Python 式整体接收保留元素；Go 式多返回不能伪装为一个元组。"""
        parts = (BindingValue(variables=("input",)), BindingValue(kind="literal"))
        aggregate = BindingValue(kind="array", variables=("input",), members=tuple(
            ValueMember(index, value) for index, value in enumerate(parts)
        ))
        whole = BindingPattern(kind="name", name="pair")
        bound = resolve_return(ReturnBinding(mode="single", values=(aggregate,)), whole)
        self.assertEqual({"pair": ("input",), "pair[0]": ("input",), "pair[1]": ()},
                         {item.output: item.inputs for item in bound.projections})
        invalid = resolve_return(ReturnBinding(mode="multiple", values=parts), whole)
        self.assertFalse(invalid.projections)
        self.assertIn("multiple_return_requires_component_receivers", invalid.diagnostics)

    def test_wrong_arity_and_unknown_shape_are_not_safe_empty_values(self) -> None:
        """错误接收数量与未建模形状必须显式诊断，不能静默生成干净接收值。"""
        receiver = BindingPattern(kind="array", members=(
            PatternMember(0, BindingPattern(kind="name", name="only")),
        ))
        values = (BindingValue(variables=("input",)), BindingValue(kind="literal"))
        self.assertEqual(("return_receiver_arity_mismatch",),
                         resolve_return(ReturnBinding(mode="multiple", values=values), receiver).diagnostics)
        self.assertEqual(("return_shape_unknown",), resolve_return(ReturnBinding(), receiver).diagnostics)

        nested_receiver = BindingPattern(kind="array", members=(PatternMember(0, receiver),))
        nested_value = BindingValue(kind="array", members=(ValueMember(0, BindingValue(
            kind="array", members=tuple(ValueMember(index, value) for index, value in enumerate(values)),
        )),))
        self.assertEqual(("return_receiver_arity_mismatch",), resolve_return(
            ReturnBinding(mode="single", values=(nested_value,)), nested_receiver,
        ).diagnostics)


class ReturnBindingEndToEndTests(unittest.TestCase):
    """从临时源码构建依赖图、语义索引、CFG/DFG 和安全证据，不拼装模拟流边。"""

    def analyze(self, path: str, source: str, *, extra_files: dict[str, str] | None = None) -> tuple[ProgramGraphArtifact, set[int]]:
        """返回持久化往返后的程序图和真实污点链到达的 Sink 行号。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, path).write_text(source, encoding="utf-8")
            for name, content in (extra_files or {}).items():
                Path(directory, name).write_text(content, encoding="utf-8")
            graph = ProgramGraphService().analyze(directory)
            restored = ProgramGraphArtifact.model_validate_json(graph.model_dump_json())
            self.assertEqual(graph, restored)
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1, semantic_resolver=NoNativeResolver()).run_full_analysis()
            pack = SecurityAnalysisService().analyze(project_root=directory,
                dependency_graph=analysis["dependency_graph"], semantic_index=analysis["semantic_index"])
        self.assertFalse(any(item.get("reason") == "program_graph_dataflow_error" for item in pack.scan_failures), pack.scan_failures)
        return restored, {pack.facts[item.sink_fact_id].location.line for item in pack.candidates if item.dataflow_verified}

    def assert_marked_sinks(self, path: str, source: str) -> ProgramGraphArtifact:
        """仅带 EXPECT 的危险实参应形成完整路径；SAFE 不是靠候选数量推断。"""
        expected = {number for number, line in enumerate(source.splitlines(), 1) if "EXPECT" in line}
        graph, reached = self.analyze(path, source)
        self.assertEqual(expected, reached)
        return graph

    def test_python_whole_receiver_and_direct_unpack_from_caller_source(self) -> None:
        """整体接收、直接解包、列表接收、嵌套解包和多调用实例保持分量隔离。"""
        graph = self.assert_marked_sinks("app.py", '''import os
def split(value):
 return value, "fixed"
def nested(value):
 return ("fixed", (value, "fixed"))
def run():
 value = input()
 pair = split(value)
 os.system(pair[0]) # EXPECT
 os.system(pair[1]) # SAFE
 later_command, later_safe = pair
 os.system(later_command) # EXPECT
 os.system(later_safe) # SAFE
 command, safe = split(value)
 os.system(command) # EXPECT
 os.system(safe) # SAFE
 [command2, safe2] = split(value)
 os.system(command2) # EXPECT
 os.system(safe2) # SAFE
 head, (command3, safe3) = nested(value)
 os.system(head) # SAFE
 os.system(command3) # EXPECT
 os.system(safe3) # SAFE
 clean = split("constant")
 os.system(clean[0]) # SAFE
 pair[0] = "replaced"
 os.system(pair[0]) # SAFE
''')
        split = next(item for item in graph.functions.values() if item.name == "split")
        returned = next(item for item in split.nodes.values() if item.kind == "return")
        self.assertEqual("single", returned.return_binding.mode)
        self.assertEqual("array", returned.return_binding.values[0].kind)

    def test_python_source_inside_callee_and_single_list_return(self) -> None:
        """反向查找调用者也必须保留 Python 一个变量接收多个元素的形状。"""
        self.assert_marked_sinks("app.py", '''import os
def load():
 value = input()
 return [value, "fixed"]
def run():
 pair = load()
 os.system(pair[0]) # EXPECT
 os.system(pair[1]) # SAFE
 command, safe = load()
 os.system(command) # EXPECT
 os.system(safe) # SAFE
''')

    def test_python_return_members_wait_for_actual_call_results(self) -> None:
        """返回成员中的 Source 调用不能漏掉；普通调用实参不能冒充真实返回。"""
        self.assert_marked_sinks("app.py", '''import os
def fixed(value):
 return "constant"
def same(value):
 return value
def load():
 return input(), "fixed"
def split(value):
 return fixed(value), same(value)
def run():
 pair = load()
 os.system(pair[0]) # EXPECT
 os.system(pair[1]) # SAFE
 result = split(input())
 os.system(result[0]) # SAFE
 os.system(result[1]) # EXPECT
''')

    def test_python_cross_file_return_and_starred_tail_receiver(self) -> None:
        """多文件真实调用边与单变量/星号尾部接收共用契约，不靠文件内字符串配对。"""
        source = '''import os
from helper import split
def run():
 pair = split(input())
 os.system(pair[0])
 os.system(pair[1])
 head, *tail = split(input())
 os.system(head)
 os.system(tail[0])
 os.system(tail[1])
'''
        _, reached = self.analyze("app.py", source, extra_files={"helper.py": '''def split(value):
 return value, "fixed", value
'''})
        self.assertEqual({5, 8, 10}, reached)

    def test_go_native_multiple_returns_and_discarded_components(self) -> None:
        """Go 位置与显式丢弃槽不压缩；Source 在调用者/被调者都可闭合。"""
        graph = self.assert_marked_sinks("main.go", '''package main
import ("os"; "os/exec")
func split(value string) (string, string) { return value, "fixed" }
func load() (string, string) { value := os.Getenv("CMD"); return value, "fixed" }
func main() {
 value := os.Getenv("CMD")
 command, safe := split(value)
 exec.Command(command) // EXPECT
 exec.Command(safe) // SAFE
 _, safe2 := split(value)
 exec.Command(safe2) // SAFE
 command2, _ := split(value)
 exec.Command(command2) // EXPECT
 command3, safe3 := load()
 exec.Command(command3) // EXPECT
 exec.Command(safe3) // SAFE
}
''')
        split = next(item for item in graph.functions.values() if item.name == "split")
        returned = next(item for item in split.nodes.values() if item.kind == "return")
        self.assertEqual("multiple", returned.return_binding.mode)


if __name__ == "__main__":
    unittest.main()

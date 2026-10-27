"""高影响多语言污点缺口回归；只解析源码，不执行样例。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.program_graph import ProgramGraphArtifact, ProgramGraphService
from backend.app.services.security_analysis import (
    SecurityAnalysisService,
    SecurityEvidenceCatalog,
)
from backend.app.services.security_analysis.contracts import SecurityEvidencePack
from backend.app.services.security_analysis.flow_analysis.diagnostics import FlowDiagnostics
from backend.app.services.security_analysis.scanner import SecurityScanner
from backend.app.services.security_analysis.flow_analysis import (
    ProgramGraphSecurityFlowAnalyzer,
)
from backend.app.services.security_analysis.frontends import (
    JavaSecurityFrontend,
    GoSecurityFrontend,
)
from backend.app.services.security_analysis.semantics import (
    JavaLanguageSemantics,
    GoLanguageSemantics,
)


class TaintLanguageGapTests(unittest.TestCase):
    """通过依赖分析、公共程序图和安全证据全流水线验证传播与隔离。"""

    def _analyze(self, files: dict[str, str], **limits: int) -> SecurityEvidencePack:
        """输入相对文件/源码，返回临时项目的安全包，不污染现有项目。"""
        with tempfile.TemporaryDirectory() as directory:
            for relative, source in files.items():
                target = Path(directory, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            return SecurityAnalysisService(**limits).analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                semantic_index=analysis["semantic_index"],
            )

    def _verified(self, pack: SecurityEvidencePack, rule: str) -> bool:
        """仅接受已建立 DFG 的候选；结构可达不能替代传播验证。"""
        self.assertFalse(
            any(
                item.get("reason") == "program_graph_dataflow_error"
                for item in pack.scan_failures
            )
        )
        return any(
            item.rule_id == rule and item.dataflow_verified for item in pack.candidates
        )

    def test_comments_do_not_shift_multilanguage_argument_slots(self) -> None:
        """注释不是参数；跨语言保持危险实参与安全兄弟值的同一槽位语义。"""
        cases = (
            ("app.py", "PY-SINK-SHELL", "import os\ndef execute(safe, command):\n os.system(\n # action\n SELECTED)\ndef run():\n execute(\n # prefix\n 'fixed',\n # input\n input())\n"),
            ("Main.java", "JAVA-SINK-PROCESS", 'class Main { static void execute(String safe, String command) { Runtime.getRuntime().exec(/* action */ SELECTED); } static void run() { execute(/* prefix */ "fixed", /* input */ System.getenv("CMD")); } }'),
            ("main.go", "GO-SINK-PROCESS", 'package main\nimport("os"; "os/exec")\nfunc execute(safe string, command string) { exec.Command(/* action */ SELECTED).Run() }\nfunc main() { execute(/* prefix */ "fixed", /* input */ os.Getenv("CMD")) }'),
            ("main.c", "C-SINK-SHELL", '#include <stdlib.h>\nvoid execute(char *safe, char *command) { system(/* action */ SELECTED); }\nint main() { execute(/* prefix */ "fixed", /* input */ getenv("CMD")); }'),
        )
        for path, rule, source in cases:
            for selected, expected in (("safe", False), ("command", True)):
                with self.subTest(path=path, selected=selected):
                    pack = self._analyze({path: source.replace("SELECTED", selected)})
                    self.assertEqual(expected, self._verified(pack, rule), [fact.rule_id for fact in pack.facts.values()])

    def test_static_sequence_comments_do_not_shift_java_or_go_elements(self) -> None:
        """公共静态序列适配不能把注释算成数组成员，安全兄弟元素保持隔离。"""
        cases = (
            ("Main.java", "JAVA-SINK-PROCESS", 'class Main { static void execute(String... args) { Runtime.getRuntime().exec(args[INDEX]); } static void run(){ String input=System.getenv("CMD"); execute(new String[]{"fixed", /* input */ input}); } }'),
            ("main.go", "GO-SINK-PROCESS", 'package main\nimport("os";"os/exec")\nfunc execute(args ...string){exec.Command(args[INDEX])}\nfunc main(){input:=os.Getenv("CMD");execute([]string{"fixed", /* input */ input}...)}'),
        )
        for path, rule, source in cases:
            for index, expected in ((0, False), (1, True)):
                with self.subTest(path=path, index=index):
                    self.assertEqual(expected, self._verified(self._analyze({path: source.replace("INDEX", str(index))}), rule))

    def test_fastapi_annotation_aliases_do_not_hide_real_input_models(self) -> None:
        """按规范类型而非子串过滤；Annotated/别名有效，ResponseData 仍是输入。"""
        pack = self._analyze({"app.py": "from fastapi import FastAPI, BackgroundTasks as Tasks, Response as Reply\nfrom typing import Annotated\nclass ResponseData: pass\napp=FastAPI()\n@app.post('/run')\ndef route(value: ResponseData, tasks: Annotated[Tasks, 'meta'], response: 'Reply'):\n return value\n"})
        self.assertEqual({"value"}, {fact.name for fact in pack.facts.values() if fact.fact_kind == "source"})

    def test_nested_results_support_keywords_and_multiple_layers(self) -> None:
        """共享位置协议处理关键字和多重直接嵌套，不跨越安全常量返回。"""
        for expression in ("wrap(value)", "wrap(wrap(value))"):
            for returned, expected in (("value", True), ("'fixed'", False)):
                with self.subTest(expression=expression, returned=returned):
                    pack = self._analyze({"app.py": f"import requests\ndef wrap(value):\n return {returned}\ndef run():\n value=input()\n requests.get(url={expression})\n"})
                    self.assertEqual(expected, self._verified(pack, "PY-SINK-NETWORK"))
                    for flow in pack.dataflows.values():
                        self.assertTrue(any(step.kind == "return" for step in flow.steps))
                        self.assertEqual("keyword", flow.steps[-1].argument_bindings[0].kind)
                        self.assertEqual("url", flow.steps[-1].argument_bindings[0].keyword)

    def test_assigned_project_calls_require_actual_return(self) -> None:
        """直接赋值调用同样不能把实参当返回值；正例必须包含真实调用与返回步骤。"""
        for returned, expected in (("value", True), ("'fixed'", False)):
            with self.subTest(returned=returned):
                pack = self._analyze({"app.py": f"import os\ndef wrap(value):\n return {returned}\ndef run():\n value=input()\n result=wrap(value)\n os.system(result)\n"})
                self.assertEqual(expected, self._verified(pack, "PY-SINK-SHELL"))
                if expected:
                    self.assertTrue(all(any(step.kind == "return" for step in flow.steps) for flow in pack.dataflows.values()))

    def test_forwarded_project_return_does_not_bypass_inner_constant(self) -> None:
        """return inner(value) 不能在调用结果外层继续使用扁平化原始参数。"""
        for returned, expected in (("value", True), ("'fixed'", False)):
            with self.subTest(returned=returned):
                pack = self._analyze({"app.py": f"import os\ndef inner(value):\n return {returned}\ndef outer(value):\n return inner(value)\ndef run():\n value=input()\n os.system(outer(value))\n"})
                self.assertEqual(expected, self._verified(pack, "PY-SINK-SHELL"))

    def test_candidate_budget_preserves_value_path_and_model_delivery(self) -> None:
        """端点/候选上限不能先被结构共现占满；最终按 ID 获取同一条精确传播链。"""
        pack = self._analyze({"app.py": "import requests\ndef run():\n unused=input()\n requests.get('https://fixed.invalid')\n url=input()\n requests.request('GET', url)\n"}, max_sources=1, max_sinks=1, max_candidates=1)
        self.assertEqual(1, len(pack.candidates))
        candidate = pack.candidates[0]
        self.assertTrue(candidate.dataflow_verified)
        self.assertEqual(5, pack.facts[candidate.source_fact_id].location.line)
        restored = SecurityEvidencePack.model_validate_json(pack.model_dump_json())
        catalog = SecurityEvidenceCatalog(restored.model_dump())
        summary = json.loads(catalog.summary(max_chars=20000))
        group = next(item for item in summary["items"] if item["id"] == candidate.path_group_id)
        row = next(item for item in catalog.detail(group['id'])['items'] if item['id'] == candidate.candidate_id)
        self.assertEqual("propagation_path", row["kind"])
        steps = catalog.detail(candidate.candidate_id, section="flow", limit=10)["items"]
        self.assertEqual([{"kind": "position", "position": 1, "keyword": None, "value_names": ["url"]}], steps[-1]["argument_bindings"])
        self.assertEqual(["url"], steps[-1]["input_names"])

    def test_protection_budget_preserves_observations_on_established_flow(self) -> None:
        """无关函数不能先占满防护预算；保留关联仍不证明防护语义有效。"""
        pack = self._analyze({"app.py": "import os, shlex\ndef a_unused(value):\n if value:\n  return shlex.quote(value)\ndef run():\n value=input()\n cleaned=shlex.quote(value)\n if cleaned:\n  os.system(cleaned)\n"}, max_guards=1, max_sanitizers=1)
        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL" and item.dataflow_verified)
        self.assertEqual(1, len(candidate.guard_fact_ids))
        self.assertEqual(1, len(candidate.sanitizer_fact_ids))
        for fact_id in [*candidate.guard_fact_ids, *candidate.sanitizer_fact_ids]:
            fact = pack.facts[fact_id]
            self.assertEqual("app.py::run", fact.symbol)
            self.assertEqual("not_verified", fact.metadata["protection_effectiveness"])
        catalog = SecurityEvidenceCatalog(pack.model_dump())
        delivered = catalog.detail(candidate.candidate_id, section="protections")["items"]
        self.assertEqual(2, len(delivered))

    def test_diagnostics_are_bounded_and_not_safety_verdicts(self) -> None:
        """截断诊断显式记录且无共享状态；旧产物仍可缺省读取。"""
        diagnostics = FlowDiagnostics(limit=2)
        for index in range(5):
            diagnostics.record("call_depth_limit", node_id=str(index))
        saved = diagnostics.as_dict()
        self.assertEqual(5, saved["counts"]["call_depth_limit"])
        self.assertEqual(2, len(saved["items"]))
        self.assertTrue(saved["truncated"])
        self.assertEqual({}, SecurityEvidencePack.model_validate({"schema_version": "2.4"}).flow_diagnostics)

    def test_fastapi_background_arguments_reuse_shared_value_boundaries(self) -> None:
        """调度不是普通调用；显式参数经公共 may 边界进入后台函数，再跨文件到 Sink。"""
        for invocation, expected in (("tasks.add_task(worker, value)", True),
                                     ("tasks.add_task(worker, command=value)", True),
                                     ("tasks.add_task(worker, 'fixed')", False)):
            with self.subTest(invocation=invocation):
                pack = self._analyze({
                    "app.py": "from fastapi import FastAPI, BackgroundTasks\nfrom worker import execute\napp=FastAPI()\ndef worker(command):\n execute(command)\n@app.post('/run')\ndef route(value: str, tasks: BackgroundTasks):\n " + invocation + "\n",
                    "worker.py": "import os\ndef execute(command):\n os.system(command)\n",
                })
                self.assertEqual(expected, self._verified(pack, "PY-SINK-SHELL"))
                if expected:
                    candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL" and item.dataflow_verified)
                    flow = pack.dataflows[candidate.dataflow_id]
                    self.assertTrue(flow.value_boundary_ids)
                    self.assertEqual("may_reach_sink", flow.status)
                    self.assertEqual("low", flow.confidence)
                    self.assertEqual("worker.py", flow.steps[-1].location.path)

    def test_background_tasks_do_not_guess_dynamic_or_rebound_callbacks(self) -> None:
        """同名 add_task、被重绑定的调度器及动态回调都不能凭名字补造传播。"""
        for annotation, setup, target in (("object", "", "worker"),
                                           ("BackgroundTasks", " tasks=object()\n", "worker"),
                                           ("BackgroundTasks", " worker=other\n", "worker"),
                                           ("BackgroundTasks", "", "lambda x: worker(x)")):
            with self.subTest(annotation=annotation, setup=setup, target=target):
                pack = self._analyze({"app.py": "from fastapi import FastAPI, BackgroundTasks\nimport os\napp=FastAPI()\ndef worker(command):\n os.system(command)\n@app.post('/run')\ndef route(value: str, tasks: " + annotation + "):\n" + setup + " tasks.add_task(" + target + ", value)\n"})
                self.assertFalse(self._verified(pack, "PY-SINK-SHELL"))

    def test_background_nested_argument_is_not_flattened_into_taint(self) -> None:
        """尚未建模的调度实参返回值必须显式诊断，不能重复扁平化捷径。"""
        pack = self._analyze({"app.py": "from fastapi import FastAPI, BackgroundTasks\nimport os\napp=FastAPI()\ndef clean(value):\n return 'fixed'\ndef worker(command):\n os.system(command)\n@app.post('/run')\ndef route(value: str, tasks: BackgroundTasks):\n tasks.add_task(worker, clean(value))\n"})
        self.assertFalse(self._verified(pack, "PY-SINK-SHELL"))
        self.assertTrue(any(item.get("reason") == "python_background_argument_not_modelled" for item in pack.scan_failures))

    def test_unresolved_frontier_requires_tracked_argument(self) -> None:
        """只定位输入实际抵达的未知调用，常量调用不作为传播断点。"""
        pack = self._analyze({"app.py": "def run():\n value=input()\n unknown.consume(value)\n unknown.consume('fixed')\n"})
        frontiers = [item for item in pack.flow_diagnostics["items"] if item["reason"] == "unresolved_call_with_tracked_argument"]
        self.assertEqual([3], [item["line"] for item in frontiers])
        self.assertEqual(["value"], frontiers[0]["value_names"])
        catalog = SecurityEvidenceCatalog(pack.model_dump())
        details = catalog.detail("_coverage", section="unresolved", limit=10)
        self.assertTrue(any(item.get("reason") == "unresolved_call_with_tracked_argument" for item in details["items"]))

    def test_constructor_state_is_not_faked_as_direct_value_return(self) -> None:
        """通过显式字段摘要建立链路；不再用构造器未知返回冒充整个对象污点。"""
        pack = self._analyze({"app.py": "import os\nclass Worker:\n def __init__(self, command):\n  self.command=command\n def run(self):\n  os.system(self.command)\ndef route():\n value=input()\n worker=Worker(value)\n worker.run()\n"})
        self.assertTrue(self._verified(pack, "PY-SINK-SHELL"))
        self.assertTrue(any(step.object_allocation_id for flow in pack.dataflows.values() for step in flow.steps))
        self.assertFalse(any(item["reason"] == "constructor_heap_effect_not_modelled" for item in pack.flow_diagnostics["items"]))

    def test_depth_frontier_is_visible_in_persisted_evidence(self) -> None:
        """超过共享深度预算会保留前沿信息，不能把未建立路径称为安全。"""
        depth = ProgramGraphSecurityFlowAnalyzer().max_call_depth + 1
        source = "import os\ndef route():\n value=input()\n f1(value)\n"
        source += "\n".join(f"def f{index}(value):\n f{index + 1}(value)\n" for index in range(1, depth))
        source += f"def f{depth}(value):\n os.system(value)\n"
        pack = self._analyze({"app.py": source})
        self.assertFalse(self._verified(pack, "PY-SINK-SHELL"))
        self.assertGreater(pack.flow_diagnostics["counts"].get("call_depth_limit", 0), 0)

    def test_fastapi_infrastructure_parameters_are_not_request_values(self) -> None:
        """BackgroundTasks/Response 是框架对象，不应伪装成攻击者输入。"""
        pack = self._analyze({"app.py": "from fastapi import FastAPI, BackgroundTasks, Response\napp=FastAPI()\n@app.post('/run')\ndef route(value: str, tasks: BackgroundTasks, response: Response):\n return value\n"})
        names = {fact.name for fact in pack.facts.values() if fact.fact_kind == "source"}
        self.assertEqual({"value"}, names)

    def test_network_request_method_does_not_become_sensitive_url(self) -> None:
        """request(method, url) 与 get(url) 不共用位置零作为 URL。"""
        for call, expected in (("requests.request(value, 'https://fixed.invalid')", False),
                               ("requests.request('GET', value)", True),
                               ("requests.request(method='GET', url=value)", True)):
            with self.subTest(call=call):
                pack = self._analyze({"app.py": "import requests\ndef run():\n value=input()\n " + call + "\n"})
                self.assertEqual(expected, self._verified(pack, "PY-SINK-NETWORK"))

    def test_sink_argument_is_read_before_call_result_assignment(self) -> None:
        """result = sink(value) 必须到达 value 实参，而不是误认为只有 result 值流。"""
        samples = (
            ("app.py", "import os\ndef run():\n value=input()\n result=os.system(value)\n", "PY-SINK-SHELL"),
            ("Demo.java", 'class Demo { void route() throws Exception { String value=System.getenv("CMD"); Process result=Runtime.getRuntime().exec(value); } }', "JAVA-SINK-PROCESS"),
            ("main.go", 'package main\nimport ("os"; "os/exec")\nfunc route() { value:=os.Getenv("CMD"); result:=exec.Command(value) }\n', "GO-SINK-PROCESS"),
            ("app.js", 'const cp=require("child_process"); function route() { const value=process.argv[2]; const result=cp.exec(value); }', "JS-SINK-SHELL"),
            ("main.c", '#include <stdlib.h>\nvoid route() { char *value=getenv("CMD"); int result=system(value); }\n', "C-SINK-SHELL"),
        )
        for filename, source, rule in samples:
            with self.subTest(filename=filename):
                pack = self._analyze({filename: source})
                self.assertTrue(self._verified(pack, rule))
                flow = next(pack.dataflows[item.dataflow_id] for item in pack.candidates if item.rule_id == rule and item.dataflow_verified)
                self.assertEqual(["value"], flow.steps[-1].input_names)
                self.assertEqual(0, flow.steps[-1].argument_bindings[0].position)

    def test_written_variable_does_not_taint_earlier_sensitive_read(self) -> None:
        """同一操作写回的 result 不能反向污染调用前读取的安全 result。"""
        pack = self._analyze({"app.py": "import os\ndef run():\n value=input()\n result='fixed'\n result=opaque(os.system(result), value)\n"})
        self.assertFalse(self._verified(pack, "PY-SINK-SHELL"))

    def test_background_path_keeps_intermediate_sanitizer_as_observation(self) -> None:
        """检查关联实际值流上的中间函数；不换成另一条结构最短路径，也不阻断传播。"""
        pack = self._analyze({"app.py": "from fastapi import FastAPI, BackgroundTasks\nimport html, os\napp=FastAPI()\ndef worker(command):\n transformed=html.escape(command)\n launch(transformed)\ndef launch(command):\n os.system(command)\n@app.post('/run')\ndef route(value: str, tasks: BackgroundTasks):\n tasks.add_task(worker, value)\n"})
        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL" and item.dataflow_verified)
        self.assertTrue(candidate.sanitizer_fact_ids)
        self.assertEqual({"html.escape"}, {pack.facts[key].name for key in candidate.sanitizer_fact_ids})
        self.assertTrue(all(pack.facts[key].metadata["protection_effectiveness"] == "not_verified" for key in candidate.sanitizer_fact_ids))

    def test_unproven_sanitizers_do_not_cut_unrelated_security_contexts(self) -> None:
        """HTML/URL/路径变换不能被当作 Shell 净化证明。"""
        for expression in ("html.escape(value)", "urllib.parse.quote(value)", "os.path.basename(value)"):
            with self.subTest(expression=expression):
                pack = self._analyze({"app.py": "import html, urllib.parse, os\ndef run():\n value=input()\n transformed=" + expression + "\n os.system(transformed)\n"})
                self.assertTrue(self._verified(pack, "PY-SINK-SHELL"))

    def test_file_rename_checks_actual_source_and_destination_slots(self) -> None:
        """文件移动的目标同样敏感；只报告实际被追踪的槽位，不连带声明安全源路径。"""
        for call, position, keyword in (("os.rename('fixed', value)", 1, None),
                                         ("os.rename(value, 'fixed')", 0, None),
                                         ("shutil.move(src='fixed', dst=value)", None, "dst")):
            with self.subTest(call=call):
                pack = self._analyze({"app.py": "import os, shutil\ndef run():\n value=input()\n " + call + "\n"})
                self.assertTrue(self._verified(pack, "PY-SINK-FILE-WRITE"))
                flow = next(pack.dataflows[item.dataflow_id] for item in pack.candidates if item.rule_id == "PY-SINK-FILE-WRITE" and item.dataflow_verified)
                self.assertEqual(["value"], flow.steps[-1].input_names)
                self.assertEqual(position, flow.steps[-1].argument_bindings[0].position)
                self.assertEqual(keyword, flow.steps[-1].argument_bindings[0].keyword)

    def test_nested_project_calls_use_real_returns_instead_of_raw_arguments(self) -> None:
        """共享嵌套结果槽位：固定安全返回不得把原实参直接送入外侧 Sink。"""
        for result, expected in (("value", True), ('"fixed"', False)):
            samples = (
                ("app.py", f"import os\ndef wrap(value):\n return {result}\ndef route():\n value=input()\n os.system(wrap(value))\n", "PY-SINK-SHELL"),
                ("Demo.java", f'class Demo {{ String wrap(String value) {{ return {result}; }} void route() throws Exception {{ String value=System.getenv("CMD"); Runtime.getRuntime().exec(wrap(value)); }} }}', "JAVA-SINK-PROCESS"),
                ("main.go", f'package main\nimport ("os"; "os/exec")\nfunc wrap(value string) string {{ return {result} }}\nfunc route() {{ value:=os.Getenv("CMD"); exec.Command(wrap(value)) }}\n', "GO-SINK-PROCESS"),
                ("app.js", f'const cp=require("child_process");\nfunction wrap(value) {{ return {result}; }}\nfunction route() {{ const value=process.argv[2]; cp.exec(wrap(value)); }}\n', "JS-SINK-SHELL"),
                ("main.c", f'#include <stdlib.h>\nchar *wrap(char *value) {{ return {result}; }}\nvoid route() {{ char *value=getenv("CMD"); system(wrap(value)); }}\n', "C-SINK-SHELL"),
            )
            for filename, source, rule in samples:
                with self.subTest(filename=filename, result=result):
                    self.assertEqual(expected, self._verified(self._analyze({filename: source}), rule))

    def test_python_variadic_elements_are_isolated(self) -> None:
        """输入第二个可变元素时，第一个安全元素不得被污染。"""
        for index, expected in ((0, False), (1, True)):
            with self.subTest(index=index):
                pack = self._analyze(
                    {
                        "app.py": f"from fastapi import FastAPI\nimport subprocess\napp=FastAPI()\n@app.get('/run')\ndef route(command: str):\n    execute('fixed', command)\ndef execute(*args):\n    subprocess.run(args[{index}], shell=True)\n"
                    }
                )
                self.assertEqual(expected, self._verified(pack, "PY-SINK-SHELL"))

    def test_python_keyword_variadic_members_are_isolated(self) -> None:
        """kwargs 静态键可以传播；安全兄弟键不因聚合输入误报。"""
        for key, expected in (("fixed", False), ("command", True)):
            with self.subTest(key=key):
                pack = self._analyze(
                    {
                        "app.py": f"from fastapi import FastAPI\nimport subprocess\napp=FastAPI()\n@app.get('/run')\ndef route(command: str):\n    execute(fixed='echo fixed', command=command)\ndef execute(**options):\n    subprocess.run(options['{key}'], shell=True)\n"
                    }
                )
                self.assertEqual(expected, self._verified(pack, "PY-SINK-SHELL"))

    def test_python_literal_unpacking_and_keyword_only(self) -> None:
        """字面量 *序列/**字典与仅关键字参数保留真实绑定。"""
        for call in (
            "execute(*['fixed'], command=command)",
            "execute('fixed', **{'command': command})",
        ):
            with self.subTest(call=call):
                pack = self._analyze(
                    {
                        "app.py": f"from fastapi import FastAPI\nimport subprocess\napp=FastAPI()\n@app.get('/run')\ndef route(command: str):\n    {call}\ndef execute(prefix, *, command):\n    subprocess.run(command, shell=True)\n"
                    }
                )
                self.assertTrue(self._verified(pack, "PY-SINK-SHELL"))

    def test_python_unknown_unpack_does_not_shift_known_keywords(self) -> None:
        """动态 * 长度未知时仍能绑定独立明确的 keyword-only 值，并报告局限。"""
        pack = self._analyze(
            {
                "app.py": "from fastapi import FastAPI\nimport subprocess\napp=FastAPI()\n@app.get('/run')\ndef route(command: str):\n    execute(*unknown, command=command)\ndef execute(*args, command):\n    subprocess.run(command, shell=True)\n"
            }
        )
        self.assertTrue(self._verified(pack, "PY-SINK-SHELL"))
        self.assertTrue(
            any(
                "动态 *" in item
                for flow in pack.dataflows.values()
                for item in flow.unresolved
            )
        )

    def test_java_variadic_elements_are_isolated(self) -> None:
        """Java String... 参数不能丢失名称，第二个元素经真实调用进入 Sink。"""
        for index, expected in ((0, False), (1, True)):
            with self.subTest(index=index):
                pack = self._analyze(
                    {
                        "Demo.java": f'class Demo {{\n void route() throws Exception {{\n  String command = System.getenv("CMD");\n  execute("fixed", command);\n }}\n void execute(String... args) throws Exception {{\n  Runtime.getRuntime().exec(args[{index}]);\n }}\n}}'
                    }
                )
                self.assertEqual(expected, self._verified(pack, "JAVA-SINK-PROCESS"))

    def test_packed_literal_variadic_arrays_share_binding(self) -> None:
        """Java 打包数组和 Go 切片字面量展开遵守同一槽位协议，不把安全首元素污染。"""
        for index, expected in ((0, False), (1, True)):
            for filename, source, rule in (
                (
                    "Demo.java",
                    f'class Demo {{ void route() throws Exception {{ String value=System.getenv("CMD"); execute(new String[]{{"fixed", value}}); }} void execute(String... args) throws Exception {{ Runtime.getRuntime().exec(args[{index}]); }} }}',
                    "JAVA-SINK-PROCESS",
                ),
                (
                    "main.go",
                    f'package main\nimport ("os"; "os/exec")\nfunc route() {{ value := os.Getenv("CMD"); execute([]string{{"fixed", value}}...) }}\nfunc execute(args ...string) {{ exec.Command(args[{index}]) }}\n',
                    "GO-SINK-PROCESS",
                ),
            ):
                with self.subTest(filename=filename, index=index):
                    self.assertEqual(
                        expected,
                        self._verified(self._analyze({filename: source}), rule),
                    )

    def test_python_unpacking_is_shared_with_rule_arguments(self) -> None:
        """规则引擎也能观察 **字面量中的 shell 条件，与程序图使用相同实参语义。"""
        pack = self._analyze(
            {
                "app.py": "from fastapi import FastAPI\nimport subprocess\napp=FastAPI()\n@app.get('/run')\ndef route(command: str):\n subprocess.run(*[command], **{'shell': True})\n"
            }
        )
        self.assertTrue(self._verified(pack, "PY-SINK-SHELL"))

    def test_go_variadic_elements_are_isolated(self) -> None:
        """Go 可变参数仅传播对应常量元素，不污染其他元素。"""
        for index, expected in ((0, False), (1, True)):
            with self.subTest(index=index):
                pack = self._analyze(
                    {
                        "main.go": f'package main\nimport ("os"; "os/exec")\nfunc route() {{\n command := os.Getenv("CMD")\n execute("fixed", command)\n}}\nfunc execute(args ...string) {{ exec.Command(args[{index}]) }}\n'
                    }
                )
                self.assertEqual(expected, self._verified(pack, "GO-SINK-PROCESS"))

    def test_go_readfile_and_lookupenv_status_is_not_data(self) -> None:
        """多返回值 Source 的内容和错误/是否存在分开；状态不能替代输入。"""
        for source_call in ('os.ReadFile("input.txt")', 'os.LookupEnv("CMD")'):
            for selected, expected in (("value", True), ("status", False)):
                with self.subTest(source=source_call, selected=selected):
                    pack = self._analyze(
                        {
                            "main.go": f'package main\nimport ("os"; "os/exec"; "fmt")\nfunc route() {{\n value, status := {source_call}\n exec.Command(fmt.Sprint({selected}))\n}}\n'
                        }
                    )
                    self.assertEqual(expected, self._verified(pack, "GO-SINK-PROCESS"))

    def test_go_project_multiple_returns_remain_separate(self) -> None:
        """真实调用返回两分量时，输入内容仅进入对应接收槽位，不扩散到安全返回分量。"""
        for source_in_caller in (False, True):
            for selected, expected in (("value", True), ("safe", False)):
                with self.subTest(source_in_caller=source_in_caller, selected=selected):
                    setup = (
                        'input := os.Getenv("CMD"); value, safe := split(input)'
                        if source_in_caller
                        else "value, safe := load()"
                    )
                    helper = (
                        'func split(input string) (string, string) { return input, "fixed" }'
                        if source_in_caller
                        else 'func load() (string, string) { input := os.Getenv("CMD"); return input, "fixed" }'
                    )
                    pack = self._analyze(
                        {
                            "main.go": f'package main\nimport ("os"; "os/exec")\nfunc route() {{ {setup}; exec.Command({selected}) }}\n{helper}\n'
                        }
                    )
                    self.assertEqual(expected, self._verified(pack, "GO-SINK-PROCESS"))

    def test_go_context_api_uses_command_and_url_not_context(self) -> None:
        """Context 插入首位后，真正危险参数的位置应顺移。"""
        for call, rule in (
            ("exec.CommandContext(ctx, value)", "GO-SINK-PROCESS-CONTEXT"),
            (
                'http.NewRequestWithContext(ctx, "GET", value, nil)',
                "GO-SINK-NETWORK-REQUEST-CONTEXT",
            ),
        ):
            for argument, expected in (("value", True), ('"fixed"', False)):
                with self.subTest(call=call, argument=argument):
                    pack = self._analyze(
                        {
                            "main.go": f'package main\nimport ("os"; "os/exec"; "net/http"; "context")\nfunc route() {{ ctx := context.Background(); value := os.Getenv("CMD"); {call.replace("value", argument)} }}\n'
                        }
                    )
                    self.assertEqual(expected, self._verified(pack, rule))

    def test_go_sql_context_uses_query_and_preserves_parameter_role(self) -> None:
        """ExecContext 的 query 是第二参数，绑定参数是第三位及之后。"""
        for query, expected in (("value", True), ('"SELECT 1"', False)):
            with self.subTest(query=query):
                pack = self._analyze(
                    {
                        "main.go": f'package main\nimport ("os"; "database/sql"; "context")\nfunc route(db *sql.DB) {{ ctx := context.Background(); value := os.Getenv("QUERY"); db.ExecContext(ctx, {query}, value) }}\n'
                    }
                )
                self.assertEqual(expected, self._verified(pack, "GO-SINK-SQL-CONTEXT"))
                sink = next(
                    fact
                    for fact in pack.facts.values()
                    if fact.rule_id == "GO-SINK-SQL-CONTEXT"
                )
                self.assertEqual([1], sink.metadata["value_flow"]["arguments"])
                self.assertTrue(sink.metadata["parameter_argument_present"])

    def test_c_buffer_status_and_sibling_isolation(self) -> None:
        """read 写入内容与返回数量、安全兄弟缓冲区有不同的值定义。"""
        for selected, expected in (("buffer", True), ("count", False), ("safe", False)):
            with self.subTest(selected=selected):
                pack = self._analyze(
                    {
                        "main.c": f'#include <unistd.h>\n#include <stdlib.h>\nint run(void) {{\n char buffer[64], safe[64] = "fixed";\n int count = read(0, buffer, 64);\n return system({selected});\n}}\n'
                    }
                )
                self.assertEqual(expected, self._verified(pack, "C-SINK-SHELL"))

    def test_direct_inline_source_to_sink_all_core_languages(self) -> None:
        """核心语言无需临时赋值即可建立直接 Source 路径，也兼容已有 JS 规则。"""
        for filename, source, rule in (
            (
                "main.py",
                "import os, subprocess\ndef run():\n subprocess.run(os.getenv('CMD'), shell=True)\n",
                "PY-SINK-SHELL",
            ),
            (
                "Demo.java",
                'class Demo { void run() throws Exception { Runtime.getRuntime().exec(System.getenv("CMD")); } }',
                "JAVA-SINK-PROCESS",
            ),
            (
                "main.go",
                'package main\nimport ("os"; "os/exec")\nfunc run() { exec.Command(os.Getenv("CMD")) }',
                "GO-SINK-PROCESS",
            ),
            (
                "main.c",
                '#include <stdlib.h>\nint run(void) { return system(getenv("CMD")); }',
                "C-SINK-SHELL",
            ),
            (
                "main.js",
                "const fs=require('fs'); const cp=require('child_process'); function run(){ cp.exec(fs.readFileSync('input.txt','utf8')); }",
                "JS-SINK-SHELL",
            ),
        ):
            with self.subTest(filename=filename):
                self.assertTrue(self._verified(self._analyze({filename: source}), rule))

    def test_inline_source_through_project_wrapper_checks_actual_return(self) -> None:
        """Source 直接进入 helper 时走真实调用；固定返回 helper 不得把输入带回调用者。"""
        for returned, expected in (("value", True), ("'fixed'", False)):
            with self.subTest(returned=returned):
                source = f"import os, subprocess\ndef clean(value):\n return {returned}\ndef run():\n result=clean(os.getenv('CMD'))\n subprocess.run(result, shell=True)\n"
                self.assertEqual(
                    expected,
                    self._verified(self._analyze({"main.py": source}), "PY-SINK-SHELL"),
                )

    def test_inline_source_keyword_parameter(self) -> None:
        """嵌套 Source 可以绑定精确关键字实参，不把同操作其他关键字污染。"""
        source = "import os, subprocess\ndef execute(*, command, safe):\n subprocess.run(command, shell=True)\ndef run():\n execute(command=os.getenv('CMD'), safe='fixed')\n"
        self.assertTrue(
            self._verified(self._analyze({"main.py": source}), "PY-SINK-SHELL")
        )

    def test_unreachable_inline_source_is_not_verified(self) -> None:
        """Source/Sink 虽在同一语句，但整个语句不可达时不能凭局部相交建立路径。"""
        source = "import os, subprocess\ndef run():\n return\n subprocess.run(os.getenv('CMD'), shell=True)\n"
        self.assertFalse(
            self._verified(self._analyze({"main.py": source}), "PY-SINK-SHELL")
        )

    def test_c_and_cpp_buffer_flows_across_function(self) -> None:
        """C/C++ 同一输出规则可接已有普通函数参数传播，证据保留成功条件局限。"""
        for filename in ("main.c", "main.cpp"):
            with self.subTest(filename=filename):
                pack = self._analyze(
                    {
                        filename: "#include <stdio.h>\n#include <stdlib.h>\nint execute(char *value) { return system(value); }\nint run(void) {\n char buffer[64];\n fgets(buffer, 64, stdin);\n return execute(buffer);\n}\n"
                    }
                )
                self.assertTrue(self._verified(pack, "C-SINK-SHELL"))
                candidate = next(item for item in pack.candidates if item.path_kind == "interprocedural_dataflow")
                details = SecurityEvidenceCatalog(pack.model_dump()).detail(candidate.candidate_id, section="unresolved", limit=10)
                self.assertTrue(any("输出参数" in item.get("description", "") for item in details["items"]))

    def test_c_complex_output_pointer_reports_gap(self) -> None:
        """buffer+offset 不猜测别名，保留事实和显式缺口。"""
        pack = self._analyze(
            {
                "main.c": "#include <unistd.h>\n#include <stdlib.h>\nint run(int offset) { char buffer[64]; read(0, buffer + offset, 64); return system(buffer); }"
            }
        )
        self.assertFalse(self._verified(pack, "C-SINK-SHELL"))
        self.assertTrue(
            any(
                item.get("reason") == "output_argument_binding_unresolved"
                for item in pack.scan_failures
            )
        )

    def test_c_whole_buffer_overwrite_stops_flow(self) -> None:
        """词法整槽位安全覆写停止路径；并非声称支持所有 strcpy/内存副作用。"""
        pack = self._analyze(
            {
                "main.c": '#include <unistd.h>\n#include <stdlib.h>\nint run(void) { char *buffer; read(0, buffer, 64); buffer = "fixed"; return system(buffer); }'
            }
        )
        self.assertFalse(self._verified(pack, "C-SINK-SHELL"))

    def test_c_unreachable_buffer_read_does_not_seed_flow(self) -> None:
        """return 后读取不可达，不因 Source/Sink 存在于同函数就验证路径。"""
        pack = self._analyze(
            {
                "main.c": "#include <unistd.h>\n#include <stdlib.h>\nint run(void) { char buffer[64]; return 0; read(0, buffer, 64); return system(buffer); }"
            }
        )
        self.assertFalse(self._verified(pack, "C-SINK-SHELL"))

    def test_new_overlays_do_not_mutate_original_program_graph(self) -> None:
        """参数槽位和输出定义只存在于消费者私有图，持久化原图 JSON 不变。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.c").write_text(
                "#include <unistd.h>\n#include <stdlib.h>\nint run(void) { char buffer[64]; read(0, buffer, 64); return system(buffer); }",
                encoding="utf-8",
            )
            Path(directory, "main.py").write_text(
                "import os, subprocess\ndef route():\n execute('fixed', os.getenv('CMD'))\ndef execute(*args):\n subprocess.run(args[1], shell=True)\n",
                encoding="utf-8",
            )
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            graph = ProgramGraphService().analyze(
                directory, dependency_graph=analysis["dependency_graph"]
            )
            scanner = SecurityScanner()
            scan = scanner.scan(
                directory, dependency_graph=analysis["dependency_graph"]
            )
            original = graph.model_dump_json()
            flows = ProgramGraphSecurityFlowAnalyzer(
                semantics=scanner.semantics
            ).analyze(
                graph,
                sources=scan.sources,
                sinks=scan.sinks,
                sanitizers=scan.sanitizers,
                dependency_graph=analysis["dependency_graph"],
            )
        self.assertTrue(any(item.language == "c" for item in flows))
        self.assertEqual(original, graph.model_dump_json())

    def test_program_parameter_metadata_roundtrip(self) -> None:
        """新声明类别可以持久化；历史缺少可选字段仍按普通位置参数读取。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.py").write_text(
                "def f(a, /, *args, key=None, **kw):\n return a\n", encoding="utf-8"
            )
            graph = ProgramGraphService().analyze(directory, languages=["python"])
        restored = ProgramGraphArtifact.model_validate_json(graph.model_dump_json())
        function = next(iter(restored.functions.values()))
        self.assertEqual(
            {
                "a": "positional_only",
                "args": "variadic_positional",
                "key": "keyword_only",
                "kw": "variadic_keyword",
            },
            function.parameter_kinds,
        )
        data = graph.model_dump()
        for record in data["functions"].values():
            record.pop("parameter_kinds")
        self.assertEqual(
            {},
            next(
                iter(ProgramGraphArtifact.model_validate(data).functions.values())
            ).parameter_kinds,
        )

    def test_ir_and_program_variadic_binding_are_aligned(self) -> None:
        """安全 IR 与公共图对 Java/Go 同一打包字面量使用相同绑定协议。"""
        for filename, source, frontend, semantics in (
            (
                "Demo.java",
                'class Demo { void caller(String value) { target(new String[]{"fixed", value}); } void target(String... args) {} }',
                JavaSecurityFrontend(),
                JavaLanguageSemantics(),
            ),
            (
                "main.go",
                'package main\nfunc caller(value string) { target([]string{"fixed", value}...) }\nfunc target(args ...string) {}',
                GoSecurityFrontend(),
                GoLanguageSemantics(),
            ),
        ):
            with (
                self.subTest(filename=filename),
                tempfile.TemporaryDirectory() as directory,
            ):
                Path(directory, filename).write_text(source, encoding="utf-8")
                ir = frontend.build(directory)
                graph = ProgramGraphService().analyze(
                    directory, languages=[frontend.language]
                )
                call = next(
                    item for item in ir.calls if item.qualified_name == "target"
                )
                target = next(item for item in ir.functions if item.name == "target")
                program_call = next(
                    item
                    for function in graph.functions.values()
                    for node in function.nodes.values()
                    for item in node.calls
                    if item.name == "target"
                )
                program_target = next(
                    item for item in graph.functions.values() if item.name == "target"
                )
                self.assertEqual(
                    semantics.bind_arguments(call, target),
                    semantics.bind_program_arguments(program_call, program_target),
                )


if __name__ == "__main__":
    unittest.main()

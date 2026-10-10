"""高影响多语言污点缺口回归；只解析源码，不执行样例。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.program_graph import ProgramGraphArtifact, ProgramGraphService
from backend.app.services.security_analysis import (
    SecurityAnalysisService,
    SecurityEvidencePromptBuilder,
)
from backend.app.services.security_analysis.contracts import SecurityEvidencePack
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

    def _analyze(self, files: dict[str, str]) -> SecurityEvidencePack:
        """输入相对文件/源码，返回临时项目的安全包，不污染现有项目。"""
        with tempfile.TemporaryDirectory() as directory:
            for relative, source in files.items():
                target = Path(directory, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            return SecurityAnalysisService().analyze(
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
                finding = next(
                    item
                    for item in SecurityEvidencePromptBuilder().build(pack).findings
                    if item.claim == "interprocedural_dataflow"
                )
                self.assertTrue(any("输出参数" in item for item in finding.limitations))

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

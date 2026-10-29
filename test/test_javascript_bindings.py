"""公共结构化绑定及 Node.js 服务端静态管线回归。"""

from __future__ import annotations

from test import test_javascript_security as fixtures
from backend.app.services.value_binding import (
    BindingPattern,
    BindingValue,
    PatternMember,
    StructuredBindingResolver,
    ValueMember,
)
import unittest


class BindingProtocolTests(unittest.TestCase):
    """不依赖 AST/图的公共协议契约验证。"""

    def test_default_null_missing_and_rest(self) -> None:
        """null 不使用默认值；缺失字段使用默认值；对象 rest 排除已选择字段。"""
        default = BindingPattern(
            "default",
            members=(PatternMember(None, BindingPattern("name", "x")),),
            fallback=BindingValue(variables=("fallback",)),
        )
        resolver = StructuredBindingResolver()
        self.assertEqual(
            ("fallback",),
            resolver.resolve(default, BindingValue(kind="missing"))
            .projections[0]
            .inputs,
        )
        self.assertEqual(
            (),
            resolver.resolve(default, BindingValue(kind="literal"))
            .projections[0]
            .inputs,
        )
        pattern = BindingPattern(
            "object",
            members=(
                PatternMember("unsafe", BindingPattern("name", "used")),
                PatternMember(
                    None,
                    BindingPattern(
                        "rest",
                        members=(PatternMember(None, BindingPattern("name", "rest")),),
                    ),
                ),
            ),
        )
        value = BindingValue(
            "object",
            members=(
                ValueMember("unsafe", BindingValue(variables=("taint",))),
                ValueMember("safe", BindingValue("literal")),
            ),
        )
        projections = {
            item.output: item.inputs
            for item in resolver.resolve(pattern, value).projections
        }
        self.assertEqual(("taint",), projections["used"])
        self.assertEqual((), projections["rest.safe"])
        self.assertNotIn("rest.unsafe", projections)

    def test_dynamic_selector_and_budget_are_explicit(self) -> None:
        """动态键不猜测；恶意深度/数量写入诊断。"""
        pattern = BindingPattern(
            "object", members=(PatternMember(None, BindingPattern("name", "out")),)
        )
        self.assertIn(
            "dynamic_binding_selector_not_resolved",
            StructuredBindingResolver().resolve(pattern, BindingValue()).diagnostics,
        )
        value = BindingValue(
            "object",
            members=(
                ValueMember("a", BindingValue()),
                ValueMember("b", BindingValue()),
            ),
        )
        self.assertIn(
            "binding_projection_budget_exceeded",
            StructuredBindingResolver(max_projections=1)
            .resolve(BindingPattern("name", "out"), value)
            .diagnostics,
        )


class JavaScriptBindingIntegrationTests(unittest.TestCase):
    """继承已有静态管线夹具，并补齐复杂 pattern 和框架边界。"""

    _analyze = fixtures.JavaScriptSecurityTests._analyze

    def _verified(self, files: dict[str, str], rule: str) -> list[int]:
        """返回已验证候选的 Sink 行号，不把结构候选当漏洞。"""
        pack = self._analyze(files)
        return [
            pack.facts[item.sink_fact_id].location.line
            for item in pack.candidates
            if item.rule_id == rule and item.dataflow_verified
        ]

    def test_nested_alias_default_static_computed_and_safe_sibling(self) -> None:
        """重命名与静态计算键保留污点；固定安全字段和默认字面量不串流。"""
        lines = self._verified(
            {
                "view.js": """const {exec}=require('node:child_process'); function render() {
 const obj = {nested: {bad: process.env.CMD, safe: 'fixed'}};
 const {nested: {['bad']: renamed, safe}} = obj; const {absent = 'fixed'} = {};
 exec(renamed);
 exec(safe);
 exec(absent);
}"""
            },
            "JS-SINK-SHELL",
        )
        self.assertIn(4, lines)
        self.assertNotIn(5, lines)
        self.assertNotIn(6, lines)

    def test_array_holes_and_rest_positions(self) -> None:
        """数组空槽不改变后续位置，rest 对静态数组重新编号。"""
        lines = self._verified(
            {
                "view.js": """const {exec}=require('node:child_process'); function render() {
 const [safe,,...tail] = ['fixed', 'skip', process.env.CMD, 'safe'];
 exec(safe);
 exec(tail[0]);
 exec(tail[1]);
}"""
            },
            "JS-SINK-SHELL",
        )
        self.assertIn(4, lines)
        self.assertNotIn(3, lines)
        self.assertNotIn(5, lines)

    def test_assignment_destructuring_and_overwrite(self) -> None:
        """赋值解构与声明同义；整体覆写使旧的危险字段定义失效。"""
        lines = self._verified(
            {
                "view.js": """const {exec}=require('node:child_process'); function render() {
 let obj = {bad: process.env.CMD}; let value;
 ({bad: value} = obj);
 exec(value);
 obj = {bad: 'fixed'};
 const {bad: safe} = obj;
 exec(safe);
}"""
            },
            "JS-SINK-SHELL",
        )
        self.assertIn(4, lines)
        self.assertNotIn(7, lines)

    def test_destructured_parameters_across_files(self) -> None:
        """跨文件调用按实际对象字段绑定形参，安全字段不得获得兄弟字段污点。"""
        lines = self._verified(
            {
                "main.js": "import {render} from './worker.js'; export function run() { const v = process.env.CMD; render({bad:v,safe:'fixed'}); }",
                "worker.js": """const {exec}=require('node:child_process'); export function render({bad: renamed, safe}) {
 exec(renamed);
 exec(safe);
}""",
            },
            "JS-SINK-SHELL",
        )
        self.assertIn(2, lines)
        self.assertNotIn(3, lines)

    def test_express_destructured_request(self) -> None:
        """嵌套 request 参数只为选中的 HTTP 字段创建入口 Source。"""
        self.assertTrue(
            self._verified(
                {
                    "server.js": "const express=require('express'); const {exec}=require('child_process'); const app=express(); app.get('/', ({query:{cmd}}, res)=> {exec(cmd);});"
                },
                "JS-SINK-SHELL",
            )
        )


    def test_node_http_and_cli_trust(self) -> None:
        """Node http 注册的 req.url 与 CLI 参数可进入 Sink，但保留不同信任等级。"""
        self.assertTrue(
            self._verified(
                {
                    "server.js": "const http=require('node:http'); const cp=require('node:child_process'); http.createServer((req,res)=>{const url=req.url; cp.exec(url);});"
                },
                "JS-SINK-SHELL",
            )
        )
        self.assertTrue(
            self._verified(
                {
                    "run.js": "const cp=require('child_process'); function launch(){const arg=process.argv[2]; cp.exec(arg);}"
                },
                "JS-SINK-SHELL",
            )
        )


    def test_dynamic_patterns_spread_and_framework_gaps_reported(self) -> None:
        """未知动态绑定语义必须进入持久化缺口。"""
        pack = self._analyze(
            {
                "view.js": "function render(key){const {[key]:value,...rest}=data; const [first,...tail]=[...data];}"
            }
        )
        reasons = {item.get("reason") for item in pack.scan_failures}
        self.assertIn("dynamic_binding_selector_not_resolved", reasons)
        self.assertIn("opaque_rest_exclusions_not_proven", reasons)
        self.assertIn("dynamic_array_spread_positions_not_resolved", reasons)
    def test_object_spread_overwrites_are_not_assumed_safe(self) -> None:
        """不确定 spread 可覆写前面的安全字段，之后的显式字面量仍覆盖该字段。"""
        lines = self._verified(
            {
                "view.js": """const {exec}=require('node:child_process'); function render(){
 const input=process.env.CMD;
 const {html:bad}={html:'fixed',...input};
 exec(bad);
 const {html:safe}={...input,html:'fixed'};
 exec(safe);
}"""
            },
            "JS-SINK-SHELL",
        )
        self.assertIn(4, lines)
        self.assertNotIn(6, lines)
        self.assertTrue(
            self._verified(
                {
                    "view.js": "const {exec}=require('node:child_process'); function render(){const input=process.env.CMD; const {keep,...rest}={keep:'fixed',...input}; exec(rest);}"
                },
                "JS-SINK-SHELL",
            )
        )

    def test_unknown_object_parameter_and_promise_file_source(self) -> None:
        """不透明对象形参保守传播并标 may；await 文件调用不是同步回调模型。"""
        self.assertTrue(
            self._verified(
                {
                    "main.js": "import {render} from './worker.js'; export function run(){const data=process.env; render(data);}",
                    "worker.js": "import {exec} from 'child_process'; export function render({CMD}){exec(CMD);}",
                },
                "JS-SINK-SHELL",
            )
        )
        self.assertTrue(
            self._verified(
                {
                    "run.ts": "import {readFile} from 'node:fs/promises'; import {exec} from 'child_process'; async function run(){const value=await readFile('config.txt'); exec(value);}"
                },
                "JS-SINK-SHELL",
            )
        )

    def test_program_binding_profiles_round_trip(self) -> None:
        """服务端 TypeScript 参数的递归绑定契约可 JSON 往返。"""
        import tempfile
        from pathlib import Path
        from backend.app.services.program_graph import ProgramGraphArtifact, ProgramGraphService
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "run.ts").write_text("function run({value}:{value:string}){return value;}", encoding="utf-8")
            graph = ProgramGraphService().analyze(directory)
        self.assertEqual([], graph.failures)
        recovered = ProgramGraphArtifact.model_validate_json(graph.model_dump_json())
        self.assertEqual(graph, recovered)
        self.assertTrue(any(item.parameter_patterns for item in recovered.functions.values()))


if __name__ == "__main__":
    unittest.main()

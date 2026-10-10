"""公共结构化绑定及 Vue/React/Node.js 真实静态管线回归。"""

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
                "view.js": """function render() {
 const obj = {nested: {bad: location.hash, safe: 'fixed'}};
 const {nested: {['bad']: renamed, safe}} = obj; const {absent = 'fixed'} = {};
 document.body.innerHTML = renamed;
 document.body.innerHTML = safe;
 document.body.innerHTML = absent;
}"""
            },
            "JS-SINK-DOM",
        )
        self.assertIn(4, lines)
        self.assertNotIn(5, lines)
        self.assertNotIn(6, lines)

    def test_array_holes_and_rest_positions(self) -> None:
        """数组空槽不改变后续位置，rest 对静态数组重新编号。"""
        lines = self._verified(
            {
                "view.js": """function render() {
 const [safe,,...tail] = ['fixed', 'skip', location.hash, 'safe'];
 document.body.innerHTML = safe;
 document.body.innerHTML = tail[0];
 document.body.innerHTML = tail[1];
}"""
            },
            "JS-SINK-DOM",
        )
        self.assertIn(4, lines)
        self.assertNotIn(3, lines)
        self.assertNotIn(5, lines)

    def test_assignment_destructuring_and_overwrite(self) -> None:
        """赋值解构与声明同义；整体覆写使旧的危险字段定义失效。"""
        lines = self._verified(
            {
                "view.js": """function render() {
 let obj = {bad: location.hash}; let value;
 ({bad: value} = obj);
 document.body.innerHTML = value;
 obj = {bad: 'fixed'};
 const {bad: safe} = obj;
 document.body.innerHTML = safe;
}"""
            },
            "JS-SINK-DOM",
        )
        self.assertIn(4, lines)
        self.assertNotIn(7, lines)

    def test_destructured_parameters_across_files(self) -> None:
        """跨文件调用按实际对象字段绑定形参，安全字段不得获得兄弟字段污点。"""
        lines = self._verified(
            {
                "main.js": "import {render} from './worker.js'; export function run() { const v = location.hash; render({bad:v,safe:'fixed'}); }",
                "worker.js": """export function render({bad: renamed, safe}) {
 document.body.innerHTML = renamed;
 document.body.innerHTML = safe;
}""",
            },
            "JS-SINK-DOM",
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

    def test_react_raw_html_and_safe_children(self) -> None:
        """React 原生标签 __html 为 Sink；普通 children 与自定义组件不是该 Sink。"""
        lines = self._verified(
            {
                "view.tsx": """import React from 'react';
export function View() {
 const html = location.hash;
 return <div dangerouslySetInnerHTML={{__html:html}}/>;
}
export function Safe() { const html=location.hash; return <div>{html}</div>; }
export function Custom() { const html=location.hash; return <Widget dangerouslySetInnerHTML={{__html:html}}/>; }
"""
            },
            "JS-SINK-REACT-HTML",
        )
        self.assertEqual([4], lines)

    def test_react_destructured_props(self) -> None:
        """props 来自实际 React 组件语法且保留可信性前提。"""
        self.assertTrue(
            self._verified(
                {
                    "view.jsx": "import React from 'react'; export function View({html}) {return <div dangerouslySetInnerHTML={{__html:html}}/>;}"
                },
                "JS-SINK-REACT-HTML",
            )
        )

    def test_vue_script_setup_js_and_ts(self) -> None:
        """SFC 原始行号、脚本数据流与模板写入保持可追溯。"""
        for lang in ("", ' lang="ts"'):
            with self.subTest(lang=lang):
                self.assertEqual(
                    [2],
                    self._verified(
                        {
                            "View.vue": '<template>\n<div v-html="html"></div><p>{{html}}</p>\n</template>\n<script setup'
                            + lang
                            + ">\nconst html=location.hash;\n</script>"
                        },
                        "JS-SINK-VUE-HTML",
                    ),
                )

    def test_vue_props_and_interpolation_boundary(self) -> None:
        """defineProps 宏仅在 setup 生效；普通插值不成为原始 HTML Sink。"""
        pack = self._analyze(
            {
                "View.vue": '<template><div v-html="html"></div><p>{{html}}</p></template>\n<script setup>\nconst {html}=defineProps(["html"]);\n</script>'
            }
        )
        self.assertTrue(
            any(
                item.dataflow_verified
                for item in pack.candidates
                if item.rule_id == "JS-SINK-VUE-HTML"
            )
        )
        self.assertEqual(
            1,
            len(
                [
                    item
                    for item in pack.facts.values()
                    if item.rule_id == "JS-SINK-VUE-HTML"
                ]
            ),
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

    def test_render_api_and_jsx_container_field_isolation(self) -> None:
        """render API 保留原始 HTML 角色；JSX 容器的无关危险字段不污染 __html。"""
        for filename, source, rule in (
            (
                "view.jsx",
                "import React from 'react'; function View(){const html=location.hash; return React.createElement('div',{dangerouslySetInnerHTML:{__html:html}});}",
                "JS-SINK-REACT-HTML",
            ),
            (
                "view.js",
                "import {h} from 'vue'; function render(){const html=location.hash; return h('div',{innerHTML:html});}",
                "JS-SINK-VUE-HTML",
            ),
            (
                "view.tsx",
                "import React from 'react'; function View(){const html={__html:location.hash}; return <div dangerouslySetInnerHTML={html}/>;}",
                "JS-SINK-REACT-HTML",
            ),
        ):
            with self.subTest(filename=filename):
                self.assertTrue(self._verified({filename: source}, rule))
        self.assertFalse(
            self._verified(
                {
                    "safe.tsx": "import React from 'react'; function View(){const html={__html:'fixed',unused:location.hash}; return <div dangerouslySetInnerHTML={html}/>;}"
                },
                "JS-SINK-REACT-HTML",
            )
        )

    def test_dynamic_patterns_spread_and_framework_gaps_reported(self) -> None:
        """未知动态语义必须进入持久化缺口，Options API 不伪装已验证模板流。"""
        pack = self._analyze(
            {
                "view.js": "function render(key){const {[key]:value,...rest}=data; const [first,...tail]=[...data];}"
            }
        )
        reasons = {item.get("reason") for item in pack.scan_failures}
        self.assertIn("dynamic_binding_selector_not_resolved", reasons)
        self.assertIn("opaque_rest_exclusions_not_proven", reasons)
        self.assertIn("dynamic_array_spread_positions_not_resolved", reasons)
        pack = self._analyze(
            {
                "View.vue": '<template><div v-html="html"></div></template><script>export default {data(){return {html:location.hash};}};</script>'
            }
        )
        self.assertIn(
            "vue_options_api_and_template_scope_not_modeled",
            {item.get("reason") for item in pack.scan_failures},
        )
        self.assertFalse(any(item.dataflow_verified for item in pack.candidates))

    def test_object_spread_overwrites_are_not_assumed_safe(self) -> None:
        """不确定 spread 可覆写前面的安全字段，之后的显式字面量仍覆盖该字段。"""
        lines = self._verified(
            {
                "view.js": """function render(){
 const input=location.hash;
 const {html:bad}={html:'fixed',...input};
 document.body.innerHTML=bad;
 const {html:safe}={...input,html:'fixed'};
 document.body.innerHTML=safe;
}"""
            },
            "JS-SINK-DOM",
        )
        self.assertIn(4, lines)
        self.assertNotIn(6, lines)
        self.assertTrue(
            self._verified(
                {
                    "view.js": "function render(){const input=location.hash; const {keep,...rest}={keep:'fixed',...input}; document.body.innerHTML=rest;}"
                },
                "JS-SINK-DOM",
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

    def test_program_binding_profiles_round_trip_and_vue_discovery(self) -> None:
        """递归绑定契约可 JSON 往返，程序图独立发现 TS Vue 而非错误选 JS grammar。"""
        import tempfile
        from pathlib import Path
        from backend.app.services.program_graph import (
            ProgramGraphArtifact,
            ProgramGraphService,
        )

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "View.vue").write_text(
                '<template><div v-html="html"></div></template><script setup lang="ts">const html: string=location.hash;</script>',
                encoding="utf-8",
            )
            Path(directory, "run.ts").write_text(
                "function run({nested:{value='fixed'}}:{nested:{value?:string}}){return value;}",
                encoding="utf-8",
            )
            graph = ProgramGraphService().analyze(directory)
        self.assertEqual([], graph.failures)
        self.assertEqual(["typescript"], graph.languages)
        recovered = ProgramGraphArtifact.model_validate_json(graph.model_dump_json())
        self.assertEqual(graph, recovered)
        self.assertTrue(
            any(item.parameter_patterns for item in recovered.functions.values())
        )


if __name__ == "__main__":
    unittest.main()

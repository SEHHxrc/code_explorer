"""常见输入边界回归：只有观察到真实框架注册才绑定外部输入。"""

from __future__ import annotations

import unittest

from backend.app.services.security_analysis.contracts import SecurityEvidencePack
from backend.app.services.security_analysis import SecurityEvidencePromptBuilder
from test import test_javascript_security as fixtures


class JavaScriptInputTests(unittest.TestCase):
    """复用完整分析管线，验证 Source、值流及反例，不执行测试源码。"""

    _analyze = fixtures.JavaScriptSecurityTests._analyze

    def _verified(self, pack: SecurityEvidencePack, rule: str) -> bool:
        """返回指定 Sink 是否有公共 DFG 验证的候选路径。"""
        return any(
            item.rule_id == rule and item.dataflow_verified for item in pack.candidates
        )

    def test_react_inline_event_values(self) -> None:
        """原生 input/textarea 的 target/currentTarget.value 可在回调内连到 Sink。"""
        for tag, receiver, filename in (
            ("input", "target", "view.jsx"),
            ("textarea", "currentTarget", "view.tsx"),
        ):
            with self.subTest(filename=filename):
                pack = self._analyze(
                    {
                        filename: f"import React from 'react'; function View() {{ return <{tag} onChange={{event => {{ document.body.innerHTML = event.{receiver}.value; }}}} />; }}"
                    }
                )
                self.assertTrue(self._verified(pack, "JS-SINK-DOM"))
                self.assertEqual(1, pack.coverage.source_count)

    def test_react_named_callback_and_destructuring(self) -> None:
        """本作用域具名回调及嵌套参数解构复用相同绑定协议。"""
        pack = self._analyze(
            {
                "view.jsx": "import React from 'react'; function View() { const handle = ({currentTarget: {value}}) => { document.body.innerHTML = value; }; return <input onInput={handle} />; }"
            }
        )
        self.assertTrue(self._verified(pack, "JS-SINK-DOM"))

    def test_event_other_fields_and_custom_components_not_sources(self) -> None:
        """自定义组件、非表单原生节点及事件其他字段不能被扩大为表单输入。"""
        for tag, value in (
            ("Custom", "event.target.value"),
            ("div", "event.target.value"),
            ("input", "event.type"),
        ):
            with self.subTest(tag=tag, value=value):
                pack = self._analyze(
                    {
                        "view.jsx": f"import React from 'react'; function View() {{ return <{tag} onChange={{event => {{ document.body.innerHTML = {value}; }}}} />; }}"
                    }
                )
                self.assertEqual(0, pack.coverage.source_count)
                self.assertFalse(self._verified(pack, "JS-SINK-DOM"))

    def test_event_registration_without_react_evidence(self) -> None:
        """任意 JSX 不能证明 React；未知运行库时不凭形状推断事件 Source。"""
        pack = self._analyze(
            {
                "view.jsx": "function View() { return <input onChange={event => { document.body.innerHTML = event.target.value; }} />; }"
            }
        )
        self.assertEqual(0, pack.coverage.source_count)

    def test_automatic_jsx_runtime(self) -> None:
        """静态 React 项目依赖支持无显式 React import 的现代 JSX。"""
        pack = self._analyze(
            {
                "package.json": '{"dependencies":{"react":"^18.0.0"}}',
                "view.tsx": "function View(props: {html: string}) { return <input onChange={event => { document.body.innerHTML = event.currentTarget.value; }} />; }",
            }
        )
        self.assertTrue(self._verified(pack, "JS-SINK-DOM"))
        props = self._analyze(
            {
                "package.json": '{"dependencies":{"react":"^18.0.0"}}',
                "view.jsx": "function View({html}) { return <div dangerouslySetInnerHTML={{__html: html}} />; }",
            }
        )
        self.assertTrue(self._verified(props, "JS-SINK-REACT-HTML"))

    def test_vue_or_mixed_dependencies_not_automatic_react(self) -> None:
        """Vue/React 混合依赖、无 React 或损坏配置不能单独证明 JSX 语义。"""
        for package in (
            '{"dependencies":{"vue":"^3.0.0"}}',
            '{"dependencies":{"vue":"^3.0.0","react":"^18.0.0"}}',
            "{broken",
        ):
            with self.subTest(package=package):
                pack = self._analyze(
                    {
                        "package.json": package,
                        "view.jsx": "function View({html}) { return <div dangerouslySetInnerHTML={{__html: html}} />; }",
                    }
                )
                self.assertEqual(0, pack.coverage.source_count)

    def test_rebound_named_callback_not_associated(self) -> None:
        """重新绑定具名回调不应将旧函数错误标记成被注册的输入处理者。"""
        pack = self._analyze(
            {
                "view.jsx": "import React from 'react'; function View() { let handle = event => { document.body.innerHTML = event.target.value; }; handle = replacement; return <input onChange={handle} />; }"
            }
        )
        self.assertEqual(0, pack.coverage.source_count)

    def test_node_request_data_callback(self) -> None:
        """HTTP 请求体 data 的首个参数在回调内具有真实输入路径。"""
        pack = self._analyze(
            {
                "server.js": "const http = require('node:http'); const cp = require('node:child_process'); http.createServer((req, res) => { req.on('data', chunk => { cp.exec(chunk.toString()); }); });"
            }
        )
        self.assertTrue(self._verified(pack, "JS-SINK-SHELL"))
        source = next(
            item
            for item in pack.facts.values()
            if item.rule_id == "JS-SOURCE-NODE-BODY"
        )
        self.assertEqual("untrusted", source.trust_class)

    def test_vue_model_to_html_js_and_ts(self) -> None:
        """文本输入到模板 HTML 具有保守 may 值流，保留 JS/TS 原位置。"""
        for language in ("js", "ts"):
            with self.subTest(language=language):
                pack = self._analyze(
                    {
                        "View.vue": f'<template>\n<div v-html="html"></div>\n<input v-model.trim="html" />\n</template>\n<script setup lang="{language}">\nimport {{ref}} from "vue";\nconst html = ref("");\n</script>'
                    }
                )
                self.assertTrue(self._verified(pack, "JS-SINK-VUE-HTML"))
                source = next(
                    item
                    for item in pack.facts.values()
                    if item.rule_id == "JS-SOURCE-VUE-MODEL"
                )
                self.assertEqual(3, source.location.line)
                candidate = next(
                    item for item in pack.candidates if item.dataflow_verified
                )
                flow = pack.dataflows[candidate.dataflow_id]
                self.assertTrue(any(step.certainty == "may" for step in flow.steps))
                self.assertEqual("low", flow.confidence)
                envelope = SecurityEvidencePromptBuilder().build(pack)
                finding = next(
                    item
                    for item in envelope.findings
                    if item.rule_id == "JS-SINK-VUE-HTML"
                )
                self.assertEqual("may", finding.source.context["binding_certainty"])

    def test_vue_mutable_variable_and_whitespace(self) -> None:
        """保留表达式两侧空格和原始 UTF-8 坐标；普通可写 setup 变量可建立模型。"""
        pack = self._analyze(
            {
                "View.vue": '<script setup>let html = "";</script>\n<template>中文<textarea v-model=" html " /><div v-html=" html " /></template>'
            }
        )
        self.assertTrue(self._verified(pack, "JS-SINK-VUE-HTML"))

    def test_vue_unsupported_model_boundaries(self) -> None:
        """const 标量、未知 ref 工厂、动态模板作用域及自定义组件保留缺口。"""
        for script, template in (
            ('const html = "";', '<input v-model="html" />'),
            ('const html = otherFactory("");', '<input v-model="html" />'),
            ('let html = "";', '<Custom v-model="html" />'),
            ('let html = "";', '<input type="checkbox" v-model="html" />'),
            (
                'let html = "";',
                '<section v-for="html in rows"><input v-model="html" /></section>',
            ),
        ):
            with self.subTest(template=template):
                pack = self._analyze(
                    {
                        "View.vue": f'<script setup>{script}</script><template>{template}<div v-html="html" /></template>'
                    }
                )
                self.assertEqual(0, pack.coverage.source_count)
                self.assertFalse(self._verified(pack, "JS-SINK-VUE-HTML"))

    def test_vue_safe_sibling_not_tainted(self) -> None:
        """输入只写特定 ref；安全兄弟绑定的 HTML 不被该输入污染。"""
        pack = self._analyze(
            {
                "View.vue": '<script setup>import {ref} from "vue"; const html = ref(""); const safe = ref("fixed");</script><template><input v-model="html" /><div v-html="safe" /></template>'
            }
        )
        self.assertEqual(1, pack.coverage.source_count)
        self.assertFalse(self._verified(pack, "JS-SINK-VUE-HTML"))

    def test_vue_ref_alias_and_namespace(self) -> None:
        """静态 ref 别名、namespace 与 shallowRef 保持相同模板身份。"""
        for declaration, constructor in (
            ("import {ref as state} from 'vue';", "state"),
            ("import * as Vue from 'vue';", "Vue.shallowRef"),
        ):
            with self.subTest(constructor=constructor):
                pack = self._analyze(
                    {
                        "View.vue": f'<script setup>{declaration} const html = {constructor}("");</script><template><input v-model="html" /><div v-html="html" /></template>'
                    }
                )
                self.assertTrue(self._verified(pack, "JS-SINK-VUE-HTML"))

    def test_node_named_body_callback(self) -> None:
        """HTTP handler 内唯一具名 data 回调保留精确函数身份。"""
        pack = self._analyze(
            {
                "server.ts": "import * as http from 'node:http'; import {exec} from 'node:child_process'; http.createServer((req, res) => { function consume(chunk: Buffer) { exec(chunk.toString()); } req.once('data', consume); });"
            }
        )
        self.assertTrue(self._verified(pack, "JS-SINK-SHELL"))

    def test_http_handler_names_do_not_cross_scopes(self) -> None:
        """被注册 handler 的同名局部函数不能继承请求类型或请求体 Source。"""
        pack = self._analyze(
            {
                "server.js": "const http = require('http'); const cp = require('child_process'); function handle(req, res) {} http.createServer(handle); function other() { function handle(req, res) { req.on('data', chunk => { cp.exec(chunk.toString()); }); } }"
            }
        )
        self.assertEqual(0, pack.coverage.source_count)

    def test_shadowed_http_factory_does_not_bind_request(self) -> None:
        """被函数形参遮蔽的 http 工厂不能为嵌套 handler 提供平台依据。"""
        pack = self._analyze(
            {
                "server.js": "const http = require('http'); const cp = require('child_process'); function other(http) { http.createServer((req, res) => { req.on('data', chunk => { cp.exec(chunk.toString()); }); }); }"
            }
        )
        self.assertEqual(0, pack.coverage.source_count)

    def test_arbitrary_emitter_and_other_http_events_not_sources(self) -> None:
        """普通对象的 on/data、请求 end 事件、response 参数均不误认请求体。"""
        for registration in ("other.on('data'", "req.on('end'", "res.on('data'"):
            with self.subTest(registration=registration):
                pack = self._analyze(
                    {
                        "server.js": f"const http = require('http'); const cp = require('child_process'); http.createServer((req, res) => {{ {registration}, chunk => {{ cp.exec(chunk.toString()); }}); }});"
                    }
                )
                self.assertEqual(0, pack.coverage.source_count)
                self.assertFalse(self._verified(pack, "JS-SINK-SHELL"))


if __name__ == "__main__":
    unittest.main()

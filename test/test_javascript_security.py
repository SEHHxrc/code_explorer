"""JS/TS 安全规则、公共图复用、误报边界和证据投影回归。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.security_analysis import (
    SecurityAnalysisService,
    SecurityEvidencePromptBuilder,
)
from backend.app.services.security_analysis.contracts import SecurityEvidencePack


class JavaScriptSecurityTests(unittest.TestCase):
    """通过真实依赖图、公共 CFG/DFG 和 LLM 投影验证新增前端。"""

    def _analyze(self, files: dict[str, str]) -> SecurityEvidencePack:
        """输入临时项目文件，返回完整管线生成的持久化安全证据包。"""
        with tempfile.TemporaryDirectory() as directory:
            for name, source in files.items():
                Path(directory, name).write_text(source, encoding="utf-8")
            result = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            return SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=result["dependency_graph"],
                analysis_diagnostics=result["diagnostics"],
                semantic_index=result["semantic_index"],
            )

    def test_browser_dom_flow_and_exact_positions(self) -> None:
        """浏览器输入经变量赋值到真实 DOM 接收者，验证精确属性写入位置。"""
        pack = self._analyze(
            {
                "view.js": "function render() {\n const value = location.hash;\n const el = document.getElementById('root');\n el.innerHTML = value;\n}\n"
            }
        )
        candidate = next(
            item for item in pack.candidates if item.rule_id == "JS-SINK-DOM"
        )
        self.assertTrue(candidate.dataflow_verified)
        self.assertEqual(4, pack.facts[candidate.sink_fact_id].location.line)
        self.assertIn("javascript-typescript-core/1.2", pack.rule_packs)
        self.assertIn("javascript", pack.languages_analyzed)

    def test_safe_sibling_and_same_line_are_not_tainted(self) -> None:
        """同一行的安全兄弟初始化不得获得其他变量的外部输入。"""
        pack = self._analyze(
            {
                "view.js": "function render() { const value = location.hash, safe = 'fixed'; document.body.innerHTML = safe; }"
            }
        )
        candidates = [item for item in pack.candidates if item.rule_id == "JS-SINK-DOM"]
        self.assertTrue(candidates)
        self.assertTrue(all(not item.dataflow_verified for item in candidates))

    def test_direct_property_sources_to_dom_and_shell(self) -> None:
        """直接属性写入/实参能验证词法值连接，且不重复子属性 Source。"""
        for filename, source, rule_id in (
            (
                "view.js",
                "function render() { document.body.innerHTML = location.hash; }",
                "JS-SINK-DOM",
            ),
            (
                "run.js",
                "const cp=require('child_process'); function run() { cp.exec(process.env.CMD); }",
                "JS-SINK-SHELL",
            ),
        ):
            with self.subTest(filename=filename):
                pack = self._analyze({filename: source})
                self.assertTrue(
                    any(
                        item.dataflow_verified and item.rule_id == rule_id
                        for item in pack.candidates
                    )
                )
                self.assertEqual(1, pack.coverage.source_count)

    def test_esm_alias_environment_to_shell(self) -> None:
        """ESM 别名和 node: 前缀规范为平台 API，保留环境变量可信性前提。"""
        pack = self._analyze(
            {
                "run.ts": "import { exec as run } from 'node:child_process';\nfunction launch() {\n const value: string = process.env.CMD;\n run(value);\n}\n"
            }
        )
        candidate = next(
            item for item in pack.candidates if item.rule_id == "JS-SINK-SHELL"
        )
        self.assertTrue(candidate.dataflow_verified)
        self.assertEqual(
            "external_configuration", pack.facts[candidate.source_fact_id].trust_class
        )
        self.assertTrue(pack.facts[candidate.sink_fact_id].metadata["shell"])

    def test_commonjs_destructuring_and_inline_express_handler(self) -> None:
        """常见匿名 Express handler 有独立程序图身份；res/next 不作为 Source。"""
        pack = self._analyze(
            {
                "server.js": "const express = require('express');\nconst {exec: run} = require('child_process');\nconst app = express();\napp.get('/run', (req, res, next) => {\n const value = req.query.cmd;\n run(value);\n});\n"
            }
        )
        self.assertTrue(pack.entrypoints)
        candidates = [
            item for item in pack.candidates if item.rule_id == "JS-SINK-SHELL"
        ]
        self.assertTrue(candidates)
        self.assertTrue(any(item.dataflow_verified for item in candidates))
        self.assertFalse(
            any(item.name in {"res", "next"} for item in pack.facts.values())
        )

    def test_named_express_handler_and_response_not_http_source(self) -> None:
        """路由登记的第一个参数才是 request，普通 response 属性不应被误认。"""
        pack = self._analyze(
            {
                "server.ts": "import express from 'express';\nimport { exec } from 'child_process';\nconst app = express();\nfunction handle(req: Request, res: Response) {\n const value = req.body.command;\n exec(value);\n const safe = res.body;\n exec(safe);\n}\napp.post('/run', handle);\n"
            }
        )
        self.assertTrue(pack.entrypoints)
        self.assertTrue(any(item.dataflow_verified for item in pack.candidates))
        self.assertFalse(
            any(
                item.dataflow_verified
                and pack.facts[item.sink_fact_id].location.line == 8
                for item in pack.candidates
            )
        )

    def test_shadowed_globals_imports_and_arbitrary_dom_property(self) -> None:
        """同名自定义函数与普通对象的 innerHTML 不能伪装成平台危险 API。"""
        pack = self._analyze(
            {
                "view.js": "import {exec} from 'child_process';\nfunction safe(exec, eval, location, Function) {\n const v = location.hash;\n exec(v); eval(v); new Function(v);\n const obj = {}; obj.innerHTML = v;\n}\n"
            }
        )
        self.assertFalse(pack.candidates)
        self.assertFalse(
            any(item.fact_kind == "source" for item in pack.facts.values())
        )

    def test_execfile_shell_option_is_not_default(self) -> None:
        """execFile 默认无 Shell；只有显式 shell:true 才检查命令参数注入。"""
        pack = self._analyze(
            {
                "run.js": "const cp = require('child_process');\nfunction launch() {\n const value = process.env.CMD;\n cp.execFile('echo', [value]);\n cp.spawn('echo', [value], {shell: true});\n}\n"
            }
        )
        shell = [
            item for item in pack.candidates if item.rule_id == "JS-SINK-SHELL-OPTION"
        ]
        self.assertTrue(any(item.dataflow_verified for item in shell))
        plain = [item for item in pack.candidates if item.rule_id == "JS-SINK-PROCESS"]
        self.assertTrue(all(not item.dataflow_verified for item in plain))

    def test_tsx_and_anonymous_functions_use_shared_parser(self) -> None:
        """TSX 不产生 grammar 错误；JS/TS 使用同一控制和数据流实现。"""
        pack = self._analyze(
            {
                "view.tsx": "export function View() {\n const value = location.hash;\n document.body.innerHTML = value;\n return <div>hello</div>;\n}\n"
            }
        )
        self.assertIn("typescript", pack.languages_analyzed)
        self.assertFalse(
            any("parse_error" in item.get("reason", "") for item in pack.scan_failures)
        )
        self.assertTrue(any(item.dataflow_verified for item in pack.candidates))

    def test_literal_sink_and_textcontent_do_not_confirm_xss(self) -> None:
        """安全文本写入不作 DOM Sink，固定 HTML 不产生已验证外部输入路径。"""
        pack = self._analyze(
            {
                "view.js": "function render() {\n const value = location.hash;\n document.body.textContent = value;\n document.body.innerHTML = '<b>fixed</b>';\n}\n"
            }
        )
        self.assertTrue(all(not item.dataflow_verified for item in pack.candidates))
        self.assertFalse(
            any(item.name.endswith("textContent") for item in pack.facts.values())
        )

    def test_unmatched_sanitizer_keeps_candidate_and_json_contract(self) -> None:
        """未验证净化上下文不能删除污点路径；投影有界且是有效 JSON。"""
        pack = self._analyze(
            {
                "view.js": "import DOMPurify from 'dompurify';\nfunction render() {\n const value = location.hash;\n const cleaned = DOMPurify.sanitize(value);\n document.body.innerHTML = cleaned;\n}\n"
            }
        )
        self.assertTrue(
            any(item.fact_kind == "sanitizer" for item in pack.facts.values())
        )
        self.assertTrue(any(item.dataflow_verified for item in pack.candidates))
        builder = SecurityEvidencePromptBuilder()
        envelope = builder.build(pack)
        self.assertTrue(envelope.findings)
        self.assertEqual(
            envelope.schema_version,
            json.loads(envelope.model_dump_json())["schema_version"],
        )

    def test_cross_file_call_uses_resolved_dependency_edge(self) -> None:
        """模块间传播只沿已解析调用边，仍是 may-reach 而不是确定漏洞。"""
        pack = self._analyze(
            {
                "main.js": "import {execute} from './worker.js';\nexport function run() { const v = process.env.CMD; execute(v); }\n",
                "worker.js": "import {exec} from 'child_process';\nexport function execute(value) { exec(value); }\n",
            }
        )
        self.assertTrue(
            any(
                item.dataflow_verified and item.path_kind == "interprocedural_dataflow"
                for item in pack.candidates
            )
        )

    def test_dynamic_shell_options_are_not_assumed_false(self) -> None:
        """动态 options 不能当成默认 shell:false，必须显式产生覆盖缺口。"""
        pack = self._analyze(
            {
                "run.js": "const cp = require('child_process');\nfunction run(options) { const v = process.env.CMD; cp.spawn('echo', [v], options); }"
            }
        )
        self.assertTrue(
            any(
                item.get("reason") == "javascript_dynamic_process_options_not_modeled"
                for item in pack.scan_failures
            )
        )
        self.assertFalse(
            any(
                item.rule_id in {"JS-SINK-PROCESS", "JS-SINK-SHELL-OPTION"}
                for item in pack.facts.values()
            )
        )

    def test_reassigned_import_and_destructured_shadow_do_not_match_platform(
        self,
    ) -> None:
        """重绑定导入和解构局部名称不得继续保留平台 API 身份。"""
        pack = self._analyze(
            {
                "view.js": "const cp = require('child_process');\nfunction run() { cp = custom; const {location, eval: ev} = custom; const v = location.hash; cp.exec(v); ev(v); }"
            }
        )
        self.assertFalse(pack.candidates)


if __name__ == "__main__":
    unittest.main()

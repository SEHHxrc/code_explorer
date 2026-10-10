"""实际状态传播断点与误连反例；通过完整管线，不执行样例或框架。"""

from __future__ import annotations

import unittest

from backend.app.services.security_analysis import SecurityEvidencePromptBuilder
from backend.app.services.security_analysis.contracts import SecurityEvidencePack
from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.program_graph import ProgramGraphService
from pathlib import Path
import tempfile
from test import test_javascript_security as fixtures


class JavaScriptStateTests(unittest.TestCase):
    """验证规则无关边界与已有参数/返回传播兼容，不把所有状态当 Source。"""

    _analyze = fixtures.JavaScriptSecurityTests._analyze

    def _verified(self, pack: SecurityEvidencePack, rule: str) -> bool:
        """仅检查实际 DFG 候选，不把结构候选当验证成功。"""
        return any(
            item.rule_id == rule and item.dataflow_verified for item in pack.candidates
        )

    def test_react_state_event_to_render(self) -> None:
        """事件值进入 hook 状态，重渲染读取到原生 HTML Sink，保留 may 和边界。"""
        for filename, declaration in (
            ("View.jsx", "import React, {useState} from 'react';"),
            ("View.tsx", "import React from 'react';"),
        ):
            factory = "useState" if filename.endswith(".jsx") else "React.useState"
            with self.subTest(filename=filename):
                pack = self._analyze(
                    {
                        filename: f"{declaration}\nfunction View() {{\n const [html, setHtml] = {factory}('');\n return <><input onChange={{event => {{ setHtml(event.target.value); }}}} /><div dangerouslySetInnerHTML={{{{__html: html}}}} /></>;\n}}"
                    }
                )
                self.assertTrue(self._verified(pack, "JS-SINK-REACT-HTML"))
                candidate = next(
                    item for item in pack.candidates if item.dataflow_verified
                )
                flow = pack.dataflows[candidate.dataflow_id]
                self.assertTrue(flow.value_boundary_ids)
                self.assertTrue(
                    all(
                        item.startswith("value-boundary:")
                        for item in flow.value_boundary_ids
                    )
                )
                self.assertFalse(
                    any(
                        item.startswith("value-boundary:")
                        for item in flow.call_edge_ids
                    )
                )
                self.assertEqual("low", flow.confidence)
                envelope = SecurityEvidencePromptBuilder().build(pack)
                finding = next(
                    item
                    for item in envelope.findings
                    if item.rule_id == "JS-SINK-REACT-HTML"
                )
                self.assertTrue(any(step.certainty == "may" for step in finding.flow))
                self.assertTrue(any("批处理" in item for item in finding.limitations))
                restored = SecurityEvidencePack.model_validate_json(
                    pack.model_dump_json()
                )
                self.assertEqual(
                    flow.value_boundary_ids,
                    restored.dataflows[flow.flow_id].value_boundary_ids,
                )

    def test_react_named_callback_and_hook_alias(self) -> None:
        """静态 hook 别名与同组件具名回调复用相同绑定。"""
        pack = self._analyze(
            {
                "View.jsx": "import React, {useState as state} from 'react'; function View() { const [html, setHtml] = state(''); const change = event => { setHtml(event.currentTarget.value); }; return <><textarea onChange={change} /><div dangerouslySetInnerHTML={{__html:html}} /></>; }"
            }
        )
        self.assertTrue(self._verified(pack, "JS-SINK-REACT-HTML"))

    def test_react_safe_sibling_state_not_tainted(self) -> None:
        """只写入实际 setter 所属的状态槽位，不扩散到安全兄弟状态。"""
        pack = self._analyze(
            {
                "View.jsx": "import React, {useState} from 'react'; function View() { const [html,setHtml]=useState(''); const [safe,setSafe]=useState('fixed'); return <><input onChange={event=>setHtml(event.target.value)} /><div dangerouslySetInnerHTML={{__html:safe}} /></>; }"
            }
        )
        self.assertFalse(self._verified(pack, "JS-SINK-REACT-HTML"))

    def test_structured_state_is_not_flattened_into_safe_fields(self) -> None:
        """复杂状态更新未建模时明确诊断，不把危险成员错误扩散到安全字段。"""
        pack = self._analyze({"View.jsx": "import React,{useState} from 'react';function View(){const [data,setData]=useState({});return <><input onChange={event=>setData({safe:'fixed',unsafe:event.target.value})}/><div dangerouslySetInnerHTML={{__html:data.safe}}/></>;}"})
        self.assertFalse(self._verified(pack, "JS-SINK-REACT-HTML"))
        self.assertIn("javascript_structured_state_update_not_modeled", {item.get("reason") for item in pack.scan_failures})

    def test_react_state_not_global_source(self) -> None:
        """没有外部输入的 useState 与固定值 setter 不产生新 Source。"""
        pack = self._analyze(
            {
                "View.jsx": "import React, {useState} from 'react'; function View() { const [html,setHtml]=useState('fixed'); return <><input onChange={event=>setHtml('fixed')} /><div dangerouslySetInnerHTML={{__html:html}} /></>; }"
            }
        )
        self.assertEqual(0, pack.coverage.source_count)
        self.assertFalse(self._verified(pack, "JS-SINK-REACT-HTML"))

    def test_react_setter_shadow_and_functional_update(self) -> None:
        """局部同名函数不变成 setter；功能式 updater 保留缺口而非猜测返回。"""
        for body in (
            "let setHtml = other; setHtml(event.target.value);",
            "setHtml(previous => event.target.value);",
        ):
            with self.subTest(body=body):
                pack = self._analyze(
                    {
                        "View.jsx": f"import React, {{useState}} from 'react'; function View() {{ const [html,setHtml]=useState(''); return <><input onChange={{event=>{{{body}}}}} /><div dangerouslySetInnerHTML={{{{__html:html}}}} /></>; }}"
                    }
                )
                self.assertFalse(self._verified(pack, "JS-SINK-REACT-HTML"))

    def test_react_components_with_same_state_names_stay_separate(self) -> None:
        """同文件多个组件同名状态不能连接到其他组件的 Sink。"""
        pack = self._analyze(
            {
                "View.jsx": "import React,{useState} from 'react'; function Input() {const [html,setHtml]=useState(''); return <input onChange={event=>setHtml(event.target.value)} />;} function Output() {const [html,setHtml]=useState('fixed'); return <div dangerouslySetInnerHTML={{__html:html}} />;}"
            }
        )
        self.assertFalse(self._verified(pack, "JS-SINK-REACT-HTML"))

    def test_node_string_body_data_to_end(self) -> None:
        """同一请求 data 累积变量到 end 命令实参，保持跨回调 may。"""
        pack = self._analyze(
            {
                "server.js": "const http=require('http'); const cp=require('child_process'); http.createServer((req,res)=>{ let body=''; req.on('data', chunk=>{ body += chunk.toString(); }); req.on('end', ()=>{ cp.exec(body); }); });"
            }
        )
        self.assertTrue(self._verified(pack, "JS-SINK-SHELL"))
        flow = next(item for item in pack.dataflows.values() if item.value_boundary_ids)
        self.assertEqual("interprocedural", flow.scope)
        self.assertTrue(any("data→end" in item for item in flow.unresolved))

    def test_node_array_body_data_to_end(self) -> None:
        """局部空数组 push 接收请求分块，读取聚合结果进入 Sink。"""
        pack = self._analyze(
            {
                "server.ts": "import * as http from 'node:http'; import {exec} from 'node:child_process'; http.createServer((req,res)=>{const chunks:Buffer[]=[]; req.on('data',chunk=>{chunks.push(chunk);}); req.on('end',()=>{const body=Buffer.concat(chunks).toString(); exec(body);});});"
            }
        )
        self.assertTrue(self._verified(pack, "JS-SINK-SHELL"))

    def test_node_body_overwrite_in_data_or_end_kills_taint(self) -> None:
        """写入侧最终固定覆盖、读取侧固定覆盖，均不能保留旧输入路径。"""
        for data, end in (
            ("body+=chunk;body='fixed';", "cp.exec(body);"),
            ("body+=chunk;", "body='fixed';cp.exec(body);"),
        ):
            with self.subTest(data=data, end=end):
                pack = self._analyze(
                    {
                        "server.js": f"const http=require('http'); const cp=require('child_process'); http.createServer((req,res)=>{{let body=''; req.on('data',chunk=>{{{data}}});req.on('end',()=>{{{end}}});}});"
                    }
                )
                self.assertFalse(self._verified(pack, "JS-SINK-SHELL"))

    def test_node_scalar_guard_uses_common_cfg(self) -> None:
        """正常请求体大小检查/条件写入不应切断路径，使用公共 CFG 而非忽略回调。"""
        for data in (
            "body+=chunk; if(body.length>1000){req.destroy();}",
            "if(chunk.length){body+=chunk;}",
        ):
            with self.subTest(data=data):
                pack = self._analyze(
                    {
                        "server.js": f"const http=require('http');const cp=require('child_process');http.createServer((req,res)=>{{let body='';req.on('data',chunk=>{{{data}}});req.on('end',()=>cp.exec(body));}});"
                    }
                )
                self.assertTrue(self._verified(pack, "JS-SINK-SHELL"))

    def test_node_data_return_and_conditional_overwrite(self) -> None:
        """不可达写入和条件分支后的确定安全覆写都由公共到达定义阻断。"""
        for data in (
            "return;body+=chunk;",
            "if(chunk.length){body+=chunk;}body='fixed';",
        ):
            with self.subTest(data=data):
                pack = self._analyze(
                    {
                        "server.js": f"const http=require('http');const cp=require('child_process');http.createServer((req,res)=>{{let body='';req.on('data',chunk=>{{{data}}});req.on('end',()=>cp.exec(body));}});"
                    }
                )
                self.assertFalse(self._verified(pack, "JS-SINK-SHELL"))

    def test_state_boundary_then_normal_call(self) -> None:
        """状态边界可继续进入既有实参传播，不混淆两类边的 ID。"""
        pack = self._analyze(
            {
                "server.js": "const http=require('http');const cp=require('child_process');function run(value){cp.exec(value);}http.createServer((req,res)=>{let body='';req.on('data',chunk=>{body+=chunk;});req.on('end',()=>run(body));});"
            }
        )
        self.assertTrue(self._verified(pack, "JS-SINK-SHELL"))
        flow = next(item for item in pack.dataflows.values() if item.value_boundary_ids)
        self.assertTrue(flow.call_edge_ids)
        self.assertTrue(set(flow.value_boundary_ids).isdisjoint(flow.call_edge_ids))

    def test_node_body_local_shadow_not_capture(self) -> None:
        """data/end 中的局部同名 body 不属于外层共享槽位。"""
        for data, end in (
            ("let body=chunk;", "cp.exec(body);"),
            ("body+=chunk;", "const body='fixed';cp.exec(body);"),
        ):
            with self.subTest(data=data, end=end):
                pack = self._analyze(
                    {
                        "server.js": f"const http=require('http'); const cp=require('child_process'); http.createServer((req,res)=>{{let body='';req.on('data',chunk=>{{{data}}});req.on('end',()=>{{{end}}});}});"
                    }
                )
                self.assertFalse(self._verified(pack, "JS-SINK-SHELL"))

    def test_node_distinct_handlers_do_not_share_body(self) -> None:
        """跨 handler 的同名变量及相同请求参数名不会连接。"""
        pack = self._analyze(
            {
                "server.js": "const http=require('http');const cp=require('child_process');http.createServer((req,res)=>{let body='';req.on('data',chunk=>{body+=chunk;});});http.createServer((req,res)=>{let body='fixed';req.on('end',()=>{cp.exec(body);});});"
            }
        )
        self.assertFalse(self._verified(pack, "JS-SINK-SHELL"))

    def test_unreachable_react_state_writer_not_connected(self) -> None:
        """return 后的 setter 不可达，不产生回调到渲染的边界。"""
        pack = self._analyze(
            {
                "View.jsx": "import React,{useState} from 'react';function View(){const [html,setHtml]=useState('');return <><input onChange={event=>{return;setHtml(event.target.value);}}/><div dangerouslySetInnerHTML={{__html:html}}/></>;}"
            }
        )
        self.assertFalse(self._verified(pack, "JS-SINK-REACT-HTML"))

    def test_callback_body_not_evaluated_in_parent_graph(self) -> None:
        """注册回调时不执行其赋值/调用；三类图使用同一真实回调作用域。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "server.js").write_text(
                "function parent(req){const callback=chunk=>{const inner=chunk;run(inner);};req.on('data',chunk=>{body+=chunk;run(body);});}",
                encoding="utf-8",
            )
            result = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            graph = ProgramGraphService().analyze(
                directory, dependency_graph=result["dependency_graph"]
            )
            parent = next(
                item
                for item in graph.functions.values()
                if item.symbol_id == "server.js::parent"
            )
            self.assertFalse(
                any(
                    "inner" in node.definitions or "body" in node.definitions
                    for node in parent.nodes.values()
                )
            )
            self.assertFalse(
                any(
                    call.name == "run"
                    for node in parent.nodes.values()
                    for call in node.calls
                )
            )
            owners = [
                call["caller_symbol"]
                for call in result["semantic_index"]["callsites"].values()
                if call["name"] == "run"
            ]
            self.assertTrue(
                all(name.startswith("server.js::parent::") for name in owners)
            )
            self.assertFalse(
                any(
                    node.uses
                    for node in parent.nodes.values()
                    if "const callback=" in node.code
                )
            )

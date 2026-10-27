"""编译器语义接入的离线回归：不联网、不执行测试项目、不调用模型。"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.semantic_index import SemanticIndex, SemanticIndexArtifact
from backend.app.services.semantic_index.resolution import ProjectSemanticResult
from backend.app.services.language_backends.typescript import TOOL_DIRECTORY, TypeScriptSemanticResolver
from backend.app.services.language_backends.wire import decode_table_result
from backend.app.services.security_analysis.flow_analysis.call_graph import CallTransitionIndex
from backend.app.services.security_analysis import SecurityAnalysisService, SecurityEvidenceCatalog
from backend.app.services.program_graph import ProgramGraphService


class UnavailableResolver:
    """离线模拟未安装工具，验证既有范围仍可保守分析。"""

    def resolve(self, project_root: str, *, file_paths: list[str], max_file_bytes: int) -> ProjectSemanticResult:
        """返回明确诊断，不读取或执行测试项目。"""
        return ProjectSemanticResult(backend="typescript", status="unavailable", files=file_paths,
                                     diagnostics=[{"reason": "test_backend_unavailable"}])


class SemanticFallbackTests(unittest.TestCase):
    """失败不伪造编译器成功，也不能使其他语言退化。"""

    def test_unavailable_is_visible_on_graph_and_index(self) -> None:
        """同一回退原因进入图边、语义索引和运行摘要。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "a.ts").write_text("function f(){}\nfunction run(){f();}\n", encoding="utf8")
            result = UnifiedCodeAnalyzer(directory, max_workers=1, semantic_resolver=UnavailableResolver()).run_full_analysis()
        artifact = SemanticIndexArtifact.model_validate(result["semantic_index"])
        call = next(item for item in artifact.callsites.values() if item.name == "f")
        self.assertEqual("test_backend_unavailable", call.fallback_reason)
        self.assertEqual("may", call.targets[0].certainty)
        self.assertEqual("unavailable", artifact.backend_runs[0]["status"])
        self.assertEqual(artifact.backend_runs, result["diagnostics"]["semantic_backends"])

    def test_timeout_is_explicit(self) -> None:
        """进程超时不能冒充空的成功分析。"""
        import subprocess
        with patch("backend.app.services.language_backends.typescript.shutil.which", return_value="node"), \
             patch.object(Path, "is_file", return_value=True), \
             patch("backend.app.services.language_backends.worker.subprocess.run", side_effect=subprocess.TimeoutExpired("node", 1)):
            result = TypeScriptSemanticResolver().resolve(".", file_paths=["a.ts"], max_file_bytes=1000)
        self.assertEqual("failed", result.status)
        self.assertEqual("semantic_worker_timeout", result.diagnostics[0]["reason"])

    def test_corrupt_shared_table_reference_is_rejected(self) -> None:
        """负索引不能悄悄解引用为最后一个文件，损坏协议不能冒充有效证据。"""
        with self.assertRaises(ValueError):
            decode_table_result({
                "wire_format": "tables-v1", "files": ["a.ts"], "declarations": [], "type_literals": [],
                "calls": [[-1, 1, 1, 1, 4, [], None, "direct"]], "types": [],
            })


@unittest.skipUnless(shutil.which("node") and (TOOL_DIRECTORY / "node_modules/typescript/lib/typescript.js").is_file(),
                     "需先在 backend/tools/typescript_semantics 执行 npm ci --ignore-scripts")
class TypeScriptSemanticTests(unittest.TestCase):
    """使用真实、锁定版本编译器检查声明绑定及下游一致性。"""

    def analyze(self, files: dict[str, str]) -> dict:
        """只在独立临时项目运行分析，返回普通可序列化产物。"""
        with tempfile.TemporaryDirectory() as directory:
            for name, content in files.items():
                target = Path(directory, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf8")
            return UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()

    def test_native_variable_identity_is_consumed_by_shared_dataflow(self) -> None:
        """TS 块级遮蔽与跨函数实参使用声明槽位，而不是按同名变量合并。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.ts").write_text('''function execute(command: string) { eval(command); }
function run() {
 let value = window.location.hash;
 { let value = "fixed"; eval(value); }
 execute(value);
 let safe = "fixed";
 { let safe = window.location.hash; eval(safe); }
 eval(safe);
}
''', encoding="utf8")
            result = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            index = SemanticIndex.load(result["semantic_index"])
            self.assertTrue(index.artifact.variable_references)
            graph = ProgramGraphService().analyze(directory, semantic_index=index)
            self.assertEqual(graph, type(graph).model_validate_json(graph.model_dump_json()))
            pack = SecurityAnalysisService().analyze(project_root=directory,
                dependency_graph=result["dependency_graph"], semantic_index=result["semantic_index"])
        self.assertFalse(any(item.get("reason") == "program_graph_dataflow_error" for item in pack.scan_failures))
        self.assertEqual({1, 7}, {pack.facts[item.sink_fact_id].location.line
                                 for item in pack.candidates if item.dataflow_verified})

    def test_reexport_alias_avoids_legacy_guess_and_reaches_shared_index(self) -> None:
        """重导出别名绑定真实实现，图边和安全调用索引完全一致。"""
        result = self.analyze({
            "impl.ts": "export function execute(value:string) { return value; }\n",
            "barrel.ts": "export { execute as launch } from './impl';\n",
            "app.ts": "import { launch as action } from './barrel';\nexport function run(value:string) { return action(value); }\n",
        })
        artifact = SemanticIndexArtifact.model_validate(result["semantic_index"])
        call = next(item for item in artifact.callsites.values() if item.name == "action")
        self.assertEqual(["impl.ts::execute"], [item.target_symbol for item in call.targets])
        self.assertEqual("typescript_declaration_binding", call.targets[0].resolution_method)
        self.assertIsNone(call.fallback_reason)
        edge = next(item for item in result["dependency_graph"]["links"] if item.get("id") == call.targets[0].edge_id)
        self.assertEqual("impl.ts::execute", edge["target"])
        transitions = CallTransitionIndex.from_semantic_index(artifact).for_source("app.ts::run")
        self.assertEqual([(edge["id"], "impl.ts::execute")], [(item.edge_id, item.target) for item in transitions])
        self.assertEqual("6.0.3", artifact.backend_runs[0]["version"])

    def test_factory_receiver_and_distinct_same_named_method(self) -> None:
        """调用返回类型定位真实方法，而不是全项目同名方法并集。"""
        result = self.analyze({"app.ts": (
            "class A { run(value:string) { return value; } }\n"
            "class B { run(value:string) { return ''; } }\n"
            "function make() { return new A(); }\n"
            "function caller(value:string) { const instance=make(); return instance.run(value); }\n"
        )})
        artifact = SemanticIndexArtifact.model_validate(result["semantic_index"])
        call = next(item for item in artifact.callsites.values() if item.receiver == "instance")
        self.assertEqual(["app.ts::A::run"], [item.target_symbol for item in call.targets])
        self.assertEqual("may", call.targets[0].certainty)
        native_type = next(item for item in artifact.variable_types.values() if item.name == "instance")
        self.assertEqual("A", native_type.type_literal)
        self.assertEqual("typescript_type_checker", native_type.resolution_method)

    def test_native_shadowed_types_keep_distinct_declaration_locations(self) -> None:
        """真实 TypeScript 类型检查器的参数/块变量同名事实不能在公共索引中丢失。"""
        result = self.analyze({"app.ts": "function run(value:string){\n { const value=42; console.log(value); }\n return value;\n}\n"})
        index = SemanticIndex.load(result["semantic_index"])
        facts = index.variable_types_for("app.ts::run", "value")
        self.assertEqual(2, len(facts))
        self.assertEqual({"parameter", "variable"}, {item.role for item in facts})
        self.assertEqual(2, len({item.declaration_id for item in facts}))
        self.assertIsNone(index.variable_type("app.ts::run", "value"))
        self.assertEqual("string", index.variable_types_for("app.ts::run", "value", role="parameter")[0].type_literal)

    def test_variable_references_keep_shadowing_import_alias_and_unknown_type(self) -> None:
        """引用的是源码实体；块遮蔽、写引用、简写属性和 any 不可按名字合并或丢弃。"""
        result = self.analyze({
            "state.ts": "export let shared:any;\n",
            "app.ts": "import {shared as renamed} from './state';\nfunction run(value:string){\n let current=value;\n { let current=7; current++; const copy={current}; }\n current=renamed;\n return current;\n}\n",
        })
        index = SemanticIndex.load(result["semantic_index"])
        refs = list(index.artifact.variable_references.values())
        current = [item for item in refs if item.name == "current" and item.location.path == "app.ts"]
        self.assertEqual([4, 4, 3, 3], [index.variable_declaration(item.declaration_ids[0]).location.line for item in current])
        alias = next(item for item in refs if item.name == "renamed" and item.location.line == 5)
        declaration = index.variable_declaration(alias.declaration_ids[0])
        self.assertEqual(("state.ts", "shared"), (declaration.location.path, declaration.name))
        self.assertEqual((), index.declaration_types_for_reference(alias.reference_id))
        self.assertIsNone(alias.unresolved_reason)

    def test_unicode_columns_and_constructor_identity(self) -> None:
        """UTF-16 与 UTF-8 差异不应造成调用点错位；构造器统一映射类身份。"""
        result = self.analyze({"a.ts": (
            "class Box { constructor(value:string) {} }\n"
            "function f(value:string) { return value; }\n"
            "function run(){const 中文='😀'; const b=new Box(中文); return f(中文);}\n"
        )})
        artifact = SemanticIndexArtifact.model_validate(result["semantic_index"])
        calls = [item for item in artifact.callsites.values() if item.name in {"Box", "f"}]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(item.targets[0].resolution_method == "typescript_declaration_binding" for item in calls))
        allocation = next(item for item in calls if item.name == "Box")
        self.assertEqual(["a.ts::Box"], [item.target_symbol for item in allocation.targets])
        self.assertEqual("instantiates", allocation.targets[0].relation)

    def test_paths_configuration_without_build_or_install(self) -> None:
        """只读取 tsconfig 的路径配置，不执行其中或仓库中的脚本。"""
        result = self.analyze({
            "tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@lib/*": ["lib/*"]}}}),
            "lib/a.ts": "export function f(value:string){return value;}\n",
            "a.ts": "import {f as launch} from '@lib/a';\nfunction run(value:string){return launch(value);}\n",
            "package.json": json.dumps({"scripts": {"prepare": "exit 99"}}),
        })
        artifact = SemanticIndexArtifact.model_validate(result["semantic_index"])
        call = next(item for item in artifact.callsites.values() if item.name == "launch")
        self.assertEqual("typescript_declaration_binding", call.targets[0].resolution_method)
        self.assertEqual("lib/a.ts::f", call.targets[0].target_symbol)
        self.assertTrue(any(item["reason"] == "compiler_options_sha256" for item in artifact.backend_runs[0]["diagnostics"]))

    def test_union_preserves_both_possible_functions(self) -> None:
        """联合函数不能只保留编译器合成签名所引用的第一条声明。"""
        result = self.analyze({"a.ts": (
            "function a(v:string){return v;}\nfunction b(v:string){return v;}\n"
            "function run(v:string, flag:boolean){ const f=flag?a:b; return f(v); }\n"
        )})
        call = next(item for item in result["semantic_index"]["callsites"].values() if item["name"] == "f")
        self.assertEqual({"a.ts::a", "a.ts::b"}, {item["target_symbol"] for item in call["targets"]})
        self.assertTrue(all(item["certainty"] == "may" for item in call["targets"]))

    def test_missing_dependency_and_interface_are_explicit_fallbacks(self) -> None:
        """缺少实现或依赖不应被编译器签名掩盖为已确认调用。"""
        result = self.analyze({"a.ts": (
            "import missing from 'not-installed';\ninterface I { exec(v:string):void }\n"
            "function run(x:I, v:string){x.exec(v); missing(v);}\n"
        )})
        artifact = SemanticIndexArtifact.model_validate(result["semantic_index"])
        self.assertTrue(all(item.fallback_reason for item in artifact.callsites.values()))
        self.assertEqual("partial", artifact.backend_runs[0]["status"])
        self.assertTrue(any(item["reason"] in {"compiler_unknown_callable", "compiler_external_or_unmapped_declaration"}
                            for item in artifact.backend_runs[0]["diagnostics"]))

    def test_javascript_and_tsx_use_the_same_backend(self) -> None:
        """原生 JS 和 TSX 均使用类型检查器绑定，不强制项目先转换为 TS。"""
        for name, source in (
            ("a.js", "const helper=value=>value;\nfunction run(value){return helper(value);}\n"),
            ("a.tsx", "function helper(value:string){return value;}\nconst View=()=> <div>{helper(location.hash)}</div>;\n"),
        ):
            with self.subTest(name=name):
                result = self.analyze({name: source})
                call = next(item for item in result["semantic_index"]["callsites"].values() if item["name"] == "helper")
                self.assertEqual("typescript_declaration_binding", call["targets"][0]["resolution_method"])

    def test_real_flow_uses_correct_sensitive_parameter_and_delivers_original_edges(self) -> None:
        """重导出定位必须帮助实际值流，错误参数位置不能因此变为污点路径。"""
        for arguments, expected in (("'fixed', value", True), ("value, 'fixed'", False)):
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as directory:
                files = {
                    "impl.ts": "import {exec} from 'node:child_process';\nexport function execute(safe:string,command:string){ exec(command); }\n",
                    "barrel.ts": "export {execute as launch} from './impl';\n",
                    "app.ts": "import {launch as action} from './barrel';\nfunction run(){ const value=process.env.CMD; action(" + arguments + "); }\n",
                }
                for name, source in files.items():
                    Path(directory, name).write_text(source, encoding="utf8")
                analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
                pack, enriched = SecurityAnalysisService().analyze_with_semantic_index(
                    project_root=directory, dependency_graph=analysis["dependency_graph"],
                    semantic_index=analysis["semantic_index"], analysis_diagnostics=analysis["diagnostics"],
                )
                flows = [item for item in pack.candidates if item.rule_id == "JS-SINK-SHELL" and item.dataflow_verified]
                self.assertEqual(expected, bool(flows))
                self.assertEqual(analysis["semantic_index"]["backend_runs"], enriched["backend_runs"])
                if flows:
                    native_ids = {item["id"] for item in analysis["dependency_graph"]["links"]
                                  if item.get("resolution_method") == "typescript_declaration_binding"}
                    flow = pack.dataflows[flows[0].dataflow_id]
                    self.assertTrue(native_ids.intersection(flow.call_edge_ids))
                    page = SecurityEvidenceCatalog(pack.model_dump()).detail(flows[0].candidate_id, section="flow", limit=100)
                    self.assertEqual([step.model_dump() for step in flow.steps], page["items"])
                self.assertEqual(set(files), {item.name for item in Path(directory).iterdir()})

    def test_config_cannot_read_outside_project_or_load_plugins(self) -> None:
        """越界配置、仓库 JS 插件和 NODE_OPTIONS 均不能执行或扩大源码读取范围。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "project")
            root.mkdir()
            Path(directory, "outside.json").write_text('{"compilerOptions":{"strict":true}}', encoding="utf8")
            Path(root, "tsconfig.json").write_text(json.dumps({
                "extends": "../outside.json", "compilerOptions": {"plugins": [{"name": "./evil.js"}]},
            }), encoding="utf8")
            Path(root, "evil.js").write_text("require('fs').writeFileSync('executed','yes');", encoding="utf8")
            Path(root, "a.ts").write_text("function f(){}\nfunction run(){f();}\n", encoding="utf8")
            with patch.dict("os.environ", {"NODE_OPTIONS": "--require=./evil.js", "NODE_PATH": str(root)}):
                result = TypeScriptSemanticResolver().resolve(str(root), file_paths=["a.ts"], max_file_bytes=2000000)
            self.assertEqual("partial", result.status)
            self.assertTrue(any(item["reason"] == "compiler_config_error" for item in result.diagnostics))
            self.assertFalse(Path(root, "executed").exists())
            self.assertTrue(all(item.location.path == "a.ts" for item in result.calls))

    def test_unconfigured_scripts_do_not_bind_unrelated_globals(self) -> None:
        """无工程配置的独立脚本不得因全局同名声明被静默串联。"""
        result = self.analyze({
            "a.ts": "function helper(value:string){return value;}\n",
            "b.ts": "function helper(value:string){return '';}\n",
            "app.ts": "function run(value:string){return helper(value);}\n",
        })
        call = next(item for item in result["semantic_index"]["callsites"].values() if item["name"] == "helper")
        self.assertTrue(call["fallback_reason"])
        self.assertFalse(any(item["resolution_method"] == "typescript_declaration_binding" for item in call["targets"]))

    def test_inherited_constructor_keeps_allocated_class(self) -> None:
        """继承构造签名属于 Base，但 new Child 的分配目标不能变成 Base。"""
        result = self.analyze({"a.ts": (
            "class Base { constructor(value:string){} }\nclass Child extends Base {}\n"
            "function run(value:string){ const item=new Child(value); }\n"
        )})
        call = next(item for item in result["semantic_index"]["callsites"].values() if item["name"] == "Child")
        self.assertEqual(["a.ts::Child"], [item["target_symbol"] for item in call["targets"]])
        self.assertEqual("typescript_declaration_binding", call["targets"][0]["resolution_method"])


if __name__ == "__main__":
    unittest.main()

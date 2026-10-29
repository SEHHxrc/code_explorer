"""Go 官方类型/SSA 离线回归；测试项目不运行，不下载其依赖。"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import os

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.semantic_index import SemanticIndexArtifact, SemanticIndex, SemanticSSAView
from backend.app.services.language_backends.go import GoSemanticResolver, WORKER_PATH
from backend.app.services.language_backends.go_environment import GoAnalysisConfig
from backend.app.services.semantic_index.resolution import ProjectSemanticResult
from backend.app.services.language_backends.worker import run_worker, SemanticWorkerError, WorkerResponse
from backend.app.services.security_analysis.service import SecurityAnalysisService
from backend.app.services.program_graph import ProgramGraphService


AVAILABLE = WORKER_PATH.is_file() and shutil.which("go") is not None


def host_environment_response() -> WorkerResponse:
    """生成根目录外的假缓存元数据；协议单元测试无需启动 Go。"""
    root = Path(tempfile.gettempdir()) / "code-explorer-go-cache-fixture"
    return WorkerResponse({"GOPATH": str(root / "gopath"), "GOMODCACHE": str(root / "modules"),
                           "GOCACHE": str(root / "build"), "GOHOSTOS": "windows", "GOHOSTARCH": "amd64"}, 100, {})


@unittest.skipUnless(AVAILABLE, "需要构建 backend/tools/go_semantics 的可信工作进程")
class GoSemanticTests(unittest.TestCase):
    """核对声明映射、SSA 身份、φ、覆盖边界以及跨语言接入不互相污染。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "go.mod").write_text("module example.test/sample\n\ngo 1.23.0\n", encoding="utf-8")

    @classmethod
    def setUpClass(cls) -> None:
        """仅系统明确拒绝工具执行时跳过原生集成用例；协议错误仍应失败。"""
        result = GoSemanticResolver().resolve(".", file_paths=[], max_file_bytes=100)
        if any(item.get("reason") == "semantic_worker_execution_denied" for item in result.diagnostics):
            raise unittest.SkipTest("操作系统应用控制拒绝运行 Go 分析工具，不能认定原生集成通过")

    def write(self, name: str, source: str) -> None:
        """只在本用例的临时目录写入静态夹具。"""
        (self.root / name).write_text(source, encoding="utf-8")

    def test_native_shadowed_types_use_the_shared_declaration_contract(self) -> None:
        """真实 Go 检查器与 TS 使用同一声明级契约，同类型或不同类型均保留内层声明。"""
        self.write("main.go", 'package sample\nfunc run(value string) string {\n { value := 42; _ = value }\n return value\n}\n')
        result = UnifiedCodeAnalyzer(str(self.root), max_workers=1).run_full_analysis()
        index = SemanticIndex.load(result["semantic_index"])
        facts = index.variable_types_for("main.go::run", "value")
        self.assertEqual(2, len(facts))
        self.assertEqual({"string", "int"}, {item.type_literal for item in facts})
        self.assertEqual({"parameter", "variable"}, {item.role for item in facts})
        self.assertEqual(2, len({item.declaration_id for item in facts}))
        self.assertIsNone(index.variable_type("main.go::run", "value"))

    def test_variable_references_use_go_objects_across_files_and_shadowing(self) -> None:
        """读取和写入均绑定源码实体，后读取的跨文件全局声明不能变成未知或同名猜测。"""
        self.write("main.go", 'package sample\nfunc run(value string) string {\n current:=value\n { current:=7; _=current }\n current=shared\n return current\n}\n')
        self.write("state.go", 'package sample\nvar shared string\n')
        result = UnifiedCodeAnalyzer(str(self.root), max_workers=1).run_full_analysis()
        index = SemanticIndex.load(result["semantic_index"])
        refs = list(index.artifact.variable_references.values())
        current = [item for item in refs if item.name == "current"]
        self.assertEqual([4, 3, 3], [index.variable_declaration(item.declaration_ids[0]).location.line for item in current])
        shared = next(item for item in refs if item.name == "shared")
        self.assertEqual("state.go", index.variable_declaration(shared.declaration_ids[0]).location.path)
        self.assertIsNone(shared.unresolved_reason)
        self.assertEqual("string", index.declaration_types_for_reference(shared.reference_id)[0].type_literal)

    def resolve(self) -> ProjectSemanticResult:
        return GoSemanticResolver().resolve(str(self.root), file_paths=sorted(
            path.name for path in self.root.glob("*.go")), max_file_bytes=100_000)

    def test_multifile_direct_calls_share_production_index(self) -> None:
        self.write("main.go", "package sample\nfunc Entry(x string) string { return Helper(x) }\n")
        self.write("helper.go", "package sample\nfunc Helper(x string) string { return x }\n")
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        result = UnifiedCodeAnalyzer(str(self.root), max_workers=1).run_full_analysis()
        artifact = SemanticIndexArtifact.model_validate(result["semantic_index"])
        targets = [target for site in artifact.callsites.values() if site.name == "Helper" for target in site.targets]
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].resolution_method, "go_declaration_binding")
        self.assertEqual(targets[0].certainty, "may")
        self.assertEqual(len(artifact.native_ssa), 2)
        view = SemanticIndex(artifact)
        self.assertIsInstance(view, SemanticSSAView)
        self.assertIsNotNone(view.native_ssa_for(targets[0].target_symbol))
        self.assertEqual(artifact.backend_runs[0]["ssa_mapped_functions"], 2)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})

    def test_native_calls_use_common_return_contract_end_to_end(self) -> None:
        """原生编译器调用事实进入公共 CFG/DFG 后，返回分量仍隔离而非另用 Go 求解器。"""
        self.write("main.go", '''package sample
import ("os"; "os/exec")
func split(value string) (string, string) { return value, "fixed" }
func run() {
 value := os.Getenv("CMD")
 command, safe := split(value)
 exec.Command(command)
 exec.Command(safe)
}
''')
        result = UnifiedCodeAnalyzer(str(self.root), max_workers=1).run_full_analysis()
        index = SemanticIndex.load(result["semantic_index"])
        call = next(item for item in index.artifact.callsites.values() if item.name == "split")
        self.assertTrue(call.targets)
        self.assertEqual({"go_declaration_binding"}, {item.resolution_method for item in call.targets})
        pack = SecurityAnalysisService().analyze(project_root=self.root,
            dependency_graph=result["dependency_graph"], semantic_index=result["semantic_index"])
        self.assertFalse(any(item.get("reason") == "program_graph_dataflow_error" for item in pack.scan_failures))
        self.assertEqual({7}, {pack.facts[item.sink_fact_id].location.line
                              for item in pack.candidates if item.dataflow_verified})

    def test_native_variable_identity_survives_shadowing_and_argument_binding(self) -> None:
        """Go 声明/引用事实实际参与公共污点链，内层同名变量不能杀死或污染外层。"""
        self.write("main.go", '''package sample
import ("os"; "os/exec")
func execute(command string) { exec.Command(command) }
func run() {
 value := os.Getenv("CMD")
 { value := "fixed"; exec.Command(value) }
 execute(value)
 safe := "fixed"
 { safe := os.Getenv("CMD"); exec.Command(safe) }
 exec.Command(safe)
}
''')
        result = UnifiedCodeAnalyzer(str(self.root), max_workers=1).run_full_analysis()
        index = SemanticIndex.load(result["semantic_index"])
        self.assertTrue(index.artifact.variable_references)
        graph = ProgramGraphService().analyze(self.root, semantic_index=index)
        function = next(item for item in graph.functions.values() if item.name == "run")
        writes = [node.definition_declarations["value"] for node in function.nodes.values()
                  if "value" in node.definition_declarations]
        self.assertEqual(2, len(set(writes)))
        pack, enriched = SecurityAnalysisService().analyze_with_semantic_index(project_root=self.root,
            dependency_graph=result["dependency_graph"], semantic_index=result["semantic_index"])
        values = SemanticIndex.load(enriched)
        self.assertEqual("1.6", values.artifact.schema_version)
        parameter = next(item for item in values.artifact.parameters.values() if item.name == "command")
        self.assertIn(parameter.declaration_id, values.artifact.variable_declarations)
        self.assertEqual({"command": parameter.declaration_id}, values.artifact.value_slots[parameter.slot_id].value_ids)
        self.assertFalse(any(item.get("reason") == "program_graph_dataflow_error" for item in pack.scan_failures))
        self.assertEqual({3, 9}, {pack.facts[item.sink_fact_id].location.line
                                 for item in pack.candidates if item.dataflow_verified})

    def test_phi_and_parameter_identity(self) -> None:
        self.write("flow.go", "package sample\nfunc Flow(input string, flag bool) string {\n value := input\n if flag { value = \"safe\" }\n return value\n}\n")
        result = self.resolve()
        self.assertEqual(result.status, "completed", result.diagnostics)
        function = result.ssa_functions[0]
        self.assertEqual(len(function.parameters), 2)
        phis = [item for item in function.instructions.values() if item.opcode == "Phi"]
        self.assertEqual(len(phis), 1)
        self.assertIn(function.parameters[0], phis[0].operands)
        self.assertEqual(len(phis[0].operands), len(function.blocks[phis[0].block].predecessors))
        self.assertTrue(any(item.source_name == "value" for item in function.instructions.values()))

    def test_reassignment_does_not_keep_old_parameter_value(self) -> None:
        self.write("kill.go", 'package sample\nfunc Kill(input string) string { input = "safe"; return input }\n')
        function = self.resolve().ssa_functions[0]
        returned = next(item for item in function.instructions.values() if item.opcode == "Return")
        self.assertNotIn(function.parameters[0], returned.operands)
        self.assertTrue(all(function.values[key].kind == "constant" for key in returned.operands))

    def test_interface_dispatch_not_faked_as_unique_target(self) -> None:
        self.write("iface.go", "package sample\ntype Worker interface { Run(string) string }\nfunc Entry(w Worker, x string) string { return w.Run(x) }\n")
        result = self.resolve()
        self.assertEqual(result.calls[0].unresolved_reason, "go_interface_dispatch")
        self.assertEqual(result.calls[0].dispatch, "virtual")
        self.assertFalse(result.calls[0].declarations)
        invoke = next(item for item in result.ssa_functions[0].instructions.values() if item.invocation_kind == "invoke")
        self.assertEqual(len(invoke.call_arguments), 1)
        self.assertIsNotNone(invoke.receiver_value)
        self.assertEqual(invoke.call_location, result.calls[0].location)

    def test_missing_dependency_keeps_uncertainty_and_no_fake_ssa(self) -> None:
        self.write("broken.go", 'package sample\nimport "example.invalid/missing"\nfunc Entry() { missing.Run() }\n')
        result = self.resolve()
        self.assertEqual(result.status, "partial")
        self.assertFalse(result.ssa_functions)
        self.assertIn("go_package_type_errors", result.diagnostic_counts)
        self.assertIn("go_dependency_unavailable", result.diagnostic_counts)
        self.assertFalse((self.root / "go.sum").exists())

    def test_build_tag_exclusion_is_reported(self) -> None:
        self.write("main.go", "package sample\nfunc Entry() {}\n")
        self.write("excluded.go", "//go:build never_enabled_in_test\n\npackage sample\nfunc Hidden() {}\n")
        result = self.resolve()
        self.assertEqual(result.status, "partial")
        self.assertTrue(any(item.get("path") == "excluded.go" and item["reason"] == "go_file_not_loaded" for item in result.diagnostics))
        self.assertEqual([item.name for item in result.ssa_functions], ["Entry"])

    def test_generic_and_unicode_byte_locations(self) -> None:
        self.write("main.go", "package sample\nfunc Echo[T any](x T) T { return x }\nfunc Entry() string { 名称 := \"值\"; return Echo(名称) }\n")
        result = self.resolve()
        call = result.calls[0]
        self.assertEqual(call.declarations[0].name, "Echo")
        line = (self.root / "main.go").read_text(encoding="utf-8").splitlines()[2]
        self.assertEqual(call.location.column, len(line[:line.index("Echo(")].encode("utf-8")) + 1)

    def test_explicit_tags_load_tagged_source(self) -> None:
        self.write("main.go", "package sample\nfunc Entry() {}\n")
        self.write("tagged.go", "//go:build validation_tag\n\npackage sample\nfunc Tagged() {}\n")
        result = GoSemanticResolver(configuration=GoAnalysisConfig(build_tags=("validation_tag",))).resolve(
            str(self.root), file_paths=["main.go", "tagged.go"], max_file_bytes=100_000)
        self.assertEqual(result.status, "completed", result.diagnostics)
        self.assertEqual({item.name for item in result.ssa_functions}, {"Entry", "Tagged"})
        self.assertEqual(result.analysis_configuration["build_tags"], ["validation_tag"])

    def test_explicit_platform_loads_linux_source_without_running_target(self) -> None:
        self.write("main.go", "package sample\nfunc Entry() {}\n")
        self.write("target_linux.go", "package sample\nfunc Platform() {}\n")
        self.write("target_windows.go", "package sample\nfunc Platform() {}\n")
        result = GoSemanticResolver(configuration=GoAnalysisConfig(target_os="linux", target_arch="amd64")).resolve(
            str(self.root), file_paths=["main.go", "target_linux.go", "target_windows.go"], max_file_bytes=100_000)
        self.assertTrue(any(item.location.path == "target_linux.go" for item in result.ssa_functions))
        self.assertFalse(any(item.location.path == "target_windows.go" for item in result.ssa_functions))
        self.assertEqual(result.analysis_configuration["target_os"], "linux")

    def test_method_declaration_maps_to_existing_canonical_symbol(self) -> None:
        self.write("types.go", "package sample\ntype Worker struct {}\n")
        self.write("methods.go", "package sample\nfunc (w *Worker) Run(x string) string { return x }\nfunc Entry(w *Worker,x string) string { return w.Run(x) }\n")
        result = UnifiedCodeAnalyzer(str(self.root), max_workers=1).run_full_analysis()
        artifact = SemanticIndexArtifact.model_validate(result["semantic_index"])
        targets = [target for site in artifact.callsites.values() if site.name == "Run" for target in site.targets]
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].resolution_method, "go_declaration_binding")
        self.assertTrue(any(item.name == "Run" for item in artifact.native_ssa.values()))
        self.assertIn(targets[0].target_symbol, artifact.callables)
        method = next(item for item in artifact.native_ssa.values() if item.name == "Run")
        self.assertEqual(len(method.parameters), 1)
        self.assertIsNotNone(method.receiver_parameter)

    def test_mixed_typescript_go_backends_are_separate(self) -> None:
        self.write("main.go", "package sample\nfunc Entry() {}\n")
        self.write("main.ts", "function helper(x:string) { return x; } helper('value');\n")
        result = UnifiedCodeAnalyzer(str(self.root), max_workers=1).run_full_analysis()
        runs = result["semantic_index"]["backend_runs"]
        self.assertEqual({run["backend"] for run in runs}, {"go", "typescript"})
        self.assertEqual(len(result["semantic_index"]["native_ssa"]), 1)


class GoProtocolTests(unittest.TestCase):
    """无工具也可执行的失败及协议安全回归。"""

    def test_unavailable_is_explicit(self) -> None:
        with patch("backend.app.services.language_backends.go.WORKER_PATH", Path("does-not-exist")):
            result = GoSemanticResolver().resolve(".", file_paths=["main.go"], max_file_bytes=100)
        self.assertEqual(result.status, "unavailable")

    def test_corrupt_phi_is_rejected(self) -> None:
        result = ProjectSemanticResult.model_validate({"backend":"go", "status":"completed", "ssa_functions":[{
            "name":"f", "kind":"function", "location":{"path":"a.go"},
            "values":{"v0":{"kind":"parameter"}}, "parameters":["v0"],
            "blocks":[{"instructions":["i0"]}],
            "instructions":{"i0":{"opcode":"Phi", "block":0, "operands":["v0"], "result":"v0"}}
        }]})
        with self.assertRaises(ValueError):
            GoSemanticResolver._validate_ssa(result)

    def test_execution_denied_is_not_json_error(self) -> None:
        error = OSError("blocked")
        error.winerror = 4551
        with patch("backend.app.services.language_backends.worker.subprocess.run", side_effect=error):
            with self.assertRaisesRegex(SemanticWorkerError, "semantic_worker_execution_denied"):
                run_worker(["trusted-worker"], {}, directory=Path("."), environment={},
                           timeout_seconds=1, max_output_bytes=10)

    def test_timeout_is_not_json_error(self) -> None:
        with patch("backend.app.services.language_backends.worker.subprocess.run", side_effect=subprocess.TimeoutExpired("worker", 1)):
            with self.assertRaisesRegex(SemanticWorkerError, "semantic_worker_timeout"):
                run_worker(["trusted-worker"], {}, directory=Path("."), environment={},
                           timeout_seconds=1, max_output_bytes=10)

    def test_go_environment_disables_external_drivers_and_downloads(self) -> None:
        response = WorkerResponse({"backend":"go", "version":"x/tools-v0.52.0", "status":"completed",
                                   "wire_format":"tables-v1", "files":[], "declarations":[],
                                   "type_literals":[], "calls":[], "types":[]}, 10, {})
        with patch.dict(os.environ, {"GOPACKAGESDRIVER":"evil", "GOFLAGS":"-toolexec=evil", "GOOS":"alien"}), \
             patch("backend.app.services.language_backends.go.WORKER_PATH", Path(__file__)), \
             patch("backend.app.services.language_backends.go.shutil.which", return_value="go"), \
             patch("backend.app.services.language_backends.go_environment.run_worker", return_value=host_environment_response()), \
             patch("backend.app.services.language_backends.go.run_worker", return_value=response) as run:
            result = GoSemanticResolver(configuration=GoAnalysisConfig()).resolve(".", file_paths=["a.go"], max_file_bytes=100)
        self.assertEqual(result.status, "completed", result.diagnostics)
        environment = run.call_args.kwargs["environment"]
        self.assertEqual(environment["GOPACKAGESDRIVER"], "off")
        self.assertEqual(environment["GOPROXY"], "off")
        self.assertEqual(environment["GOFLAGS"], "-mod=readonly")
        self.assertEqual(environment["GOOS"], "windows")
        self.assertEqual(environment["GOENV"], "off")
        self.assertEqual(result.analysis_configuration["cache_sources"]["GOMODCACHE"], "host_go_env")

    def test_execution_denied_retains_selected_analysis_configuration(self) -> None:
        with patch("backend.app.services.language_backends.go.WORKER_PATH", Path(__file__)), \
             patch("backend.app.services.language_backends.go.shutil.which", return_value="go"), \
             patch("backend.app.services.language_backends.go_environment.run_worker", return_value=host_environment_response()), \
             patch("backend.app.services.language_backends.go.run_worker", side_effect=SemanticWorkerError("semantic_worker_execution_denied")):
            result = GoSemanticResolver(configuration=GoAnalysisConfig(target_os="linux")).resolve(".", file_paths=["a.go"], max_file_bytes=100)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.diagnostics[0]["reason"], "semantic_worker_execution_denied")
        self.assertEqual(result.summary()["analysis_configuration"]["target_os"], "linux")


class GoEnvironmentTests(unittest.TestCase):
    """验证缓存复用、显式构建条件和安全钩子隔离，不依赖原生 worker。"""

    def prepare(self, config: GoAnalysisConfig = GoAnalysisConfig()) -> tuple[dict[str, str], dict[str, object]]:
        """用固定宿主元数据验证配置，默认不启动任何进程。"""
        with patch("backend.app.services.language_backends.go_environment.run_worker", return_value=host_environment_response()):
            return config.prepare("go", project_root=Path(__file__).resolve().parents[1], tool_directory=Path("."))

    def test_reuses_effective_host_caches_without_exposing_paths(self) -> None:
        environment, summary = self.prepare()
        self.assertEqual(environment["GOMODCACHE"], host_environment_response().payload["GOMODCACHE"])
        self.assertEqual(len(summary["cache_paths_sha256"]), 64)
        self.assertNotIn(environment["GOMODCACHE"], str(summary))

    def test_target_and_tags_are_explicit_and_audited(self) -> None:
        environment, summary = self.prepare(GoAnalysisConfig(target_os="linux", target_arch="arm64", build_tags=("integration", "debug", "debug")))
        self.assertEqual((environment["GOOS"], environment["GOARCH"]), ("linux", "arm64"))
        self.assertEqual(summary["build_tags"], ["debug", "integration"])
        self.assertEqual(environment["CGO_ENABLED"], "0")

    def test_explicit_cache_overrides_host_cache(self) -> None:
        cache = str(Path(tempfile.gettempdir()) / "explicit-go-module-cache")
        environment, summary = self.prepare(GoAnalysisConfig(module_cache=cache))
        self.assertEqual(environment["GOMODCACHE"], cache)
        self.assertEqual(summary["cache_sources"]["GOMODCACHE"], "application_config")

    def test_rejects_cache_inside_project_and_relative_cache(self) -> None:
        for cache in ("relative-cache", str(Path(__file__).resolve().parents[1] / "cache")):
            with self.subTest(cache=cache), self.assertRaisesRegex(ValueError, "go_cache_must_be_outside_project"):
                self.prepare(GoAnalysisConfig(module_cache=cache))

    def test_rejects_flags_disguised_as_build_tags(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_go_build_tags"):
            self.prepare(GoAnalysisConfig(build_tags=("debug -toolexec=evil",)))
        with self.assertRaisesRegex(ValueError, "invalid_go_build_target"):
            self.prepare(GoAnalysisConfig(target_os="linux -x"))

    def test_application_environment_config_parsing(self) -> None:
        with patch.dict(os.environ, {"CODE_EXPLORER_GO_TARGET_OS":"linux", "CODE_EXPLORER_GO_TARGET_ARCH":"amd64",
                                     "CODE_EXPLORER_GO_BUILD_TAGS":"debug, integration", "CODE_EXPLORER_GO_MODULE_CACHE":""}):
            config = GoAnalysisConfig.from_environment()
        self.assertEqual(config.target_os, "linux")
        self.assertEqual(config.build_tags, ("debug", "integration"))

    @unittest.skipUnless(shutil.which("go"), "需要已安装的 Go 执行只读 go env")
    def test_real_go_env_preserves_user_config_cache_but_not_hooks(self) -> None:
        """读取临时用户配置，仅复用缓存；分析进程不继承其中的 GOFLAGS/外部钩子。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "module-cache"
            env_file = root / "go-env"
            env_file.write_text(f"GOMODCACHE={cache}\nGOFLAGS=-toolexec=must-not-run\nGOCACHEPROG=must-not-run\n", encoding="utf-8", newline="\n")
            with patch.dict(os.environ, {"GOENV":str(env_file), "GOMODCACHE":""}), \
                 patch("backend.app.services.language_backends.go_environment.LOCAL_ENVIRONMENT", root / "not-prepared"):
                environment, _ = GoAnalysisConfig().prepare(shutil.which("go"), project_root=root / "project", tool_directory=Path(__file__).resolve().parents[1])
            self.assertEqual(environment["GOMODCACHE"], str(cache))
            self.assertEqual(environment["GOENV"], "off")
            self.assertEqual(environment["GOFLAGS"], "-mod=readonly")
            self.assertEqual(environment["GOCACHEPROG"], "")

    def test_prepared_local_environment_is_used_without_changing_global_go(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            (local / "modules").mkdir()
            (local / "build").mkdir()
            with patch("backend.app.services.language_backends.go_environment.LOCAL_ENVIRONMENT", local):
                environment, summary = self.prepare()
                self.assertEqual(summary["cache_sources"]["GOMODCACHE"], "host_go_env")
                (local / "ready").touch()
                environment, summary = self.prepare()
                self.assertEqual(environment["GOMODCACHE"], str(local / "modules"))
                self.assertEqual(summary["cache_sources"]["GOMODCACHE"], "project_local_environment")

    def test_local_environment_inside_analyzed_root_is_not_used_for_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / ".goenv"
            (local / "modules").mkdir(parents=True)
            (local / "build").mkdir()
            (local / "ready").touch()
            with patch("backend.app.services.language_backends.go_environment.LOCAL_ENVIRONMENT", local), \
                 patch("backend.app.services.language_backends.go_environment.run_worker", return_value=host_environment_response()):
                environment, summary = GoAnalysisConfig().prepare("go", project_root=root, tool_directory=Path("."))
            self.assertEqual(summary["cache_sources"]["GOMODCACHE"], "host_go_env")


if __name__ == "__main__":
    unittest.main()

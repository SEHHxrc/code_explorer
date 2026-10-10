# -*- coding: utf-8 -*-
"""Static security structural evidence regression tests."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.app.agents.context_builder import ProjectContextBuilder
from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.security_analysis import SecurityAnalysisService
from backend.app.services.security_analysis import SecurityEvidencePromptBuilder
from backend.app.services.security_analysis.knowledge import (
    NullSecurityKnowledgeProvider,
    SecurityKnowledgeRequest,
)
from backend.app.services.security_analysis.frontends import (
    GoSecurityFrontend,
    JavaSecurityFrontend,
    PythonSecurityFrontend,
)
from backend.app.services.security_analysis.python_scanner import PythonSecurityScanner
from backend.app.services.security_analysis.registry import FrontendRegistry, RulePackRegistry
from backend.app.services.security_analysis.rules import CallRule, RulePack
from backend.app.services.security_analysis.semantics import (
    GoLanguageSemantics,
    JavaLanguageSemantics,
    PythonLanguageSemantics,
)


class SecurityAnalysisTests(unittest.TestCase):
    """验证 Python 与 Java 的确定性静态安全证据。"""

    def test_fastapi_to_subprocess_candidate_has_path_snippets_and_uncertainty(self):
        """A route reaching a process sink should produce traceable structural evidence."""
        source = (
            "from fastapi import FastAPI\n"
            "import subprocess\n"
            "app = FastAPI()\n\n"
            "@app.post('/run')\n"
            "def run(command: str):\n"
            "    if not command:\n"
            "        raise ValueError('missing')\n"
            "    return helper(command)\n\n"
            "def helper(command: str):\n"
            "    api_key = 'top-secret-value'\n"
            "    return subprocess.run(command, shell=True)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            program = PythonSecurityFrontend().build(directory, file_paths=["app.py"])
            helper_callsite_id = next(
                call.callsite_id
                for call in program.calls
                if call.qualified_name == "helper"
            )
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
                semantic_index=analysis["semantic_index"],
            )

        self.assertTrue(pack.dataflow_verified)
        self.assertEqual("2.3", pack.schema_version)
        self.assertEqual("1.3", pack.ir_version)
        self.assertEqual(["python-core/1.0", "python-fastapi/1.0"], pack.rule_packs)
        self.assertEqual("static_security_evidence", pack.analysis_kind)
        self.assertEqual(1, len(pack.entrypoints))
        self.assertEqual("http_route", next(iter(pack.entrypoints.values())).kind)
        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL")
        source_fact = pack.facts[candidate.source_fact_id]
        sink_fact = pack.facts[candidate.sink_fact_id]
        edge = pack.call_edges[candidate.call_edge_ids[0]]
        snippets = [pack.snippets[item] for item in candidate.snippet_ids]
        self.assertEqual("candidate", candidate.status)
        self.assertEqual("interprocedural_dataflow", candidate.path_kind)
        self.assertTrue(candidate.dataflow_verified)
        self.assertEqual("may_reach_sink", candidate.taint_status)
        self.assertEqual("command", source_fact.name)
        self.assertEqual(6, source_fact.location.line)
        self.assertEqual(13, sink_fact.location.line)
        self.assertEqual(1, len(candidate.call_edge_ids))
        self.assertEqual(9, edge.callsite.line)
        self.assertEqual(
            edge.callsite_id,
            analysis["dependency_graph"]["links"][
                next(
                    index for index, edge in enumerate(analysis["dependency_graph"]["links"])
                    if edge.get("relation") == "calls"
                    and str(edge.get("source", "")).endswith("::run")
                    and str(edge.get("target", "")).endswith("::helper")
                )
            ]["callsite_id"],
        )
        self.assertEqual(helper_callsite_id, edge.callsite_id)
        self.assertEqual("local_scope", edge.resolution_method)
        self.assertEqual("inferred", edge.provenance)
        self.assertIsNone(edge.unresolved_reason)
        self.assertTrue(sink_fact.metadata["shell"])
        self.assertEqual([0], sink_fact.metadata["value_flow"]["arguments"])
        self.assertEqual("sink", sink_fact.metadata["value_flow"]["argument_role"])
        self.assertTrue(candidate.guard_fact_ids)
        self.assertTrue(any(snippet.role == "source" for snippet in snippets))
        self.assertTrue(any(snippet.role == "sink" for snippet in snippets))
        self.assertNotIn("top-secret-value", "\n".join(item.text for item in snippets))
        self.assertTrue(any("跨过程" in item for item in candidate.limitations))
        candidate_payload = candidate.model_dump()
        self.assertNotIn("source", candidate_payload)
        self.assertNotIn("sink", candidate_payload)
        self.assertNotIn("call_path", candidate_payload)
        self.assertNotIn("snippets", candidate_payload)
        self.assertIn(candidate.source_fact_id, pack.facts)
        self.assertIn(candidate.sink_fact_id, pack.facts)
        self.assertTrue(all(item in pack.call_edges for item in candidate.call_edge_ids))
        self.assertTrue(all(item in pack.snippets for item in candidate.snippet_ids))
        persisted = pack.model_dump()
        self.assertNotIn("dependency_graph", persisted)
        self.assertNotIn("repo_map", persisted)
        self.assertNotIn("manifest", persisted)

    def test_environment_to_shell_candidate_keeps_trust_precondition(self):
        """External configuration must remain conditional on attacker influence."""
        source = (
            "import os\n\n"
            "def launch():\n"
            "    command = os.getenv('APP_COMMAND')\n"
            "    return os.system(command)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "worker.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL")
        self.assertEqual("external_configuration", pack.facts[candidate.source_fact_id].trust_class)
        self.assertEqual([], candidate.call_edge_ids)
        self.assertTrue(any("环境变量" in item for item in candidate.preconditions))
        self.assertTrue(candidate.dataflow_verified)
        self.assertEqual("may_reach_sink", candidate.taint_status)
        self.assertEqual("intra_procedural_dataflow", candidate.path_kind)
        self.assertIn(candidate.dataflow_id, pack.dataflows)

    def test_python_assignment_chain_produces_ordered_def_use_flow(self):
        """函数参数经普通赋值和 f-string 到 Sink 时应形成真实函数内路径。"""
        source = (
            "from fastapi import FastAPI\n"
            "import subprocess\n"
            "app = FastAPI()\n\n"
            "@app.post('/run')\n"
            "def run(user: str):\n"
            "    value = user.strip()\n"
            "    command = f'echo {value}'\n"
            "    return subprocess.run(command, shell=True)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL")
        flow = pack.dataflows[candidate.dataflow_id]
        assignments = [item for item in flow.steps if item.kind == "assignment"]
        self.assertEqual("may_reach_sink", flow.status)
        self.assertEqual(["value", "command"], [item.output_names[0] for item in assignments])
        self.assertEqual(
            [(["user"], ["value"]), (["value"], ["command"])],
            [(item.input_names, item.output_names) for item in assignments],
        )
        self.assertEqual(list(range(len(flow.steps))), [item.order for item in flow.steps])

    def test_known_sanitizer_breaks_python_def_use_flow(self):
        """规则声明的净化返回值不得继续产生已验证数据流。"""
        source = (
            "from fastapi import FastAPI\n"
            "from markupsafe import escape\n"
            "import os\n"
            "app = FastAPI()\n\n"
            "@app.get('/run')\n"
            "def run(command: str):\n"
            "    safe = escape(command)\n"
            "    return os.system(safe)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL")
        self.assertFalse(candidate.dataflow_verified)
        self.assertEqual("not_analyzed", candidate.taint_status)

    def test_python_unpacking_does_not_cross_contaminate_safe_output(self):
        """位置敏感解包不得把不可信输入传播到对应常量的安全输出。"""
        source = (
            "from fastapi import FastAPI\n"
            "import os\n"
            "app = FastAPI()\n\n"
            "@app.get('/run')\n"
            "def run(user: str):\n"
            "    tainted, safe = user, 'fixed'\n"
            "    return os.system(safe)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL")
        self.assertFalse(candidate.dataflow_verified)
        self.assertEqual("not_analyzed", candidate.taint_status)

    def test_python_source_call_unpacking_keeps_assigned_target_precise(self):
        """调用返回 Source 位于解构首项时不得污染对应常量的兄弟目标。"""
        source = (
            "import os\n\n"
            "def run():\n"
            "    tainted, safe = os.getenv('COMMAND'), 'fixed'\n"
            "    return os.system(safe)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL")
        self.assertFalse(candidate.dataflow_verified)
        self.assertEqual("structural_call_path", candidate.path_kind)

    def test_python_attribute_write_reaches_matching_sink_field(self):
        """写入属性的不可信值应能沿相同访问路径到达危险参数。"""
        source = (
            "from fastapi import FastAPI\n"
            "import os\n"
            "app = FastAPI()\n\n"
            "@app.get('/run')\n"
            "def run(user: str):\n"
            "    holder.command = user\n"
            "    return os.system(holder.command)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL")
        self.assertTrue(candidate.dataflow_verified)
        flow = pack.dataflows[candidate.dataflow_id]
        self.assertTrue(any(
            item.output_names == ["holder.command"]
            for item in flow.steps
            if item.kind == "assignment"
        ))

    def test_python_attribute_paths_do_not_cross_contaminate_sibling_field(self):
        """写入一个属性的不可信值不得传播到同对象的另一个常量属性。"""
        source = (
            "from fastapi import FastAPI\n"
            "import os\n"
            "app = FastAPI()\n\n"
            "@app.get('/run')\n"
            "def run(user: str):\n"
            "    holder.tainted = user\n"
            "    holder.safe = 'fixed'\n"
            "    return os.system(holder.safe)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL")
        self.assertFalse(candidate.dataflow_verified)
        self.assertEqual("not_analyzed", candidate.taint_status)

    def test_python_static_subscript_reaches_matching_sink_element(self):
        """写入静态下标的不可信值应沿相同规范化元素路径到达 Sink。"""
        source = (
            "from fastapi import FastAPI\n"
            "import os\n"
            "app = FastAPI()\n\n"
            "@app.get('/run')\n"
            "def run(user: str):\n"
            "    payload['command'] = user\n"
            "    return os.system(payload[\"command\"])\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL")
        self.assertTrue(candidate.dataflow_verified)
        flow = pack.dataflows[candidate.dataflow_id]
        self.assertTrue(any(
            item.output_names == ['payload["command"]']
            for item in flow.steps
            if item.kind == "assignment"
        ))

    def test_python_static_subscripts_do_not_cross_contaminate_sibling_element(self):
        """污染静态下标不得传播到同一容器的另一个常量下标。"""
        source = (
            "from fastapi import FastAPI\n"
            "import os\n"
            "app = FastAPI()\n\n"
            "@app.get('/run')\n"
            "def run(user: str):\n"
            "    payload['tainted'] = user\n"
            "    payload['safe'] = 'fixed'\n"
            "    return os.system(payload['safe'])\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL")
        self.assertFalse(candidate.dataflow_verified)
        self.assertEqual("not_analyzed", candidate.taint_status)

    def test_known_sanitizer_blocks_interprocedural_argument_flow(self):
        """调用前净化的返回值不得通过实参到形参边重新变成污点。"""
        source = (
            "from fastapi import FastAPI\n"
            "from markupsafe import escape\n"
            "import os\n"
            "app = FastAPI()\n\n"
            "@app.get('/run')\n"
            "def run(command: str):\n"
            "    safe = escape(command)\n"
            "    return launch(safe)\n\n"
            "def launch(value: str):\n"
            "    return os.system(value)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL")
        self.assertFalse(candidate.dataflow_verified)
        self.assertEqual("structural_call_path", candidate.path_kind)
        self.assertTrue(candidate.sanitizer_fact_ids)

    def test_llm_projection_is_self_contained_compact_and_truthful(self):
        """LLM 格式应展开必要证据且永远不携带完整图或 Repo Map。"""
        source = (
            "from fastapi import FastAPI\n"
            "import os\n"
            "app = FastAPI()\n\n"
            "@app.get('/run')\n"
            "def run(command: str):\n"
            "    return os.system(command)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        rendered = SecurityEvidencePromptBuilder().render(
            pack,
            question="命令注入 CWE-78",
            max_chars=12_000,
        )
        payload = __import__("json").loads(rendered)
        finding = payload["findings"][0]
        self.assertLessEqual(len(rendered), 12_000)
        self.assertEqual("static_security_evidence", payload["kind"])
        self.assertEqual("intra_procedural_dataflow", finding["claim"])
        self.assertEqual("command", finding["source"]["name"])
        self.assertEqual("FASTAPI-ROUTE-PARAM", finding["source"]["rule_id"])
        self.assertEqual("os.system", finding["sink"]["name"])
        self.assertEqual("PY-SINK-SHELL", finding["sink"]["rule_id"])
        self.assertEqual(
            "cfg_value_flow_with_bounded_calls",
            payload["analysis"]["dataflow_scope"]["kind"],
        )
        self.assertEqual(
            "cfg_used_without_path_feasibility_proof",
            payload["analysis"]["dataflow_scope"]["control_flow"],
        )
        self.assertEqual(
            "cfg_used_without_path_feasibility_proof",
            finding["confidence_dimensions"]["control_flow"],
        )
        self.assertEqual([], payload["analysis"]["coverage_gaps"])
        self.assertEqual(0, payload["analysis"]["omitted_coverage_gaps"])
        self.assertNotIn("dependency_graph", rendered)
        self.assertNotIn("repo_map", rendered)
        packet = ProjectContextBuilder().build(
            project_id="p1",
            question="检查命令注入",
            artifact={
                "manifest": {
                    "project_name": "demo",
                    "languages": ["python"],
                    "frameworks": ["FastAPI"],
                    "entrypoints": [],
                },
                "repo_map": "PROJECT demo",
                "security_evidence": pack.model_dump(),
            },
        )
        self.assertIn("STATIC_SECURITY_EVIDENCE", packet.prompt_context)
        self.assertIn("intra_procedural_dataflow", packet.prompt_context)

    def test_candidate_limit_marks_only_actual_truncation(self):
        """Exactly filling the budget is complete; an additional candidate is truncation."""
        one_source = (
            "from fastapi import FastAPI\n"
            "import os\n"
            "app = FastAPI()\n\n"
            "@app.get('/run')\n"
            "def run(command: str):\n"
            "    return os.system(command)\n"
        )
        two_sources = one_source.replace("command: str", "command: str, option: str")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "app.py")
            target.write_text(one_source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            complete = SecurityAnalysisService(max_candidates=1).analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )
            target.write_text(two_sources, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            truncated = SecurityAnalysisService(max_candidates=1).analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        self.assertEqual(1, len(complete.candidates))
        self.assertFalse(complete.coverage.candidate_limit_reached)
        self.assertEqual(1, len(truncated.candidates))
        self.assertTrue(truncated.coverage.candidate_limit_reached)

    def test_scanner_reuses_dependency_graph_file_scope(self):
        """Security parsing should follow dependency-analysis files instead of rediscovery."""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "included.py").write_text(
                "import os\ndef run(command):\n    return os.system(command)\n",
                encoding="utf-8",
            )
            Path(directory, "excluded.py").write_text(
                "def unsafe(value):\n    return eval(value)\n",
                encoding="utf-8",
            )
            graph = {
                "nodes": [{
                    "id": "included.py",
                    "file": "included.py",
                    "lang": "python",
                    "kind": "module",
                }],
                "links": [],
            }
            scan = PythonSecurityScanner().scan(directory, dependency_graph=graph)

        self.assertEqual(1, scan.files_considered)
        self.assertEqual(1, scan.files_scanned)
        self.assertTrue(any(item.rule_id == "PY-SINK-SHELL" for item in scan.sinks))
        self.assertFalse(any(item.rule_id == "PY-SINK-CODE-EVAL" for item in scan.sinks))

    def test_dynamic_rule_condition_is_reported_as_unresolved(self):
        """动态文件模式不能被静默当成规则不匹配。"""
        source = (
            "def load(path, mode):\n"
            "    return open(path, mode)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.py").write_text(source, encoding="utf-8")
            graph = {
                "nodes": [{
                    "id": "main.py",
                    "file": "main.py",
                    "lang": "python",
                    "kind": "module",
                }],
                "links": [],
            }
            scan = PythonSecurityScanner().scan(directory, dependency_graph=graph)

        diagnostic = next(
            item for item in scan.failures
            if item.get("reason") == "rule_condition_unresolved"
        )
        self.assertEqual("main.py", diagnostic["path"])
        self.assertTrue(diagnostic["callsite_id"].startswith("callsite:"))
        self.assertFalse(any(item.name == "open" for item in scan.sources + scan.sinks))

    def test_open_file_handle_is_not_claimed_as_external_file_content(self):
        """``open()`` returns a handle, so it must not seed content taint by itself."""
        source = "def load():\n    return open('input.txt', 'r')\n"
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.py").write_text(source, encoding="utf-8")
            scan = PythonSecurityScanner().scan(directory)

        self.assertFalse(any(item.name == "open" for item in scan.sources))

    def test_knowledge_interface_is_explicitly_disabled_by_default(self):
        """The reserved RAG interface must not inject external knowledge yet."""
        request = SecurityKnowledgeRequest(cwe_ids=("CWE-78",), rule_ids=("PY-SINK-SHELL",))
        self.assertEqual(
            [],
            asyncio.run(NullSecurityKnowledgeProvider().retrieve(request)),
        )

    def test_route_without_input_is_entrypoint_not_source(self):
        """A route itself is control reachability, not untrusted data."""
        source = (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n\n"
            "@app.get('/health')\n"
            "def health():\n"
            "    return {'ok': True}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        self.assertEqual(1, len(pack.entrypoints))
        self.assertFalse(any(fact.fact_kind == "source" for fact in pack.facts.values()))

    def test_unsupported_language_is_visible_in_coverage(self):
        """Unregistered languages must be reported instead of silently treated as scanned."""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.rs").write_text("fn main() {}\n", encoding="utf-8")
            graph = {
                "nodes": [{
                    "id": "main.rs",
                    "file": "main.rs",
                    "lang": "rust",
                    "kind": "module",
                }],
                "links": [],
            }
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=graph,
                analysis_diagnostics={},
            )

        self.assertEqual(["rust"], pack.unsupported_languages)
        self.assertEqual(1, pack.coverage.files_considered)
        self.assertEqual(0, pack.coverage.files_scanned)
        self.assertEqual(1, pack.coverage.unsupported_language_count)
        envelope = SecurityEvidencePromptBuilder().build(pack)
        self.assertEqual("unsupported_language", envelope.analysis["coverage_gaps"][0]["reason"])
        self.assertEqual(0, envelope.analysis["omitted_coverage_gaps"])

    def test_all_dependency_languages_degrade_explicitly_in_security_analysis(self):
        """依赖图支持的语言必须被扫描或明确列为未支持，不能崩溃或静默遗漏。"""
        sources = {
            "main.py": "def run(value):\n    return value\n",
            "Main.java": "class Main { int run(int value) { return value; } }\n",
            "main.js": "function run(value) { return value; }\n",
            "main.ts": "function typed(value: number): number { return value; }\n",
            "main.go": "package main\nfunc run(value int) int { return value }\n",
            "main.c": "int run(int value) { return value; }\n",
            "main.cpp": "int cpp_run(int value) { return value; }\n",
            "main.rs": "fn run(value: i32) -> i32 { value }\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, source in sources.items():
                Path(directory, name).write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        self.assertTrue(pack.completed)
        self.assertEqual(["c", "cpp", "go", "java", "javascript", "python", "typescript"], pack.languages_analyzed)
        self.assertEqual(
            ["rust"],
            pack.unsupported_languages,
        )
        self.assertEqual(len(sources), pack.coverage.files_considered)
        self.assertEqual(7, pack.coverage.files_scanned)
        self.assertEqual(1, pack.coverage.unsupported_language_count)
        self.assertFalse(pack.dataflow_verified)
        unsupported = {
            str(item.get("language") or "")
            for item in pack.scan_failures
            if item.get("reason") == "unsupported_language"
        }
        self.assertEqual(set(pack.unsupported_languages), unsupported)

    def test_frontend_registry_rejects_duplicate_language(self):
        """Only one active frontend may own a language."""
        with self.assertRaises(ValueError):
            FrontendRegistry([PythonSecurityFrontend(), PythonSecurityFrontend()])

    def test_rule_pack_registry_rejects_rule_id_collision_per_language(self):
        """Layered packs must not silently redefine a rule for the same language."""
        first = RulePack(
            name="first",
            version="1.0",
            languages=("python",),
            call_rules=(CallRule("RULE-1", "sink", "test", ("a",)),),
        )
        second = RulePack(
            name="second",
            version="1.0",
            languages=("python",),
            call_rules=(CallRule("RULE-1", "sink", "test", ("b",)),),
        )
        with self.assertRaises(ValueError):
            RulePackRegistry((first, second))

    def test_python_semantics_maps_positional_and_keyword_arguments(self):
        """Language semantics should map call arguments without claiming taint."""
        source = (
            "def target(first, second):\n"
            "    return first\n\n"
            "def caller():\n"
            "    return target('x', second='y')\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.py").write_text(source, encoding="utf-8")
            program = PythonSecurityFrontend().build(directory, file_paths=["main.py"])

        call = next(item for item in program.calls if item.qualified_name == "target")
        target = next(item for item in program.functions if item.name == "target")
        bindings = PythonLanguageSemantics().bind_arguments(call, target)
        self.assertEqual(["first", "second"], [item.parameter_name for item in bindings])
        self.assertTrue(all(item.callsite_id == call.callsite_id for item in bindings))

    def test_java_spring_parameter_reaches_runtime_exec(self):
        """Spring 参数经 Java 局部赋值到 Runtime.exec 时应产生已验证数据流。"""
        source = (
            "import org.springframework.web.bind.annotation.*;\n"
            "@RestController\n"
            "class CommandController {\n"
            "  @GetMapping(\"/run\")\n"
            "  String run(@RequestParam String command) throws Exception {\n"
            "    String value = command.trim();\n"
            "    Runtime.getRuntime().exec(value);\n"
            "    return value;\n"
            "  }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "CommandController.java").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            program = JavaSecurityFrontend().build(
                directory,
                file_paths=["CommandController.java"],
            )
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        function = next(item for item in program.functions if item.name == "run")
        sink_call = next(item for item in program.calls if item.qualified_name.endswith(".exec"))
        self.assertEqual("CommandController.java::CommandController::run", function.symbol)
        self.assertEqual("/run", next(iter(pack.entrypoints.values())).metadata["route_path"])
        self.assertEqual(["java"], pack.languages_analyzed)
        self.assertIn("java-core/1.0", pack.rule_packs)
        self.assertIn("java-spring-web/1.0", pack.rule_packs)
        candidate = next(item for item in pack.candidates if item.rule_id == "JAVA-SINK-PROCESS")
        sink = pack.facts[candidate.sink_fact_id]
        self.assertEqual(sink_call.callsite_id, sink.metadata["callsite_id"])
        self.assertTrue(candidate.dataflow_verified)
        self.assertEqual("intra_procedural_dataflow", candidate.path_kind)

    def test_go_net_http_form_value_reaches_exec_command(self):
        """net/http 请求值进入 exec.Command 时应产生可追溯 Go 数据流证据。"""
        source = (
            "package main\n\n"
            "import (\n"
            '  "net/http"\n'
            '  "os/exec"\n'
            ")\n\n"
            "func handler(w http.ResponseWriter, r *http.Request) {\n"
            '  command := r.FormValue("command")\n'
            "  exec.Command(command).Run()\n"
            "}\n\n"
            "func main() { http.HandleFunc(\"/run\", handler) }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.go").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            program = GoSecurityFrontend().build(directory, file_paths=["main.go"])
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        handler = next(item for item in program.functions if item.name == "handler")
        source_call = next(
            item for item in program.calls
            if item.qualified_name == "http.Request.FormValue"
        )
        sink_call = next(
            item for item in program.calls
            if item.qualified_name == "exec.Command"
        )
        self.assertEqual("main.go::handler", handler.symbol)
        self.assertEqual(("command",), source_call.assigned_targets)
        self.assertEqual(["go"], pack.languages_analyzed)
        self.assertIn("go-core/1.1", pack.rule_packs)
        self.assertIn("go-net-http/1.0", pack.rule_packs)
        entrypoint = next(iter(pack.entrypoints.values()))
        self.assertEqual("net/http", entrypoint.framework)
        self.assertEqual("/run", entrypoint.metadata["route_path"])
        candidate = next(item for item in pack.candidates if item.rule_id == "GO-SINK-PROCESS")
        sink = pack.facts[candidate.sink_fact_id]
        self.assertEqual(sink_call.callsite_id, sink.metadata["callsite_id"])
        self.assertTrue(candidate.dataflow_verified)
        self.assertEqual("intra_procedural_dataflow", candidate.path_kind)

    def test_go_request_value_crosses_resolved_function_boundary(self):
        """Go 请求值应沿已解析调用的实参到形参进入下游进程 Sink。"""
        source = (
            "package main\n\n"
            "import (\n"
            '  "net/http"\n'
            '  "os/exec"\n'
            ")\n\n"
            "func handler(w http.ResponseWriter, r *http.Request) {\n"
            '  command := r.FormValue("command")\n'
            "  launch(command)\n"
            "}\n\n"
            "func launch(value string) { exec.Command(value).Run() }\n\n"
            "func main() { http.HandleFunc(\"/run\", handler) }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.go").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
                semantic_index=analysis["semantic_index"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "GO-SINK-PROCESS")
        self.assertTrue(candidate.dataflow_verified)
        self.assertEqual("interprocedural_dataflow", candidate.path_kind)
        self.assertEqual(1, len(candidate.call_edge_ids))

    def test_go_safe_sibling_value_does_not_verify_process_flow(self):
        """Go 请求 Source 不得穿过无关常量变量污染 exec.Command。"""
        source = (
            "package main\n\n"
            "import (\n"
            '  "net/http"\n'
            '  "os/exec"\n'
            ")\n\n"
            "func handler(w http.ResponseWriter, r *http.Request) {\n"
            '  tainted, safe := r.FormValue("command"), "fixed"\n'
            "  _ = tainted\n"
            "  exec.Command(safe).Run()\n"
            "}\n\n"
            "func main() { http.HandleFunc(\"/run\", handler) }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.go").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "GO-SINK-PROCESS")
        self.assertFalse(candidate.dataflow_verified)
        self.assertEqual("structural_call_path", candidate.path_kind)

    def test_java_spring_parameter_crosses_resolved_method_boundary(self):
        """Spring 参数应沿已解析调用的实参到形参进入下游 Runtime.exec。"""
        source = (
            "import org.springframework.web.bind.annotation.*;\n"
            "@RestController\n"
            "class CommandController {\n"
            "  @PostMapping(\"/run\")\n"
            "  String run(@RequestBody String command) throws Exception {\n"
            "    launch(command);\n"
            "    return \"ok\";\n"
            "  }\n"
            "  void launch(String value) throws Exception {\n"
            "    String prepared = value.trim();\n"
            "    Runtime.getRuntime().exec(prepared);\n"
            "  }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "CommandController.java").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
                semantic_index=analysis["semantic_index"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "JAVA-SINK-PROCESS")
        flow = pack.dataflows[candidate.dataflow_id]
        self.assertTrue(candidate.dataflow_verified)
        self.assertEqual("interprocedural_dataflow", candidate.path_kind)
        self.assertEqual("interprocedural", flow.scope)
        self.assertEqual(1, len(candidate.call_edge_ids))
        self.assertTrue(any(item.kind == "call" for item in flow.steps))
        rendered = SecurityEvidencePromptBuilder().build(pack)
        finding = next(item for item in rendered.findings if item.rule_id == "JAVA-SINK-PROCESS")
        self.assertEqual("interprocedural_dataflow", finding.claim)
        self.assertTrue(any(item.kind == "call" for item in finding.flow))

    def test_java_source_returns_to_caller_sink(self):
        """Java 被调用函数产生的 Source 应经返回槽位流入调用者 Sink。"""
        source = (
            "import org.springframework.web.bind.annotation.*;\n"
            "@RestController\n"
            "class CommandController {\n"
            "  @GetMapping(\"/run\")\n"
            "  String run() throws Exception {\n"
            "    String command = readCommand();\n"
            "    Runtime.getRuntime().exec(command);\n"
            "    return command;\n"
            "  }\n"
            "  String readCommand() {\n"
            "    String value = System.getenv(\"COMMAND\");\n"
            "    return value;\n"
            "  }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "CommandController.java").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
                semantic_index=analysis["semantic_index"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "JAVA-SINK-PROCESS")
        flow = pack.dataflows[candidate.dataflow_id]
        self.assertTrue(candidate.dataflow_verified)
        self.assertEqual("interprocedural_dataflow", candidate.path_kind)
        self.assertTrue(any(item.kind == "return" for item in flow.steps))
        self.assertEqual(1, len(flow.call_edge_ids))
        self.assertTrue(all(item in pack.call_edges for item in flow.call_edge_ids))
        rendered = SecurityEvidencePromptBuilder().build(pack)
        finding = next(item for item in rendered.findings if item.rule_id == "JAVA-SINK-PROCESS")
        self.assertTrue(any(item.kind == "return" for item in finding.flow))

    def test_python_source_returns_to_caller_sink(self):
        """Python helper 中的 Source 应经返回槽位流入 FastAPI 调用者 Sink。"""
        source = (
            "import os\n"
            "import subprocess\n"
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n\n"
            "def read_command():\n"
            "    value = os.getenv('COMMAND')\n"
            "    return value\n\n"
            "@app.get('/run')\n"
            "def run():\n"
            "    command = read_command()\n"
            "    return subprocess.run(command, shell=True)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
                semantic_index=analysis["semantic_index"],
            )

        candidate = next(item for item in pack.candidates if item.rule_id == "PY-SINK-SHELL")
        flow = pack.dataflows[candidate.dataflow_id]
        self.assertTrue(candidate.dataflow_verified)
        self.assertTrue(any(item.kind == "return" for item in flow.steps))

    def test_java_servlet_inheritance_marks_http_entrypoint(self):
        """仅在直接观察到 HttpServlet 继承时识别 doGet/doPost 入口。"""
        source = (
            "import jakarta.servlet.http.*;\n"
            "class DemoServlet extends HttpServlet {\n"
            "  protected void doGet(HttpServletRequest request, HttpServletResponse response) {\n"
            "    String name = request.getParameter(\"name\");\n"
            "  }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "DemoServlet.java").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        entrypoint = next(iter(pack.entrypoints.values()))
        self.assertEqual("servlet", entrypoint.framework)
        self.assertEqual("get", entrypoint.metadata["route_method"])
        self.assertTrue(any(item.rule_id == "JAVA-SOURCE-HTTP-REQUEST" for item in pack.facts.values()))

    def test_java_declared_receiver_type_connects_environment_to_sql(self):
        """Java 声明类型应把变量接收者限定为 JDBC Sink，并连接调用返回值。"""
        source = (
            "import java.sql.Statement;\n"
            "class QueryService {\n"
            "  void run(Statement statement) throws Exception {\n"
            "    String query = System.getenv(\"QUERY\");\n"
            "    statement.executeQuery(query);\n"
            "  }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "QueryService.java").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            program = JavaSecurityFrontend().build(
                directory,
                file_paths=["QueryService.java"],
            )
            pack = SecurityAnalysisService().analyze(
                project_root=directory,
                dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"],
            )

        sql_call = next(item for item in program.calls if item.qualified_name.endswith("executeQuery"))
        self.assertEqual("Statement.executeQuery", sql_call.qualified_name)
        candidate = next(item for item in pack.candidates if item.rule_id == "JAVA-SINK-SQL")
        self.assertEqual(
            "external_configuration",
            pack.facts[candidate.source_fact_id].trust_class,
        )
        self.assertTrue(candidate.dataflow_verified)

    def test_java_semantics_maps_positional_arguments(self):
        """Java 语义按声明顺序映射位置实参。"""
        source = (
            "class Demo {\n"
            "  String target(String first, int second) { return first; }\n"
            "  String caller() { return target(\"x\", 2); }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Demo.java").write_text(source, encoding="utf-8")
            program = JavaSecurityFrontend().build(directory, file_paths=["Demo.java"])

        call = next(item for item in program.calls if item.qualified_name == "target")
        target = next(item for item in program.functions if item.name == "target")
        bindings = JavaLanguageSemantics().bind_arguments(call, target)
        self.assertEqual(["first", "second"], [item.parameter_name for item in bindings])

    def test_go_semantics_maps_positional_arguments(self):
        """Go 语义按声明顺序映射普通位置实参。"""
        source = (
            "package main\n"
            "func target(first string, second int) string { return first }\n"
            "func caller() string { return target(\"x\", 2) }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.go").write_text(source, encoding="utf-8")
            program = GoSecurityFrontend().build(directory, file_paths=["main.go"])

        call = next(item for item in program.calls if item.qualified_name == "target")
        target = next(item for item in program.functions if item.name == "target")
        bindings = GoLanguageSemantics().bind_arguments(call, target)
        self.assertEqual(["first", "second"], [item.parameter_name for item in bindings])


if __name__ == "__main__":
    unittest.main()

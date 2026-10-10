"""模型真实用量、报告完整性、工具观察与零候选输入的回归契约。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.app.agents.observations import compact_observation, render_observation_window
from backend.app.agents.orchestrator import AgentRunManager
from backend.app.agents.contracts import AgentRunRequest
from backend.app.experiments.context import SecurityExperimentContextBuilder
from backend.app.experiments.report import REPORT_END, assess_answer
from backend.app.llm.base import ModelTurn, ToolCall
from backend.app.llm.response_metadata import ModelResponseMetadata, ModelUsage, response_metadata
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.providers.openai_provider import OpenAIResponsesProvider
from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.security_analysis import SecurityAnalysisService, SecurityEvidencePromptBuilder
from test import test_agent_foundation as fixtures

ANSWER = "## 结论\n未确认。\n## 已核实问题\n无。\n## 未确认风险\n无。\n## 范围与局限\n仅样本。\n" + REPORT_END


class ExperimentQualityTests(unittest.TestCase):
    def test_real_usage_zero_and_missing_are_distinct(self):
        metadata = response_metadata({"choices": [{"finish_reason": "length"}], "usage": {
            "prompt_tokens": 123, "completion_tokens": 40, "total_tokens": 163,
            "prompt_tokens_details": {"cached_tokens": 0}, "completion_tokens_details": {"reasoning_tokens": 31},
        }}, responses_api=False)
        self.assertEqual(ModelUsage(123, 40, 163, 0, 31), metadata.usage)
        self.assertEqual("length", metadata.finish_reason)
        self.assertIsNone(response_metadata({}, responses_api=False).usage.output_tokens)

    def test_report_transport_and_semantic_coverage_are_separate(self):
        complete = assess_answer(ANSWER, ModelResponseMetadata(finish_reason="stop"), require_report=True)
        self.assertEqual("complete", complete["status"])
        self.assertEqual("not_proven", complete["semantic_coverage"])
        self.assertEqual("unknown", assess_answer(ANSWER, ModelResponseMetadata(), require_report=True)["status"])
        self.assertEqual("incomplete", assess_answer(ANSWER, ModelResponseMetadata(finish_reason="length"), require_report=True)["status"])
        self.assertEqual("incomplete", assess_answer(ANSWER[:-len(REPORT_END)], ModelResponseMetadata(finish_reason="stop"), require_report=True)["status"])

    def test_window_preserves_json_evidence_and_whole_lines(self):
        evidence = {"path": "main.py", "line": 20, "symbol": "run"}
        observation = {"call_id": "c1", "name": "read_file_range", "arguments": {"path": "main.py", "start_line": 1, "end_line": 60},
                       "result": {"content": {"path": "main.py", "start_line": 1, "end_line": 60,
                                               "text": "\n".join(f"{i}: " + "x" * 250 for i in range(1, 61))}, "evidence": [evidence], "truncated": False}}
        compact = compact_observation(observation, 2000)
        self.assertEqual([evidence], compact["result"]["evidence"])
        self.assertLess(compact["result"]["content"]["end_line"], 60)
        self.assertTrue(all(len(line.split(': ', 1)[1]) == 250 for line in compact["result"]["content"]["text"].splitlines()))
        rendered, metrics = render_observation_window([observation], 2200)
        self.assertEqual(1, len(json.loads(rendered)["observations"]))
        self.assertLessEqual(len(rendered), 2200)
        self.assertEqual(1, metrics["compacted_observations"])
        self.assertEqual(60, observation["result"]["content"]["end_line"])

    def test_latest_source_is_not_starved_by_many_old_observations(self):
        old = [{"call_id": str(i), "name": "list_project_files", "arguments": {},
                "result": {"content": {"files": ["src/" + "long" * 80 + ".py"]}, "evidence": []}} for i in range(10)]
        latest = {"call_id": "latest", "name": "read_file_range", "arguments": {"path": "main.py"},
                  "result": {"content": {"path": "main.py", "start_line": 1, "end_line": 20,
                                         "text": "\n".join(f"{i}: user = input()" for i in range(1, 21))},
                             "evidence": [{"path": "main.py", "line": 1}]}}
        text, _ = render_observation_window([*old, latest], 1600)
        source = next(item for item in json.loads(text)["observations"] if item["call_id"] == "latest")
        self.assertIn("1: user = input()", source["result"]["content"]["text"])
        self.assertLessEqual(len(text), 1600)

    def test_compact_directory_keeps_whole_paths_and_omission_count(self):
        paths = [f"src/folder/{i}_long_name.py" for i in range(100)]
        observation = {"call_id": "files", "name": "list_project_files", "arguments": {},
                       "result": {"content": {"files": paths}, "evidence": []}}
        text, _ = render_observation_window([observation], 900)
        result = json.loads(text)["observations"][0]["result"]["content"]
        self.assertTrue(result["files"])
        self.assertTrue(all(item in paths for item in result["files"]))
        self.assertEqual(100, len(result["files"]) + result["omitted_files"])
        self.assertLessEqual(len(text), 900)

    def test_no_candidate_context_includes_real_input_locations_and_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.py").write_bytes(b"value = input('value')\nprint(int(value, 16))\n")
            analysis = UnifiedCodeAnalyzer(directory).run_full_analysis()
            pack = SecurityAnalysisService().analyze(project_root=directory, dependency_graph=analysis["dependency_graph"])
            self.assertFalse(pack.candidates)
            envelope = SecurityEvidencePromptBuilder().build(pack)
            observation = next(item for item in envelope.observations if item.kind == "source")
            self.assertEqual("main.py", observation.location.path)
            self.assertEqual(1, observation.location.line)
            self.assertIsNotNone(observation.snippet)
            self.assertTrue(envelope.analysis["rule_coverage_available"])
            self.assertEqual(0, pack.coverage.entrypoint_count)
            self.assertEqual(1, pack.coverage.source_count)
            self.assertIn("not_project_safety", envelope.analysis["count_semantics"]["zero_detection"])

    def test_small_context_keeps_both_input_locations_without_broken_json(self):
        from backend.app.llm.registry import ModelLimits
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.py").write_bytes(b"a = input('a')\nb = input('b')\n")
            analysis = UnifiedCodeAnalyzer(directory).run_full_analysis()
            pack = SecurityAnalysisService().analyze(project_root=directory, dependency_graph=analysis["dependency_graph"])
            with patch("backend.app.experiments.context.get_model_limits", return_value=ModelLimits(5500, 2400)):
                packet = SecurityExperimentContextBuilder().build(project_id="p", question="是否存在 RCE？", artifact={"security_evidence": pack.model_dump()})
            value = json.loads(packet.prompt_context.split("\n", 1)[1])
            self.assertEqual(2, len(value["observations"]))
            self.assertEqual(0, value["omitted_observations"])
            self.assertEqual({1, 2}, {item.line for item in packet.evidence})

    def test_baseline_preflight_rejects_over_budget_question(self):
        from backend.app.llm.registry import ModelLimits
        with patch("backend.app.experiments.context.get_model_limits", return_value=ModelLimits(5500, 2400)):
            with self.assertRaises(ValueError):
                SecurityExperimentContextBuilder(with_evidence=False).build(
                    project_id="p", question="长问题" * 2000, artifact={},
                )

    def test_small_budget_keeps_dangerous_sink_before_verbose_entrypoints(self):
        from backend.app.llm.registry import ModelLimits
        with tempfile.TemporaryDirectory() as directory:
            source = "from fastapi import FastAPI\napp = FastAPI()\n" + "\n".join(
                f"@app.get('/path{i}')\ndef route{i}(value: str):\n    return eval(value)\n" for i in range(20)
            )
            Path(directory, "main.py").write_text(source, encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory).run_full_analysis()
            pack = SecurityAnalysisService().analyze(project_root=directory, dependency_graph=analysis["dependency_graph"])
            with patch("backend.app.experiments.context.get_model_limits", return_value=ModelLimits(5500, 2400)):
                packet = SecurityExperimentContextBuilder().build(project_id="p", question="是否存在 RCE？", artifact={"security_evidence": pack.model_dump()})
            value = json.loads(packet.prompt_context.split("\n", 1)[1])
            self.assertTrue(value["findings"] or any(item["name"] == "eval" for item in value["observations"]))
            self.assertTrue(value["truncated"])

    def test_registration_prefilter_preserves_original_match_order(self):
        """对比旧全量匹配结果，并确保无关调用不进入每个函数的注册扫描。"""
        from backend.app.services.security_analysis.ir import IRCall, IRExpression, IRFunction, IRLocation, SecurityProgramIR
        from backend.app.services.security_analysis.rule_engine import SecurityRuleEngine
        from backend.app.services.security_analysis.rules.base import EntrypointRule, RulePack
        location = IRLocation("app.js", 1, 1, 1, 10, "loc")
        rule = EntrypointRule("route", "fixture", ("javascript",), registration_call_methods=(("register", "GET"),))
        pack = RulePack("fixture", "1", ("javascript",), entrypoint_rules=(rule,))
        calls = [IRCall("unrelated", str(i), "app.js", location) for i in range(200)] + [
            IRCall("app.register", "a", "app.js", location, (IRExpression("'/first'", is_literal=True, literal="/first"), IRExpression("pkg.handler"))),
            IRCall("register", "b", "app.js", location, (IRExpression("'/second'", is_literal=True, literal="/second"), IRExpression("pkg::handler"))),
        ]
        program = SecurityProgramIR("javascript", functions=[IRFunction("app.js::handler", "handler", (), (), location)], calls=calls)
        engine = SecurityRuleEngine()
        original = engine._registered_function_entrypoint
        with patch.object(engine, "_registered_function_entrypoint", side_effect=lambda function, filtered, rule: original(function, calls, rule)):
            expected = engine.evaluate(program, (pack,))
        with patch.object(engine, "_registered_function_entrypoint", wraps=original) as matcher:
            actual = engine.evaluate(program, (pack,))
        self.assertEqual(expected, actual)
        self.assertEqual(2, len(matcher.call_args.args[1]))
        self.assertEqual("/first", actual.entrypoints[0].metadata["route_path"])

    def test_verbose_header_cannot_displace_first_complete_finding(self):
        """增加预算后从紧凑头部切回详细头部，不能反而丢失原可容纳的完整候选。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.py").write_text(
                "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/run')\ndef run(value: str):\n    return eval(value)\n",
                encoding="utf-8",
            )
            analysis = UnifiedCodeAnalyzer(directory).run_full_analysis()
            pack = SecurityAnalysisService().analyze(project_root=directory, dependency_graph=analysis["dependency_graph"])
            builder = SecurityEvidencePromptBuilder()
            header = builder._empty_envelope(pack)
            finding = builder._finding(pack, pack.candidates[0])
            compact = builder._compact_header(header, pack)
            enough = builder._size(compact.model_copy(update={"findings": [finding]})) + 128
            larger = max(enough + 1, int(builder._size(header) / 0.65) + 1)
            self.assertTrue(builder.build(pack, max_chars=enough).findings)
            self.assertTrue(builder.build(pack, max_chars=larger).findings)

    def test_full_large_tool_observation_is_persisted_with_all_evidence(self):
        async def scenario():
            store = fixtures.MemoryRunStore()
            manager = AgentRunManager(store=store)
            provider = AsyncMock()
            provider.name, provider.model = "mock", "mock"
            provider.generate_with_tools.side_effect = [
                ModelTurn("", "mock", "mock", (ToolCall("read", "read_file_range", {"path": "main.py", "start_line": 1, "end_line": 150}),)),
                ModelTurn("完成", "mock", "mock", metadata=ModelResponseMetadata(finish_reason="stop", usage=ModelUsage(100, 5, 105))),
            ]
            with tempfile.TemporaryDirectory() as directory:
                Path(directory, "main.py").write_text("\n".join("print('" + "x" * 100 + "')" for _ in range(150)), encoding="utf-8")
                with patch("backend.app.agents.orchestrator.create_model_provider", return_value=provider):
                    await manager._run(run_id="r1", project_id="p1", user_id="u1", project_root=directory, artifact=fixtures.sample_artifact(), request=AgentRunRequest(question="检查", max_steps=2))
            result = next(payload["result"] for event, payload in store.events if event == "tool.completed")
            self.assertGreater(len(result["content"]["text"]), 16000)
            self.assertEqual(150, result["content"]["end_line"])
            self.assertTrue(result["evidence"])
            self.assertEqual("completed", store.status)
            messages = provider.generate_with_tools.call_args_list[1].kwargs["messages"]
            self.assertEqual(["user", "assistant", "tool"], [item["role"] for item in messages])
            result = json.loads(messages[-1]["content"])
            self.assertTrue(result["evidence"])
        asyncio.run(scenario())

    def test_both_adapters_preserve_empty_incomplete_response_metadata(self):
        async def scenario():
            compatible = OpenAICompatibleProvider(provider_name="compatible", base_url="http://mock", model="requested")
            payload = {"model": "actual", "choices": [{"finish_reason": "length", "message": {"content": ""}}], "usage": {"prompt_tokens": 10, "completion_tokens": 50}}
            with patch("backend.app.llm.providers.compatible_provider.post_json", new=AsyncMock(return_value=payload)):
                result = await compatible.generate(instructions="i", prompt="p")
            self.assertEqual("actual", result.model)
            self.assertEqual("length", result.metadata.finish_reason)
            self.assertEqual(50, result.metadata.usage.output_tokens)
            responses = OpenAIResponsesProvider(api_key="not-real", model="requested")
            with patch("backend.app.llm.providers.openai_provider.post_json", new=AsyncMock(return_value={"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "usage": {"input_tokens": 12, "output_tokens": 30}, "output": []})):
                result = await responses.generate(instructions="i", prompt="p")
            self.assertEqual("max_output_tokens", result.metadata.incomplete_reason)
            self.assertEqual(30, result.metadata.usage.output_tokens)
        asyncio.run(scenario())

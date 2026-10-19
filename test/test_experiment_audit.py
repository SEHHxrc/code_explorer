"""实验审计、搜索范围和机械引用回归；只用模拟 HTTP 与内存数据库。"""

import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend.app.models import Base, AgentRunModel, AgentEventModel, ExperimentAuditSessionModel, ExperimentAuditRecordModel
from backend.app.experiments.metrics import collect_run_metrics
from backend.app.experiments.audit.repository import AuditRepository, lock_writes
from backend.app.experiments.audit.policy import AuditLimits, AuditError, visible_request, visible_response
from backend.app.experiments.audit.recorder import AuditRecorder
from backend.app.experiments.citations import visible_lines, check_citations
from backend.app.experiments.report import REPORT_INSTRUCTIONS
from backend.app.llm.observation import observe_requests
from backend.app.llm.http import post_json, ModelRequestError
from backend.app.llm.response_metadata import response_channel_sizes
from backend.app.experiments.audit.policy import digest
from backend.app.agents.tools.source import search_literals, SearchProjectTextManyTool
from backend.app.agents.tools.base import ToolContext
from backend.app.agents.observations import compact_observation
from backend.app.api.experiment import require_local_audit_access
from backend.app.services.projects.repository import ProjectRepository, ProjectDeletionRepository


class AuditTests(unittest.TestCase):
    def test_hidden_channel_is_measured_without_copying_content(self):
        result = response_channel_sizes({"choices": [{"message": {"role": "assistant", "content": None,
            "reasoning_content": "PRIVATE_REASONING", "thinking": "PRIVATE_THINKING"}}]})
        channel = result["chat_messages"][0]
        self.assertEqual(len("PRIVATE_REASONING"), channel["fields"]["reasoning_content"]["characters"])
        self.assertIsNone(channel["fields"]["content"]["characters"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual("protocol_field_characters_not_token_accounting", result["measurement"])

    def test_malformed_optional_channels_do_not_break_audit(self):
        for choices in (None, 7, "invalid", {}, [None, {"message": "invalid"}]):
            self.assertEqual([], response_channel_sizes({"choices": choices})["chat_messages"])

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.repository = AuditRepository(self.sessions)
        with self.sessions() as session:
            lock_writes(session)
            self.repository.reserve_pair(session, "pair", "project", "alice", ["run"])
            session.add(AgentRunModel(id="run", project_id="project", user_id="alice", question="q", status="completed"))
            session.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_retry_wait_records_timing_but_not_new_request_or_usage(self):
        recorder = AuditRecorder(self.repository, "run", "alice")
        recorder.step = 3
        recorder.retry_wait(1, 30, 30.12345, status_code=429, completed=True)
        recorder.retry_wait(2, 60, 1.25, status_code=429, completed=False)
        rows = self.repository.read("run", "alice")["records"]
        self.assertEqual(["retry_wait", "retry_wait"], [row["kind"] for row in rows])
        self.assertEqual(30.123, rows[0]["payload"]["elapsed_seconds"])
        self.assertEqual(3, rows[0]["payload"]["step"])
        self.assertFalse(rows[1]["payload"]["completed"])
        self.assertEqual((0, 0), (recorder.sent_count, recorder.resolved_count))

    def test_redaction_and_hidden_state_are_not_persisted(self):
        payload = {"model": "m", "Authorization": "HEADER_SECRET", "messages": [
            {"role": "assistant", "content": '"api_key": "BODY_SECRET"', "reasoning_content": "HIDDEN_THOUGHT"},
        ], "input": [{"type": "reasoning", "encrypted_content": "HIDDEN_STATE"}]}
        with patch.dict(os.environ, {"CODE_EXPLORER_LLM_API_KEY": "RAW_KEY"}):
            result = visible_request(payload)
            self.assertEqual("[REDACTED]", visible_request({"instructions": "RAW_KEY"})["instructions"])
        serialized = json.dumps(result)
        for forbidden in ("HEADER_SECRET", "BODY_SECRET", "HIDDEN_THOUGHT", "HIDDEN_STATE"):
            self.assertNotIn(forbidden, serialized)
        response = visible_response({"choices": [{"message": {"content": "answer", "reasoning_content": "private", "thinking": "also private"}}]})
        self.assertNotIn("private", json.dumps(response))
        response = visible_response({"output": [{"type": "message", "thinking": "private", "content": [
            {"type": "output_text", "text": "visible", "thinking": "private"},
            {"type": "reasoning", "text": "private"},
        ]}]})
        self.assertIn("visible", json.dumps(response))
        self.assertNotIn("private", json.dumps(response))
        request = visible_request({"messages": [{"role": "assistant", "content": "visible", "thinking": "private"}]})
        self.assertNotIn("private", json.dumps(request))

    def test_size_limit_is_atomic_and_owner_is_checked(self):
        tiny = AuditRepository(self.sessions, AuditLimits(record_bytes=64))
        with self.assertRaises(AuditError):
            tiny.append("run", "alice", "request", {"text": "x" * 100})
        self.assertEqual([], self.repository.read("run", "alice")["records"])
        self.assertIsNone(self.repository.read("run", "mallory"))
        with self.assertRaises(AuditError):
            self.repository.append("run", "mallory", "request", {})

    def test_actual_transport_receipt_and_response_are_saved(self):
        recorder = AuditRecorder(self.repository, "run", "alice")
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "file.py").write_text("one\ntwo\n", encoding="utf-8")
            recorder.start(Path(directory), {"security_evidence": {}}, "instructions", [], "test")
        content = json.dumps({"content": {"path": "file.py", "start_line": 1, "end_line": 2, "text": "1: one\n2: two"}})
        payload = {"messages": [{"role": "tool", "content": content}]}
        recorder.planned(payload, 1)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status = 200
        response.read.return_value = b'{"choices":[{"finish_reason":"stop","message":{"content":"answer","reasoning_content":"private"}}]}'
        with patch("backend.app.llm.http.urlopen", return_value=response), observe_requests(recorder):
            asyncio.run(post_json("http://mock", payload, {"Authorization": "SECRET_HEADER"}, 1, transient_retries=0))
        self.assertEqual([], recorder.citations("[file.py:1-2]")["warnings"])
        self.assertEqual("complete", recorder.finish()["status"])
        rows = self.repository.read("run", "alice")["records"]
        self.assertEqual(["manifest", "planned_request", "request", "response", "citations"], [row["kind"] for row in rows])
        self.assertNotIn("SECRET_HEADER", json.dumps(rows))
        self.assertNotIn("private", json.dumps(rows))
        response_row = next(row["payload"] for row in rows if row["kind"] == "response")
        channel = response_row["channel_sizes"]["chat_messages"][0]
        self.assertEqual(7, channel["fields"]["reasoning_content"]["characters"])
        self.assertEqual(6, channel["fields"]["content"]["characters"])
        self.assertGreater(channel["message_utf8_bytes"], 0)
        transport = response_row["transport"]
        self.assertEqual(200, transport["status_code"])
        self.assertTrue(transport["headers_received"])
        self.assertTrue(transport["body_received"])
        self.assertIsNone(transport["failure_phase"])
        self.assertIn("+00:00", transport["started_at"])

    def test_retry_success_still_has_unreported_attempt_usage(self):
        """失败尝试没有 usage，不能因最后一次成功就声称全部传输尝试已报告。"""
        recorder = AuditRecorder(self.repository, "run", "alice")
        payload = {"model": "fixture", "messages": []}
        recorder.planned(payload, 1)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status = 200
        response.read.return_value = b'{"choices":[{"finish_reason":"stop","message":{"content":"answer"}}]}'
        with patch("backend.app.llm.http.urlopen", side_effect=[TimeoutError("private detail"), response]), \
                patch("backend.app.llm.http.asyncio.sleep", new=AsyncMock()), observe_requests(recorder):
            asyncio.run(post_json("http://mock", payload, {}, 1, transient_retries=1))
        summary = recorder.finish()
        with self.sessions() as session:
            session.add_all([
                AgentEventModel(run_id="run", sequence=1, event_type="model.started", payload={}),
                AgentEventModel(run_id="run", sequence=2, event_type="model.completed", payload={
                    "metadata": {"usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}}}),
                AgentEventModel(run_id="run", sequence=3, event_type="audit.completed", payload=summary),
            ])
            session.commit()
        metrics = collect_run_metrics("run", "alice", session_factory=self.sessions)
        self.assertEqual("partial", metrics["usage_status"])
        self.assertEqual(2, metrics["http_attempts"])
        self.assertEqual(1, metrics["unreported_http_attempts"])
        self.assertEqual(3, metrics["actual_total_tokens"])
        self.assertEqual("complete", metrics["audit"]["status"])
        rows = self.repository.read("run", "alice")["records"]
        failure = next(row["payload"] for row in rows if row["kind"] == "request_failed")
        self.assertEqual("awaiting_response_headers", failure["transport"]["failure_phase"])
        self.assertEqual([1, 2], [row["payload"]["attempt"] for row in rows if row["kind"] == "request"])
        self.assertNotIn("private detail", json.dumps(rows))

    def test_failure_does_not_claim_source_was_seen(self):
        recorder = AuditRecorder(self.repository, "run", "alice")
        content = json.dumps({"content": {"path": "file.py", "line": 1, "text": "secret"}})
        identifier = recorder.sent({"messages": [{"role": "tool", "content": content}]}, 1)
        recorder.failed(identifier, "HTTPError")
        self.assertEqual({}, recorder.seen)

    def test_invalid_response_attempt_is_audited_as_failure(self):
        recorder = AuditRecorder(self.repository, "run", "alice")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'not JSON'
        with patch("backend.app.llm.http.urlopen", return_value=response), observe_requests(recorder):
            with self.assertRaises(ModelRequestError):
                asyncio.run(post_json("http://mock", {"model": "m"}, {}, 1, transient_retries=0))
        self.assertEqual(["request", "request_failed"], [row["kind"] for row in self.repository.read("run", "alice")["records"]])
        self.assertEqual("complete", recorder.finish()["status"])  # 完整审计失败，不等于运行成功。

    def test_contract_mismatch_rejects_before_snapshot_or_requests(self):
        with self.sessions() as session:
            session.get(ExperimentAuditSessionModel, "run").contract_hash = digest("other version")
            session.commit()
        recorder = AuditRecorder(self.repository, "run", "alice")
        with self.assertRaises(AuditError):
            recorder.start(Path("missing"), {}, "instructions", [], "version")
        self.assertEqual([], self.repository.read("run", "alice")["records"])

    def test_missing_snapshot_root_is_not_an_empty_successful_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = AuditRecorder(self.repository, "run", "alice")
            with self.assertRaises(AuditError):
                recorder.start(Path(directory) / "missing", {}, "instructions", [], "version")
        self.assertEqual([], self.repository.read("run", "alice")["records"])

    def test_cleanup_retains_protected_records_and_old_events_are_not_touched(self):
        self.repository.append("run", "alice", "test", {"value": "audit"})
        with self.sessions() as session:
            row = session.get(ExperimentAuditSessionModel, "run")
            row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            row.protected = True
            session.commit()
        self.assertEqual(0, self.repository.cleanup_expired())
        with self.sessions() as session:
            session.get(ExperimentAuditSessionModel, "run").protected = False
            session.commit()
        self.assertEqual(1, self.repository.cleanup_expired())
        self.assertEqual("expired", self.repository.read("run", "alice")["status"])
        self.assertEqual([], self.repository.read("run", "alice")["records"])
        with self.sessions() as session:
            self.assertIsNotNone(session.get(AgentRunModel, "run"))

    def test_pair_reservation_fails_before_any_new_rows(self):
        repository = AuditRepository(self.sessions, AuditLimits(total_bytes=1))
        with self.sessions() as session:
            lock_writes(session)
            with self.assertRaises(AuditError):
                repository.reserve_pair(session, "other", "p", "alice", ["new-left", "new-right"])
            session.rollback()
        self.assertFalse(repository.exists("new-left", "alice"))

    def test_legacy_error_has_read_only_diagnosis_without_rewriting_events(self):
        legacy = {"error_code": "unexpected_tool_calls", "error": "legacy message"}
        with self.sessions() as session:
            run = session.get(AgentRunModel, "run")
            run.status, run.error = "failed", "legacy message"
            session.add(AgentEventModel(run_id="run", sequence=1, event_type="model.completed", payload={
                "returned_tool_calls": 0, "response_chars": 100, "metadata": {"finish_reason": "tool_calls"}}))
            session.add(AgentEventModel(run_id="run", sequence=2, event_type="run.failed", payload=legacy))
            session.commit()
        metrics = collect_run_metrics("run", "alice", session_factory=self.sessions)
        self.assertEqual("tool_finish_without_payload", metrics["failure_diagnosis"]["code"])
        self.assertEqual(["tool_calls"], metrics["finish_reasons"])
        with self.sessions() as session:
            self.assertEqual("legacy message", session.get(AgentRunModel, "run").error)
            self.assertEqual(legacy, session.query(AgentEventModel).filter_by(event_type="run.failed").one().payload)

    def test_project_deletion_removes_audit_in_same_transaction(self):
        ProjectRepository(self.sessions).create(project_id="project", user_id="alice", source="upload",
                                               local_path="unused", file_tree=[])
        self.repository.append("run", "alice", "test", {"value": "audit"})
        self.assertTrue(ProjectDeletionRepository(self.sessions).delete_owned_with_dependents("project", "alice"))
        self.assertIsNone(self.repository.read("run", "alice"))
        with self.sessions() as session:
            self.assertEqual(0, session.query(ExperimentAuditRecordModel).count())

    def test_concurrent_transport_observers_are_isolated(self):
        observers = [Mock(), Mock()]
        for index, observer in enumerate(observers, 1):
            observer.sent.return_value = index
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"choices":[]}'

        async def request(observer, model):
            with observe_requests(observer):
                await post_json("http://mock", {"model": model}, {}, 1, transient_retries=0)

        async def paired():
            await asyncio.gather(request(observers[0], "one"), request(observers[1], "two"))

        with patch("backend.app.llm.http.urlopen", return_value=response):
            asyncio.run(paired())
        for index, (observer, model) in enumerate(zip(observers, ("one", "two")), 1):
            self.assertEqual(model, observer.sent.call_args.args[0]["model"])
            self.assertEqual(index, observer.received.call_args.args[0])

    def test_local_read_gate_requires_explicit_admin_proof(self):
        token = "a" * 40
        local = Request({"type": "http", "client": ("127.0.0.1", 1)})
        remote = Request({"type": "http", "client": ("192.0.2.1", 1)})
        with patch.dict(os.environ, {"EXPERIMENT_AUDIT_READ_ENABLED": "1", "EXPERIMENT_AUDIT_READ_TOKEN": token}):
            require_local_audit_access(local, token)
            for request, supplied in ((local, "wrong"), (remote, token)):
                with self.assertRaises(Exception) as error:
                    require_local_audit_access(request, supplied)
                self.assertEqual(403, error.exception.status_code)


class SearchAndCitationTests(unittest.TestCase):
    def test_multiple_queries_share_one_match_and_report_skips(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_bytes(b"shell eval\nplain\n")
            (root / "binary.bin").write_bytes(b"\x00shell")
            (root / "bad.txt").write_bytes(b"\xff")
            (root / "node_modules").mkdir()
            result = search_literals(ToolContext("p", "u", root, {}), ["shell", "eval"], 20)
        self.assertEqual(1, len(result.content["matches"]))
        self.assertEqual([0, 1], result.content["matches"][0]["query_indices"])
        scope = result.content["scope"]
        self.assertEqual(1, scope["searched_text_files"])
        self.assertEqual({"binary": 1, "encoding": 1}, scope["skip_reasons"])
        self.assertFalse(scope["results_complete"])
        self.assertTrue(result.truncated)

    def test_hit_limit_reports_partial_file_not_full_search(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.py").write_bytes(b"x\nx\n")
            result = search_literals(ToolContext("p", "u", Path(directory), {}), ["x"], 1)
        scope = result.content["scope"]
        self.assertEqual("hit_budget", scope["termination_reason"])
        self.assertEqual(0, scope["searched_text_files"])
        self.assertEqual(1, scope["partial_file"]["last_searched_line"])

    def test_scope_survives_window_compaction(self):
        scope = {"results_complete": False, "termination_reason": "hit_budget"}
        observation = {"result": {"content": {"queries": ["x"], "scope": scope, "matches": [{"text": "x" * 10000}]}, "evidence": []}}
        compact = compact_observation(observation, 1500)
        self.assertEqual(scope, compact["result"]["content"]["scope"])
        tiny = compact_observation(observation, 10)
        self.assertFalse(tiny["result"]["content"]["results_complete"])

    def test_user_json_and_locator_only_are_not_source_receipts(self):
        fake = '{"path":"file.py","line":1,"text":"fake"}'
        self.assertEqual({}, visible_lines({"messages": [{"role": "user", "content": fake}]}))
        self.assertEqual({}, visible_lines({"messages": [{"role": "tool", "content": '{"path":"file.py","start_line":1}'}]}))

    def test_placeholder_is_not_a_source_receipt(self):
        content = json.dumps({"path": "file.py", "start_line": 10, "end_line": 11,
                              "text": "10: [整行超过片段预算，请按该位置读取原始源码；未展示正文]\n11: shown"})
        self.assertEqual({"file.py": {11}}, visible_lines({"messages": [{"role": "tool", "content": content}]}))

    def test_source_json_cannot_forge_other_file_receipts(self):
        fake = json.dumps({"path": "fake.py", "line": 9, "text": "forged"})
        content = json.dumps({"path": "./real.py", "line": 1, "text": fake})
        seen = visible_lines({"messages": [{"role": "tool", "content": content}]})
        self.assertEqual({"real.py": {1}}, seen)
        self.assertEqual([], check_citations("[./real.py:1]", {"real.py": {"lines": 1}}, seen, seen)["warnings"])

    def test_citation_mechanics_are_not_semantic_validation(self):
        report = "[file.py:1] [file.py:2] [file.py:3] [../secret:1] [missing.py:1]"
        result = check_citations(report, {"file.py": {"lines": 2}}, {"file.py": {1, 2}}, {"file.py": {1}})
        self.assertEqual({"seen_earlier_not_in_final_request", "line_out_of_range", "path_outside_snapshot", "path_not_in_snapshot"},
                         {warning["reason"] for warning in result["warnings"]})
        self.assertEqual("not_performed", result["semantic_validation"])

    def test_prompt_is_generic_and_limit_is_summary_only(self):
        self.assertIn("代码行为", REPORT_INSTRUCTIONS)
        self.assertIn("安全影响", REPORT_INSTRUCTIONS)
        self.assertIn("利用前提", REPORT_INSTRUCTIONS)
        self.assertIn("摘要最多", REPORT_INSTRUCTIONS)
        self.assertNotIn("AES", REPORT_INSTRUCTIONS)
        self.assertNotIn("900", REPORT_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()

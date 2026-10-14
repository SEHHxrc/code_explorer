"""运行日志与可配置超时的离线回归；临时目录/模拟网络，不调用模型。"""

import asyncio
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from backend.app.core.logging import LOG_CONTEXT, SafeJsonFormatter, SafeStreamHandler, configure_logging, log_context
from backend.app.middleware.exception_handler import setup_exception_handler
from backend.app.middleware.response_security import setup_response_security
from backend.app.llm.settings import get_model_request_timeout
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.providers.openai_provider import OpenAIResponsesProvider


class OperationalLoggingTests(unittest.TestCase):
    def setUp(self):
        self.loggers = [logging.getLogger(name) for name in ("backend.app", "uvicorn.error", "uvicorn.access")]
        self.saved = [(logger.handlers[:], logger.level, logger.propagate, logger.disabled) for logger in self.loggers]
        for logger in self.loggers:
            logger.handlers = []
            logger.disabled = False
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {
            "CODE_EXPLORER_LOG_DIR": self.directory.name, "CODE_EXPLORER_LOG_FILE_ENABLED": "1",
            "CODE_EXPLORER_LOG_LEVEL": "DEBUG", "CODE_EXPLORER_LOG_MAX_BYTES": "10485760",
            "CODE_EXPLORER_LOG_BACKUP_COUNT": "5", "CODE_EXPLORER_LLM_API_KEY": "ACTUAL_CONFIGURED_KEY",
        })
        self.environment.start()
        self.console = io.StringIO()
        self.console_patch = patch("backend.app.core.logging.SafeStreamHandler", side_effect=lambda: SafeStreamHandler(self.console))
        self.console_patch.start()

    def tearDown(self):
        handlers = set(handler for logger in self.loggers for handler in logger.handlers)
        for handler in handlers:
            handler.close()
        for logger, (handlers, level, propagate, disabled) in zip(self.loggers, self.saved):
            logger.handlers, logger.propagate, logger.disabled = handlers, propagate, disabled
            logger.setLevel(level)
        self.environment.stop()
        self.console_patch.stop()
        self.console.close()
        self.directory.cleanup()

    def records(self, path):
        for handler in logging.getLogger("backend.app").handlers:
            handler.flush()
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_setup_is_idempotent_and_rotation_uses_process_file(self):
        with patch("backend.app.core.logging.os.getpid", return_value=4321):
            target = configure_logging("api")
            self.assertEqual(target, configure_logging("api"))
        self.assertEqual("api-4321.jsonl", target.name)
        self.assertEqual(2, len(logging.getLogger("backend.app").handlers))
        logging.getLogger("backend.app.example").info("single_event")
        self.assertEqual(["single_event"], [row["event"] for row in self.records(target)])
        self.assertTrue(logging.getLogger("uvicorn.access").disabled)

    def test_sensitive_text_fields_and_exception_source_are_not_saved(self):
        target = configure_logging()
        logger = logging.getLogger("backend.app.example")
        logger.info('key ACTUAL_CONFIGURED_KEY password=VERY_SECRET Bearer HEADER_SECRET https://host/private?token=URL_SECRET',
                    extra={"request_id": "trace", "body": "RAW_BODY", "prompt": "RAW_PROMPT", "headers": {"Authorization": "AUTH_SECRET"}})
        try:
            raise RuntimeError("EXCEPTION_BODY_SECRET")
        except RuntimeError:
            logger.exception("local_failure")
        text = target.read_text(encoding="utf-8")
        for secret in ("ACTUAL_CONFIGURED_KEY", "VERY_SECRET", "HEADER_SECRET", "URL_SECRET", "RAW_BODY", "RAW_PROMPT", "AUTH_SECRET", "EXCEPTION_BODY_SECRET"):
            self.assertNotIn(secret, text)
        rows = self.records(target)
        self.assertEqual("trace", rows[0]["request_id"])
        self.assertEqual("RuntimeError", rows[1]["exception"]["type"])
        self.assertTrue(rows[1]["exception"]["frames"])
        self.assertTrue(all(set(frame) == {"file", "function", "line"} for frame in rows[1]["exception"]["frames"]))

    def test_json_parse_position_is_available_without_response_body(self):
        target = configure_logging()
        try:
            raise json.JSONDecodeError("CUSTOM_PRIVATE_ERROR", "PRIVATE_RESPONSE_BODY", 3)
        except json.JSONDecodeError:
            logging.getLogger("backend.app.llm").exception("response.parse_failed")
        row = self.records(target)[0]
        self.assertEqual(3, row["exception"]["json_position"])
        self.assertNotIn("PRIVATE", json.dumps(row))

    def test_async_context_is_isolated_inherited_by_thread_and_restored(self):
        target = configure_logging()
        before = LOG_CONTEXT.get()

        async def scenario():
            async def task(identifier):
                with log_context(request_id=identifier):
                    await asyncio.sleep(0)
                    await asyncio.to_thread(logging.getLogger("backend.app.example").info, "thread_event")
            await asyncio.gather(task("left"), task("right"))

        asyncio.run(scenario())
        self.assertEqual({"left", "right"}, {row["request_id"] for row in self.records(target)})
        self.assertEqual(before, LOG_CONTEXT.get())

    def test_context_restores_after_exception(self):
        before = LOG_CONTEXT.get()
        with self.assertRaises(RuntimeError):
            with log_context(run_id="temporary"):
                raise RuntimeError
        self.assertEqual(before, LOG_CONTEXT.get())

    def test_analysis_thread_pool_inherits_project_context(self):
        from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
        target = configure_logging()
        analyzer = UnifiedCodeAnalyzer(self.directory.name, max_workers=2)
        logger = logging.getLogger("backend.app.example")
        with log_context(project_id="analysis_project"), \
                patch.object(analyzer, "_collect_files", return_value=[str(Path(self.directory.name, "one.py")), str(Path(self.directory.name, "two.py"))]), \
                patch.object(analyzer._parser_pool, "ensure_available"), \
                patch.object(analyzer, "_analyze_file", side_effect=lambda path: logger.info("file_analyzed")):
            analyzer.run_full_analysis()
        rows = [row for row in self.records(target) if row["event"] == "file_analyzed"]
        self.assertEqual(2, len(rows))
        self.assertTrue(all(row["project_id"] == "analysis_project" for row in rows))

    def test_file_rotation_limits_active_process_backups(self):
        with patch.dict(os.environ, {"CODE_EXPLORER_LOG_MAX_BYTES": "1024", "CODE_EXPLORER_LOG_BACKUP_COUNT": "2"}):
            target = configure_logging("agent-worker")
        for index in range(30):
            logging.getLogger("backend.app.example").info("event_" + str(index) + "_" + "x" * 200)
        files = list(Path(self.directory.name).glob(target.name + "*"))
        self.assertLessEqual(len(files), 3)
        for file in files:
            for line in file.read_text(encoding="utf-8").splitlines():
                json.loads(line)

    def test_unwritable_file_falls_back_without_exposing_path(self):
        with patch("backend.app.core.logging.Path.mkdir", side_effect=PermissionError("PRIVATE_PATH")):
            self.assertIsNone(configure_logging())
        self.assertEqual(1, len(logging.getLogger("backend.app").handlers))

    def test_sink_failure_does_not_echo_raw_log_arguments(self):
        stream = io.StringIO()
        stream.close()
        handler = SafeStreamHandler(stream)
        handler.setFormatter(SafeJsonFormatter())
        record = logging.LogRecord("backend.app", logging.ERROR, __file__, 1, "%s", ("RAW_LOG_SECRET",), None)
        fallback = io.StringIO()
        with patch.object(sys, "__stderr__", fallback):
            handler.emit(record)
        self.assertNotIn("RAW_LOG_SECRET", fallback.getvalue())
        self.assertIn("logging_sink_failed", fallback.getvalue())

    def test_access_logging_uses_route_template_and_preserves_sse_and_errors(self):
        target = configure_logging()
        app = FastAPI()
        setup_exception_handler(app)
        setup_response_security(app)

        @app.get("/stream/{value}")
        async def stream(value: str):
            logging.getLogger("backend.app.example").info("route_executed")
            return StreamingResponse(iter([b"data:one\n\n", b"data:two\n\n"]), media_type="text/event-stream")

        @app.post("/fail")
        async def fail():
            raise RuntimeError("PRIVATE_FAILURE_TEXT")

        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
                response = await client.get("/stream/PRIVATE_PATH_VALUE?api_key=QUERY_SECRET", headers={"X-Request-ID": "trace_A"})
                self.assertEqual("data:one\n\ndata:two\n\n", response.text)
                self.assertEqual("trace_A", response.headers["X-Request-ID"])
                self.assertEqual(500, (await client.post("/fail", content="RAW_REQUEST_BODY")).status_code)

        asyncio.run(scenario())
        rows = self.records(target)
        accesses = [row for row in rows if row["event"] == "http.response_started"]
        self.assertEqual(2, len(accesses))
        self.assertEqual("/stream/{value}", accesses[0]["route"])
        self.assertEqual(1, sum(row["event"] == "http.unhandled_error" for row in rows))
        self.assertEqual("trace_A", next(row for row in rows if row["event"] == "route_executed")["request_id"])
        text = json.dumps(rows)
        for secret in ("PRIVATE_PATH_VALUE", "QUERY_SECRET", "PRIVATE_FAILURE_TEXT", "RAW_REQUEST_BODY"):
            self.assertNotIn(secret, text)

    def test_invalid_log_configuration_is_rejected_without_echoing_values(self):
        with patch.dict(os.environ, {"CODE_EXPLORER_LOG_LEVEL": "PASSWORD_SECRET"}):
            with self.assertRaises(ValueError) as error:
                configure_logging()
            self.assertNotIn("PASSWORD_SECRET", str(error.exception))


class ModelTimeoutConfigurationTests(unittest.TestCase):
    def test_default_and_invalid_settings(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(300, get_model_request_timeout())
        for value in ("", "invalid", "0", "-1", "901", "nan", "inf"):
            with patch.dict(os.environ, {"CODE_EXPLORER_LLM_REQUEST_TIMEOUT_SECONDS": value}, clear=True):
                with self.assertRaises(ValueError):
                    get_model_request_timeout()

    def test_provider_freezes_setting_probe_overrides_and_never_sends_it_in_payload(self):
        for provider_type, module in ((OpenAICompatibleProvider, "compatible_provider"), (OpenAIResponsesProvider, "openai_provider")):
            with patch.dict(os.environ, {"CODE_EXPLORER_LLM_REQUEST_TIMEOUT_SECONDS": "420"}, clear=True):
                provider = provider_type(provider_name="compatible", base_url="http://mock", model="mock") if module == "compatible_provider" else provider_type(api_key="fake", model="mock")
            with patch.dict(os.environ, {"CODE_EXPLORER_LLM_REQUEST_TIMEOUT_SECONDS": "500"}, clear=True), \
                    patch(f"backend.app.llm.providers.{module}.post_json", new=AsyncMock(return_value={})) as send:
                asyncio.run(provider.generate_with_tools(instructions="safe", prompt="question", tools=[]))
                self.assertEqual(420, send.call_args.kwargs["timeout"])
                self.assertNotIn("timeout", send.call_args.args[1])
                asyncio.run(provider.generate_with_tools(instructions="safe", prompt="question", tools=[], timeout=30))
                self.assertEqual(30, send.call_args.kwargs["timeout"])
                for value in (0, -1, float("inf"), True):
                    with self.assertRaises(ValueError):
                        asyncio.run(provider.generate_with_tools(instructions="safe", prompt="question", tools=[], timeout=value))
                self.assertEqual(2, send.await_count)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
import asyncio
import json
import unittest

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from backend.app.middleware.response_security import setup_response_security


async def request(app, path, *, method="GET", headers=None, body=b""):
    """不依赖 httpx，直接通过 ASGI 协议执行一次完整请求。"""
    request_sent = False
    wait_forever = asyncio.Event()
    messages = []

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await wait_forever.wait()

    async def send(message):
        messages.append(message)

    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("test", 1234),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = {
        name.decode("latin-1"): value.decode("latin-1")
        for name, value in start["headers"]
    }
    return start["status"], response_headers, response_body


class ResponseSecurityMiddlewareTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        setup_response_security(app)

        @app.get("/api/json")
        async def json_endpoint():
            return {"value": 1}

        @app.get("/api/events")
        async def event_endpoint():
            async def events():
                yield "event: ready\\ndata: {}\\n\\n"
            return StreamingResponse(events(), media_type="text/event-stream")

        self.app = app

    def test_adds_security_headers_without_changing_json(self):
        status, headers, body = asyncio.run(request(
            self.app, "/api/json", headers={"X-Request-ID": "request-123"}
        ))
        self.assertEqual(200, status)
        self.assertEqual({"value": 1}, json.loads(body))
        self.assertEqual("request-123", headers["x-request-id"])
        self.assertEqual("nosniff", headers["x-content-type-options"])
        self.assertEqual("no-store", headers["cache-control"])

    def test_does_not_rewrite_sse_body(self):
        status, headers, body = asyncio.run(request(self.app, "/api/events"))
        self.assertEqual(200, status)
        self.assertEqual(b"event: ready\\ndata: {}\\n\\n", body)
        self.assertTrue(headers["content-type"].startswith("text/event-stream"))

    def test_rejects_oversized_declared_body(self):
        status, _headers, body = asyncio.run(request(
            self.app,
            "/api/json",
            method="POST",
            headers={"Content-Length": str(221 * 1024 * 1024)},
            body=b"x",
        ))
        self.assertEqual(413, status)
        self.assertEqual(41300, json.loads(body)["code"])


if __name__ == "__main__":
    unittest.main()
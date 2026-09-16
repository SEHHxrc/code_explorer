import unittest
from types import SimpleNamespace

from backend.app.api.sse import persisted_events, sse_response


class FakeEvent:
    def __init__(self, sequence, event_type, payload):
        self.sequence = sequence
        self.type = event_type
        self.payload = payload

    def model_dump(self):
        return {"sequence": self.sequence, "type": self.type, "payload": self.payload}


class SharedSseTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_stream_drains_last_persisted_event(self):
        event = FakeEvent(4, "run.completed", {"answer": "完成"})
        calls = []

        def events_after(sequence):
            calls.append(sequence)
            return [event] if sequence < event.sequence else []

        chunks = [chunk async for chunk in persisted_events(
            after=0,
            events_after=events_after,
            current_view=lambda: SimpleNamespace(status="completed"),
            terminal_statuses={"completed"},
            poll_seconds=0,
        )]
        self.assertEqual(calls, [0, 4])
        self.assertEqual(len(chunks), 1)
        self.assertIn("id: 4\nevent: run.completed\ndata: ", chunks[0])
        self.assertIn('"answer": "完成"', chunks[0])

    async def test_response_disables_proxy_buffering_and_cache(self):
        async def stream():
            if False:
                yield ""

        response = sse_response(stream())
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertTrue(response.media_type.startswith("text/event-stream"))


if __name__ == "__main__":
    unittest.main()
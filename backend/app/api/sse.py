"""FastAPI SSE 的共享编码、心跳和持久化事件轮询。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi.responses import StreamingResponse

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def sse_response(stream: AsyncIterator[str]) -> StreamingResponse:
    """用统一响应头包装异步 SSE 迭代器。"""
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


async def persisted_events(
    *,
    after: int,
    events_after: Callable[[int], list],
    current_view: Callable[[], Any],
    terminal_statuses: set[str],
    poll_seconds: float = 0.25,
) -> AsyncIterator[str]:
    """轮询有序事件，写入心跳，并在终态事件全部发送后结束。"""
    sequence = after
    last_heartbeat = time.monotonic()
    while True:
        events = events_after(sequence)
        for event in events:
            sequence = event.sequence
            payload = json.dumps(event.model_dump(), ensure_ascii=False)
            yield f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n"
        view = current_view()
        if view is None or (view.status in terminal_statuses and not events):
            break
        if time.monotonic() - last_heartbeat > 10:
            yield ": heartbeat\n\n"
            last_heartbeat = time.monotonic()
        await asyncio.sleep(poll_seconds)

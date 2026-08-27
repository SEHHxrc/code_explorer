# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


async def post_json(url: str, payload: dict, headers: dict[str, str], timeout: float) -> dict:
    """在线程中发送 JSON POST；输入 URL、载荷、请求头和超时，输出解码字典。"""
    return await asyncio.to_thread(_post_json_sync, url, payload, headers, timeout)


def _post_json_sync(url: str, payload: dict, headers: dict[str, str], timeout: float) -> dict:
    """执行同步请求并将网络错误转换为不泄露响应内容的运行时错误。"""
    request_headers = {"Content-Type": "application/json", **headers}
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Model endpoint returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError("Unable to connect to model endpoint") from exc

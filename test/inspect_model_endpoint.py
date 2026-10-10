"""显式检查配置平台；不连接项目数据库，不读取源码、不修改实验记录。

运行 ``python test/inspect_model_endpoint.py`` 只查询模型目录。
仅打印允许列表内的模型信息，不输出密钥、完整平台响应或代理认证信息。
"""

from __future__ import annotations

import json
import argparse
import asyncio
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, getproxies, urlopen


def main() -> None:
    """加载现有 .env，查询一次 /models 并打印当前模型的安全元数据。"""
    from dotenv import dotenv_values

    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="额外执行最多两次无项目内容的生成探测")
    parser.add_argument("--reasoning-effort", choices=("low", "high", "max"))
    arguments = parser.parse_args()
    config = dotenv_values(root / "backend" / ".env")
    base_url = (config.get("CODE_EXPLORER_LLM_BASE_URL") or "").rstrip("/")
    model = config.get("CODE_EXPLORER_LLM_MODEL") or ""
    key = config.get("CODE_EXPLORER_LLM_API_KEY") or ""
    proxies = getproxies()
    print(json.dumps({"configured_model": model, "scheme": urlsplit(base_url).scheme,
                      "proxy_schemes": sorted(proxies)}, ensure_ascii=False), flush=True)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    request = Request(base_url + "/models", headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError("模型目录响应超过检查上限")
        payload = json.loads(body)
    except HTTPError as exc:
        print(json.dumps({"status_code": exc.code, "metadata_available": False}))
        return
    except (URLError, TimeoutError, ValueError):
        print(json.dumps({"metadata_available": False, "error": "连接失败或响应无效"}, ensure_ascii=False))
        return
    fields = {"id", "context_window", "context_length", "max_context_length", "max_input_tokens",
              "max_output_tokens", "max_tokens", "supported_parameters"}
    items = payload.get("data", []) if isinstance(payload, dict) else []
    selected = [item for item in items if isinstance(item, dict) and item.get("id") == model]
    result = {"visible_models": len(items), "selected_model_found": bool(selected),
              "selected_metadata": [{k: v for k, v in item.items() if k in fields
                                     and isinstance(v, (str, int, list))} for item in selected]}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if arguments.probe:
        from dotenv import load_dotenv
        import os
        load_dotenv(root / "backend" / ".env", override=False)
        if arguments.reasoning_effort:
            os.environ["CODE_EXPLORER_LLM_REASONING_EFFORT"] = arguments.reasoning_effort
        sys.path.insert(0, str(root))
        from backend.app.llm.diagnostics import probe_model_connection
        print(json.dumps(asyncio.run(probe_model_connection()), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.exit(main())

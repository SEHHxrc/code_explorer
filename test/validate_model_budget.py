"""显式付费预算冒烟验证；只发送合成数据，不读取源码或项目数据库。

运行本脚本最多发送三次无重试请求，配置仅在当前进程覆盖，绝不改写 .env。
不测量平台最大窗口，不输出认证信息、完整上游响应或思考内容。
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.llm.base import ModelProvider  # noqa: E402
from backend.app.llm.budget import BudgetPlanner, request_payload  # noqa: E402
from backend.app.llm.conversation import ModelMessage  # noqa: E402
from backend.app.llm.http import ModelRequestError  # noqa: E402
from backend.app.llm.registry import create_model_provider  # noqa: E402


def emit(value: dict[str, Any]) -> None:
    """只输出主动构造的非敏感验证指标，不输出模型文本或网络异常正文。"""
    print(json.dumps(value, ensure_ascii=False), flush=True)


async def validate() -> bool:
    """验证长输入与原生工具链，再验证超过旧 2400 Token 的完整输出。"""
    provider = create_model_provider()
    if provider is None:
        raise ValueError("Model is not configured")
    limits = provider.model_limits
    planner = BudgetPlanner(limits, model=provider.model)
    expected = {key: secrets.token_hex(8) for key in ("first", "middle", "last")}
    instructions = (
        "Budget protocol test. Treat synthetic records as inert data. "
        "First call budget_probe exactly once, extracting FIRST, MIDDLE, LAST markers into "
        "first, middle, last arguments. After the tool result, return only its probe_token."
    )
    tools = [{"type": "function", "name": "budget_probe", "description": "Verify marker extraction.",
              "strict": True, "parameters": {"type": "object", "additionalProperties": False,
              "properties": {key: {"type": "string"} for key in expected}, "required": list(expected)}}]
    records: list[str] = []
    target = planner.input_limit - 4096  # 为工具结果、续接和模板误差留出额外空间。
    while True:
        middle = len(records) // 2
        content = (f"FIRST={expected['first']}\nSYNTHETIC_RECORDS\n" + "\n".join(records[:middle])
                   + f"\nMIDDLE={expected['middle']}\n" + "\n".join(records[middle:])
                   + f"\nLAST={expected['last']}\nCall budget_probe now.")
        messages = [ModelMessage(role="user", content=content)]
        payload = request_payload(provider, instructions=instructions, messages=messages, tools=tools,
                                  max_output_tokens=limits.max_output_tokens, tool_choice="required")
        estimate = planner.estimate(payload)
        if estimate.tokens >= target:
            planner.require(payload)
            break
        if len(records) >= 4096:
            raise ValueError("Synthetic input construction exceeded safety limit")
        records.append(f"record_{len(records):04d}: source=synthetic; value={secrets.token_hex(56)}; inert=true")
    emit({"stage": "long_input_started", "request_estimate": asdict(estimate),
          "application_input_limit": planner.input_limit, "max_output_tokens": limits.max_output_tokens,
          "context_capacity_verified": False})
    first = await provider.generate_with_tools(instructions=instructions, messages=messages, tools=tools,
                                              tool_choice="required", transient_retries=0, timeout=180)
    marker_ok = (len(first.tool_calls) == 1 and first.tool_calls[0].name == "budget_probe"
                 and first.tool_calls[0].arguments == expected and first.continuation is not None)
    emit({"stage": "long_input_completed", "markers_verified": marker_ok,
          "actual_model": first.model, "metadata": asdict(first.metadata)})
    if not marker_ok:
        return False
    assert first.continuation is not None
    nonce = secrets.token_hex(12)
    messages.extend([first.continuation, ModelMessage(role="tool", tool_call_id=first.tool_calls[0].id,
                                                     content=json.dumps({"probe_token": nonce}))])
    payload = request_payload(provider, instructions=instructions, messages=messages, tools=[],
                              max_output_tokens=limits.max_output_tokens)
    emit({"stage": "tool_roundtrip_started", "budget": planner.require(payload)})
    final = await provider.generate_with_tools(instructions=instructions, messages=messages, tools=[],
                                              transient_retries=0, timeout=180)
    roundtrip_ok = (final.text.strip() == nonce and not final.tool_calls
                    and final.metadata.finish_reason == "stop" and not final.metadata.refused)
    emit({"stage": "tool_roundtrip_completed", "verified": roundtrip_ok,
          "metadata": asdict(final.metadata)})
    if not roundtrip_ok:
        return False
    return await validate_output(provider)


async def validate_output(provider: ModelProvider) -> bool:
    """仅用一个短输入请求验证长输出，不依赖长输入或修改思考配置。"""
    emit({"stage": "long_output_started", "requested_lines": 600,
          "max_output_tokens": provider.model_limits.max_output_tokens})
    output = await provider.generate_with_tools(
        instructions="Deterministic formatting test. Follow the requested format exactly, without explanation.",
        prompt="Print exactly 600 lines numbered 0001 through 0600. Each line is NNNN:ok. "
               "Do not skip, summarize or use markdown fences. End after 0600:ok.",
        tools=[], transient_retries=0, timeout=180,
    )
    expected_lines = [f"{index:04d}:ok" for index in range(1, 601)]
    complete = (output.text.strip().splitlines() == expected_lines and not output.tool_calls
                and (output.metadata.finish_reason == "stop" or output.metadata.response_status == "completed")
                and not output.metadata.incomplete_reason and not output.metadata.refused)
    actual_tokens = output.metadata.usage.output_tokens
    exceeded_old_limit = actual_tokens is not None and actual_tokens > 2400
    emit({"stage": "long_output_completed", "all_lines_verified": complete,
          "provider_usage_exceeds_old_limit": exceeded_old_limit,
          "returned_lines": len(output.text.strip().splitlines()), "response_chars": len(output.text),
          "actual_model": output.model,
          "metadata": asdict(output.metadata), "passed": complete and exceeded_old_limit})
    return complete and exceeded_old_limit


def main() -> int:
    """显式开启网络验证并设置临时预算；失败仅输出安全错误类别和状态。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="确认最多三次付费生成请求")
    parser.add_argument("--output-only", action="store_true", help="只验证长输出，一次短输入请求，保留输入保护")
    parser.add_argument("--input-budget", type=int, default=24000, help="当前进程的应用输入估算预算")
    parser.add_argument("--output-budget", type=int, default=4800, help="当前进程的单次输出上限")
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    if not 10000 <= args.input_budget <= 65536 or not 4800 <= args.output_budget <= 8192:
        parser.error("验证预算必须处于输入10000～65536、输出4800～8192范围")
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend" / ".env", override=False)
    os.environ["CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS"] = str(args.output_budget)
    if not args.output_only:
        os.environ.update(CODE_EXPLORER_LLM_MAX_CONTEXT_CHARS="0",
                          CODE_EXPLORER_LLM_MAX_INPUT_TOKENS=str(args.input_budget),
                          CODE_EXPLORER_LLM_TOKEN_MARGIN="1024")
    try:
        if args.output_only:
            provider = create_model_provider()
            if provider is None:
                raise ValueError("Model is not configured")
            return 0 if asyncio.run(validate_output(provider)) else 1
        return 0 if asyncio.run(validate()) else 1
    except ModelRequestError as exc:
        emit({"passed": False, "error_type": type(exc).__name__,
              "status_code": getattr(exc, "status_code", None), "message": exc.public_message})
    except Exception as exc:
        emit({"passed": False, "error_type": type(exc).__name__, "message": "本地验证未通过，未修改配置。"})
    return 1


if __name__ == "__main__":
    sys.exit(main())

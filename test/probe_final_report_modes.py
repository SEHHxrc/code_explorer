"""最终报告兼容性固定用例；只用合成源码，不读取实验数据库、不执行模型工具。

默认仅打印计划；显式 --send 后用 backend/.env 发送两次请求，零自动重试。
运行：.venv/Scripts/python.exe test/probe_final_report_modes.py --send
每次输出上限取有限配置与 4096 的较小值；配置留空仍显式限制为 4096。长度终止不能判为兼容通过。
输出只有协议诊断、usage 和哈希，不保存密钥、隐藏推理或完整平台响应。
小用例通过不保证较大上下文也能收尾，不改变实际 .env 或实验条件。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.agents.orchestrator import AgentRunManager
from backend.app.agents.reporting import phase_message, PHASE_VERSION
from backend.app.experiments.context import SECURITY_EXPERIMENT_INSTRUCTIONS
from backend.app.experiments.tools import create_experiment_tool_registry
from backend.app.experiments.report import assess_answer
from backend.app.experiments.findings import parse_ledger
from backend.app.experiments.audit.policy import digest
from backend.app.llm.base import ModelOutputError
from backend.app.llm.conversation import ModelMessage, validate_messages
from backend.app.llm.http import ModelRequestError
from backend.app.llm.registry import create_model_provider


def fixed_history() -> list[ModelMessage]:
    """构造完整配对历史，仅包含合成文件和已获得的工具观察，不实际执行工具。"""
    messages = [
        ModelMessage(role="user", content="这是合成协议测试，不是真实项目安全审计。已读取 fixture.py。停止调查，只输出共同契约的完整台账 JSON，findings 为空，六类覆盖如实标记，说明合成范围和局限；不编造问题，不手写 Markdown。"),
        ModelMessage(role="assistant", content=None, tool_calls=[{"id": "fixture-read", "type": "function", "function": {
            "name": "read_file_range", "arguments": '{"path":"fixture.py","start_line":1,"end_line":1}',
        }}]),
        ModelMessage(role="tool", tool_call_id="fixture-read", content=json.dumps({"content": {
            "path": "fixture.py", "start_line": 1, "end_line": 1, "text": "1: print('synthetic fixture')",
        }, "evidence": [], "truncated": False}, ensure_ascii=False)),
        phase_message(remaining_rounds=0, final=True),
    ]
    validate_messages(messages)
    return messages


async def probe() -> list[dict[str, Any]]:
    """两种收尾模式各发送一次；只报告观察事实，不自动选模式或纠正结束状态。"""
    provider = create_model_provider()
    if provider is None:
        raise ValueError("模型未配置；未发送请求。")
    schemas = create_experiment_tool_registry().schemas()
    history = fixed_history()
    results = []
    for mode in ("tools_none", "history_no_tools"):
        tools = schemas if mode == "tools_none" else []
        limit = provider.model_limits.max_output_tokens
        ceiling = min(limit, 4096) if limit is not None else 4096
        payload = provider.request_payload(instructions=SECURITY_EXPERIMENT_INSTRUCTIONS, messages=history,
                                           tools=tools, tool_choice="none", max_output_tokens=ceiling)
        row: dict[str, Any] = {"mode": mode, "phase_version": PHASE_VERSION, "request_sha256": digest(payload),
                              "tool_schema_count": len(tools), "tool_choice_sent": payload.get("tool_choice"),
                              "max_output_tokens": ceiling, "retries": 0}
        started = time.monotonic()
        try:
            turn = await provider.generate_with_tools(instructions=SECURITY_EXPERIMENT_INSTRUCTIONS,
                messages=history, tools=tools, tool_choice="none", max_output_tokens=ceiling,
                transient_retries=0, timeout=60)
            row.update(metadata=asdict(turn.metadata), actual_model=turn.model, actual_tool_calls=len(turn.tool_calls),
                       visible_chars=len(turn.text), visible_sha256=digest(turn.text))
            try:
                text = AgentRunManager._require_answer(turn, forbid_tools=True)
                report = parse_ledger(text).render()
                row["answer_completeness"] = assess_answer(report, turn.metadata, require_report=True)
                row["passed"] = row["answer_completeness"]["status"] == "complete"
                row["error_code"] = None if row["passed"] else "completion_not_confirmed"
            except ModelOutputError as error:
                row.update(passed=False, error_code=error.code)
            except ValueError:
                row.update(passed=False, error_code="invalid_findings_ledger")
        except ModelRequestError as error:
            row.update(passed=False, error_code=type(error).__name__, status_code=getattr(error, "status_code", None))
        row["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        results.append(row)
        print(json.dumps(row, ensure_ascii=True), flush=True)
    return results


def main() -> None:
    """显式加载本机配置；默认不发网络请求，输出不含地址或代理凭据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="允许两次真实合成请求，可能产生模型费用")
    arguments = parser.parse_args()
    if not arguments.send:
        print(json.dumps({"requests_sent": 0, "planned_modes": ["tools_none", "history_no_tools"]}))
        return
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend" / ".env", override=True)
    results = asyncio.run(probe())
    if not any(item["passed"] for item in results):
        sys.exit(1)


if __name__ == "__main__":
    main()

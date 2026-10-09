"""从智能体持久化事件计算与供应商无关的实验指标。"""

from __future__ import annotations

import math
from typing import Callable

from sqlalchemy.orm import Session

from backend.app.models import AgentEventModel, AgentRunModel, SessionLocal


def collect_run_metrics(run_id: str, user_id: str, *, session_factory: Callable[[], Session] = SessionLocal) -> dict:
    """计算耗时、静态上下文/答案字符估算、工具调用和去重证据数量。"""
    session = session_factory()
    try:
        run = session.query(AgentRunModel).filter(
            AgentRunModel.id == run_id,
            AgentRunModel.user_id == user_id,
        ).first()
        if run is None:
            return {}
        events = session.query(AgentEventModel).filter(
            AgentEventModel.run_id == run_id,
        ).order_by(AgentEventModel.sequence).all()
        context_chars = 0
        tool_calls = 0
        evidence_count = 0
        initial_evidence_count = 0
        tool_evidence: set[tuple] = set()
        request_chars = 0
        response_chars = 0
        started_at = None
        finished_at = None
        for event in events:
            if event.event_type == "context.ready":
                context_chars = int((event.payload or {}).get("characters") or 0)
                initial_evidence_count = len((event.payload or {}).get("evidence") or [])
            elif event.event_type == "tool.requested":
                tool_calls += 1
            elif event.event_type == "model.started":
                request_chars += int((event.payload or {}).get("request_chars") or 0)
            elif event.event_type == "model.completed":
                response_chars += int((event.payload or {}).get("response_chars") or 0)
            elif event.event_type == "tool.completed":
                result = (event.payload or {}).get("result")
                if isinstance(result, dict):
                    for evidence in result.get("evidence") or []:
                        if isinstance(evidence, dict):
                            tool_evidence.add((evidence.get("path"), evidence.get("line"), evidence.get("symbol")))
            elif event.event_type == "run.started":
                started_at = event.created_at
            elif event.event_type == "run.completed":
                evidence_count = len((event.payload or {}).get("evidence") or [])
            if event.event_type in {"run.completed", "run.failed", "run.cancelled"}:
                finished_at = event.created_at
        duration_ms = None
        queue_duration_ms = None
        if started_at and finished_at:
            duration_ms = max(0, round((finished_at - started_at).total_seconds() * 1000, 2))
        if started_at and run.created_at:
            queue_duration_ms = max(0, round((started_at - run.created_at).total_seconds() * 1000, 2))
        answer_chars = len(run.answer or "")
        return {
            "duration_ms": duration_ms,
            "queue_duration_ms": queue_duration_ms,
            "context_characters": context_chars,
            "request_characters": request_chars,
            "estimated_input_tokens": math.ceil(request_chars / 4),
            "answer_characters": answer_chars,
            "response_characters": response_chars,
            "estimated_output_tokens": math.ceil(response_chars / 4) if any(event.event_type == "model.completed" for event in events) else None,
            "token_measurement": "normalized_characters_divided_by_four_not_provider_usage",
            "response_measurement_available": any(event.event_type == "model.completed" for event in events),
            "tool_calls": tool_calls,
            "model_requests": sum(event.event_type == "model.started" for event in events),
            "evidence_count": evidence_count,
            "initial_evidence_count": initial_evidence_count,
            "tool_evidence_count": len(tool_evidence),
            "provider": run.provider,
            "model": run.model,
        }
    finally:
        session.close()

"""从智能体持久化事件计算与供应商无关的实验指标。"""

from __future__ import annotations

import math

from backend.app.models import AgentEventModel, AgentRunModel, SessionLocal


def collect_run_metrics(run_id: str, user_id: str) -> dict:
    """计算耗时、上下文估算 Token、工具调用、证据和答案长度。"""
    session = SessionLocal()
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
        for event in events:
            if event.event_type == "context.ready":
                context_chars = int((event.payload or {}).get("characters") or 0)
            elif event.event_type == "tool.requested":
                tool_calls += 1
            elif event.event_type == "run.completed":
                evidence_count = len((event.payload or {}).get("evidence") or [])
        duration_ms = None
        if run.created_at and run.updated_at:
            duration_ms = max(0, round((run.updated_at - run.created_at).total_seconds() * 1000, 2))
        answer_chars = len(run.answer or "")
        return {
            "duration_ms": duration_ms,
            "context_characters": context_chars,
            "estimated_input_tokens": math.ceil(context_chars / 4),
            "answer_characters": answer_chars,
            "estimated_output_tokens": math.ceil(answer_chars / 4),
            "tool_calls": tool_calls,
            "evidence_count": evidence_count,
            "provider": run.provider,
            "model": run.model,
        }
    finally:
        session.close()
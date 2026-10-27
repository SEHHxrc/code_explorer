"""从智能体持久化事件计算与供应商无关的实验指标。"""

from __future__ import annotations

import math
from collections import Counter
from typing import Callable

from sqlalchemy.orm import Session

from backend.app.models import AgentEventModel, AgentRunModel, ExperimentAuditSessionModel, SessionLocal


def collect_run_metrics(run_id: str, user_id: str, *, session_factory: Callable[[], Session] = SessionLocal) -> dict:
    """读取指定用户运行事件，返回真实/估算用量、终止质量、时延及证据指标。"""
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
        evidence_keys: set[tuple] = set()
        initial_evidence_count = 0
        tool_evidence: set[tuple] = set()
        request_chars = 0
        response_chars = 0
        output_estimates = []
        started_at = None
        finished_at = None
        metadata_rows = []
        completion = {}
        windows = []
        observation_window_version = None
        input_estimates = []
        budget_configurations = []
        batch_reports = []
        pending_batch = None
        citation_check = None
        audit_summary = {"status": "unavailable"}
        contract_hash = None
        execution_policy = None
        experiment_protocol = None
        last_model_payload = {}
        findings_ledger = None
        format_retries = 0
        format_rejections = []
        format_skips = []
        phase_requests: Counter[str] = Counter()
        for event in events:
            payload = event.payload or {}
            if event.event_type == "model.started" and payload.get("phase"):
                phase_requests[payload["phase"]] += 1
            if event.event_type == "report.format_retry_started":
                format_retries += 1
            elif event.event_type == "report.format_rejected":
                format_rejections.append({"step": payload.get("step"), "category": payload.get("category")})
            elif event.event_type == "report.format_retry_skipped":
                format_skips.append({"step": payload.get("step"), "reason": payload.get("reason")})
            if event.event_type == "findings.recorded":
                findings_ledger = payload
            if event.event_type == "batch.completed":
                batch_reports.append({key: payload[key] for key in ("batch_id", "report", "status", "semantic_coverage") if key in payload})
                pending_batch = None
            elif event.event_type == "batch.pending":
                pending_batch = {"batch_id": payload.get("batch_id"), "status": "unreported"}
            if event.event_type == "citations.checked":
                citation_check = payload
            elif event.event_type == "audit.completed":
                audit_summary = payload
            # 失败/取消的运行同样可能已有证据；读取原事件，不回写历史记录。
            result = payload.get("result")
            candidates = list(payload.get("evidence") or [])
            if isinstance(result, dict):
                candidates.extend(result.get("evidence") or [])
            for evidence in candidates:
                if isinstance(evidence, dict) and evidence.get("path"):
                    evidence_keys.add((evidence.get("path"), evidence.get("line"), evidence.get("symbol")))
            if event.event_type == "context.ready":
                context_chars = int((event.payload or {}).get("characters") or 0)
                initial_evidence_count = len((event.payload or {}).get("evidence") or [])
            elif event.event_type == "tool.requested":
                tool_calls += 1
            elif event.event_type == "model.started":
                request_chars += int((event.payload or {}).get("request_chars") or 0)
                payload = event.payload or {}
                if isinstance(payload.get("input_token_estimate"), dict):
                    input_estimates.append(payload["input_token_estimate"])
                    budget_configurations.append({key: payload.get(key) for key in (
                        "input_token_budget", "max_output_tokens", "context_window_tokens", "capacity_source",
                        "max_context_chars", "safety_margin_tokens", "budget_version", "output_token_parameter", "reasoning_effort",
                        "output_limit_mode", "output_reserve_tokens", "output_reserve_basis",
                    )} | {"token_count_method": payload["input_token_estimate"].get("method")})
            elif event.event_type == "model.completed":
                last_model_payload = payload
                response_chars += int((event.payload or {}).get("response_chars") or 0)
                if isinstance(payload.get("response_utf8_bytes"), int):
                    output_estimates.append(payload["response_utf8_bytes"])
                metadata_rows.append((event.payload or {}).get("metadata") or {})
            elif event.event_type == "context.window":
                windows.append(event.payload or {})
            elif event.event_type == "tool.completed":
                result = (event.payload or {}).get("result")
                if isinstance(result, dict):
                    for evidence in result.get("evidence") or []:
                        if isinstance(evidence, dict):
                            tool_evidence.add((evidence.get("path"), evidence.get("line"), evidence.get("symbol")))
            elif event.event_type == "run.started":
                contract_hash = payload.get("contract_hash")
                execution_policy = payload.get("execution_policy")
                experiment_protocol = payload.get("experiment_protocol")
                started_at = event.created_at
                observation_window_version = (event.payload or {}).get("observation_window_version")
            elif event.event_type in {"run.completed", "run.failed"}:
                completion = event.payload or {}
            if event.event_type in {"run.completed", "run.failed", "run.cancelled"}:
                finished_at = event.created_at
        duration_ms = None
        queue_duration_ms = None
        if started_at and finished_at:
            duration_ms = max(0, round((finished_at - started_at).total_seconds() * 1000, 2))
        if started_at and run.created_at:
            queue_duration_ms = max(0, round((started_at - run.created_at).total_seconds() * 1000, 2))
        # 保留原事件，只覆盖实时存储状态，避免过期正文仍被显示为“已保存”。
        audit_owner = session.query(ExperimentAuditSessionModel).filter_by(run_id=run_id, user_id=user_id).first()
        if audit_owner is not None:
            audit_summary = {**audit_summary, "status": audit_owner.status,
                             "payload_bytes": audit_owner.payload_bytes, "protected": audit_owner.protected}
        answer_chars = len(run.answer or "")
        model_requests = sum(event.event_type == "model.started" for event in events)
        usage_rows = [metadata.get("usage") or {} for metadata in metadata_rows]
        measured = sum(
            usage.get("input_tokens") is not None and usage.get("output_tokens") is not None
            for usage in usage_rows
        )
        usage_status = "complete" if model_requests and measured == model_requests else (
            "partial" if any(any(value is not None for value in usage.values()) for usage in usage_rows) else "unavailable"
        )
        # 逻辑轮次成功不代表重试尝试已报告用量；只读派生，不回写旧事件或补计费。
        http_attempts = audit_summary.get("actual_requests")
        unreported_http_attempts = max(0, http_attempts - measured) if isinstance(http_attempts, int) else None
        if unreported_http_attempts and usage_status == "complete":
            usage_status = "partial"
        # 只消费本次已冻结的声明与实测审计，不用当前环境回填旧记录或改写供应商 usage。
        network = audit_summary.get("network_accounting") or {}
        zero_attempts = network.get("assumed_zero_token_attempts", 0)
        known_attempts = measured + zero_attempts
        accounted_usage_status = ("complete_with_declared_zero_rejections" if zero_attempts else "complete") if (
            isinstance(http_attempts, int) and http_attempts > 0 and known_attempts == http_attempts and measured == len(metadata_rows)
        ) else usage_status
        excluded_ms = adjusted_ms = None
        if network.get("policy") and duration_ms is not None:
            excluded_ms = round(network.get("excluded_request_ms", 0) + network.get("excluded_retry_wait_ms", 0), 2)
            if not network.get("unknown_rejection_timings") and 0 <= excluded_ms <= duration_ms:
                adjusted_ms = round(duration_ms - excluded_ms, 2)
        accounted_tokens = None
        if accounted_usage_status in {"complete", "complete_with_declared_zero_rejections"} and network.get("policy"):
            accounted_tokens = sum(usage.get("total_tokens") if usage.get("total_tokens") is not None else
                                   usage["input_tokens"] + usage["output_tokens"] for usage in usage_rows)

        def usage_total(key: str) -> int | None:
            """累计供应商确实报告的字段；未知值不当作零，不与字符估算混加。"""
            values = [usage[key] for usage in usage_rows if usage.get(key) is not None]
            return sum(values) if values else None

        # 只读派生诊断：旧版将空工具结束标记误归为实际调用时，不回写原错误或供应商状态。
        failure_diagnosis = None
        if run.status == "failed" and last_model_payload.get("returned_tool_calls") == 0 and (
            (last_model_payload.get("metadata") or {}).get("finish_reason") in {"tool_calls", "function_call"}
        ):
            failure_diagnosis = {"code": "tool_finish_without_payload", "source": "derived_from_original_events",
                "message": "本轮实际工具调用载荷为 0，但供应商结束原因为工具调用；不能据此认定再次请求了工具。原错误与结束状态保持不变。"}
        return {
            "failure_diagnosis": failure_diagnosis,
            "citation_check": citation_check,
            "audit": audit_summary,
            "contract_hash": contract_hash,
            "execution_policy": execution_policy,
            "experiment_protocol": experiment_protocol,
            "output_diagnostics": completion.get("output_diagnostics"),
            "max_request_input_estimate": max((item["tokens"] for item in input_estimates), default=None),
            "duration_ms": duration_ms,
            "adjusted_duration_ms": adjusted_ms,
            "excluded_network_ms": excluded_ms,
            "network_accounting": network or None,
            "queue_duration_ms": queue_duration_ms,
            "context_characters": context_chars,
            "request_characters": request_chars,
            "estimated_input_tokens": sum(item["tokens"] for item in input_estimates) if input_estimates else math.ceil(request_chars / 4),
            "answer_characters": answer_chars,
            "response_characters": response_chars,
            "estimated_output_tokens": (sum(output_estimates) if len(output_estimates) == len(metadata_rows)
                                        else math.ceil(response_chars / 4)) if metadata_rows else None,
            "output_estimation_method": ("utf8_bytes_estimate" if len(output_estimates) == len(metadata_rows)
                                         else "legacy_characters_divided_by_four") if metadata_rows else None,
            "token_measurement": "local_request_estimate_not_provider_usage" if input_estimates else "normalized_characters_divided_by_four_not_provider_usage",
            "input_estimate_status": "complete" if len(input_estimates) == model_requests and model_requests else "partial" if input_estimates else "legacy_approximation",
            "input_estimation_methods": sorted({item.get("method", "unknown") for item in input_estimates}),
            "budget_configurations": budget_configurations[:1],
            "budget_changed_during_run": any(item != budget_configurations[0] for item in budget_configurations[1:]),
            "usage_status": usage_status,
            "accounted_usage_status": accounted_usage_status,
            "accounted_model_total_tokens": accounted_tokens,
            "assumed_zero_token_attempts": zero_attempts,
            "unknown_cost_http_attempts": max(0, unreported_http_attempts - zero_attempts) if unreported_http_attempts is not None else None,
            "usage_accounting_scope": "reported_responses_not_provider_invoice",
            "http_attempts": http_attempts,
            "unreported_http_attempts": unreported_http_attempts,
            "usage_measured_requests": measured,
            "actual_input_tokens": usage_total("input_tokens"),
            "actual_output_tokens": usage_total("output_tokens"),
            "actual_total_tokens": usage_total("total_tokens"),
            "cached_input_tokens": usage_total("cached_input_tokens"),
            "reasoning_tokens": usage_total("reasoning_tokens"),
            "finish_reasons": [metadata.get("incomplete_reason") or metadata.get("finish_reason") or metadata.get("response_status") or "unknown" for metadata in metadata_rows],
            "termination_reason": completion.get("termination_reason") or (run.status if run.status != "completed" else "unknown"),
            "answer_completeness": completion.get("answer_completeness") or {"status": "unknown", "semantic_coverage": "not_proven"},
            "observation_compactions": sum(int(window.get("compacted_observations") or 0) for window in windows),
            "observation_window_version": observation_window_version,
            "max_omitted_observations": max((int(window.get("omitted_observations") or 0) for window in windows), default=0),
            "actual_models": sorted({str((event.payload or {}).get("actual_model")) for event in events if event.event_type == "model.completed" and (event.payload or {}).get("actual_model")}),
            "response_measurement_available": any(event.event_type == "model.completed" for event in events),
            "tool_calls": tool_calls,
            "model_requests": model_requests,
            "phase_requests": dict(phase_requests),
            "phase_request_semantics": "application_started_not_http_attempts_or_provider_execution",
            "batch_reports": batch_reports,
            "findings_ledger": findings_ledger,
            "report_format": {"retries": format_retries, "rejections": format_rejections, "skipped": format_skips},
            "pending_batch": pending_batch,
            "evidence_count": len(evidence_keys),
            "initial_evidence_count": initial_evidence_count,
            "tool_evidence_count": len(tool_evidence),
            "provider": run.provider,
            "model": run.model,
        }
    finally:
        session.close()

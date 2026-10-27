"""独立 API 知识 Agent：有界提出/校验建议，复用模型传输，不占代码调查轮次。"""

from __future__ import annotations

import time
import uuid
import logging
from dataclasses import asdict
from typing import Any

from backend.app.llm.api_knowledge import LlmApiKnowledgeProvider
from backend.app.llm.base import ModelProvider
from backend.app.llm.budget import ModelBudgetError
from backend.app.llm.http import ModelRequestError, RETRY_POLICY_VERSION
from backend.app.llm.observation import observe_requests
from backend.app.llm.response_metadata import response_metadata
from backend.app.services.security_analysis.api_contracts import ApiKnowledgeRequest

API_AGENT_VERSION = "api-knowledge-agent-v1"
logger = logging.getLogger(__name__)


class _KnowledgeTransport:
    """隔离父分析的传输观察器，只保存哈希、用量与安全诊断，不保存认证或正文。"""

    def __init__(self) -> None:
        """每个知识任务建立独立尝试与退避记录。"""
        self.attempts: list[dict[str, Any]] = []
        self.waits: list[dict[str, Any]] = []

    def sent(self, payload: dict[str, Any], attempt: int, *, wire_sha256: str = "", wire_bytes: int = 0) -> int:
        """记录实际 HTTP 尝试；请求正文只在传输层使用，不进入知识任务结果。"""
        self.attempts.append({"attempt": attempt, "wire_sha256": wire_sha256, "wire_bytes": wire_bytes,
                              "status": "sent", "responses_api": "messages" not in payload})
        return len(self.attempts) - 1

    def received(self, request_id: int, payload: dict[str, Any], *, diagnostics: dict[str, Any] | None = None) -> None:
        """保留供应商真实用量与终止原因，不推算缺失 Token。"""
        item = self.attempts[request_id]
        item.update(status="received", metadata=asdict(response_metadata(payload, responses_api=item["responses_api"])))

    def failed(self, request_id: int, error_type: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        """失败只保留类型和 HTTP 状态，未知用量不能按零计。"""
        self.attempts[request_id].update(status="failed", error_type=error_type,
                                         status_code=(diagnostics or {}).get("status_code"))

    def retry_wait(self, attempt: int, requested_seconds: float, elapsed_seconds: float, *,
                   status_code: int | None, completed: bool) -> None:
        """记录共享传输层实际执行的退避，不在知识 Agent 重实现重试算法。"""
        self.waits.append({"attempt": attempt, "requested_seconds": requested_seconds, "elapsed_seconds": elapsed_seconds,
                           "status_code": status_code, "completed": completed})


class ApiKnowledgeAgent:
    """显式知识任务，有自己的模型请求上限；无源码/执行工具，不注册静态净化规则。"""

    def __init__(self, provider: ModelProvider, *, max_model_requests: int = 2) -> None:
        """注入同一传输基础设施的供应商；额度每次 run 独立，不引用主分析 max_steps。"""
        if not 1 <= max_model_requests <= 4:
            raise ValueError("知识 Agent 模型请求上限必须为 1～4")
        self.provider = LlmApiKnowledgeProvider(provider)
        self.max_model_requests = max_model_requests

    async def run(self, request: ApiKnowledgeRequest) -> dict[str, Any]:
        """返回格式化建议、全部逻辑请求与传输尝试；只对格式失败作有界重新提交。"""
        started, task_id = time.monotonic(), uuid.uuid4().hex
        observer, attempts = _KnowledgeTransport(), []
        proposal = None
        status, feedback = "unresolved", ""
        with observe_requests(observer):
            for ordinal in range(1, self.max_model_requests + 1):
                try:
                    advice = await self.provider.describe(request, feedback=feedback)
                except (ModelRequestError, ModelBudgetError) as exc:
                    attempts.append({"ordinal": ordinal, "error_code": type(exc).__name__,
                                     "status_code": getattr(exc, "status_code", None), "metadata": None})
                    status = "failed"
                    break  # 网络重传由 Provider/http 负责，避免两层叠加放大请求。
                attempts.append({"ordinal": ordinal, **asdict(advice),
                                 "proposal": advice.proposal.model_dump(mode="json") if advice.proposal else None})
                if advice.proposal is not None:
                    proposal = advice.proposal.model_dump(mode="json")
                    status = "proposed" if advice.proposal.returns else "unresolved"
                    break
                if advice.error_code != "invalid_api_advice_schema_or_slots":
                    break  # 不重放空白、截断、拒答或错误的工具调用。
                feedback = "上一轮未通过 JSON/重复槽位/已知签名范围校验；仅重新提交符合 schema 的建议，不增加项目事实。"
        metadata = [item.get("metadata") for item in observer.attempts] if observer.attempts else [item["metadata"] for item in attempts]
        totals = {field: sum(item["usage"][field] for item in metadata)
                  if metadata and all(item and item["usage"].get(field) is not None for item in metadata) else None
                  for field in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens")}
        logger.info("api_knowledge.completed", extra={"task_id": task_id, "stage": status,
                                                     "count": len(attempts), "attempt": len(observer.attempts)})
        return {"id": task_id, "version": API_AGENT_VERSION, "status": status,
                "api": request.model_dump(mode="json"), "provenance": "llm_proposal",
                "validation_scope": "schema_and_slot_bounds_only", "proposal": proposal,
                "attempts": attempts, "model_requests": len(attempts), "max_model_requests": self.max_model_requests,
                "transport_attempts": observer.attempts, "retry_waits": observer.waits,
                "retry_policy": RETRY_POLICY_VERSION, "usage": totals,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "limitation": "仅为第三方 API 待核验知识，不是源码事实、净化证明或可执行模型；不消耗主分析调查轮次。"}

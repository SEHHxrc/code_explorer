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
from backend.app.llm.observation import MemoryWireObserver, observe_requests
from backend.app.services.security_analysis.api_contracts import ApiKnowledgeRequest

API_AGENT_VERSION = "api-knowledge-agent-v1"
logger = logging.getLogger(__name__)


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
        observer, attempts = MemoryWireObserver(), []
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
        totals = observer.usage([item["metadata"] for item in attempts])
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

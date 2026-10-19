from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, TYPE_CHECKING

from backend.app.agents.batching import AnalysisBatches
from backend.app.agents.context_builder import ContextBuilder, ProjectContextBuilder
from backend.app.agents.contracts import AgentEvidence, AgentRunRequest, ContextPacket
from backend.app.agents.run_store import AgentRunStore
from backend.app.agents.tools import create_project_tool_registry
from backend.app.agents.tools.base import ToolContext, ToolRegistry
from backend.app.llm.http import ModelEndpointError, ModelRequestError
from backend.app.llm.registry import ModelLimits, create_model_provider, get_model_limits
from backend.app.llm.response_metadata import ModelResponseMetadata
from backend.app.llm.base import ModelOutputError, ModelTurn
from backend.app.agents.conversation_window import (
    CONVERSATION_VERSION, ToolRound, assistant_message, render_conversation_window, round_messages,
)
from backend.app.llm.budget import BudgetPlanner, ModelBudgetError, initial_message, request_payload
from backend.app.llm.conversation import ModelMessage
from backend.app.experiments.report import REPORT_VERSION, assess_answer, BATCH_PROTOCOL, CUMULATIVE_PROTOCOL, FULL_SOURCE_PROTOCOL
from backend.app.experiments.findings import parse_ledger
from backend.app.agents.instructions import AGENT_INSTRUCTIONS
from backend.app.llm.observation import observe_requests
from backend.app.experiments.audit import AuditError
from backend.app.experiments.audit.policy import contract_digest
from backend.app.agents.reporting import execution_policy, phase_message
from backend.app.llm.settings import get_final_report_mode
from backend.app.core.logging import log_context

if TYPE_CHECKING:
    from backend.app.experiments.audit.recorder import AuditRecorder

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class AgentRunManager:
    """进程内智能体运行编排器。

    输入运行、项目、分析产物和请求后创建后台任务；输出通过 ``AgentRunStore``
    持久化为状态与有序事件。该类不提供 Shell、Docker 或写文件能力。
    """

    def __init__(
        self,
        store: AgentRunStore | None = None,
        *,
        context_builder: ContextBuilder | None = None,
        tools: ToolRegistry | None = None,
        instructions: str | None = None,
    ) -> None:
        """注入上下文、工具和指令策略；默认提供只读项目上下文与静态安全证据。"""
        self.store = store or AgentRunStore()
        self.context_builder = context_builder or ProjectContextBuilder()
        self.tools = tools or create_project_tool_registry()
        self.instructions = instructions or AGENT_INSTRUCTIONS
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self.audit: AuditRecorder | None = None
        self.audit_artifact: dict[str, Any] = {}
        self._audit_finished = False

    def start(
        self,
        *,
        run_id: str,
        project_id: str,
        user_id: str,
        project_root: str,
        artifact: dict[str, Any],
        request: AgentRunRequest,
    ) -> None:
        """启动一次后台运行。

        输入运行/项目/用户标识、项目根目录、分析产物和请求；无直接返回值，进度
        通过存储事件读取。
        """
        task = asyncio.create_task(self._run(
            run_id=run_id,
            project_id=project_id,
            user_id=user_id,
            project_root=project_root,
            artifact=artifact,
            request=request,
        ))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))

    def cancel(self, run_id: str) -> bool:
        """取消仍在本进程执行的任务；成功发出取消时输出 ``True``。"""
        task = self._tasks.get(run_id)
        if not task or task.done():
            return False
        task.cancel()
        return True

    async def _run(
        self,
        *,
        run_id: str,
        project_id: str,
        user_id: str,
        project_root: str,
        artifact: dict[str, Any],
        request: AgentRunRequest,
        claimed: bool = False,
    ) -> None:
        """执行模型—工具循环并写入全部生命周期事件。

        输入已验证的运行上下文；无返回值。模型不可用时输出确定性静态答案，异常
        则转换为 ``failed`` 状态，取消则转换为 ``cancelled`` 状态。
        """
        tool_context: ToolContext | None = None
        batch_mode = False
        try:
            logger.info("agent.run_started", extra={"run_id": run_id, "project_id": project_id})
            final_metadata = ModelResponseMetadata()
            last_turn: ModelTurn | None = None
            final_mode = get_final_report_mode()
            batch_mode = artifact.get("_experiment_protocol") == BATCH_PROTOCOL
            full_source = artifact.get("_experiment_protocol") == FULL_SOURCE_PROTOCOL
            cumulative = artifact.get("_experiment_protocol") == CUMULATIVE_PROTOCOL
            if (full_source or cumulative) and self.audit is None:
                raise AuditError("完整源码实验必须启用审计，不能跳过输入快照验证。")
            policy = execution_policy(request.max_steps, final_mode, batch_mode=batch_mode, full_source=full_source, model=request.model)
            if not artifact.get("_experiment_protocol"):
                policy.pop("findings_ledger", None)  # 普通项目问答仍是自由文本，不声明已经采用实验台账。
            if cumulative:
                from backend.app.experiments.cumulative import policy as cumulative_policy
                policy = cumulative_policy(request.max_steps, final_mode, artifact["_cumulative_input"]["budget_bytes"],
                                           artifact["_cumulative_input"].get("selection_version"), model=request.model)
            if not claimed:
                self.store.update(run_id, status="running")
            require_report = bool(artifact.get("_experiment_protocol"))
            self._emit(run_id, "run.started", {
                "project_id": project_id, "instrumentation_version": "2.0",
                "observation_window_version": CONVERSATION_VERSION,
                "report_version": REPORT_VERSION if require_report else None,
                "execution_policy": policy, "experiment_protocol": artifact.get("_experiment_protocol"),
                "contract_hash": contract_digest(self.instructions, self.tools.schemas(), REPORT_VERSION, policy) if require_report else None,
            })
            provider = create_model_provider(request.model) if request.use_model else None
            if provider is None and artifact.get("_experiment_protocol"):
                raise RuntimeError("Security experiment requires a configured model; static fallback is not a valid trial")
            packet = self.context_builder.build(
                project_id=project_id,
                question=request.question,
                artifact=artifact,
                model=request.model,
                budgeted=provider is not None,
            )
            self._emit(run_id, "context.ready", {
                "project_name": packet.project_name,
                "characters": len(packet.prompt_context),
                "evidence": [item.model_dump() for item in packet.evidence],
            })

            if provider is None:
                answer = self._static_answer(request.question, artifact)
                await self._complete(run_id, answer, provider=None, model=None, evidence=packet.evidence)
                return

            self.store.update(run_id, provider=provider.name, model=provider.model)
            tool_context = ToolContext(
                project_id=project_id,
                user_id=user_id,
                project_root=Path(project_root).resolve(),
                artifact=artifact,
            )
            tool_schemas = self.tools.schemas()
            if self.audit is not None:
                await asyncio.to_thread(self.audit.start, tool_context.project_root, self.audit_artifact,
                                        self.instructions, tool_schemas, REPORT_VERSION, policy)
                # 轻量历史条件与完整审计分离；后者过期不会删除新试次的共同提示词，无源码/工具结果。
                self._emit(run_id, "experiment.snapshot", self.audit.prompt_snapshot)
                frozen_input = artifact.get("_full_source_input") if full_source else artifact.get("_cumulative_input") if cumulative else None
                if frozen_input is not None and any(self.audit.manifest.get(path) != expected for path, expected in
                                       frozen_input["source_manifest"].items()):
                    raise AuditError("源码已偏离创建配对时的冻结范围；未发送模型请求。")
            frozen_limits = getattr(provider, "model_limits", None)
            limits = frozen_limits if isinstance(frozen_limits, ModelLimits) else get_model_limits(request.model or provider.model)
            planner = BudgetPlanner(limits, model=provider.model)
            initial = initial_message(request.question, packet.prompt_context)
            messages = [initial]
            rounds: list[ToolRound] = []
            evidence = list(packet.evidence)
            answer = ""
            final_metadata = ModelResponseMetadata()
            termination_reason = "model_final_answer"
            for step in range(1, request.max_steps + 1):
                stage = phase_message(remaining_rounds=0, final=True) if full_source else phase_message(remaining_rounds=request.max_steps - step + 1)
                tails = [stage, tool_context.batches.message()] if batch_mode else [stage]
                if rounds:
                    messages, window = render_conversation_window(
                        rounds, initial=initial, instructions=self.instructions, tools=tool_schemas,
                        planner=planner, provider=provider, tail_messages=tails,
                    )
                    self._emit(run_id, "context.window", {"step": step, **window})
                else:
                    messages = [initial, *tails]
                payload = request_payload(provider, instructions=self.instructions, messages=messages,
                                          tools=tool_schemas, model=provider.model,
                                          max_output_tokens=limits.max_output_tokens)
                budget = planner.require(payload)
                self._emit(run_id, "model.started", {
                    "step": step,
                    "phase": "final_report" if full_source else "investigation", "remaining_tool_rounds": 0 if full_source else request.max_steps - step + 1,
                    "prompt_chars": sum(len(message.get("content") or "") for message in messages),
                    "tool_count": len(tool_schemas),
                    "request_chars": len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
                    **budget,
                })
                turn = await self._generate(provider, payload, step,
                    instructions=self.instructions,
                    messages=messages,
                    tools=tool_schemas,
                )
                last_turn = turn
                final_metadata = turn.metadata
                if batch_mode:
                    tool_context.batches.metadata = turn.metadata
                    tool_context.batches.observe(messages)
                response_text = turn.text + (json.dumps([
                    {"name": call.name, "arguments": call.arguments} for call in turn.tool_calls
                ], ensure_ascii=False, separators=(",", ":")) if turn.tool_calls else "")
                self._emit(run_id, "model.completed", {
                    "step": step,
                    "response_chars": len(response_text),
                    "response_utf8_bytes": len(response_text.encode("utf-8")),
                    "metadata": asdict(turn.metadata), "actual_model": turn.model,
                })
                if full_source:
                    answer = self._accept_answer(run_id, turn, forbid_tools=True, require_report=True)
                    break  # 一次报告；空白、工具载荷或不完整结果都不自动重试生成/切换模式。
                if not turn.tool_calls:
                    final_metadata = turn.metadata
                    if batch_mode and (tool_context.batches.active_ids or tool_context.batches.scopes):
                        report = self._require_answer(turn)
                        self._publish_batch(run_id, tool_context, report)
                        rounds = []
                        initial = self._batch_initial(request, packet, tool_context)
                        continue
                    if batch_mode and tool_context.batches.reports:
                        break  # 已有批次必须进入共同的汇总阶段，不能把调查阶段文本当最终答案。
                    answer = self._accept_answer(run_id, turn, require_report=require_report)
                    break

                observations = []
                batches_before = len(tool_context.batches.reports)
                for call in turn.tool_calls:
                    self._emit(run_id, "tool.requested", {
                        "step": step, "call_id": call.id, "name": call.name,
                        "arguments": call.arguments,
                    })
                    try:
                        if call.name == "submit_analysis_batch" and len(turn.tool_calls) != 1:
                            raise ValueError("提交批次报告必须单独调用；其他工具结果尚未展示，不能在同轮提交。")
                        result = await self.tools.execute(call.name, tool_context, call.arguments)
                        evidence.extend(result.evidence)
                        observation = result.model_dump()
                        observations.append({"call_id": call.id, "name": call.name,
                                             "arguments": call.arguments, "result": observation})
                        self._emit(run_id, "tool.completed", {
                            "step": step, "call_id": call.id, "name": call.name,
                            "result": observation,
                            "evidence": [item.model_dump() for item in result.evidence],
                        })
                        if batch_mode and call.name == "get_static_security_evidence":
                            self._emit(run_id, "batch.selected", {"batch_id": len(tool_context.batches.reports) + 1,
                                "evidence_ids": sorted(tool_context.batches.active_ids), "status": "selected"})
                    except Exception as exc:
                        public_error = str(exc) if isinstance(exc, ValueError) else "Tool execution failed safely."
                        error = {"error": public_error, "type": type(exc).__name__}
                        observations.append({"call_id": call.id, "name": call.name,
                                             "arguments": call.arguments, "result": error})
                        self._emit(run_id, "tool.failed", {
                            "step": step, "call_id": call.id, "name": call.name,
                            "error": public_error,
                        })
                rounds.append(ToolRound(assistant_message(turn), observations))
                if batch_mode and len(tool_context.batches.reports) > batches_before:
                    self._emit(run_id, "batch.completed", {**tool_context.batches.reports[-1], "metadata": asdict(turn.metadata)})
                    rounds = []
                    initial = self._batch_initial(request, packet, tool_context)

            if not answer:
                termination_reason = "tool_step_budget_exhausted"
                pending_scopes = False
                if batch_mode and rounds:
                    # 最后一轮工具结果尚未发送；只预检是否需要收尾，不能提前记为已展示。
                    pending = AnalysisBatches(completed_scopes=tool_context.batches.completed_scopes)
                    pending.observe(round_messages(rounds[-1]))
                    pending_scopes = bool(pending.scopes)
                if batch_mode and (tool_context.batches.active_ids or tool_context.batches.scopes or pending_scopes):
                    batch_turn = await self._report_turn(run_id, provider, planner, initial, rounds, tool_schemas,
                        request.max_steps + 1, final_mode, [tool_context.batches.message()], phase="batch_report", batch_context=tool_context)
                    last_turn, final_metadata = batch_turn, batch_turn.metadata
                    tool_context.batches.metadata = batch_turn.metadata
                    report = self._require_answer(batch_turn, forbid_tools=True)
                    self._publish_batch(run_id, tool_context, report)
                    rounds = []
                    initial = self._batch_initial(request, packet, tool_context)
                if batch_mode:
                    final = await self._report_turn(run_id, provider, planner, initial,
                        rounds, tool_schemas, request.max_steps + 2, final_mode,
                        [tool_context.batches.message()], phase="final_report", batch_context=tool_context)
                    last_turn, final_metadata = final, final.metadata
                    answer = self._accept_answer(run_id, final, forbid_tools=True, require_report=True)
                else:
                    final = await self._report_turn(run_id, provider, planner, initial, rounds, tool_schemas,
                        request.max_steps + 1, final_mode, [], phase="final_report")
                    last_turn, final_metadata = final, final.metadata
                    answer = self._accept_answer(run_id, final, forbid_tools=True, require_report=require_report)
            await self._complete(
                run_id, answer, provider=provider.name, model=provider.model,
                evidence=self._dedupe_evidence(evidence),
                metadata=final_metadata, require_report=require_report,
                termination_reason=termination_reason,
            )
        except asyncio.CancelledError:
            logger.info("agent.run_cancelled", extra={"run_id": run_id})
            self.store.update(run_id, status="cancelled")
            self._emit(run_id, "run.cancelled", {})
        except ModelRequestError as exc:
            self.store.update(run_id, status="failed", error=exc.public_message)
            payload: dict[str, Any] = {
                "error": exc.public_message,
                "error_type": type(exc).__name__,
                "retryable": exc.retryable,
            }
            if isinstance(exc, ModelEndpointError):
                payload.update({
                    "status_code": exc.status_code,
                    "error_code": exc.error_code or f"http_{exc.status_code}",
                    "retry_after": exc.retry_after,
                    "request_id": exc.request_id,
                })
            self._emit(run_id, "run.failed", payload)
            logger.warning(
                "Agent model request failed: run=%s type=%s status=%s code=%s request_id=%s",
                run_id,
                type(exc).__name__,
                getattr(exc, "status_code", None),
                getattr(exc, "error_code", None),
                getattr(exc, "request_id", None),
            )
        except AuditError as exc:
            logger.warning("agent.audit_failed", extra={"run_id": run_id, "error_type": "AuditError"})
            self.store.update(run_id, status="failed", error=str(exc))
            self._emit(run_id, "run.failed", {"error": str(exc), "error_type": "AuditError",
                                              "error_code": "audit_incomplete", "retryable": False})
        except ModelOutputError as exc:
            self.store.update(run_id, status="failed", error=exc.public_message)
            self._emit(run_id, "run.failed", {
                "error": exc.public_message, "error_type": "ModelOutputError", "error_code": exc.code,
                "retryable": False, "termination_reason": exc.code,
                "final_response_metadata": asdict(final_metadata),
                "output_diagnostics": {"returned_tool_calls": len(last_turn.tool_calls) if last_turn else 0,
                    "has_visible_text": bool(last_turn and last_turn.text.strip()), "final_report_mode": final_mode,
                    "report_contract_complete": assess_answer(last_turn.text, final_metadata, require_report=False).get("report_complete") if last_turn else None,
                    "findings_contract_rejected": exc.code == "invalid_findings_ledger"},
                "answer_completeness": {"status": "incomplete", "semantic_coverage": "not_proven"},
            })
            logger.warning("Agent model output rejected: run=%s code=%s finish_reason=%s", run_id, exc.code, final_metadata.finish_reason)
        except ModelBudgetError:
            logger.warning("agent.input_budget_exceeded", extra={"run_id": run_id})
            message = "应用输入预算不足以容纳完整证据或工具会话；请提高应用预算或缩小读取范围。这不是模型平台容量判定。"
            self.store.update(run_id, status="failed", error=message)
            self._emit(run_id, "run.failed", {"error": message, "error_type": "ModelBudgetError",
                                              "error_code": "application_budget_exceeded", "retryable": False})
        except Exception as exc:
            public_error = "Agent run failed safely."
            self.store.update(run_id, status="failed", error=public_error)
            self._emit(run_id, "run.failed", {
                "error": public_error,
                "error_type": type(exc).__name__,
            })
            logger.exception("Agent run failed: %s", run_id)
        finally:
            if batch_mode and tool_context is not None and (tool_context.batches.active_ids or tool_context.batches.scopes):
                self._emit(run_id, "batch.pending", {"batch_id": len(tool_context.batches.reports) + 1,
                    "evidence_ids": sorted(tool_context.batches.active_ids), "scopes": sorted(tool_context.batches.scopes),
                    "status": "unreported", "semantic_coverage": "not_proven"})
            if self.audit is not None and not self._audit_finished:
                try:
                    summary = await asyncio.to_thread(self.audit.finish)
                    self._emit(run_id, "audit.completed", summary)
                    self._audit_finished = True
                except Exception:
                    logger.warning("agent.audit_finalize_failed", extra={"run_id": run_id}, exc_info=True)
                    self._emit(run_id, "audit.completed", {"status": "incomplete", "version": "experiment-audit-v1"})

    @staticmethod
    def _batch_initial(request: AgentRunRequest, packet: ContextPacket, context: ToolContext) -> ModelMessage:
        """每批更新初始目录以排除已报告 ID；控制组不构造任何静态索引。"""
        prompt = packet.prompt_context
        if "security_evidence" in context.artifact:
            prefix = "STATIC_SECURITY_EVIDENCE\n"
            ceiling = min(8000, len(packet.prompt_context) - len(prefix))
            prompt = prefix + context.evidence_index.security.summary(excluded=context.batches.completed_ids, max_chars=ceiling)
        return initial_message(request.question, prompt)

    def _publish_batch(self, run_id: str, context: ToolContext, report: str) -> None:
        """将原始台账响应发布为本批报告；未完成/未展示不能推进已分析集合。"""
        try:
            batch = context.batches.publish(report, sorted(context.batches.active_ids), sorted(context.batches.scopes))
        except ValueError as exc:
            raise ModelOutputError("incomplete_batch_report", str(exc)) from exc
        self._emit(run_id, "batch.completed", {**batch, "metadata": asdict(context.batches.metadata)})

    async def _report_turn(
        self, run_id: str, provider: Any, planner: BudgetPlanner, initial: ModelMessage,
        rounds: list[ToolRound], schemas: list[dict[str, Any]], step: int, final_mode: str,
        tail: list[ModelMessage], *, phase: str, batch_context: ToolContext | None = None,
    ) -> ModelTurn:
        """统一批次收尾与最终汇总传输；冻结报告模式，不修正供应商终止状态。"""
        tools = schemas if final_mode == "tools_none" else []
        stage = phase_message(remaining_rounds=0, final=True)
        if phase == "batch_report":
            stage = ModelMessage(role="user", content="APPLICATION_STAGE\n当前仅输出本批完整发现台账 JSON，不新增调查，不调用工具；不是全项目最终结论。覆盖只限实际检查范围，保留未核实条件，不手写计数或结束标记。")
        archive = []
        ceiling = 12000
        while True:
            if phase == "final_report" and batch_context is not None:
                archive = [batch_context.batches.evidence_message(ceiling)]
            messages, window = render_conversation_window(rounds, initial=initial, instructions=self.instructions,
                tools=tools, planner=planner, provider=provider, tool_choice="none", tail_messages=[*tail, *archive, stage])
            payload = request_payload(provider, instructions=self.instructions, messages=messages, tools=tools,
                tool_choice="none", model=provider.model, max_output_tokens=planner.limits.max_output_tokens)
            if not archive or planner.fits(payload) or ceiling == 0:
                break
            ceiling //= 2
        self._emit(run_id, "context.window", {"step": step, **window})
        budget = planner.require(payload)
        self._emit(run_id, "model.started", {"step": step, "phase": phase, "remaining_tool_rounds": 0,
            "final_report_mode": final_mode, "prompt_chars": sum(len(item.get("content") or "") for item in messages),
            "tool_count": 0, "tool_choice_requested": "none", "tool_schema_count": len(tools),
            "request_chars": len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))), **budget})
        turn = await self._generate(provider, payload, step, instructions=self.instructions, messages=messages,
                                    tools=tools, tool_choice="none")
        if batch_context is not None:
            batch_context.batches.observe(messages)
        self._emit(run_id, "model.completed", {"step": step, "response_chars": len(turn.text),
            "returned_tool_calls": len(turn.tool_calls), "response_utf8_bytes": len(turn.text.encode("utf-8")),
            "metadata": asdict(turn.metadata), "actual_model": turn.model})
        return turn

    async def _generate(self, provider: Any, payload: dict[str, Any], step: int, **kwargs: Any) -> ModelTurn:
        """传输观察只覆盖本次模型调用；普通 Agent 不创建实验审计记录。"""
        if self.audit is not None:
            await asyncio.to_thread(self.audit.planned, payload, step)
        started = time.monotonic()
        with log_context(step=step, provider=provider.name, model=provider.model), observe_requests(self.audit):
            logger.info("agent.model_started")
            turn = await provider.generate_with_tools(**kwargs)
            logger.info("agent.model_completed", extra={"duration_ms": round((time.monotonic() - started) * 1000, 2),
                "finish_reason": turn.metadata.finish_reason or turn.metadata.response_status,
                "input_tokens": turn.metadata.usage.input_tokens, "output_tokens": turn.metadata.usage.output_tokens,
                "tool_calls": len(turn.tool_calls)})
        if self.audit is not None:
            await asyncio.to_thread(self.audit.turn, turn.text,
                                    [{"id": call.id, "name": call.name, "arguments": call.arguments} for call in turn.tool_calls],
                                    asdict(turn.metadata))
        return turn

    @staticmethod
    def _require_answer(turn: ModelTurn, *, forbid_tools: bool = False) -> str:
        """验证可见回答；区分拒答、输出耗尽、违反工具禁用与正常终止却空白。"""
        metadata = turn.metadata
        if forbid_tools and turn.tool_calls:
            raise ModelOutputError("unexpected_tool_calls", "禁用新工具的最终报告阶段仍收到实际工具调用载荷；本次实验无效，请检查端点的工具禁用协议。未执行这些调用。")
        answer = turn.text.strip()
        if not turn.tool_calls and metadata.finish_reason in {"tool_calls", "function_call"}:
            detail = "可见内容为空。" if not answer else "存在可见文本，但不能把该结束状态当作正常报告完成。"
            raise ModelOutputError("tool_finish_without_payload", "供应商报告工具调用结束状态，但未返回可执行的工具调用载荷；" + detail + "本次实验无效，请检查端点响应协议，不应仅增加轮次。")
        if answer:
            return answer  # 完整性和台账格式由实验接受入口校验，普通问答不强制安全报告。
        if metadata.refused:
            raise ModelOutputError("model_refused", "模型拒绝生成本次报告；未返回可见回答，本次实验无效。")
        if metadata.finish_reason == "length" or metadata.incomplete_reason:
            raise ModelOutputError("empty_incomplete_output", "模型在生成可见回答前已提前终止；请检查输出或思考预算。本次实验无效。")
        raise ModelOutputError("empty_model_answer", "模型返回空白回答，不能作为有效安全报告；供应商用量和终止原因已保留。")

    def _accept_answer(self, run_id: str, turn: ModelTurn, *, forbid_tools: bool = False, require_report: bool = False) -> str:
        """保留原响应审计，从完整台账生成报告并持久化；不自动纠正状态或重试生成。"""
        text = self._require_answer(turn, forbid_tools=forbid_tools)
        if not require_report:
            return text
        if not assess_answer(text, turn.metadata, require_report=False)["transport_complete"]:
            raise ModelOutputError("incomplete_findings_ledger", "模型台账未正常完整终止；原始内容、用量和终止状态已保留，未生成有效报告。")
        try:
            ledger = parse_ledger(text)
        except ValueError as exc:
            raise ModelOutputError("invalid_findings_ledger", str(exc)) from exc
        self._emit(run_id, "findings.recorded", {"version": ledger.schema_version, "ledger": ledger.model_dump(),
                                                "semantic_coverage": "not_proven"})
        return ledger.render()

    async def _complete(
        self,
        run_id: str,
        answer: str,
        *,
        provider: str | None,
        model: str | None,
        evidence: list[AgentEvidence],
        metadata: ModelResponseMetadata | None = None,
        require_report: bool = False,
        termination_reason: str = "static_fallback",
    ) -> None:
        """分块发出答案、持久化完成状态并输出去重后的证据。"""
        for index in range(0, len(answer), 240):
            self._emit(run_id, "model.delta", {"delta": answer[index:index + 240]})
            await asyncio.sleep(0)
        evidence = self._dedupe_evidence(evidence)
        if self.audit is not None:
            citations = await asyncio.to_thread(self.audit.citations, answer)
            self._emit(run_id, "citations.checked", citations)
            summary = await asyncio.to_thread(self.audit.finish)
            self._audit_finished = True
            self._emit(run_id, "audit.completed", summary)
        self.store.update(
            run_id, status="completed", answer=answer,
            provider=provider, model=model,
        )
        self._emit(run_id, "run.completed", {
            "answer": answer,
            "provider": provider,
            "model": model,
            "evidence": [item.model_dump() for item in evidence],
            "termination_reason": termination_reason,
            "final_response_metadata": asdict(metadata or ModelResponseMetadata()),
            "answer_completeness": assess_answer(answer, metadata or ModelResponseMetadata(), require_report=require_report),
        })
        logger.info("agent.run_completed", extra={"run_id": run_id, "provider": provider, "model": model})

    def _emit(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """向指定运行追加一条事件；无返回值。"""
        self.store.add_event(run_id, event_type, payload)

    @staticmethod
    def _static_answer(question: str, artifact: dict[str, Any]) -> str:
        """输入问题与分析产物，在模型禁用时输出确定性概览文本。"""
        overview = str(artifact.get("overview") or "当前项目已有静态分析结果，但没有可用的模型概览。")
        return (
            "当前未启用可调用工具的大模型，因此返回确定性项目分析。\n\n"
            f"用户问题：{question}\n\n{overview}"
        )

    @staticmethod
    def _dedupe_evidence(items: list[AgentEvidence]) -> list[AgentEvidence]:
        """按路径、行号和符号去重证据并保持首次出现顺序。"""
        result = []
        seen = set()
        for item in items:
            key = (item.path, item.line, item.symbol)
            if not item.path or key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result


agent_run_manager = AgentRunManager()

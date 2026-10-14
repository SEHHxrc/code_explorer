"""单变量输出预算、收尾模式或请求超时诊断：复用指定旧配对的冻结条件。

默认只预检，不联网、不写记录。--send 才进行一次参数接受探测和一次 A/B 配对；
仅诊断进程改变一个变量，实际 .env 与正式数据库保持不变。模式诊断不额外探测输出。
新审计存于独立 SQLite，正常 Agent Worker 执行真实请求；不调用旧版手工编排路径。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import time
import traceback
from urllib.error import URLError
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.agents.reporting import execution_policy, phase_message
from backend.app.agents.run_store import AgentRunStore
from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.worker import AgentQueueWorker
from backend.app.experiments.audit import AuditError, AuditRepository
from backend.app.experiments.audit.recorder import AuditRecorder
from backend.app.experiments.audit.policy import digest, contract_digest
from backend.app.experiments.context import SECURITY_EXPERIMENT_INSTRUCTIONS, SecurityExperimentContextBuilder, prepare_experiment_artifact
from backend.app.experiments.contracts import ComparisonRequest
from backend.app.experiments.repository import ExperimentRepository
from backend.app.experiments.report import REPORT_VERSION, assess_answer
from backend.app.experiments.service import ExperimentComparisonService
from backend.app.experiments.tools import create_experiment_tool_registry
from backend.app.llm.budget import BudgetPlanner, initial_message
from backend.app.llm.conversation import ModelMessage
from backend.app.llm.http import get_json, ModelRequestError, RETRY_POLICY_VERSION, MAX_TRANSIENT_RETRIES, MAX_RATE_LIMIT_WAIT_SECONDS
from backend.app.llm.registry import create_model_provider, get_model_api_key, get_model_configuration, get_model_limits
from backend.app.llm.settings import get_final_report_mode
from backend.app.llm.observation import observe_requests
from backend.app.llm.observation import WIRE_OBSERVER
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.providers.openai_provider import OpenAIResponsesProvider
from backend.app.llm import http as model_http
from backend.app.models import Base
from backend.app.services.projects import ProjectArtifactRepository, ProjectRepository


def emit(event: str, **data: Any) -> None:
    """仅打印阶段、允许字段与哈希，不输出项目源码、地址、凭据或隐藏推理。"""
    print(json.dumps({"event": event, **data}, ensure_ascii=True, default=str), flush=True)


def write_result(path: Path, data: Any) -> None:
    """更新本次新建目录中的动态结果；不得用该目录覆盖旧试次。"""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def load_source(comparison_id: str, database_path: Path | None = None) -> dict[str, Any]:
    """从只读正式/诊断数据库获取冻结条件和两组实际首请求，不修改历史记录。"""
    path = (database_path or ROOT / "database.sqlite").resolve()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as database:
        database.row_factory = sqlite3.Row
        row = database.execute("SELECT * FROM experiment_comparisons WHERE id=?", (comparison_id,)).fetchone()
        if row is None:
            raise ValueError("指定原配对不存在。")
        source = dict(row)
        source["project"] = dict(database.execute("SELECT * FROM projects WHERE id=?", (row["project_id"],)).fetchone())
        source["lanes"] = {}
        for strategy, run_id in (("baseline", row["baseline_run_id"]), ("security_evidence", row["graph_run_id"])):
            run = dict(database.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone())
            events = [(item["event_type"], json.loads(item["payload"])) for item in database.execute(
                "SELECT event_type,payload FROM agent_events WHERE run_id=? ORDER BY sequence", (run_id,))]
            audits = [(item["kind"], json.loads(item["payload"])) for item in database.execute(
                "SELECT kind,payload FROM experiment_audit_records WHERE run_id=? ORDER BY id", (run_id,))]
            source["lanes"][strategy] = {"run": run,
                "started": next(value for kind, value in events if kind == "run.started"),
                "budget": next(value for kind, value in events if kind == "model.started"),
                "manifest": next(value for kind, value in audits if kind == "manifest"),
                "request": next(value for kind, value in audits if kind == "request"),
                "timeout_seconds": next(((value.get("transport") or {}).get("timeout_seconds") for kind, value in audits
                                         if kind in {"response", "request_failed"} and value.get("transport")), None)}
        condition = path.parent / "condition.json"
        if condition.is_file():
            previous = json.loads(condition.read_text(encoding="utf-8"))
            if previous.get("new_comparison") == comparison_id:
                source["transport_retry_policy"] = previous.get("transport_retry_policy")
    return source


def verify_conditions(source: dict[str, Any], artifact: dict[str, Any], target: int) -> dict[str, Any]:
    """预检共同契约、原预算及完整首请求；仅移除输出变量比较实际请求哈希。"""
    cfg, limits = get_model_configuration(), get_model_limits()
    if not cfg.configured or not 128 <= target <= 32768:
        raise ValueError("模型未配置或诊断输出值越过应用范围。")
    checks = {}
    for strategy, lane in source["lanes"].items():
        run, prior = lane["run"], lane["budget"]
        policy = execution_policy(run["max_steps"], get_final_report_mode())
        if run["model"] != cfg.model or run["question"] != source["question"] or policy != lane["started"].get("execution_policy"):
            raise ValueError("项目问题、模型或执行策略已经变化，不能作为单变量试验。")
        if digest(artifact) != lane["manifest"]["static_artifact_sha256"]:
            raise ValueError("静态产物已变化，未发送请求。")
        schemas = create_experiment_tool_registry().schemas()
        if contract_digest(SECURITY_EXPERIMENT_INSTRUCTIONS, schemas, REPORT_VERSION, policy) != lane["started"]["contract_hash"]:
            raise ValueError("共同提示词/工具/版本变化，未发送请求。")
        provider = create_model_provider(run["model"])
        packet = SecurityExperimentContextBuilder(with_evidence=strategy != "baseline").build(
            project_id=source["project_id"], question=source["question"],
            artifact=prepare_experiment_artifact(artifact, with_evidence=strategy != "baseline"), model=run["model"])
        payload = provider.request_payload(instructions=SECURITY_EXPERIMENT_INSTRUCTIONS,
            messages=[initial_message(source["question"], packet.prompt_context), phase_message(remaining_rounds=run["max_steps"])],
            tools=schemas, max_output_tokens=limits.max_output_tokens)
        budget = BudgetPlanner(limits, model=run["model"]).require(payload)
        names = ("input_token_budget", "max_output_tokens", "context_window_tokens", "max_context_chars",
                 "safety_margin_tokens", "budget_version", "output_token_parameter", "reasoning_effort")
        extra = ("output_limit_mode", "output_reserve_tokens", "output_reserve_basis")
        if any(budget.get(name) != prior.get(name) for name in names + tuple(name for name in extra if name in prior)) or digest(payload) != lane["request"]["sha256"]:
            raise ValueError("首请求或原预算与旧审计不一致，不能只归因输出变量。")
        checks[strategy] = {"first_request_matches": True, "original_output_tokens": prior["max_output_tokens"],
                            "first_request_sha256": digest(payload)}
    if source["lanes"]["baseline"]["started"]["execution_policy"] != source["lanes"]["security_evidence"]["started"]["execution_policy"]:
        raise ValueError("旧两组不是相同策略，未开始试验。")
    return {"kind": "single_variable_output_budget_diagnostic", "formal_effectiveness_scoring": False,
            "source_comparison": source["id"], "configuration": {"provider": cfg.provider, "model": cfg.model},
            "changed_variable": "max_output_tokens",
            "limits_before": asdict(limits), "target_output_tokens": target, "checks": checks}


def verify_report_mode_conditions(source: dict[str, Any], artifact: dict[str, Any], mode: str) -> dict[str, Any]:
    """先核对原模式完整条件，再声明仅改变收尾模式；不修改环境或提示词。"""
    if mode not in {"tools_none", "history_no_tools"} or mode == get_final_report_mode():
        raise ValueError("须显式选择与原配对不同的合法收尾模式。")
    output = get_model_limits().max_output_tokens
    plan = verify_conditions(source, artifact, output if output is not None else 2400)
    return {**plan, "kind": "single_variable_final_report_mode_diagnostic", "changed_variable": "final_report_mode",
            "target_output_tokens": output, "original_final_report_mode": get_final_report_mode(),
            "target_final_report_mode": mode}


def verify_timeout_conditions(source: dict[str, Any], artifact: dict[str, Any], timeout: float) -> dict[str, Any]:
    """保持模型协议/预算/重试条件不变；仅声明增加诊断进程 socket 超时，不改 .env。"""
    prior = {lane.get("timeout_seconds") for lane in source["lanes"].values()}
    if len(prior) != 1 or None in prior or not next(iter(prior)) < timeout <= 600:
        raise ValueError("两组须有相同的已记录超时，新值必须更大且不超过 600 秒。")
    retry = source.get("transport_retry_policy") or {}
    if any(retry.get(key) != value for key, value in {
        "version": RETRY_POLICY_VERSION, "max_retries": MAX_TRANSIENT_RETRIES,
        "max_rate_limit_wait_seconds": MAX_RATE_LIMIT_WAIT_SECONDS, "fallback_429_wait_seconds": [30, 60],
    }.items()):
        raise ValueError("旧传输重试策略未记录或已变化，不能只归因超时变量。")
    output = get_model_limits().max_output_tokens
    plan = verify_conditions(source, artifact, output if output is not None else 2400)
    return {**plan, "kind": "single_variable_request_timeout_diagnostic", "changed_variable": "request_timeout",
            "target_output_tokens": output, "target_final_report_mode": get_final_report_mode(),
            "original_request_timeout_seconds": next(iter(prior)), "target_request_timeout_seconds": timeout,
            "history_limitation": "重新进行同初始条件调查，不声称逐字重放未持久化隐藏续接状态的晚期请求"}


def safe_timeout_frames(error: BaseException) -> list[dict[str, Any]]:
    """仅返回模块文件名/函数名/行号；不读取帧变量、源码文本、地址或异常消息。"""
    cause = error.reason if isinstance(error, URLError) and isinstance(error.reason, BaseException) else error
    if not isinstance(cause, TimeoutError):
        return []  # HTTPError 也是 URLError，但 429/503 不能冒充 socket 超时。
    return [{"module": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
            for frame in traceback.extract_tb(cause.__traceback__)[-12:]]


async def probe_output_parameter(audit: AuditRepository, store: AgentRunStore, *, project_id: str, user_id: str,
                                 output: Path, requested: int) -> None:
    """仅输出预算试验执行一次参数接受探测；收尾模式试验不调用、不新增费用。"""
    provider = create_model_provider()
    probe_id = "output-capability-probe"
    store.create(run_id=probe_id, project_id=project_id, user_id=user_id,
                 request=AgentRunRequest(question="参数接受探测"))
    with store.session_factory() as session:
        from backend.app.experiments.audit.repository import lock_writes
        lock_writes(session)
        audit.reserve_pair(session, "capability-probe", project_id, user_id, [probe_id])
        session.commit()
    recorder = AuditRecorder(audit, probe_id, user_id)
    result: dict[str, Any] = {}
    try:
        cfg = get_model_configuration()
        try:
            catalog = await get_json(cfg.base_url.rstrip("/") + "/models", {"Authorization": "Bearer " + get_model_api_key()}, 30, transient_retries=0)
            selected = [item for item in catalog.get("data", []) if isinstance(item, dict) and item.get("id") == cfg.model]
            result["advertised_limits"] = [{key: item[key] for key in ("id", "max_output_tokens", "max_completion_tokens", "max_tokens") if key in item} for item in selected]
            if any(isinstance(item.get("max_output_tokens"), int) and item["max_output_tokens"] < requested for item in selected):
                raise ValueError("模型目录披露的输出上限低于诊断值，未继续。")
        except ModelRequestError as error:
            result["catalog_error"] = type(error).__name__
        with observe_requests(recorder):
            turn = await provider.generate_with_tools(instructions="这是参数接受诊断，不是项目分析。仅返回 OK，不调用工具。",
                messages=[ModelMessage(role="user", content="返回 OK。")], tools=[], max_output_tokens=requested,
                transient_retries=0, timeout=60)
        result.update(metadata=asdict(turn.metadata), visible_chars=len(turn.text),
                      accepted_parameter=True, actual_model=turn.model, maximum_capacity_proven=False)
        requests = [row["payload"]["visible"] for row in audit.read(probe_id, user_id)["records"] if row["kind"] == "request"]
        result["actual_output_tokens_requested"] = next((requests[0].get(key) for key in
            ("max_tokens", "max_completion_tokens", "max_output_tokens") if key in requests[0]), None)
        if result["actual_output_tokens_requested"] != requested:
            raise ValueError("参数探测实际发送上限不符。")
        if turn.tool_calls or assess_answer(turn.text, turn.metadata, require_report=False)["status"] != "complete":
            raise ValueError("参数探测未正常返回可见文本，未继续消耗完整配对。")
        store.update(probe_id, status="completed")
    except Exception as error:
        store.update(probe_id, status="failed", error=type(error).__name__)
        result.setdefault("accepted_parameter", False)
        result.update(failure_type=type(error).__name__)
        raise
    finally:
        recorder.finish()
        write_result(output / "parameter_probe.json", result)
        emit("parameter_probe.completed", **result)


async def run_trial(source: dict[str, Any], artifact: dict[str, Any], plan: dict[str, Any], output: Path) -> None:
    """运行一次隔离配对；正常 Worker/真实审计验证输出参数、阶段及唯一变量。"""
    output.relative_to(ROOT / "test" / "experiment_results")
    output.mkdir(parents=True, exist_ok=False)
    original = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
        ROOT / "database.sqlite", ROOT / "backend/.env", ROOT / "backend/storage/artifacts" / (source["project_id"] + ".json"),
        Path(plan.get("source_database", ROOT / "database.sqlite")))}
    plan["original_file_hashes"] = original
    plan["transport_retry_policy"] = {"version": RETRY_POLICY_VERSION, "max_retries": MAX_TRANSIENT_RETRIES,
        "max_rate_limit_wait_seconds": MAX_RATE_LIMIT_WAIT_SECONDS, "fallback_429_wait_seconds": [30, 60],
        "changed_since_source_trial": (source.get("transport_retry_policy") or {}).get("version") != RETRY_POLICY_VERSION,
        "cost_comparison_with_source": "separate_diagnostic_condition"}
    write_result(output / "condition.json", plan)
    engine = create_engine("sqlite:///" + (output / "diagnostic.sqlite").as_posix(), connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    projects, store = ProjectRepository(sessions), AgentRunStore(sessions)
    project = source["project"]
    projects.create(project_id=project["id"], user_id=project["user_id"], source=project["repo_url"],
                    local_path=project["local_path"], file_tree=json.loads(project["file_tree"]))
    mode_trial = plan.get("changed_variable") in {"final_report_mode", "request_timeout"}
    timeout_trial = plan.get("changed_variable") == "request_timeout"
    if mode_trial:
        os.environ["CODE_EXPLORER_LLM_FINAL_REPORT_MODE"] = plan["target_final_report_mode"]
    else:
        os.environ["CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS"] = str(plan["target_output_tokens"])
    audit = AuditRepository(sessions)
    user_id = project["user_id"]
    if not mode_trial:
        await probe_output_parameter(audit, store, project_id=project["id"], user_id=user_id,
                                     output=output, requested=plan["target_output_tokens"])
    comparisons = ExperimentComparisonService(repository=ExperimentRepository(sessions), projects=projects, run_store=store)
    original_start, original_sent = AuditRecorder.start, AuditRecorder.sent
    first_requests_by_run: dict[str, str] = {}
    original_compatible_generate = OpenAICompatibleProvider.generate_with_tools
    original_responses_generate = OpenAIResponsesProvider.generate_with_tools
    original_urlopen = model_http.urlopen

    async def timed_generate(self: Any, **kwargs: Any) -> Any:
        """诊断专用：仅覆盖 timeout，其他模型参数、原生会话与重试预算保持原样。"""
        kwargs["timeout"] = plan["target_request_timeout_seconds"]
        method = original_compatible_generate if isinstance(self, OpenAICompatibleProvider) else original_responses_generate
        return await method(self, **kwargs)

    def traced_urlopen(*args: Any, **kwargs: Any) -> Any:
        """只在诊断进程计量 urllib 建立/发送/读取状态行失败的安全栈投影。"""
        if kwargs.get("timeout") != plan["target_request_timeout_seconds"]:
            raise AuditError("实际传输超时与冻结诊断值不同，停止请求。")
        try:
            return original_urlopen(*args, **kwargs)
        except (TimeoutError, URLError) as error:
            observer = WIRE_OBSERVER.get()
            frames = safe_timeout_frames(error)
            if frames and isinstance(observer, AuditRecorder):
                observer._append("timeout_location", {"step": observer.step, "exception_type": type(error).__name__,
                    "frames": frames, "source_and_locals_not_saved": True})
            raise

    def checked_start(self: AuditRecorder, *args: Any, **kwargs: Any) -> None:
        """本机源码快照须与旧审计完全相同，差异在发送前停止。"""
        original_start(self, *args, **kwargs)
        reference = source["lanes"]["baseline"]["manifest"]["files"]
        if self.manifest != reference:
            raise AuditError("源码已变化，不能进行单变量诊断。")

    def checked_sent(self: AuditRecorder, payload: dict[str, Any], attempt: int, **kwargs: Any) -> int:
        """核对真实上限和收尾定义；模式试验首请求必须原样相同。"""
        key = next((name for name in ("max_tokens", "max_completion_tokens", "max_output_tokens") if name in payload), None)
        actual_output = payload[key] if key else None
        if actual_output != plan["target_output_tokens"]:
            raise AuditError("实际发送的输出上限与诊断条件不同，停止请求。")
        if mode_trial and plan["target_final_report_mode"] == "history_no_tools":
            final = any(item.get("role") == "user" and isinstance(item.get("content"), str)
                        and item["content"].startswith("APPLICATION_STAGE\n当前为最终报告阶段")
                        for item in payload.get("messages", payload.get("input", [])))
            if final and ("tools" in payload or "tool_choice" in payload):
                raise AuditError("history_no_tools 最终请求仍含工具定义或选择字段，停止请求。")
        if self.step == 1:
            previous = plan["limits_before"]["max_output_tokens"]
            neutral = payload if mode_trial else ({**payload, key: previous} if previous is not None else
                {name: value for name, value in payload.items() if name != key})
            if digest(neutral) != first_requests_by_run.get(self.run_id):
                raise AuditError("首请求发生了输出预算以外的变化，停止请求。")
        receipt = original_sent(self, payload, attempt, **kwargs)
        emit("wire_request.verified", run_id=self.run_id, step=self.step, output_parameter=key, output_tokens=actual_output,
             tools_present="tools" in payload, tool_choice=payload.get("tool_choice"))
        return receipt

    from contextlib import ExitStack
    with ExitStack() as patches:
        patches.enter_context(patch.object(ProjectArtifactRepository, "load", return_value=artifact))
        patches.enter_context(patch.object(AuditRecorder, "start", checked_start))
        patches.enter_context(patch.object(AuditRecorder, "sent", checked_sent))
        if timeout_trial:
            patches.enter_context(patch.object(OpenAICompatibleProvider, "generate_with_tools", timed_generate))
            patches.enter_context(patch.object(OpenAIResponsesProvider, "generate_with_tools", timed_generate))
            patches.enter_context(patch.object(model_http, "urlopen", traced_urlopen))
        view = comparisons.create(project["id"], user_id, ComparisonRequest(question=source["question"],
            max_steps=source["lanes"]["baseline"]["run"]["max_steps"]))
        plan["new_comparison"] = view["id"]
        pair = comparisons.repository.get(view["id"], user_id)
        first_requests_by_run.update({pair.baseline_run_id: source["lanes"]["baseline"]["request"]["sha256"],
                                      pair.graph_run_id: source["lanes"]["security_evidence"]["request"]["sha256"]})
        write_result(output / "condition.json", plan)
        worker = AgentQueueWorker(worker_id="single-variable-diagnostic", store=store, projects=projects)
        last_progress: dict[str, Any] | None = None
        last_emitted = 0.0
        for _ in range(2):
            task = asyncio.create_task(worker.run_once())
            while not task.done():
                await asyncio.wait({task}, timeout=5)
                snapshot = comparisons.get(view["id"], user_id)
                progress = {lane: {"status": item["run"]["status"],
                    "model_requests": item["metrics"]["model_requests"], "finish_reasons": item["metrics"]["finish_reasons"],
                    "output_tokens": item["metrics"]["actual_output_tokens"]} for lane, item in snapshot["lanes"].items()}
                if progress != last_progress or time.monotonic() - last_emitted >= 30:
                    emit("trial.progress", comparison_id=view["id"], lanes=progress)
                    last_progress, last_emitted = progress, time.monotonic()
            await task
    result = comparisons.get(view["id"], user_id)
    result["diagnostic_only_not_effectiveness_ranking"] = True
    write_result(output / "summary.json", result)
    integrity = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() == checksum for path, checksum in original.items()}
    write_result(output / "integrity.json", integrity)
    emit("trial.completed", output=str(output), lanes={lane: {"status": item["run"]["status"], "metrics": item["metrics"]}
         for lane, item in result["lanes"].items()}, original_files_unchanged=all(integrity.values()))
    engine.dispose()


def main() -> None:
    """默认只预检；显式授权后才写独立记录并发真实请求，配置只在子进程生效。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-comparison", required=True)
    parser.add_argument("--source-database", type=Path, help="只读旧诊断数据库；默认正式数据库")
    variable = parser.add_mutually_exclusive_group()
    variable.add_argument("--output-tokens", type=int)
    variable.add_argument("--final-report-mode", choices=("tools_none", "history_no_tools"))
    variable.add_argument("--request-timeout", type=float, help="诊断进程单次 socket 超时；须大于原值且不超过 600 秒")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / "backend/.env", override=True)
    source = load_source(args.source_comparison, args.source_database)
    artifact = ProjectArtifactRepository().load(source["project_id"])
    if artifact is None:
        raise ValueError("原项目产物不存在。")
    if args.final_report_mode or args.request_timeout is not None:
        # 恢复选定基准的输出/原模式，而不是将实际 .env 的其他试验条件混入本次。
        original_limit = source["lanes"]["baseline"]["budget"]["max_output_tokens"]
        os.environ["CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS"] = str(original_limit) if original_limit is not None else ""
        os.environ["CODE_EXPLORER_LLM_FINAL_REPORT_MODE"] = source["lanes"]["baseline"]["started"]["execution_policy"]["final_report_mode"]
        plan = verify_timeout_conditions(source, artifact, args.request_timeout) if args.request_timeout is not None else \
            verify_report_mode_conditions(source, artifact, args.final_report_mode)
    else:
        plan = verify_conditions(source, artifact, args.output_tokens if args.output_tokens is not None else 16384)
    plan["source_database"] = str((args.source_database or ROOT / "database.sqlite").resolve())
    emit("conditions.verified", **plan)
    if args.send:
        prefix = "request-timeout-" if args.request_timeout is not None else ("report-mode-" if args.final_report_mode else "output-budget-")
        output = ROOT / "test/experiment_results" / (prefix + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        asyncio.run(run_trial(source, artifact, plan, output))


if __name__ == "__main__":
    main()

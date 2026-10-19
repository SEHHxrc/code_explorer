"""两个现有项目、两档累计预算的诊断入口；默认仅预检，--run-model 才调用模型。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.app.agents.contracts import AgentClaim, AgentRunRequest
from backend.app.agents.run_store import AgentRunStore
from backend.app.agents.worker import AgentQueueWorker
from backend.app.core.logging import configure_logging
from backend.app.experiments.audit.policy import digest
from backend.app.experiments.cumulative import CumulativeContextBuilder, NAVIGATION_PROTOCOL, SELECTION_VERSION, prepare_inputs
from backend.app.experiments.full_source_trial import _heartbeat
from backend.app.experiments.repository import ComparisonRecord, ExperimentRepository
from backend.app.experiments.service import ExperimentComparisonService
from backend.app.llm.registry import get_model_configuration
from backend.app.models import ExperimentAuditRecordModel
from backend.app.services.projects import ProjectArtifactRepository, ProjectRepository
from backend.app.experiments.citations import visible_lines

PROJECTS = ("cf6d1bdc605c4310a4e300421a4e34ef", "9fe4ebcad1d5421aa4599df843ab322b")
QUESTION = "请审查固定范围内的代码安全问题，核实输入、危险实现和对应防护，区分源码行为、安全影响及利用前提；正确排除不成立的风险并明确范围局限。"


def actual_coverage(repository: ExperimentRepository, run_id: str, frozen: dict[str, Any]) -> dict[str, Any]:
    """从有响应的实际请求统计唯一展示行；失败发送和仅返回工具尚未续接的行不冒充已展示。"""
    with repository.session_factory() as session:
        rows = session.query(ExperimentAuditRecordModel.id, ExperimentAuditRecordModel.kind,
                             ExperimentAuditRecordModel.payload).filter_by(run_id=run_id).all()
    resolved = {payload["request_id"] for _, kind, payload in rows if kind == "response"}
    seen: dict[str, set[int]] = {}
    for identifier, kind, payload in rows:
        if kind == "request" and identifier in resolved:
            for path, numbers in visible_lines(payload["visible"]).items():
                seen.setdefault(path, set()).update(numbers)
    shown_bytes, total_lines = 0, 0
    for file in frozen["files"]:
        lines = file["text"].splitlines(keepends=True)
        total_lines += len(lines)
        shown_bytes += sum(len(text.encode("utf-8")) for number, text in enumerate(lines, 1)
                           if number in seen.get(file["path"], set()))
    return {"unique_source_bytes_shown": shown_bytes, "scope_source_bytes": frozen["source_bytes"],
            "unique_source_lines_shown": sum(map(len, seen.values())), "scope_source_lines": total_lines,
            "all_source_shown": sum(map(len, seen.values())) == total_lines,
            "static_bytes_charged": len(frozen["static_text"].encode("utf-8")),
            "cumulative_budget_bytes": frozen["budget_bytes"],
            "full_source_capacity": frozen["source_bytes"] + len(frozen["static_text"].encode("utf-8")) <= frozen["budget_bytes"],
            "shown_lines": {path: sorted(numbers) for path, numbers in seen.items()}}


async def run_trials(*, run_model: bool, project_ids: tuple[str, ...] = PROJECTS,
                     conditions: tuple[str, ...] = ("baseline_fits", "neither_fits"), static_max_bytes: int = 8000) -> dict[str, Any]:
    """先预检所选项目/条件，冻结输入，再复用持久化 Worker；不补跑失败、不自动评分。"""
    repository, projects, artifacts = ExperimentRepository(), ProjectRepository(), ProjectArtifactRepository()
    store = AgentRunStore()
    service = ExperimentComparisonService(repository=repository)
    worker_id = f"cumulative-trial-{os.getpid()}"
    worker = AgentQueueWorker(worker_id=worker_id, store=store)
    jobs = []
    if not project_ids or not conditions:
        raise ValueError("必须明确至少一个项目和预算条件。")
    for project_id in dict.fromkeys(project_ids):
        project = projects.get_owned(project_id, "default_user")
        if project is None:
            raise ValueError("固定项目不存在，请先确认项目清单。")
        root = Path(project.local_path)
        # 预先固定：所有 Python 文件，包括测试与前端 Python；不因风险标签排除文件。
        paths = sorted(path.relative_to(root).as_posix() for path in root.rglob("*.py"))
        pack = artifacts.load(project_id)["security_evidence"]
        for condition in dict.fromkeys(conditions):
            inputs = prepare_inputs(root, paths, pack, condition, static_max_bytes=static_max_bytes)
            for prepared in inputs.values():
                CumulativeContextBuilder().build(project_id=project_id, question=QUESTION, artifact=prepared)
            jobs.append((project_id, condition, inputs))
            frozen = inputs["security_evidence"]["_cumulative_input"]
            print(f"PREPARED project={project_id} condition={condition} files={len(paths)} source_bytes={frozen['source_bytes']} budget={frozen['budget_bytes']} static_bytes={len(frozen['static_text'].encode('utf-8'))}", flush=True)
    implementation = {path.as_posix(): digest(path.read_text(encoding="utf-8-sig"))
                      for path in sorted(Path("backend/app").rglob("*.py"))}
    result: dict[str, Any] = {"protocol": NAVIGATION_PROTOCOL,
        "selection": SELECTION_VERSION, "static_max_bytes": static_max_bytes, "question": QUESTION, "max_steps": 8,
        "implementation_sha256": digest(implementation), "implementation_files": implementation,
        "human_review_status": "pending_not_automatically_scored", "comparisons": []}
    if not run_model:
        return result
    output = Path("backend/storage/experiments") / f"cumulative-{uuid.uuid4().hex}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"RESULT_FILE {output.resolve()}", flush=True)
    # 事前保存全部输入及选样条件；中断也不会丢失已创建试次和资源消耗。
    result["planned_conditions"] = [{"project_id": pid, "condition": condition,
        "source_manifest": inputs["baseline"]["_cumulative_input"]["source_manifest"],
        "budget_bytes": inputs["baseline"]["_cumulative_input"]["budget_bytes"],
        "static_sha256": digest(inputs["security_evidence"]["_cumulative_input"]["static_text"])}
        for pid, condition, inputs in jobs]

    def save() -> None:
        """保存本次新试次的全部进展，不覆盖历史实验文件。"""
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    save()
    order = ["baseline", "security_evidence"]
    random.SystemRandom().shuffle(order)
    for project_id, condition, inputs in jobs:
        run_ids = {name: uuid.uuid4().hex for name in order}
        lanes = list(order)
        random.SystemRandom().shuffle(lanes)
        record = ComparisonRecord(uuid.uuid4().hex, project_id, "default_user", QUESTION,
            run_ids["baseline"], run_ids["security_evidence"], dict(zip(("left", "right"), lanes)), list(order))
        request = AgentRunRequest(question=QUESTION, max_steps=8, model=get_model_configuration().model)
        repository.create_pair(record, request, cumulative_inputs=inputs, claimed_by=worker_id)
        entry: dict[str, Any] = {"project_id": project_id, "condition": condition,
                               "comparison_id": record.comparison_id, "runs": {}, "coverage": {}}
        result["comparisons"].append(entry)
        save()
        print(f"PAIR condition={condition} comparison={record.comparison_id}", flush=True)
        heartbeat = asyncio.create_task(_heartbeat(store, list(run_ids.values()), worker_id))
        try:
            for strategy in order:
                run_id = run_ids[strategy]
                print(f"RUN_START {run_id} strategy={strategy}", flush=True)
                await worker.execute_claim(AgentClaim(run_id=run_id, project_id=project_id, user_id="default_user",
                    question=QUESTION, use_model=True, max_steps=8, model=request.model, strategy="cumulative_" + strategy))
                view = store.get(run_id, "default_user")
                entry["runs"][strategy] = view.model_dump(mode="json")
                entry["coverage"][strategy] = actual_coverage(repository, run_id, inputs[strategy]["_cumulative_input"])
                save()
                print(f"RUN_END {run_id} status={view.status} source_bytes_shown={entry['coverage'][strategy]['unique_source_bytes_shown']}", flush=True)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        entry["comparison"] = service.get(record.comparison_id, "default_user")
        save()
        order.reverse()
    result["valid_pairs"] = sum(item["comparison"]["valid_for_review"] for item in result["comparisons"])
    save()
    return result


def main() -> None:
    """只读加载既有 .env；新协议/新记录不与 v7/v8 结果合并。"""
    load_dotenv(Path("backend/.env"), override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-model", action="store_true")
    parser.add_argument("--static-max-bytes", type=int, default=8000, help="事前固定静态摘要上限；不调用旧选择器反推额度。")
    parser.add_argument("--project-id", action="append", choices=PROJECTS)
    parser.add_argument("--condition", action="append", choices=("baseline_fits", "neither_fits"))
    args = parser.parse_args()
    configure_logging("agent-worker")
    result = asyncio.run(run_trials(run_model=args.run_model, static_max_bytes=args.static_max_bytes,
        project_ids=tuple(args.project_id or PROJECTS), conditions=tuple(args.condition or ("baseline_fits", "neither_fits"))))
    print(json.dumps({"protocol": result["protocol"], "pairs": len(result["comparisons"]),
                      "valid_pairs": result.get("valid_pairs")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

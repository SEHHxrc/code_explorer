"""经用户授权运行真实模型的配对重测；所有新记录与工作区必须隔离保存。

此脚本不属于单元测试，不自动执行，不修改正式数据库、原产物或输入 ZIP。
小/中/大项目的标签沿用用户指定，不按 ZIP 大小推断统计代表性。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.run_store import AgentRunStore
from backend.app.agents.orchestrator import AgentRunManager
from backend.app.agents.observations import OBSERVATION_WINDOW_VERSION
from backend.app.experiments.context import SECURITY_EXPERIMENT_INSTRUCTIONS, SecurityExperimentContextBuilder, prepare_experiment_artifact
from backend.app.experiments.contracts import ComparisonRequest, ExperimentError
from backend.app.experiments.repository import ExperimentRepository
from backend.app.experiments.service import ExperimentComparisonService
from backend.app.experiments.tools import create_experiment_tool_registry
from backend.app.llm.registry import get_model_configuration, get_model_limits
from backend.app.models import Base
from backend.app.services.projects import AnalyzeProjectCommand, ProjectArtifactRepository, ProjectImportService, ProjectRepository, ProjectSource
from backend.app.services.projects.progress import ImportProgressStore
from backend.app.services.project_workspace import ProjectWorkspaceService
from backend.app.services.project_workspace.paths import ProjectWorkspacePaths

SAMPLES = (
    ("small", Path(r"C:\Users\SEHH\Downloads\aes-python-main.zip"), 3),
    ("medium", Path(r"C:\Users\SEHH\Downloads\audio2sheet-main.zip"), 2),
    ("large", Path(r"E:\GitNexus-main.zip"), 1),
)
QUESTION = "项目是否存在对输入处理不当导致RCE或其他安全性问题"


def write_json(path: Path, value: object) -> None:
    """只写入本次新建且隔离的结果目录，保留结构化 JSON。"""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def log(event: str, **values: object) -> None:
    """输出不含凭据或源码正文的阶段日志，供长任务监控。"""
    print(json.dumps({"event": event, **values}, ensure_ascii=False, default=str), flush=True)


async def run(output: Path, sizes: list[str]) -> None:
    """在新数据库和新工作区中导入所选样本，按随机组别顺序运行授权试次。"""
    root = Path(__file__).resolve().parents[1]
    allowed = root / "test" / "experiment_results"
    output.relative_to(allowed)
    if output.exists():
        raise ValueError("Output directory already exists; refusing to overwrite experiment records")
    load_dotenv(root / "backend" / ".env", override=False)
    config, limits = get_model_configuration(), get_model_limits()
    if not config.configured:
        raise ValueError("No configured model; no real experiment has been started")
    output.mkdir(parents=True, exist_ok=False)
    immutable = [root / "database.sqlite", *[path for _, path, _ in SAMPLES], *sorted((root / "backend/storage/artifacts").glob("*.json"))]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in immutable if path.is_file()}
    write_json(output / "protocol.json", {
        "protocol": "static-security-v2", "report_version": "concise-security-v1",
        "question": QUESTION, "configuration": {"provider": config.provider, "model": config.model},
        "limits": asdict(limits), "max_steps": 4, "seed": 20261009,
        "observation_window_version": OBSERVATION_WINDOW_VERSION,
        "instructions_sha256": hashlib.sha256(SECURITY_EXPERIMENT_INSTRUCTIONS.encode("utf-8")).hexdigest(),
        "tool_schema_sha256": hashlib.sha256(json.dumps(create_experiment_tool_registry().schemas(), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "input_hashes": hashes, "samples": [{"size": label, "zip": str(path), "pairs": repeats} for label, path, repeats in SAMPLES if label in sizes],
        "note": "No automatic retry or answer continuation; failures/incomplete answers are retained, not replaced. No effectiveness conclusion without validated findings.",
    })
    engine = create_engine(f"sqlite:///{(output / 'experiments.sqlite').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    projects = ProjectRepository(sessions)
    store = AgentRunStore(sessions)
    artifacts = ProjectArtifactRepository()
    workspace = ProjectWorkspaceService(paths=ProjectWorkspacePaths(output / "workspaces"))
    imports = ProjectImportService(project_repository=projects, artifact_repository=artifacts, workspace_service=workspace)
    comparisons = ExperimentComparisonService(repository=ExperimentRepository(sessions), projects=projects, run_store=store, artifacts=artifacts)
    progress_store = ImportProgressStore()
    summary = []
    random.seed(20261009)
    with patch("backend.app.services.projects.artifacts.ARTIFACT_ROOT", output / "artifacts"):
        for label, archive, repeats in SAMPLES:
            if label not in sizes:
                continue
            log("import.started", size=label)
            try:
                with archive.open("rb") as file:
                    request_id = progress_store.create("retest")
                    task = asyncio.create_task(imports.import_project(
                        AnalyzeProjectCommand(user_id="retest", source=ProjectSource.zip(file, archive.name)),
                        progress=progress_store.claim(request_id, "retest"),
                    ))
                    while not task.done():
                        await asyncio.wait([task], timeout=5)
                        log("import.progress", size=label, **progress_store.snapshot(request_id, "retest"))
                    imported = await task
            except Exception as exc:
                log("import.failed", size=label, error_type=type(exc).__name__, message=str(exc))
                summary.append({"size": label, "import_failed": True, "error_type": type(exc).__name__})
                write_json(output / "summary.json", summary)
                continue
            artifact = artifacts.load(imported.project_id)
            record = projects.get_owned(imported.project_id, "retest")
            assert record is not None and artifact is not None
            write_json(output / f"{label}-coverage.json", artifact.get("security_evidence", {}).get("coverage", {}))
            log("import.completed", size=label, coverage=artifact.get("security_evidence", {}).get("coverage"))
            for index in range(1, repeats + 1):
                try:
                    view = comparisons.create(imported.project_id, "retest", ComparisonRequest(question=QUESTION, max_steps=4))
                except ExperimentError as exc:
                    # 预检失败也保留本次请求结果，不创建运行、不调用模型、不覆盖旧记录。
                    log("pair.preflight_failed", size=label, pair=index, error_type=type(exc).__name__)
                    summary.append({"size": label, "pair": index, "preflight_failed": True, "error_type": type(exc).__name__})
                    write_json(output / "summary.json", summary)
                    continue
                pair = comparisons.repository.get(view["id"], "retest")
                assert pair is not None
                for strategy in pair.execution_order:
                    # TEMPORARY CONTROL GROUP / 临时对照组：只用于用户授权的配对重测。
                    run_id = pair.baseline_run_id if strategy == "baseline" else pair.graph_run_id
                    manager = AgentRunManager(store=store, context_builder=SecurityExperimentContextBuilder(with_evidence=strategy != "baseline"), tools=create_experiment_tool_registry(), instructions=SECURITY_EXPERIMENT_INSTRUCTIONS)
                    prepared = prepare_experiment_artifact(artifact, with_evidence=strategy != "baseline")
                    started = time.monotonic()
                    log("run.started", size=label, pair=index, strategy=strategy, run_id=run_id)
                    task = asyncio.create_task(manager._run(
                        run_id=run_id, project_id=record.project_id, user_id="retest", project_root=record.local_path,
                        artifact=prepared, request=AgentRunRequest(question=QUESTION, max_steps=4, model=config.model),
                    ))
                    last_sequence = 0
                    while not task.done():
                        await asyncio.wait([task], timeout=5)
                        events = store.events_after(run_id, last_sequence)
                        for event in events:
                            last_sequence = event.sequence
                            if event.type in {"model.started", "model.completed", "tool.requested", "run.completed", "run.failed"}:
                                safe = {key: event.payload[key] for key in ("step", "name", "metadata", "answer_completeness", "error") if key in event.payload}
                                log(event.type, size=label, pair=index, strategy=strategy, elapsed=round(time.monotonic() - started, 1), **safe)
                    await task
                    run_view = store.get(run_id, "retest")
                    write_json(output / f"{label}-{index}-{strategy}.json", {"run": run_view.model_dump() if run_view else None, "events": [event.model_dump() for event in store.events_after(run_id, 0)]})
                view = comparisons.get(pair.comparison_id, "retest")
                item = {"size": label, "pair": index, "comparison": view, "groups": pair.blind_order}
                summary.append(item)
                write_json(output / "summary.json", summary)
                log("pair.completed", size=label, pair=index, valid_for_review=view["valid_for_review"], metrics={lane: value["metrics"] for lane, value in view["lanes"].items()})
    unchanged = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in hashes.items()}
    write_json(output / "integrity.json", unchanged)
    engine.dispose()
    if not all(unchanged.values()):
        raise RuntimeError("An original input or record changed externally; inspect integrity.json")
    log("retest.completed", output=str(output), pairs=len(summary), original_files_unchanged=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sizes", nargs="+", choices=[label for label, _, _ in SAMPLES], default=[label for label, _, _ in SAMPLES])
    args = parser.parse_args()
    asyncio.run(run(Path(args.output).resolve(), args.sizes))

"""只读性能/等价验证；不调用模型、不改目标源码、.env 或既有实验。

用法：python -X utf8 -m test.profile_semantic_backend --project-root <目录>
每次在 storage/runtime 新建独立目录，保留 Python/Node CPU profile 和语义指纹。
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import pstats
import shutil
import subprocess
import time
from typing import Any
from unittest.mock import patch

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.language_backends.typescript import TypeScriptSemanticResolver
from backend.app.services.semantic_index.resolution import ProjectSemanticResult
from test.validate_static_analysis import fingerprint


def digest(value: Any) -> str:
    """稳定哈希完整事实，不通过抽样或减少路径掩盖语义变化。"""
    result = hashlib.sha256()
    for part in json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(",", ":")).iterencode(value):
        result.update(part.encode("utf-8"))
    return result.hexdigest()


def run(root: Path, output: Path, *, profile: bool) -> dict[str, Any]:
    """运行固定单线程分析，记录完整语义哈希与分阶段成本；仅创建新诊断目录。"""
    root, output = root.resolve(), output.resolve()
    if not root.is_dir() or output.exists() or root == output or root in output.parents:
        raise ValueError("目标须存在，诊断目录须为源码目录之外的新路径")
    output.mkdir(parents=True)
    before = fingerprint(root)
    timings: dict[str, float] = {}
    original_run = subprocess.run

    def observe_process(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        """可选采样 Node CPU，同时保留唯一传输结果以验证解码等价。"""
        if not isinstance(args, list) or not any(str(part).endswith("worker.mjs") for part in args):
            return original_run(args, **kwargs)
        if profile:
            args = [args[0], "--cpu-prof", f"--cpu-prof-dir={output}", "--cpu-prof-name=node.cpuprofile", *args[1:]]
        started = time.perf_counter()
        result = original_run(args, **kwargs)
        timings["worker_seconds"] = time.perf_counter() - started
        stream = kwargs["stdout"]
        position = stream.tell()
        stream.seek(0)
        with (output / "wire.json").open("xb") as target:
            shutil.copyfileobj(stream, target)
        stream.seek(position)
        return result

    class ObservedResolver(TypeScriptSemanticResolver):
        """只观测生产入口，不改变文件、解析配置、超时或结果。"""

        def resolve(self, *args: Any, **kwargs: Any) -> ProjectSemanticResult:
            """记录工具/解码总耗时，返回原始公共结果。"""
            started = time.perf_counter()
            result = super().resolve(*args, **kwargs)
            timings["resolver_seconds"] = time.perf_counter() - started
            return result

    analyzer = UnifiedCodeAnalyzer(str(root), max_workers=1, semantic_resolver=ObservedResolver())
    original_stage = analyzer._set_progress_stage
    stage_started = time.perf_counter()
    current_stage = "initial"

    def stage(label: str) -> None:
        """记录生产阶段边界；文件范围与执行顺序不变。"""
        nonlocal stage_started, current_stage
        now = time.perf_counter()
        timings[current_stage] = now - stage_started
        current_stage, stage_started = label, now
        original_stage(label)
        print(json.dumps({"stage": label}, ensure_ascii=False), flush=True)

    analyzer._set_progress_stage = stage
    profiler = cProfile.Profile()
    started = time.perf_counter()
    with patch("backend.app.services.language_backends.worker.subprocess.run", side_effect=observe_process):
        if profile:
            profiler.enable()
        analysis = analyzer.run_full_analysis()
        if profile:
            profiler.disable()
    timings[current_stage] = time.perf_counter() - stage_started
    timings["total_seconds"] = time.perf_counter() - started
    if profile:
        profiler.dump_stats(str(output / "python.prof"))
        with (output / "python-top.txt").open("x", encoding="utf-8") as stream:
            pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(45)
    facts = {key: value for key, value in analysis["semantic_index"].items() if key != "backend_runs"}
    runs = analysis["semantic_index"].get("backend_runs", [])
    report = {
        "source_unchanged": before == fingerprint(root), "source_sha256": before[0],
        "files": before[1], "profiled": profile,
        "semantic_sha256": digest(facts), "stats": analysis["stats"],
        "timings": {key: round(value, 4) for key, value in timings.items()},
        "backend": [{key: value for key, value in item.items() if key != "diagnostics"} for item in runs],
    }
    with (output / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"output": str(output), **report}, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", action="store_true")
    arguments = parser.parse_args()
    run(arguments.project_root, arguments.output, profile=arguments.profile)

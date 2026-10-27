"""真实项目只读静态链路验证：不启动目标项目，不调用模型，不更新项目或实验记录。

用法：python -X utf8 -m test.validate_static_analysis --project-root <项目目录>
产物默认写入本工作区 storage/runtime 下的新文件，绝不覆盖旧诊断。
"""

from __future__ import annotations

import argparse
from contextvars import copy_context
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import hashlib
import json
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.program_graph import ProgramGraphService, ProgramGraphArtifact
from backend.app.services.security_analysis import SecurityAnalysisService, SecurityEvidenceCatalog
from backend.app.services.security_analysis.evidence_selection import SecurityEvidenceSelector
from backend.app.services.syntax_analysis.session import reuse_syntax, syntax_statistics

WORKSPACE = Path(__file__).resolve().parents[1]


def fingerprint(root: Path) -> tuple[str, int]:
    """哈希生产收集策略实际纳入的源码清单与正文，不读取 .env 或执行目标代码。"""
    files = UnifiedCodeAnalyzer(str(root))._collect_files()
    return digest_files(root, [Path(path) for path in files]), len(files)


def digest_files(root: Path, files: list[Path]) -> str:
    """对明确文件清单及内容计算稳定指纹；不包含认证配置或运行记录。"""
    material = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


class ObservedProgramGraphService(ProgramGraphService):
    """复用完整生产计算，仅额外保留程序图覆盖与失败摘要，不存储整张图。"""

    def __init__(self) -> None:
        """初始化生产服务及本次运行独享的观测结果。"""
        super().__init__()
        self.observation: dict[str, Any] = {}

    def analyze(self, *args: Any, **kwargs: Any) -> ProgramGraphArtifact:
        """参数原样传给生产入口；返回原图，不修改规则、预算或结果。"""
        graph = super().analyze(*args, **kwargs)
        self.observation = {"schema_version": graph.schema_version,
                            "coverage": graph.coverage.model_dump(), "failures": graph.failures}
        return graph


class PhaseTimer:
    """只观测生产阶段通知，不修改流水线、规则或分析预算。"""

    def __init__(self) -> None:
        self.stage = ""
        self.started = time.perf_counter()
        self.seconds: dict[str, float] = {}

    def phase(self, stage: str, reader: Any = None) -> None:
        """记录上一阶段耗时并刷新 stdout；reader 不参与结果计算。"""
        now = time.perf_counter()
        if self.stage:
            self.seconds[self.stage] = round(now - self.started, 4)
        self.stage, self.started = stage, now
        print(json.dumps({"phase": stage, "seconds": self.seconds}), flush=True)


@reuse_syntax
def validate(root: Path, output: Path) -> dict[str, Any]:
    """运行完整生产分析，再机械核对证据引用、参数槽位与摘要/详情交付。"""
    root, output = root.resolve(), output.resolve()
    if not root.is_dir() or output.exists() or root == output or root in output.parents:
        raise ValueError("项目须存在，输出须为项目目录之外的新文件。")
    before, file_count = fingerprint(root)
    implementation_root = WORKSPACE / "backend/app"
    implementation_files = list(implementation_root.rglob("*.py"))
    implementation_before = digest_files(implementation_root, implementation_files)
    analyzer = UnifiedCodeAnalyzer(str(root))
    started = time.perf_counter()
    print(json.dumps({"phase": "dependency", "files": file_count, "source_sha256": before}), flush=True)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(copy_context().run, analyzer.run_full_analysis)
        while True:
            try:
                analysis = future.result(timeout=20)
                break
            except TimeoutError:
                print(json.dumps({"phase": "dependency", "progress": analyzer.get_progress()}), flush=True)
    dependency_seconds = round(time.perf_counter() - started, 4)
    timer = PhaseTimer()
    observed_graph = ObservedProgramGraphService()
    pack, _ = SecurityAnalysisService(program_graph_service=observed_graph).analyze_with_semantic_index(
        project_root=root, dependency_graph=analysis["dependency_graph"],
        semantic_index=analysis["semantic_index"], analysis_diagnostics=analysis["diagnostics"],
        progress=timer,
    )
    timer.phase("validation")
    catalog = SecurityEvidenceCatalog(pack.model_dump())
    navigation = SecurityEvidenceSelector(pack).build(max_bytes=8000)
    errors: list[dict[str, Any]] = []
    if pack.coverage.files_considered != len(analysis["file_symbols"]):
        errors.append({"reason": "security_scope_count_mismatch",
                       "dependency_files": len(analysis["file_symbols"]),
                       "security_files": pack.coverage.files_considered})
    for candidate in pack.candidates:
        if candidate.source_fact_id not in pack.facts or candidate.sink_fact_id not in pack.facts:
            errors.append({"reason": "missing_endpoint", "id": candidate.candidate_id})
        if not candidate.dataflow_id:
            continue
        flow = pack.dataflows.get(candidate.dataflow_id)
        if flow is None or (flow.source_fact_id, flow.sink_fact_id) != (candidate.source_fact_id, candidate.sink_fact_id):
            errors.append({"reason": "flow_endpoint_mismatch", "id": candidate.candidate_id})
            continue
        delivered, offset = [], 0
        while True:
            page = catalog.detail(candidate.candidate_id, section="flow", offset=offset, limit=10)
            delivered.extend(page["items"])
            if not page["has_more"]:
                break
            if page["next_offset"] <= offset:
                raise ValueError("静态详情分页未前进。")
            offset = page["next_offset"]
        if delivered != [step.model_dump() for step in flow.steps]:
            errors.append({"reason": "delivery_mismatch", "id": candidate.candidate_id})
        if flow.steps:
            sink = flow.steps[-1]
            bound = {name for binding in sink.argument_bindings for name in binding.value_names}
            if bound != set(sink.input_names):
                errors.append({"reason": "sink_binding_mismatch", "id": candidate.candidate_id})
    after, after_count = fingerprint(root)
    if (before, file_count) != (after, after_count):
        errors.append({"reason": "source_changed_during_validation"})
    implementation_after = digest_files(implementation_root, list(implementation_root.rglob("*.py")))
    if implementation_before != implementation_after:
        errors.append({"reason": "implementation_changed_during_validation"})
    report = {
        "kind": "offline_static_validation", "source_sha256": before,
        "source_unchanged": before == after and file_count == after_count,
        "file_count": file_count, "dependency_seconds": dependency_seconds,
        "implementation_sha256": implementation_before,
        "implementation_unchanged": implementation_before == implementation_after,
        "program_graph": observed_graph.observation,
        "syntax_reuse": syntax_statistics(),
        "phase_seconds": timer.seconds, "dependency_stats": analysis["stats"],
        "dependency_coverage": analysis["diagnostics"].get("coverage", {}),
        "dependency_parse_failures": analysis["diagnostics"].get("parse_failures", []),
        "semantic_backends": analysis["semantic_index"].get("backend_runs", []),
        "native_ssa_mapped_functions": len(analysis["semantic_index"].get("native_ssa", {})),
        "value_backend_counts": pack.flow_diagnostics.get("value_backend_counts", {}),
        "language_files": dict(Counter(analyzer.file_lang.values())),
        "checks": {"mechanical_errors": errors, "semantic_correctness": "not_proven",
                   "scope": "production_discovery_policy_including_eligible_tests_and_fixtures"},
        "navigation": navigation, "security_pack": pack.model_dump(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"result": str(output), "source_unchanged": report["source_unchanged"],
                      "dependency_seconds": dependency_seconds, "phase_seconds": timer.seconds,
                      "coverage": pack.coverage.model_dump(), "mechanical_errors": len(errors)}, ensure_ascii=False), flush=True)
    return report


def main() -> None:
    """选择真实项目及新产物位置；完整诊断不是 A/B 或漏洞评分记录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=WORKSPACE / "backend/storage/runtime" / f"static-validation-{uuid4().hex}.json")
    args = parser.parse_args()
    validate(args.project_root, args.output)


if __name__ == "__main__":
    main()

"""静态分析只读基准：可加载受信 Git 版本对照，不调用模型、不改源码/数据库/实验记录。

在项目根目录运行；stdout 只输出规模、计时和完整产物哈希，不输出项目源码。
先用 --baseline-ref <整改前提交> 固定基线，再不加该参数测当前代码；文件清单须相同。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any

WORKSPACE = Path(__file__).resolve().parents[1]
MODULES = (
    "backend.app.services.program_index.identity",
    "backend.app.services.syntax_analysis.javascript_bindings",
    "backend.app.services.security_analysis.language_adapters.treesitter",
    "backend.app.services.security_analysis.language_adapters.javascript",
    "backend.app.services.security_analysis.language_adapters.java",
    "backend.app.services.security_analysis.language_adapters.go",
    "backend.app.services.security_analysis.language_adapters.go.api",
    "backend.app.services.security_analysis.language_adapters.go.collector",
    "backend.app.services.security_analysis.language_adapters.go.routes",
    "backend.app.services.program_graph.frontends.treesitter",
    "backend.app.services.program_graph.passes.reaching_definitions",
)


class BaselineLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """仅在诊断子进程内读取指定的受信应用模块，避免切换用户工作区。"""

    def __init__(self, reference: str) -> None:
        """解析提交并冻结源码到内存；调用者只应使用可信版本。"""
        self.reference = subprocess.check_output(
            ["git", "rev-parse", "--verify", reference + "^{commit}"], cwd=WORKSPACE, text=True,
        ).strip()
        self.sources = {}
        self.packages = set()
        for name in MODULES:
            current = name.replace(".", "/")
            legacy = current.replace("/language_adapters/", "/frontends/")
            for filename in (current + ".py", current + "/__init__.py", legacy + ".py", legacy + "/__init__.py"):
                result = subprocess.run(["git", "show", self.reference + ":" + filename], cwd=WORKSPACE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                if result.returncode == 0:
                    self.sources[name] = result.stdout.decode("utf-8-sig")
                    if filename.endswith("/__init__.py"):
                        self.packages.add(name)
                    break
            if name not in self.sources and name.rsplit(".", 1)[-1] not in {"api", "collector", "routes"}:
                raise ValueError("基线缺少分析模块：" + name)

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        """只覆盖显式模块；其他依赖继续使用本机安装和当前仓库。"""
        return importlib.util.spec_from_loader(fullname, self, is_package=fullname in self.packages) if fullname in self.sources else None

    def create_module(self, spec: Any) -> None:
        """使用默认模块创建，保留正常相对导入语义。"""
        return None

    def exec_module(self, module: Any) -> None:
        """执行本进程的受信基线，不写临时源码或 Git 工作区。"""
        filename = str(WORKSPACE / (module.__name__.replace(".", "/") + ("/__init__.py" if module.__name__ in self.packages else ".py")))
        module.__file__ = filename
        exec(compile(self.sources[module.__name__], filename, "exec"), module.__dict__)


def json_value(value: Any) -> Any:
    """完整投影产物用于等价哈希；不静默跳过不能序列化的字段。"""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if is_dataclass(value):
        return asdict(value)
    raise TypeError("Unsupported benchmark output value")


def digest(value: Any) -> str:
    """只输出完整产物指纹，不把源码正文写到 stdout。"""
    return hashlib.sha256(json.dumps(value, default=json_value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def main() -> None:
    """读取显式文件范围，运行扫描与程序图；不执行目标代码或完整全仓重导入。"""
    parser = argparse.ArgumentParser(description=__doc__)
    location = parser.add_mutually_exclusive_group(required=True)
    location.add_argument("--project-root", type=Path)
    location.add_argument("--project-id")
    parser.add_argument("--files", nargs="+", required=True)
    parser.add_argument("--baseline-ref")
    parser.add_argument("--syntax-reuse", choices=("on", "off"), default="on")
    args = parser.parse_args()
    root = args.project_root
    if args.project_id:
        with sqlite3.connect((WORKSPACE / "database.sqlite").as_uri() + "?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute("SELECT local_path FROM projects WHERE id=?", (args.project_id,)).fetchone()
            if row is None:
                raise ValueError("Project not found")
            root = Path(row[0])
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("Project directory is unavailable")
    sys.path.insert(0, str(WORKSPACE))
    baseline = BaselineLoader(args.baseline_ref) if args.baseline_ref else None
    if baseline is not None:
        sys.meta_path.insert(0, baseline)
    from backend.app.services.syntax_analysis.source_units import source_unit
    from backend.app.services.syntax_analysis.catalog import SOURCE_LANGUAGE_BY_EXTENSION
    from backend.app.services.syntax_analysis.session import syntax_session

    graph = {"nodes": []}
    fingerprints = {}
    for relative in sorted(set(args.files)):
        path = (root / relative).resolve()
        path.relative_to(root)
        raw = path.read_bytes()
        language = SOURCE_LANGUAGE_BY_EXTENSION[path.suffix.lower()]
        if path.suffix.lower() == ".vue":
            language = source_unit(relative, raw, language).language
        graph["nodes"].append({"kind": "module", "file": relative, "lang": language})
        fingerprints[relative] = hashlib.sha256(raw).hexdigest()
    print(json.dumps({"condition": "baseline" if baseline else "current", "baseline_ref": baseline.reference if baseline else None,
                      "python_hash_seed": os.getenv("PYTHONHASHSEED", "random"), "syntax_reuse": args.syntax_reuse,
                      "file_count": len(fingerprints), "source_sha256": digest(fingerprints)}), flush=True)
    with syntax_session(max_estimated_bytes=64 * 1024 * 1024 if args.syntax_reuse == "on" else 1) as session:
        measure(root, graph)
        print(json.dumps({"phase": "syntax_reuse", **session.snapshot()}), flush=True)


def measure(root: Path, graph: dict[str, Any]) -> None:
    """在相同代码和文件范围测量两个投影，缓存条件只改变复用，不改变规则或范围。"""
    from backend.app.services.security_analysis.scanner import SecurityScanner
    from backend.app.services.program_graph import ProgramGraphService

    scanner, graphs = SecurityScanner(), ProgramGraphService()
    started = time.perf_counter()
    scan = scanner.scan(root, dependency_graph=graph)
    seconds = time.perf_counter() - started
    print(json.dumps({"phase": "security_scan", "seconds": round(seconds, 4), "sha256": digest(scan),
                      "files_scanned": scan.files_scanned, "sources": len(scan.sources), "sinks": len(scan.sinks),
                      "diagnostics": len(scan.failures)}), flush=True)
    started = time.perf_counter()
    artifact = graphs.analyze(root, dependency_graph=graph, languages=set(scan.languages_analyzed))
    seconds = time.perf_counter() - started
    print(json.dumps({"phase": "program_graph", "seconds": round(seconds, 4), "sha256": digest(artifact),
                      "coverage": artifact.coverage.model_dump(), "diagnostics": len(artifact.failures)}), flush=True)


if __name__ == "__main__":
    main()

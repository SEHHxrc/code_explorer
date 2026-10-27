"""只读 Go 类型/SSA 进程适配；不安装依赖、不执行目标程序或其驱动插件。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import time
from typing import Literal

from backend.app.services.semantic_index.resolution import ProjectSemanticResult
from backend.app.services.semantic_index.ssa import validate_ssa_function
from .go_environment import GoAnalysisConfig
from .worker import SemanticWorkerError, run_worker
from .wire import decode_table_result

TOOL_DIRECTORY = Path(__file__).resolve().parents[3] / "tools" / "go_semantics"
WORKER_PATH = TOOL_DIRECTORY.parents[1] / "storage" / "runtime" / "tools" / (
    "go-semantics.exe" if os.name == "nt" else "go-semantics")


class GoSemanticResolver:
    """每任务调用锁定的 x/tools；缺少工具/模块/类型信息时保留原分析并明确降级。"""

    def __init__(self, *, timeout_seconds: float = 150,
                 max_output_bytes: int = 64 * 1024 * 1024,
                 configuration: GoAnalysisConfig | None = None) -> None:
        """设置独立工作进程时限和协议体积上限，非整个项目的运行预算。"""
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.configuration = configuration

    def resolve(self, project_root: str, *, file_paths: list[str],
                max_file_bytes: int) -> ProjectSemanticResult:
        """读取明确的 Go 文件清单，返回声明/类型/SSA；不读取 LLM 配置。"""
        paths = sorted({path for path in file_paths if path.endswith(".go")})
        configuration: dict[str, object] = {}

        def failure(status: Literal["failed", "unavailable"], reason: str) -> ProjectSemanticResult:
            """只记录稳定错误码，避免转储进程环境和主机异常文本。"""
            return ProjectSemanticResult(backend="go", status=status, files=paths,
                                         diagnostics=[{"reason": reason}], analysis_configuration=configuration)

        go = shutil.which("go")
        if not WORKER_PATH.is_file() or not go:
            return failure("unavailable", "go_semantic_tool_unavailable")
        request = {"project_root": str(Path(project_root).resolve()),
                   "files": paths, "max_file_bytes": max_file_bytes}
        started = time.perf_counter()
        try:
            config = self.configuration or GoAnalysisConfig.from_environment()
            try:
                environment, configuration = config.prepare(
                    go, project_root=Path(project_root), tool_directory=TOOL_DIRECTORY)
            except (SemanticWorkerError, KeyError):
                return failure("failed", "go_environment_probe_failed")
            except ValueError as exc:
                return failure("failed", str(exc))
            request["build_tags"] = configuration["build_tags"]
            response = run_worker([str(WORKER_PATH)], request, directory=TOOL_DIRECTORY,
                                  environment=environment, timeout_seconds=self.timeout_seconds,
                                  max_output_bytes=self.max_output_bytes)
            decoding = time.perf_counter()
            result = decode_table_result(response.payload)
            if result.backend != "go" or result.version != "x/tools-v0.52.0":
                return failure("failed", "unsupported_semantic_backend")
            self._validate_ssa(result)
            result.implementation_sha256 = hashlib.sha256(WORKER_PATH.read_bytes() + b"".join(
                (TOOL_DIRECTORY / name).read_bytes() for name in ("main.go", "ssa.go", "wire.go", "references.go", "go.mod", "go.sum"))).hexdigest()
            result.transport_bytes = response.transport_bytes
            result.analysis_configuration = configuration
            result.timings_ms.update(response.timings_ms)
            result.timings_ms["protocol_decode"] = (time.perf_counter() - decoding) * 1000
            result.timings_ms["resolver_wall"] = (time.perf_counter() - started) * 1000
            return result
        except SemanticWorkerError as exc:
            return failure("failed", str(exc))
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
            return failure("failed", "invalid_semantic_response")

    @staticmethod
    def _validate_ssa(result: ProjectSemanticResult) -> None:
        """校验 CFG 和 use-def 外键，拒绝损坏协议；不冒充污点或路径语义验证。"""
        for function in result.ssa_functions:
            validate_ssa_function(function)

"""调用锁定版本的 Node/TypeScript 工作进程，不执行项目构建或安装。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import time
from typing import Literal

from backend.app.services.semantic_index.resolution import ProjectSemanticResult
from .worker import SemanticWorkerError, run_worker
from .wire import decode_table_result

TOOL_DIRECTORY = Path(__file__).resolve().parents[3] / "tools" / "typescript_semantics"


class TypeScriptSemanticResolver:
    """以单项目进程复用编译器语义会话，超时或缺依赖时显式降级。"""

    def __init__(self, *, timeout_seconds: float = 90, memory_mb: int = 1536,
                 max_output_bytes: int = 64 * 1024 * 1024) -> None:
        """设置进程时间、V8 堆和协议输出上限；不读取模型或认证配置。"""
        self.timeout_seconds = timeout_seconds
        self.memory_mb = memory_mb
        self.max_output_bytes = max_output_bytes

    def resolve(self, project_root: str, *, file_paths: list[str],
                max_file_bytes: int) -> ProjectSemanticResult:
        """仅解析明确 JS/TS 文件；失败返回诊断，不执行被分析仓库的脚本。"""
        started = time.perf_counter()
        paths = sorted({path for path in file_paths if Path(path).suffix.lower()
                        in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}})
        excluded_count = len(set(file_paths) - set(paths))
        if not paths:
            return ProjectSemanticResult(backend="typescript", status="partial", files=[],
                                         diagnostics=[{"reason": "unsupported_source_form", "count": excluded_count}])
        node = shutil.which("node")
        if not node or not (TOOL_DIRECTORY / "node_modules/typescript/lib/typescript.js").is_file():
            return self._failure(paths, "unavailable", "node_or_typescript_unavailable")
        # 仓库和启动环境不能通过 NODE_OPTIONS/NODE_PATH 注入要执行的 JS。
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() not in {"NODE_OPTIONS", "NODE_PATH"}}
        request = {
            "protocol_version": "1.0", "project_root": str(Path(project_root).resolve()),
            "files": paths, "max_file_bytes": max_file_bytes,
        }
        try:
            response = run_worker([node, f"--max-old-space-size={self.memory_mb}",
                                   str(TOOL_DIRECTORY / "worker.mjs")], request,
                                  directory=TOOL_DIRECTORY, environment=environment,
                                  timeout_seconds=self.timeout_seconds, max_output_bytes=self.max_output_bytes)
            decoding = time.perf_counter()
            result = decode_table_result(response.payload)
            result.timings_ms.update(response.timings_ms)
            result.timings_ms["protocol_decode"] = (time.perf_counter() - decoding) * 1000
            result.transport_bytes = response.transport_bytes
            if result.backend != "typescript" or result.version != "6.0.3":
                return self._failure(paths, "failed", "unsupported_semantic_backend")
            material = b"".join((TOOL_DIRECTORY / name).read_bytes()
                                for name in ("worker.mjs", "project.mjs", "references.mjs", "package-lock.json"))
            result.implementation_sha256 = hashlib.sha256(material).hexdigest()
            if excluded_count:
                result.status = "partial"
                result.diagnostics.append({"reason": "unsupported_source_form", "count": excluded_count})
                result.diagnostic_counts["unsupported_source_form"] = excluded_count
            result.timings_ms["resolver_wall"] = (time.perf_counter() - started) * 1000
            return result
        except SemanticWorkerError as exc:
            return self._failure(paths, "failed", str(exc))
        except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            return self._failure(paths, "failed", "invalid_semantic_response")

    @staticmethod
    def _failure(paths: list[str], status: Literal["unavailable", "failed"], reason: str) -> ProjectSemanticResult:
        """只返回稳定错误码，不把进程异常正文或宿主路径写入分析产物。"""
        return ProjectSemanticResult(
            backend="typescript", status=status, files=paths,
            diagnostics=[{"reason": reason}],
        )

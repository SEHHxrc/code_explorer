"""原生语言后端共用的只读进程传输；统一超时、输出预算与失败分类。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any


class SemanticWorkerError(RuntimeError):
    """稳定错误码；不把主机异常、认证环境或 stderr 正文返回前端。"""


@dataclass(frozen=True)
class WorkerResponse:
    """JSON 原文及传输观测；语言私有协议校验仍由对应适配器负责。"""

    payload: dict[str, Any]
    transport_bytes: int
    timings_ms: dict[str, float]


def run_worker(command: list[str], request: dict[str, Any], *, directory: Path,
               environment: dict[str, str], timeout_seconds: float,
               max_output_bytes: int) -> WorkerResponse:
    """输入明确命令及环境，返回有界 JSON；不使用 shell，不自动安装或重试。"""
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            started = time.perf_counter()
            completed = subprocess.run(
                command, input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
                stdout=output, stderr=errors, env=environment, cwd=directory,
                timeout=timeout_seconds, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if completed.returncode:
                raise SemanticWorkerError("semantic_worker_failed")
            size = output.tell()
            if size > max_output_bytes:
                raise SemanticWorkerError("semantic_output_limit")
            output.seek(0)
            finished = time.perf_counter()
            payload = json.load(output)
            loaded = time.perf_counter()
            if not isinstance(payload, dict):
                raise SemanticWorkerError("invalid_semantic_response")
            return WorkerResponse(payload, size, {"worker_wall": (finished - started) * 1000,
                                                 "json_load": (loaded - finished) * 1000})
    except subprocess.TimeoutExpired as exc:
        raise SemanticWorkerError("semantic_worker_timeout") from exc
    except OSError as exc:
        # Windows 应用控制拒绝运行与语法/JSON 故障是不同问题；不得建议关闭安全策略。
        reason = "semantic_worker_execution_denied" if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 4551, 577, 1260} else "semantic_worker_launch_failed"
        raise SemanticWorkerError(reason) from exc
    except (ValueError, TypeError) as exc:
        raise SemanticWorkerError("invalid_semantic_response") from exc

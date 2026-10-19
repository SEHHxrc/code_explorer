"""实验运行审计记录器；只把实际可见协议投影持久化，不把审计输入喂给模型。"""

import hashlib
import os
import time
import math
from pathlib import Path
from typing import Any

from backend.app.agents.policy import MAX_SOURCE_BYTES
from backend.app.llm.response_metadata import response_channel_sizes
from backend.app.llm.http import RETRY_POLICY_VERSION
from backend.app.experiments.citations import visible_lines, check_citations
from .policy import AuditError, digest, contract_digest, visible_request, visible_response, sanitize
from .repository import AuditRepository


class AuditRecorder:
    """一次实验的私有观察器；实发正文与规划正文分开，测试替身不能冒充实发。"""

    def __init__(self, repository: AuditRepository, run_id: str, user_id: str) -> None:
        """绑定已预留配额的运行，不查询其他用户记录。"""
        self.repository, self.run_id, self.user_id = repository, run_id, user_id
        self.step = 0
        self.manifest: dict[str, Any] = {}
        self.seen: dict[str, set[int]] = {}
        self.final_seen: dict[str, set[int]] = {}
        self.sent_count = 0
        self.resolved_count = 0
        self.pending_lines: dict[int, dict[str, set[int]]] = {}
        self.planned_count = 0
        self.elapsed_ms = 0.0
        self.snapshot_ms = 0.0
        self.prompt_snapshot: dict[str, Any] = {}
        self.network_policy: dict[str, Any] = {}
        self.rejected_requests: set[int] = set()
        self.excluded_request_ms = 0.0
        self.excluded_wait_ms = 0.0
        self.last_excluded_failure: tuple[int, int] | None = None
        self.unknown_rejection_timings = 0

    def _append(self, kind: str, payload: dict[str, Any]) -> int:
        """计量审计自身耗时并原子保存，失败不静默丢弃。"""
        started = time.perf_counter()
        try:
            return self.repository.append(self.run_id, self.user_id, kind, payload)
        finally:
            self.elapsed_ms += (time.perf_counter() - started) * 1000

    def start(self, root: Path, artifact: dict[str, Any], instructions: str, schemas: list[dict[str, Any]], report_version: str,
              execution_policy: dict[str, Any] | None = None) -> None:
        """建立当前源码快照指纹；有界扫描，不改源码、不重新生成静态安全事实。"""
        actual_contract = contract_digest(instructions, schemas, report_version, execution_policy)
        expected = self.repository.contract(self.run_id, self.user_id)
        if expected and expected != actual_contract:
            raise AuditError("API 与 Worker 的提示词/工具契约版本不一致，请重启并创建新配对；未发送模型请求。")
        total = 0
        started = time.monotonic()

        def unreadable(error: OSError) -> None:
            """目录遍历失败不能伪装成完整快照；公共错误不暴露路径。"""
            raise AuditError("项目审计快照无法完整读取，未继续模型请求。") from error

        if not root.is_dir():
            raise AuditError("项目审计根目录不可用，未继续模型请求。")
        for current, directories, files in os.walk(root, onerror=unreadable):
            if any((Path(current) / name).is_symlink() for name in directories):
                raise AuditError("项目含未快照的符号链接，无法建立完整审计输入。")
            directories.sort()
            for name in sorted(files):
                path = Path(current) / name
                if path.is_symlink():
                    raise AuditError("项目含未快照的符号链接，无法建立完整审计输入。")
                size = path.stat().st_size
                total += size
                if len(self.manifest) >= 10000 or total > 256 * 1024 * 1024 or time.monotonic() - started > 30:
                    raise AuditError("项目审计快照超过文件/体量/时间上限，未保存不完整快照。")
                checksum = hashlib.sha256()
                before = path.stat()
                if size <= MAX_SOURCE_BYTES:
                    raw = path.read_bytes()
                    checksum.update(raw)
                    lines = len(raw.decode("utf-8", errors="replace").splitlines())
                else:
                    with path.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            checksum.update(chunk)
                    lines = None
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise AuditError("源码在审计快照期间发生变化，未继续模型请求。")
                self.manifest[path.relative_to(root).as_posix()] = {"sha256": checksum.hexdigest(), "bytes": size, "lines": lines}
        self.snapshot_ms = (time.monotonic() - started) * 1000
        self.network_policy = (execution_policy or {}).get("network_accounting") or {}
        self.prompt_snapshot = {"report_version": report_version, "execution_policy": execution_policy,
            "instructions": sanitize(instructions), "instructions_sha256": digest(instructions),
            "tools_sha256": digest(schemas), "files_sha256": digest(self.manifest), "static_artifact_sha256": digest(artifact)}
        self._append("manifest", {"version": "experiment-audit-v1", **self.prompt_snapshot,
                                  "files": self.manifest, "snapshot_time": "run_start_not_historical_backfill"})

    def planned(self, payload: dict[str, Any], step: int) -> None:
        """记录发送规划，但不把它当作真实 HTTP 请求或已展示源码。"""
        self.step = step
        self.last_excluded_failure = None
        self.planned_count += 1
        self._append("planned_request", {"step": step, "sha256": digest(payload), "visible": visible_request(payload)})

    def sent(self, payload: dict[str, Any], attempt: int, *, wire_sha256: str = "", wire_bytes: int = 0) -> int:
        """传输层在实际发送前调用，记录脱敏投影和精确规范内容哈希。"""
        projected = visible_request(payload)
        identifier = self._append("request", {"step": self.step, "attempt": attempt,
            "sha256": digest(payload), "visible_sha256": digest(projected), "visible": projected,
            "wire_sha256": wire_sha256, "wire_bytes": wire_bytes,
            "hidden_state_not_stored": True, "redacted_projection": True})
        self.sent_count += 1
        self.pending_lines[identifier] = visible_lines(projected)
        return identifier

    def received(self, request_id: int, payload: dict[str, Any], *, diagnostics: dict[str, Any] | None = None) -> None:
        """原始响应仅在内存做可见投影；隐藏推理不会进入数据库。"""
        self._append("response", {"request_id": request_id, "visible": visible_response(payload),
                                  "channel_sizes": response_channel_sizes(payload), "transport": sanitize(diagnostics)})
        self.resolved_count += 1
        self.final_seen = self.pending_lines.pop(request_id, {})
        for path, numbers in self.final_seen.items():
            self.seen.setdefault(path, set()).update(numbers)

    def failed(self, request_id: int, error_type: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        """记录对应尝试失败，公开字段不含上游正文、URL 或认证头。"""
        details = diagnostics or {}
        excluded = (request_id in self.pending_lines and details.get("status_code") in self.network_policy.get("confirmed_pre_model_statuses", [])
                    and error_type == "HTTPError" and self.network_policy.get("basis") == "operator_endpoint_declaration")
        self._append("request_failed", {"request_id": request_id, "error_type": error_type, "transport": sanitize(diagnostics),
                     "model_execution": "not_started_operator_confirmed" if excluded else "unknown",
                     "assumed_model_tokens": 0 if excluded else None})
        self.last_excluded_failure = None
        if excluded:
            self.rejected_requests.add(request_id)
            elapsed = details.get("elapsed_ms")
            if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
                self.unknown_rejection_timings += 1
            else:
                self.excluded_request_ms += elapsed
            self.last_excluded_failure = (self.step, details.get("attempt", 0))
        self.resolved_count += 1
        self.pending_lines.pop(request_id, None)

    def turn(self, text: str, calls: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
        """保存适配器规范化可见响应；模拟 Provider 仍有记录但不会伪装为传输实录。"""
        self._append("visible_turn", sanitize({"step": self.step, "text": text, "tool_calls": calls, "metadata": metadata}))

    def retry_wait(self, attempt: int, requested_seconds: float, elapsed_seconds: float, *,
                   status_code: int | None, completed: bool) -> None:
        """记录本轮失败尝试之后实际等待时间；中断不伪装成已重试，不添加请求/usage。"""
        self._append("retry_wait", {"step": self.step, "after_attempt": attempt, "policy_version": RETRY_POLICY_VERSION,
            "status_code": status_code, "requested_seconds": requested_seconds,
            "elapsed_seconds": round(elapsed_seconds, 3), "completed": completed})
        if self.last_excluded_failure == (self.step, attempt) and status_code in self.network_policy.get("confirmed_pre_model_statuses", []):
            self.excluded_wait_ms += self._elapsed_ms(elapsed_seconds * 1000)
            self.last_excluded_failure = None

    @staticmethod
    def _elapsed_ms(value: Any) -> float:
        """只接受有限非负实测耗时；未知/畸形诊断不用于剔除时间。"""
        return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else 0.0

    def citations(self, answer: str) -> dict[str, Any]:
        """根据实际发送过的正文产生机械警告，正文未审计时明确不可确认。"""
        result = check_citations(answer, self.manifest, self.seen, self.final_seen)
        result["actual_requests_recorded"] = self.sent_count
        self._append("citations", result)
        return result

    def finish(self) -> dict[str, Any]:
        """封存审计状态，归还未使用配额；不把模型响应正确性当作审计完整性。"""
        complete = bool(self.sent_count) and self.sent_count >= self.planned_count and self.resolved_count == self.sent_count
        self.repository.finish(self.run_id, self.user_id, complete=complete)
        return {"version": "experiment-audit-v1", "status": "complete" if complete else "incomplete",
                "actual_requests": self.sent_count, "planned_requests": self.planned_count,
                "audit_snapshot_ms": round(self.snapshot_ms, 2),
                "audit_write_ms": round(self.elapsed_ms, 2),
                "network_accounting": {"policy": self.network_policy,
                    "assumed_zero_token_attempts": len(self.rejected_requests),
                    "unknown_rejection_timings": self.unknown_rejection_timings,
                    "excluded_request_ms": round(self.excluded_request_ms, 2),
                    "excluded_retry_wait_ms": round(self.excluded_wait_ms, 2)} if self.network_policy else None}

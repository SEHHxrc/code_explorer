"""根据安全事实位置提取有界、可追溯的最小源码片段。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .contracts import CodeSnippet, SnippetRole, StaticSecurityFact

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*(['\"]?)[^\s,'\"]+\2"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


class SourceSlicer:
    """从受控项目根目录读取少量源码，并在持久化前遮蔽常见敏感值。"""

    def __init__(
        self,
        project_root: str | Path,
        *,
        context_lines: int = 5,
        max_file_bytes: int = 2 * 1024 * 1024,
        max_snippet_chars: int = 4000,
    ) -> None:
        """输入项目根目录及片段预算，初始化安全切片器。"""
        self.root = Path(project_root).resolve()
        self.context_lines = max(0, context_lines)
        self.max_file_bytes = max_file_bytes
        self.max_snippet_chars = max_snippet_chars
        self._cache: dict[str, list[str] | None] = {}

    def slice_fact(self, fact: StaticSecurityFact, role: SnippetRole) -> CodeSnippet | None:
        """围绕一个静态事实提取带行号的源码片段。"""
        return self.slice_location(
            path=fact.location.path,
            line=fact.location.line,
            end_line=fact.location.end_line,
            role=role,
            identity=fact.fact_id,
        )

    def slice_location(
        self,
        *,
        path: str,
        line: int,
        end_line: int | None,
        role: SnippetRole,
        identity: str,
    ) -> CodeSnippet | None:
        """围绕任意项目内位置提取片段，供中间调用点复用。"""
        lines = self._lines(path)
        if not lines:
            return None
        start = max(1, line - self.context_lines)
        requested_end = end_line or line
        end = min(len(lines), requested_end + self.context_lines)
        selected = lines[start - 1:end]
        numbered = "\n".join(
            f"{line_number}: {line}"
            for line_number, line in enumerate(selected, start=start)
        )
        text = self._redact(numbered)
        truncated = len(text) > self.max_snippet_chars
        if truncated:
            text = text[:self.max_snippet_chars] + "\n...[SNIPPET_TRUNCATED]"
        material = f"{identity}\x1f{role}\x1f{start}\x1f{end}"
        snippet_id = "snippet:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        return CodeSnippet(
            snippet_id=snippet_id,
            role=role,
            path=path,
            start_line=start,
            end_line=end,
            text=text,
            truncated=truncated,
        )

    def _lines(self, relative_path: str) -> list[str] | None:
        """安全读取并缓存项目内文本文件行。"""
        if relative_path in self._cache:
            return self._cache[relative_path]
        try:
            target = (self.root / relative_path).resolve()
            target.relative_to(self.root)
            if not target.is_file() or target.stat().st_size > self.max_file_bytes:
                self._cache[relative_path] = None
                return None
            raw = target.read_bytes()
            if b"\x00" in raw[:4096]:
                self._cache[relative_path] = None
                return None
            lines = raw.decode("utf-8", errors="replace").splitlines()
        except (OSError, ValueError):
            lines = None
        self._cache[relative_path] = lines
        return lines

    @staticmethod
    def _redact(text: str) -> str:
        """遮蔽常见凭据形式，避免静态证据包扩大敏感信息暴露。"""
        redacted = text
        for pattern in SECRET_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted

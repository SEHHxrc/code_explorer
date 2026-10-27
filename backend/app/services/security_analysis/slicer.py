"""根据安全事实位置提取有界、可追溯的最小源码片段。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from backend.app.core.redaction import redact_source

from .contracts import CodeSnippet, SnippetRole, StaticSecurityFact

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
        self._snippets: dict[tuple[str, int, int | None, SnippetRole], CodeSnippet] = {}

    def slice_fact(self, fact: StaticSecurityFact, role: SnippetRole) -> CodeSnippet | None:
        """围绕一个静态事实提取带行号的源码片段。"""
        return self.slice_location(
            path=fact.location.path,
            line=fact.location.line,
            end_line=fact.location.end_line,
            role=role,
        )

    def slice_location(
        self,
        *,
        path: str,
        line: int,
        end_line: int | None,
        role: SnippetRole,
    ) -> CodeSnippet | None:
        """围绕任意项目内位置提取片段，供中间调用点复用。"""
        key = (path, line, end_line, role)
        if key in self._snippets:
            return self._snippets[key]
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
        text = redact_source(numbered, path=path)
        truncated = len(text) > self.max_snippet_chars
        if truncated:
            original_start, original_end = start, end
            focus = min(max(line, start), end)
            selected_lines = [redact_source(f"{focus}: {lines[focus - 1]}", path=path)]
            if len(selected_lines[0]) > self.max_snippet_chars:
                selected_lines = [f"{focus}: [整行超过片段预算，请按该位置读取原始源码；未展示正文]"]
            used = len(selected_lines[0])
            start = end = focus
            while end < original_end:
                next_line = redact_source(f"{end + 1}: {lines[end]}", path=path)
                if used + len(next_line) + 1 > self.max_snippet_chars:
                    break
                selected_lines.append(next_line)
                used += len(next_line) + 1
                end += 1
            while start > original_start:
                previous_line = redact_source(f"{start - 1}: {lines[start - 2]}", path=path)
                if used + len(previous_line) + 1 > self.max_snippet_chars:
                    break
                selected_lines.insert(0, previous_line)
                used += len(previous_line) + 1
                start -= 1
            text = "\n".join(selected_lines)
        # 片段是源码资源，不属于某个事实或候选；不同角色和截断元数据仍不得合并。
        material = json.dumps((path, role, start, end, text, truncated,
                               max(1, line - self.context_lines) if truncated else None,
                               min(len(lines), requested_end + self.context_lines) if truncated else None),
                              ensure_ascii=False, separators=(",", ":"))
        snippet_id = "snippet:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        snippet = CodeSnippet(
            snippet_id=snippet_id,
            role=role,
            path=path,
            start_line=start,
            end_line=end,
            text=text,
            truncated=truncated,
            requested_start_line=max(1, line - self.context_lines) if truncated else None,
            requested_end_line=min(len(lines), requested_end + self.context_lines) if truncated else None,
        )
        self._snippets[key] = snippet
        return snippet

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
